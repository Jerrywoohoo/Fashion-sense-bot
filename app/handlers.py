"""Telegram command and message handlers."""
from __future__ import annotations

import asyncio
import io
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from botocore.exceptions import BotoCoreError, ClientError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.helpers import escape_markdown
from telegram.ext import ContextTypes

from .config import POOL_USER_ID, get_admin_test_password, is_configured_admin
from .database import (
    _is_similar_label,
    clear_user_laundry,
    confirm_capture,
    delete_all_user_garments,
    delete_capture,
    delete_garment,
    find_potential_duplicates,
    get_capture_garments,
    get_garment_by_id,
    get_user_garments,
    get_user_laundry_items,
    get_user_profile,
    image_path_is_referenced,
    insert_capture_garments,
    link_garment_to_existing,
    log_outfit_wear,
    mark_garment_verified,
    save_user_outfit,
    set_garment_laundry_status,
    update_garment_extracted_data,
    upsert_user_profile,
)
from .extractor import (
    CredentialsNotConfiguredError,
    check_aws_credentials_configured,
    compute_image_dhash,
    create_labeled_image_bytes,
    extract_demo_garment_metadata,
    extract_garment_metadata,
    garments_are_identical,
    hamming_distance,
    refine_garment_metadata,
)
from .models import (
    ExtractedGarment,
    GarmentExtractionResult,
    OutfitItemSelection,
    OutfitRecommendation,
    PhotoType,
)
from .paths import IMAGES_DIR, resolve_image_path
from .profile_flow import format_profile_summary
from .stylist_graph import StylistWorkflowError, run_stylist_workflow
from .weather import (
    SINGAPORE_LATITUDE,
    SINGAPORE_LOCATION_NAME,
    SINGAPORE_LONGITUDE,
    GeocodingError,
    geocode_location,
    parse_target_datetime,
)

logger = logging.getLogger(__name__)

_DEMO_MODE_KEY = "demo_mode"
_POOL_MODE_KEY = "pool_mode"
_AWAITING_ADMIN_PW_KEY = "awaiting_admin_pw"
_AWAITING_POOL_PROFILE_KEY = "awaiting_pool_profile"
_STYLE_PROMPT = (
    "🧭 *Where and when are you heading?*\n\n"
    "Please reply in this format separated by commas:\n"
    "`Occasion, City/Location, Time/Date`\n\n"
    "*Examples:*\n"
    "• `Football match, Singapore, tonight`\n"
    "• `Casual dinner, Tokyo, tomorrow 7pm`\n"
    "• `Office meeting, London, today 2pm`"
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return

    profile = get_user_profile(str(user.id))
    safe_first_name = escape_markdown(user.first_name, version=1) if user.first_name else "there"
    welcome_text = (
        f"👋 Welcome, {safe_first_name}!\n\n"
        "I'm your personal styling assistant. Here's what I can do:\n"
        "• 👕 *Wardrobe intake* — send me a photo of a clothing item to catalog it\n"
        "• 🧭 *Contextual styling* — tell me your plans and get an outfit suggestion\n\n"
    )
    if profile is None or profile.get("height_cm") is None:
        welcome_text += "Use /profile to set up your style profile."
    else:
        welcome_text += "Use /style to get dressed or /wardrobe to see your clothes."

    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)


async def wardrobe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return

    is_pool = bool(context.user_data.get(_POOL_MODE_KEY)) if context.user_data else False
    user_id = POOL_USER_ID if is_pool else str(user.id)
    garments = get_user_garments(user_id, verified_only=True)
    if not garments:
        empty_msg = (
            "The test pool wardrobe is empty. Send a photo of a clothing item to add one!"
            if is_pool
            else "Your wardrobe is empty. Send me a photo of a clothing item to add one!"
        )
        await update.message.reply_text(empty_msg)
        return

    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in garments:
        by_cat[g.get("category") or "top"].append(g)

    tops_cnt = len(by_cat.get("top", []))
    bots_cnt = len(by_cat.get("bottom", []))
    outer_cnt = len(by_cat.get("outerwear", []))
    shoes_cnt = len(by_cat.get("footwear", []))
    acc_cnt = len(by_cat.get("accessory", []))

    title_header = "👚 *Test Pool Wardrobe*" if is_pool else "👚 *Your Wardrobe*"
    summary = (
        f"{title_header} ({len(garments)} verified items)\n\n"
        f"• 👕 *Tops*: {tops_cnt}\n"
        f"• 👖 *Bottoms*: {bots_cnt}\n"
        f"• 🧥 *Outerwear*: {outer_cnt}\n"
        f"• 👟 *Footwear*: {shoes_cnt}\n"
        f"• 🎒 *Accessories*: {acc_cnt}\n\n"
        "Tap a category below to browse photos, delete items, or manage laundry:"
    )


    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"👕 Tops ({tops_cnt})", callback_data="wardrobe_cat_top"),
            InlineKeyboardButton(f"👖 Bottoms ({bots_cnt})", callback_data="wardrobe_cat_bottom"),
        ],
        [
            InlineKeyboardButton(f"🧥 Outerwear ({outer_cnt})", callback_data="wardrobe_cat_outerwear"),
            InlineKeyboardButton(f"👟 Footwear ({shoes_cnt})", callback_data="wardrobe_cat_footwear"),
        ],
        [
            InlineKeyboardButton(f"🎒 Accessories ({acc_cnt})", callback_data="wardrobe_cat_accessory"),
            InlineKeyboardButton(f"🖼️ View All ({len(garments)})", callback_data="wardrobe_all"),
        ],
        [
            InlineKeyboardButton("⚠️ Clear Entire Wardrobe", callback_data="wardrobe_clear_ask"),
        ],
    ])

    await update.message.reply_text(summary, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)


async def _send_wardrobe_category_view(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    category: str,
) -> None:
    """Send photo album with labeled badges and direct interactive management buttons."""
    cat_filter = None if category == "all" else category
    garments = _sort_garments_by_item_id(
        get_user_garments(user_id, verified_only=True, category=cat_filter)
    )
    cat_title = "Wardrobe" if category == "all" else category.replace("_", " ").title()

    if not garments:
        await query.message.reply_text(f"No items found in *{cat_title}*.", parse_mode=ParseMode.MARKDOWN)
        return

    # Group garments by unique resolved image path to ensure no duplicate photos
    # are sent if multiple pieces (e.g. shirt and shoes) come from the same OOTD image.
    photos_by_path: dict[Path, list[str]] = defaultdict(list)
    for garment in garments:
        resolved = resolve_image_path(garment.get("image_path"))
        if resolved and resolved.is_file():
            cat = (garment.get("category") or "item").replace("_", " ").title()
            item_id = garment["item_id"]
            desc = _format_item_title(
                garment.get("sub_category") or "piece",
                garment.get("color") or "",
                garment.get("brand"),
            )
            photos_by_path[resolved].append(f"{item_id}: {desc} ({cat})")

    labeled_buffers: list[io.BytesIO] = []
    try:
        for image_path, labels in photos_by_path.items():
            buf = await asyncio.to_thread(create_labeled_image_bytes, image_path, labels)
            labeled_buffers.append(buf)

        if labeled_buffers:
            # Telegram media groups allow up to 10 photos per message
            for i in range(0, len(labeled_buffers), 10):
                chunk = labeled_buffers[i:i + 10]
                if len(chunk) == 1:
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=chunk[0],
                        caption=f"📁 *{cat_title}* ({len(garments)} items)",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                else:
                    media_group = [InputMediaPhoto(media=b) for b in chunk]
                    await context.bot.send_media_group(
                        chat_id=query.message.chat_id,
                        media=media_group,
                    )
    except Exception:
        logger.exception("Failed to send wardrobe photos for category %s", category)
    finally:
        for buf in labeled_buffers:
            buf.close()

    # Build clean 3-button management action keyboard
    await query.message.reply_text(
        f"⚙️ *Manage {cat_title} ({len(garments)} items):*\nChoose an action below to edit, delete, or update laundry status:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_wardrobe_category_menu_keyboard(category),
    )


def _sort_garments_by_item_id(garments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort garments numerically by item ID ascending (e.g. item_101, item_102, item_103)."""
    def _key(g: dict[str, Any]) -> int:
        raw = str(g.get("item_id", ""))
        digits = "".join(filter(str.isdigit, raw))
        return int(digits) if digits else 999999
    return sorted(garments, key=_key)


def _wardrobe_category_menu_keyboard(category: str) -> InlineKeyboardMarkup:
    """Build the clean 3-action keyboard for a wardrobe category."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Edit Item", callback_data=f"wardrobe_act_edit_{category}"),
            InlineKeyboardButton("🗑️ Delete Item", callback_data=f"wardrobe_act_del_{category}"),
        ],
        [
            InlineKeyboardButton("🧺 Laundry Status", callback_data=f"wardrobe_act_laun_{category}"),
        ],
        [
            InlineKeyboardButton("⬅️ Back to Categories", callback_data="wardrobe_menu"),
        ],
    ])


def _build_wardrobe_edit_keyboard(garments: list[dict[str, Any]], category: str) -> InlineKeyboardMarkup:
    """Build item-selection buttons for editing."""
    sorted_garments = _sort_garments_by_item_id(garments)
    buttons: list[list[InlineKeyboardButton]] = []
    for g in sorted_garments:
        item_id = g["item_id"]
        desc = _format_item_title(
            g.get("sub_category") or "item",
            g.get("color") or "",
            g.get("brand"),
        )
        buttons.append([InlineKeyboardButton(f"✏️ {item_id}: {desc}", callback_data=f"wardrobe_doedit_{item_id}_{category}")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data=f"wardrobe_back_cat_{category}")])
    return InlineKeyboardMarkup(buttons)


def _build_wardrobe_delete_keyboard(garments: list[dict[str, Any]], category: str) -> InlineKeyboardMarkup:
    """Build item-selection buttons for deleting."""
    sorted_garments = _sort_garments_by_item_id(garments)
    buttons: list[list[InlineKeyboardButton]] = []
    for g in sorted_garments:
        item_id = g["item_id"]
        desc = _format_item_title(
            g.get("sub_category") or "item",
            g.get("color") or "",
            g.get("brand"),
        )
        buttons.append([InlineKeyboardButton(f"🗑️ Delete {item_id} ({desc})", callback_data=f"wardrobe_dodel_{item_id}_{category}")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data=f"wardrobe_back_cat_{category}")])
    return InlineKeyboardMarkup(buttons)


def _build_wardrobe_laundry_keyboard(garments: list[dict[str, Any]], category: str) -> InlineKeyboardMarkup:
    """Build item-selection buttons for laundry toggling."""
    sorted_garments = _sort_garments_by_item_id(garments)
    buttons: list[list[InlineKeyboardButton]] = []
    for g in sorted_garments:
        item_id = g["item_id"]
        desc = _format_item_title(
            g.get("sub_category") or "item",
            g.get("color") or "",
            g.get("brand"),
        )
        in_laun = bool(g.get("in_laundry"))
        icon = "🧼" if in_laun else "🧺"
        status_text = "In Laundry ➡️ Clean" if in_laun else "Clean ➡️ Laundry"
        buttons.append([InlineKeyboardButton(f"{icon} {item_id}: {desc} ({status_text})", callback_data=f"wardrobe_dolaun_{item_id}_{category}")])
    cat_title = "Wardrobe" if category == "all" else category.replace("_", " ").title()
    buttons.append([InlineKeyboardButton(f"🧼 Mark All {cat_title} as Clean", callback_data=f"wardrobe_cleanall_{category}")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data=f"wardrobe_back_cat_{category}")])
    return InlineKeyboardMarkup(buttons)



async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return

    is_pool = bool(context.user_data.get(_POOL_MODE_KEY)) if context.user_data else False
    user_id = POOL_USER_ID if is_pool else str(user.id)
    allowed_user_ids = {str(user.id), POOL_USER_ID} if is_pool else {str(user.id)}
    args = context.args or []

    if args:
        raw_arg = args[0].strip().lower()
        if raw_arg in ("all", "clear", "reset", "everything"):
            garments = get_user_garments(user_id, verified_only=True)
            count = len(garments)
            if count == 0:
                await message.reply_text("Your wardrobe is already empty.")
                return
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔴 YES, DELETE EVERYTHING", callback_data="wardrobe_clear_confirm"),
                ],
                [
                    InlineKeyboardButton("🟢 Cancel / Keep Wardrobe", callback_data="wardrobe_menu"),
                ],
            ])
            await message.reply_text(
                f"⚠️ *Are you sure you want to delete ALL {count} item(s) from your wardrobe?*\n\n"
                "This will permanently delete all your garments, photo appearances, outfit records, and wear history.\n\n"
                "🚨 *This action cannot be undone.*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            return

        item_id = raw_arg if raw_arg.startswith("item_") else f"item_{raw_arg}"

        garment = get_garment_by_id(item_id)
        if garment is None or garment.get("user_id") not in allowed_user_ids:
            await message.reply_text(
                f"⚠️ Item `{item_id}` was not found in your wardrobe.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        image_path = garment.get("image_path")
        delete_garment(item_id)
        if image_path and not image_path_is_referenced(image_path):
            try:
                resolved = resolve_image_path(image_path)
                if resolved:
                    resolved.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to delete image file %s", image_path)

        sub_cat = escape_markdown((garment.get("sub_category") or "item").replace("_", " "), version=1)
        await message.reply_text(
            f"🗑️ Deleted `{item_id}` (*{sub_cat}*) from your wardrobe.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    garments = get_user_garments(user_id, verified_only=True)
    if not garments:
        empty_msg = (
            "The test pool wardrobe is empty. There are no items to remove."
            if is_pool
            else "Your wardrobe is empty. There are no items to remove."
        )
        await message.reply_text(empty_msg)
        return


    buttons = []
    for garment in garments:
        item_id = garment["item_id"]
        category = (garment.get("category") or "item").title()
        desc = _format_item_title(
            garment.get("sub_category") or "piece",
            garment.get("color") or "",
            garment.get("brand"),
        )
        label = f"🗑️ {item_id}: {desc} ({category})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"delitem_{item_id}")])

    buttons.append([
        InlineKeyboardButton("⚠️ Delete Entire Wardrobe (All Items)", callback_data="wardrobe_clear_ask")
    ])

    await message.reply_text(
        "Select an item to remove, type `/delete <item_id>`, or choose to clear all:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def style_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt the user for comma-separated occasion context, or parse inline args."""
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return

    pool_mode = bool(context.user_data.get(_POOL_MODE_KEY)) if context.user_data else False
    # In pool mode style against the shared pool wardrobe; otherwise the user's own.
    style_user_id = POOL_USER_ID if pool_mode else str(user.id)

    garments = get_user_garments(style_user_id, verified_only=True)
    if len(garments) < 2:
        if pool_mode:
            await message.reply_text(
                "The test pool wardrobe needs at least 2 confirmed items. "
                "Have contributors send garment photos while in pool mode first."
            )
        else:
            await message.reply_text(
                "Your wardrobe needs at least 2 confirmed items before I can style an outfit. "
                "Upload and confirm a few garment photos first."
            )
        return

    full_text = " ".join(context.args).strip() if context.args else ""
    if "," in full_text:
        await _process_style_input(message, context, style_user_id, full_text)
        return

    if context.user_data is not None:
        context.user_data["awaiting_style_input"] = True
        # Remember which user_id to style against when the text reply arrives.
        context.user_data["style_user_id"] = style_user_id
    await message.reply_text(_STYLE_PROMPT, parse_mode=ParseMode.MARKDOWN)


def _format_style_recommendation(recommendation: OutfitRecommendation) -> str:
    selected_pieces = "\n".join(
        f"• `{item.item_id}` — {escape_markdown(item.category.replace('_', ' ').title(), version=1)} / {escape_markdown(item.sub_category.replace('_', ' '), version=1)} "
        f"({escape_markdown(item.primary_color, version=1)})\n  _{escape_markdown(item.role_in_outfit.replace('_', ' '), version=1)}_"
        for item in recommendation.items
    )
    tips = "\n".join(f"• {escape_markdown(tip, version=1)}" for tip in recommendation.styling_tips)
    return (
        f"*{escape_markdown(recommendation.outfit_name, version=1)}*\n"
        f"Occasion: {escape_markdown(recommendation.occasion, version=1)}\n\n"
        f"👕 *Selected Pieces & IDs*\n{selected_pieces}\n\n"
        f"☀️ *Weather Adaptation*\n{escape_markdown(recommendation.weather_reasoning, version=1)}\n\n"
        f"📐 *Proportion & Silhouette Breakdown*\n{escape_markdown(recommendation.proportion_reasoning, version=1)}\n\n"
        f"💡 *Actionable Styling Tips*\n{tips}"
    )


def _style_action_keyboard(items: list[OutfitItemSelection]) -> InlineKeyboardMarkup:
    """Create interactive action buttons for an outfit recommendation."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👕 Wearing This Today", callback_data="act_wear")],
        [
            InlineKeyboardButton("🔄 More Options", callback_data="act_more"),
            InlineKeyboardButton("🧺 In Laundry", callback_data="act_laundry"),
        ],
        [InlineKeyboardButton("👎 Don't Like This Look", callback_data="act_dislike")],
    ])


async def _send_outfit_photos(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    recommendation: OutfitRecommendation,
) -> None:
    """Send photos for the garments selected in the recommendation with item badges.

    The ``user_id`` here is the effective styling user (real or pool). The
    ownership check is intentionally omitted — the stylist graph already
    fetched garments that belong to ``user_id``, so checking again would
    block pool garments from displaying.
    """
    photos_by_path: dict[Path, list[str]] = defaultdict(list)
    for item in recommendation.items:
        garment = get_garment_by_id(item.item_id)
        if garment is None:
            continue
        resolved = resolve_image_path(garment.get("image_path"))
        if resolved and resolved.is_file():
            cat = (garment.get("category") or "item").replace("_", " ").title()
            desc = _format_item_title(
                garment.get("sub_category") or "piece",
                garment.get("color") or "",
                garment.get("brand"),
            )
            photos_by_path[resolved].append(f"{item.item_id}: {desc} ({cat})")

    if not photos_by_path:
        return

    labeled_buffers: list[io.BytesIO] = []
    try:
        for image_path, labels in photos_by_path.items():
            buf = await asyncio.to_thread(create_labeled_image_bytes, image_path, labels)
            labeled_buffers.append(buf)

        # Telegram caps media groups at 10 photos — send in chunks.
        for i in range(0, len(labeled_buffers), 10):
            chunk = labeled_buffers[i : i + 10]
            if len(chunk) == 1:
                await context.bot.send_photo(chat_id=message.chat_id, photo=chunk[0])
            else:
                media_group = [InputMediaPhoto(media=buf) for buf in chunk]
                await context.bot.send_media_group(chat_id=message.chat_id, media=media_group)
    except Exception:
        logger.exception("Could not read selected outfit images for user %s", user_id)
    finally:
        for buf in labeled_buffers:
            buf.close()



async def _process_style_input(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    raw_text: str,
) -> None:
    parts = [p.strip() for p in raw_text.split(",") if p.strip()]
    if not parts:
        await message.reply_text("Please provide your occasion and location separated by a comma.")
        return

    occasion = parts[0]
    location_str = parts[1] if len(parts) >= 2 else "Singapore"
    time_str = parts[2] if len(parts) >= 3 else "now"

    try:
        latitude, longitude, location_name = await asyncio.to_thread(
            geocode_location, location_str
        )
    except GeocodingError:
        await message.reply_text(
            f"⚠️ Could not locate '{location_str}'. Defaulting to Singapore forecast."
        )
        latitude, longitude, location_name = SINGAPORE_LATITUDE, SINGAPORE_LONGITUDE, SINGAPORE_LOCATION_NAME

    target_time = parse_target_datetime(time_str)

    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)
    safe_occ = escape_markdown(occasion, version=1)
    safe_loc = escape_markdown(location_name, version=1)
    safe_time = escape_markdown(time_str, version=1)
    status = await message.reply_text(
        f"🧭 Analyzing wardrobe for *{safe_occ}* in *{safe_loc}* ({safe_time})...",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        recommendation = await asyncio.to_thread(
            run_stylist_workflow,
            user_id,
            occasion,
            latitude=latitude,
            longitude=longitude,
            location_name=location_name,
            target_time=target_time,
            time_label=time_str,
        )
    except StylistWorkflowError as exc:
        logger.info("Styling workflow error: %s", exc)
        await status.edit_text(f"⚠️ I couldn't build an outfit: {exc}")
        return
    except Exception:
        logger.exception("Styling workflow failed for user %s", user_id)
        await status.edit_text("⚠️ Something went wrong while building your outfit. Please try again.")
        return

    current_item_ids = [item.item_id for item in recommendation.items]
    if context.user_data is not None:
        context.user_data["style_session"] = {
            "occasion": occasion,
            "location_name": location_name,
            "latitude": latitude,
            "longitude": longitude,
            "target_time": target_time,
            "time_str": time_str,
            "current_items": current_item_ids,
            "seen_combos": [current_item_ids],
            "excluded_items": [],
        }

    await _send_outfit_photos(message, context, user_id, recommendation)
    await status.edit_text("✨ Outfit ready!")
    await message.reply_text(
        _format_style_recommendation(recommendation),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_style_action_keyboard(recommendation.items),
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or message.text is None:
        return

    user = update.effective_user
    if user is None:
        return

    # Handle item-specific correction to an unconfirmed garment
    pending_item_id = (
        context.user_data.pop(_PENDING_ITEM_EDIT_KEY, None)
        if context.user_data is not None else None
    )
    if pending_item_id:
        is_pool = bool(context.user_data.get(_POOL_MODE_KEY)) if context.user_data else False
        allowed_user_ids = {str(user.id), POOL_USER_ID} if is_pool else {str(user.id)}
        garment = get_garment_by_id(pending_item_id)
        if not garment or garment.get("user_id") not in allowed_user_ids:
            await message.reply_text("That item is no longer available.")
            return
        target_user_id = garment.get("user_id") or (POOL_USER_ID if is_pool else str(user.id))
        feedback = message.text.strip()
        if not feedback:
            await message.reply_text("Please describe what should change.")
            return
        try:
            current_extracted = ExtractedGarment(
                category=garment["category"],
                sub_category=garment["sub_category"],
                primary_color=garment["color"],
                accent_colors=_decode_json_list(garment.get("accent_colors")),
                silhouette_fit=garment["silhouette_fit"],
                fabric_weight=garment["fabric_weight"],
                formality_tier=garment["formality_tier"],
                estimated_brand=garment.get("brand"),
                style_tags=_decode_json_list(garment.get("tags")),
                layering_role=garment["layering_role"],
            )
            single_result = GarmentExtractionResult(
                photo_type=PhotoType.SINGLE_ITEM,
                garments=[current_extracted],
            )
            refined = await asyncio.to_thread(
                refine_garment_metadata, garment["image_path"], single_result, feedback
            )
            update_garment_extracted_data(pending_item_id, refined.garments[0])

            # If editing an already-verified wardrobe item, return dedicated confirmation card
            if garment.get("is_verified") == 1:
                updated_g = refined.garments[0]
                cat = escape_markdown(updated_g.category.replace("_", " ").title(), version=1)
                raw_title = _format_item_title(updated_g.sub_category, updated_g.primary_color, updated_g.estimated_brand, max_words=None)
                title = escape_markdown(raw_title, version=1)
                fit = escape_markdown(updated_g.silhouette_fit.replace("_", " "), version=1)
                color_desc = escape_markdown(updated_g.primary_color, version=1)
                if updated_g.accent_colors:
                    accents = ", ".join(escape_markdown(c, version=1) for c in updated_g.accent_colors)
                    color_desc += f" (accents: {accents})"

                cat_code = updated_g.category.lower().strip()
                nav_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"👚 Back to {cat}", callback_data=f"wardrobe_act_edit_{cat_code}")],
                    [InlineKeyboardButton("📁 All Categories", callback_data="wardrobe_menu")],
                ])

                await message.reply_text(
                    f"✅ *Item `{pending_item_id}` updated successfully!*\n\n"
                    f"• *{title}* ({cat})\n"
                    f"• Color: {color_desc}\n"
                    f"• Fit: {fit}\n\n"
                    "Your wardrobe has been updated.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=nav_keyboard,
                )
                return

            # Otherwise (unverified intake capture), re-evaluate duplicate candidates for this capture
            capture_id = garment.get("capture_id")
            all_garments = get_capture_garments(capture_id) if capture_id else [garment]
            all_extracted = _capture_to_extraction(all_garments)
            all_item_ids = [g["item_id"] for g in all_garments]
            exclude_ids = set(all_item_ids)

            duplicates_by_index = await asyncio.gather(*[
                _confirmed_duplicate_candidates(
                    target_user_id,
                    g,
                    all_garments[idx]["image_path"],
                    exclude_capture_id=capture_id,
                    exclude_item_ids=exclude_ids,
                )
                for idx, g in enumerate(all_extracted.garments)
            ])
            duplicates = dict(zip(all_item_ids, duplicates_by_index, strict=True))
            duplicate_links = {
                item_id: candidates[0]["item_id"]
                for item_id, candidates in duplicates.items() if candidates
            }
            if capture_id and context.user_data is not None:
                context.user_data.setdefault(_DUPLICATE_LINKS_KEY, {})[capture_id] = duplicate_links.copy()
                context.user_data.setdefault("detected_duplicates", {})[capture_id] = duplicate_links.copy()

            if any(candidates for candidates in duplicates.values()):
                await _send_duplicate_comparison_photos(message, context, duplicates)

            await message.reply_text(
                f"✅ Updated details for `{pending_item_id}`!\n\n"
                + _format_extraction_summary(all_item_ids, all_extracted, duplicates),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_verification_keyboard(
                    capture_id or "",
                    bool(duplicate_links),
                    all_item_ids,
                    detected_links=duplicate_links,
                    active_links=duplicate_links.copy(),
                ),
            )
            return
        except CredentialsNotConfiguredError:
            await message.reply_text("AI correction is unavailable until AWS Bedrock is configured.")
            if context.user_data is not None:
                context.user_data[_PENDING_ITEM_EDIT_KEY] = pending_item_id
            return
        except Exception:
            logger.exception("Could not apply item correction for %s", pending_item_id)
            await message.reply_text("I couldn't apply that correction. Please try phrasing it differently.")
            if context.user_data is not None:
                context.user_data[_PENDING_ITEM_EDIT_KEY] = pending_item_id
            return

    # Handle plain-language corrections to an unconfirmed vision extraction (capture-wide).
    capture_id = (
        context.user_data.pop(_PENDING_CORRECTION_KEY, None)
        if context.user_data is not None else None
    )
    if capture_id:
        is_pool = bool(context.user_data.get(_POOL_MODE_KEY)) if context.user_data else False
        allowed_user_ids = {str(user.id), POOL_USER_ID} if is_pool else {str(user.id)}
        garments = _capture_owned_by(capture_id, str(user.id), allowed_user_ids=allowed_user_ids)
        feedback = message.text.strip()
        if not garments:
            await message.reply_text("That pending capture is no longer available.")
            return
        target_user_id = garments[0]["user_id"]
        if not feedback:
            await message.reply_text("Please describe what should change.")
            return
        try:
            current = _capture_to_extraction(garments)
            refined = await asyncio.to_thread(
                refine_garment_metadata, garments[0]["image_path"], current, feedback
            )
            item_ids = [garment["item_id"] for garment in garments]
            exclude_ids = set(item_ids)
            for item_id, corrected in zip(item_ids, refined.garments, strict=True):
                update_garment_extracted_data(item_id, corrected)
            duplicates_by_index = await asyncio.gather(*[
                _confirmed_duplicate_candidates(
                    target_user_id,
                    garment,
                    garments[idx]["image_path"],
                    exclude_capture_id=capture_id,
                    exclude_item_ids=exclude_ids,
                )
                for idx, garment in enumerate(refined.garments)
            ])
        except CredentialsNotConfiguredError:
            await message.reply_text("AI correction is unavailable until AWS Bedrock is configured.")
            context.user_data[_PENDING_CORRECTION_KEY] = capture_id
            return
        except (BotoCoreError, ClientError, ValueError):
            logger.exception("Could not apply correction for capture %s", capture_id)
            await message.reply_text("I couldn't apply that correction. Please try phrasing it differently.")
            context.user_data[_PENDING_CORRECTION_KEY] = capture_id
            return


        item_ids = [garment["item_id"] for garment in garments]
        duplicates = dict(zip(item_ids, duplicates_by_index, strict=True))
        duplicate_links = {
            item_id: candidates[0]["item_id"]
            for item_id, candidates in duplicates.items() if candidates
        }
        if context.user_data is not None:
            context.user_data.setdefault(_DUPLICATE_LINKS_KEY, {})[capture_id] = duplicate_links.copy()
            context.user_data.setdefault("detected_duplicates", {})[capture_id] = duplicate_links.copy()

        if any(candidates for candidates in duplicates.values()):
            await _send_duplicate_comparison_photos(message, context, duplicates)

        await message.reply_text(
            "✅ I updated the extraction based on your note.\n\n"
            + _format_extraction_summary(item_ids, refined, duplicates),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_verification_keyboard(
                capture_id,
                bool(duplicate_links),
                item_ids,
                detected_links=duplicate_links,
                active_links=duplicate_links.copy(),
            ),
        )
        return

    # Handle OOTD Occasion Learning:
    if context.user_data is not None and "pending_ootd_combo" in context.user_data:
        item_ids = context.user_data.pop("pending_ootd_combo")
        is_pool = bool(context.user_data.get(_POOL_MODE_KEY)) if context.user_data else False
        target_user_id = POOL_USER_ID if is_pool else str(user.id)
        occasion_text = message.text.strip()
        save_user_outfit(target_user_id, occasion_text, item_ids)
        formatted_ids = ", ".join(f"`{i}`" for i in item_ids)
        safe_occ = escape_markdown(occasion_text, version=1)
        await message.reply_text(
            f"🧠 *Saved!* I've learned that you wear {formatted_ids} for *{safe_occ}*. "
            "I'll use this example when you ask for outfit ideas!",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Handle admin pool password entry:
    if context.user_data is not None and context.user_data.get(_AWAITING_ADMIN_PW_KEY):
        context.user_data.pop(_AWAITING_ADMIN_PW_KEY, None)
        entered = message.text.strip() if message.text else ""
        expected = get_admin_test_password()
        if expected and entered == expected:
            context.user_data[_POOL_MODE_KEY] = True
            await message.reply_text(
                "✅ *Pool mode activated!*\n\n"
                "All clothing photos you send will now go directly into the shared test wardrobe.\n\n"
                "• Send clothes photos anytime to populate the pool\n"
                "• Use /profile to view or set the test styling profile\n"
                "• Use /style to test outfit recommendations\n"
                "• Use /adminlive when you want to exit pool mode",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await message.reply_text(
                "❌ Wrong password. Pool mode not activated.\n"
                "Try /admintest again if you need access."
            )
        return


    # Handle /style input:
    if context.user_data is not None and context.user_data.get("awaiting_style_input"):
        context.user_data["awaiting_style_input"] = False
        # Use the stored target user_id (pool or real) set by style_command.
        style_uid = context.user_data.pop("style_user_id", None) or str(user.id)
        await _process_style_input(message, context, style_uid, message.text)
        return

    await message.reply_text(
        "I'm not sure how to respond to that. Type /style to get dressed or /help to see commands!"

    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    help_text = (
        "*Available commands:*\n"
        "/start — Welcome message and quick intro\n"
        "/profile — View or edit style profile\n"
        "/wardrobe — View confirmed items\n"
        "/style — Get an outfit suggestion\n"
        "/laundry — Check or clean clothes in laundry\n"
        "/delete [item_id] — Remove an item from wardrobe\n"
        "/help — Show this message\n"
        "/cancel — Cancel current operation\n\n"
        "Send a photo to add a garment to your wardrobe."
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def admin_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gate entry into shared-pool test mode via a password.

    Any user who knows ADMIN_TEST_PASSWORD can enter pool mode.
    Photos uploaded in pool mode are stored under POOL_USER_ID so all
    contributors build a single shared wardrobe for stress-testing.
    """
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return

    if not get_admin_test_password():
        await message.reply_text(
            "Pool test mode is not configured. "
            "Set ADMIN_TEST_PASSWORD in your .env file to enable it."
        )
        return

    # If already in pool mode, confirm status.
    if context.user_data and context.user_data.get(_POOL_MODE_KEY):
        await message.reply_text(
            "🧪 *Pool mode is active.*\n\n"
            "Your photos are going into the shared test wardrobe.\n"
            "• Send clothes photos to add items to the pool\n"
            "• Send /profile to view or edit the test style profile\n"
            "• Send /adminlive to leave pool mode",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if context.user_data is not None:
        context.user_data[_AWAITING_ADMIN_PW_KEY] = True
    await message.reply_text(
        "🔑 Please send the admin password to enter pool test mode:"
    )


async def admin_live_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Exit pool test mode and return to normal operation. Never deletes data."""
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return

    was_in_pool = bool(context.user_data.get(_POOL_MODE_KEY)) if context.user_data else False
    if context.user_data is not None:
        context.user_data.pop(_POOL_MODE_KEY, None)
        context.user_data.pop(_AWAITING_ADMIN_PW_KEY, None)
        context.user_data.pop(_AWAITING_POOL_PROFILE_KEY, None)
        context.user_data.pop("style_user_id", None)

    if was_in_pool:
        await message.reply_text(
            "✅ Pool mode is OFF. All uploaded test garments remain safely in the database, and you are back to your personal wardrobe."
        )
    else:
        await message.reply_text("You were not in pool mode.")




_DUPLICATE_LINKS_KEY = "capture_duplicate_links"
_PENDING_CORRECTION_KEY = "pending_capture_correction"
_PENDING_ITEM_EDIT_KEY = "pending_item_correction"
_PHOTO_BATCHES: dict[str, dict[str, Any]] = {}


def _format_item_title(
    sub_category: str,
    color: str = "",
    brand: Optional[str] = None,
    max_words: Optional[int] = 4,
) -> str:
    """Format a readable garment title including brand/nickname if available.
    
    If max_words is specified (default 4), limits the title to at most that many
    words for clean display on image badges and inline buttons without overflowing.
    Full descriptions in the database remain completely un-truncated for LLM reasoning.
    """
    clean_subcat = (sub_category or "piece").replace("_", " ").strip()
    clean_color = (color or "").strip()
    clean_brand = (brand or "").strip()

    parts = []
    if clean_brand and clean_brand.lower() not in clean_subcat.lower():
        parts.append(clean_brand)
    if clean_color and clean_color.lower() not in clean_subcat.lower():
        parts.append(clean_color)
    parts.append(clean_subcat)
    full_title = " ".join(parts).strip()

    if max_words is not None and max_words > 0:
        words = full_title.split()
        if len(words) > max_words:
            return " ".join(words[:max_words])
    return full_title


def _format_extraction_summary(
    item_ids: list[str],
    extraction: GarmentExtractionResult,
    duplicates: dict[str, list[dict[str, Any]]],
) -> str:
    count = len(item_ids)
    if extraction.photo_type == PhotoType.OOTD:
        aesthetic = escape_markdown((extraction.overall_aesthetic or "unclassified outfit").replace("_", " "), version=1)
        heading = f"✨ *OOTD Detected ({aesthetic}) — {count} Items Found:*"
    else:
        heading = f"✨ *{count} Item{'s' if count > 1 else ''} Found:*"

    lines = [heading]
    for item_id, garment in zip(item_ids, extraction.garments, strict=True):
        cat = escape_markdown(garment.category.replace("_", " ").title(), version=1)
        raw_title = _format_item_title(garment.sub_category, garment.primary_color, garment.estimated_brand)
        title = escape_markdown(raw_title, version=1)
        fit = escape_markdown(garment.silhouette_fit.replace("_", " "), version=1)
        color_desc = escape_markdown(garment.primary_color, version=1)
        if garment.accent_colors:
            accents = ", ".join(escape_markdown(c, version=1) for c in garment.accent_colors)
            color_desc += f" (accents: {accents})"

        lines.append(f"• `{item_id}`: {cat} (*{title}*) — {color_desc}, {fit} fit")
        candidates = duplicates.get(item_id, [])
        if candidates:
            matched = candidates[0]
            matched_id = matched["item_id"]
            matched_raw_title = _format_item_title(
                matched.get("sub_category") or "style",
                matched.get("color") or "",
                matched.get("brand"),
            )
            matched_title = escape_markdown(matched_raw_title, version=1)
            lines.append(f"⚠️ Item `{item_id}` looks similar to saved `{matched_id}` (*{matched_title}*).")
    lines.append("\nReview the items below before adding them to your wardrobe.")
    return "\n".join(lines)


async def _send_duplicate_comparison_photos(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    duplicates: dict[str, list[dict[str, Any]]],
) -> None:
    """Send photos of matched duplicate candidates with item badges and clear match captions."""
    dups_by_path: dict[Path, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for new_item_id, candidates in duplicates.items():
        if not candidates:
            continue
        matched = candidates[0]
        resolved = resolve_image_path(matched.get("image_path"))
        if resolved and resolved.is_file():
            dups_by_path[resolved].append((new_item_id, matched))

    for resolved_path, matched_pairs in dups_by_path.items():
        labels: list[str] = []
        caption_lines: list[str] = ["🔍 *Previously Saved in Your Wardrobe:*"]
        for new_item_id, matched in matched_pairs:
            saved_id = matched.get("item_id", "saved item")
            cat = (matched.get("category") or "item").replace("_", " ").title()
            desc = _format_item_title(
                matched.get("sub_category") or "piece",
                matched.get("color") or "",
                matched.get("brand"),
            )
            labels.append(f"Saved {saved_id}: {desc} ({cat})")
            safe_desc = escape_markdown(desc, version=1)
            caption_lines.append(f"• Uploaded `{new_item_id}` matches saved `{saved_id}` ({safe_desc})")

        caption_lines.append("\nCompare with your upload before choosing to link or keep as new:")
        caption = "\n".join(caption_lines)

        try:
            buf = await asyncio.to_thread(create_labeled_image_bytes, resolved_path, labels)
            try:
                await context.bot.send_photo(
                    chat_id=message.chat_id,
                    photo=buf,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                )
            finally:
                buf.close()
        except Exception:
            logger.exception("Could not send duplicate comparison photo for %s", resolved_path)


def _verification_keyboard(
    capture_id: str,
    has_duplicates: bool,
    item_ids: list[str] | int,
    detected_links: Optional[dict[str, str]] = None,
    active_links: Optional[dict[str, str]] = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    items: list[str] = item_ids if isinstance(item_ids, list) else [f"item_{i}" for i in range(item_ids)]
    count = len(items)

    det = detected_links or {}
    act = active_links if active_links is not None else det

    if det:
        if len(det) == 1:
            i_id, saved_id = next(iter(det.items()))
            if i_id in act:
                rows.append([
                    InlineKeyboardButton(f"🔗 Link {i_id} → {saved_id}", callback_data=f"confirm_apply_{capture_id}"),
                    InlineKeyboardButton("➕ Keep as New", callback_data=f"confirm_{capture_id}"),
                ])
            else:
                rows.append([
                    InlineKeyboardButton(f"➕ Keep {i_id} as New", callback_data=f"confirm_{capture_id}"),
                    InlineKeyboardButton(f"🔗 Link {i_id} → {saved_id}", callback_data=f"confirm_apply_{capture_id}"),
                ])
            rows.append([InlineKeyboardButton("✅ Confirm All", callback_data=f"confirm_apply_{capture_id}")])
        else:
            # Multiple duplicate candidates across the upload/OOTD
            for i_id, saved_id in det.items():
                if i_id in act:
                    btn_text = f"✅ 🔗 Link {i_id} → {saved_id} (Tap to keep new)"
                else:
                    btn_text = f"➕ {i_id}: Keep as New (Tap to link)"
                rows.append([InlineKeyboardButton(btn_text, callback_data=f"confirm_toggle_{i_id}_{capture_id}")])

            rows.append([
                InlineKeyboardButton("✅ Confirm Selections", callback_data=f"confirm_apply_{capture_id}"),
                InlineKeyboardButton("➕ Keep All as New", callback_data=f"confirm_{capture_id}"),
            ])
    elif has_duplicates:
        rows.append(
            [
                InlineKeyboardButton("🔗 Link Duplicates", callback_data=f"confirm_apply_{capture_id}"),
                InlineKeyboardButton("➕ Keep as New", callback_data=f"confirm_{capture_id}"),
            ]
        )
        rows.append([InlineKeyboardButton("✅ Confirm All", callback_data=f"confirm_{capture_id}")])
    else:
        rows.append([InlineKeyboardButton(f"✅ Confirm All ({count} item{'s' if count > 1 else ''})", callback_data=f"confirm_{capture_id}")])

    # Granular Item-Specific Edit Buttons (2 per row)
    if items:
        edit_buttons = [
            InlineKeyboardButton(f"✏️ Edit {i_id}", callback_data=f"edititem_{i_id}")
            for i_id in items
        ]
        for i in range(0, len(edit_buttons), 2):
            rows.append(edit_buttons[i:i + 2])

    rows.append([
        InlineKeyboardButton("🔗 Already in Wardrobe", callback_data=f"manlink_start_{capture_id}"),
        InlineKeyboardButton("🗑️ Discard", callback_data=f"delete_{capture_id}"),
    ])
    return InlineKeyboardMarkup(rows)


def _capture_owned_by(
    capture_id: str, user_id: str, allowed_user_ids: Optional[set[str]] = None
) -> list[dict[str, Any]]:
    garments = get_capture_garments(capture_id)
    if not garments:
        return []
    valid_ids = allowed_user_ids or {user_id}
    if any(garment["user_id"] not in valid_ids for garment in garments):
        return []
    return garments



def _clear_duplicate_links(context: ContextTypes.DEFAULT_TYPE, capture_id: str) -> None:
    links = context.user_data.get(_DUPLICATE_LINKS_KEY, {})
    if isinstance(links, dict):
        links.pop(capture_id, None)


def _decode_json_list(value: Any) -> list[str]:
    """Read a JSON list stored by SQLite, returning a safe list for Pydantic."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return [str(item) for item in value] if isinstance(value, list) else []


async def _duplicate_links_for_capture(
    context: ContextTypes.DEFAULT_TYPE,
    capture_id: str,
    user_id: str,
    garments: list[dict[str, Any]],
) -> dict[str, str]:
    stored = context.user_data.get(_DUPLICATE_LINKS_KEY, {})
    if isinstance(stored, dict) and isinstance(stored.get(capture_id), dict):
        return stored[capture_id]

    links: dict[str, str] = {}
    for row in garments:
        try:
            extracted = ExtractedGarment(
                category=row["category"],
                sub_category=row["sub_category"],
                primary_color=row["color"],
                accent_colors=_decode_json_list(row.get("accent_colors")),
                silhouette_fit=row["silhouette_fit"],
                fabric_weight=row["fabric_weight"],
                formality_tier=row["formality_tier"],
                estimated_brand=row.get("brand"),
                style_tags=_decode_json_list(row.get("tags")),
                layering_role=row["layering_role"],
            )
        except (TypeError, ValueError):
            continue
        candidates = await _confirmed_duplicate_candidates(
            user_id,
            extracted,
            row.get("image_path"),
            exclude_capture_id=capture_id,
            exclude_item_ids={r["item_id"] for r in garments},
        )
        if candidates:
            links[row["item_id"]] = candidates[0]["item_id"]
    return links


async def _confirmed_duplicate_candidates(
    user_id: str,
    garment: ExtractedGarment,
    incoming_image_path: Optional[str] = None,
    exclude_capture_id: Optional[str] = None,
    exclude_item_ids: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Use visual hashing + metadata heuristics + Bedrock fallback to detect duplicates.

    Compares the incoming garment against previously verified wardrobe items only,
    explicitly excluding any item from the current upload/capture.
    """
    shortlist = find_potential_duplicates(user_id, garment)
    confirmed: list[dict[str, Any]] = []
    seen_ids: set[str] = set(exclude_item_ids or set())
    if exclude_capture_id:
        pending_rows = get_capture_garments(exclude_capture_id)
        seen_ids.update(r["item_id"] for r in pending_rows)

    # 1. Visual Perceptual Hash Check (compares against verified saved items only)
    incoming_resolved = resolve_image_path(incoming_image_path) if incoming_image_path else None
    incoming_hash = compute_image_dhash(str(incoming_resolved)) if incoming_resolved else ""
    if incoming_hash:
        verified_garments = get_user_garments(
            user_id, verified_only=True, category=garment.category
        )
        for g in verified_garments:
            if g["item_id"] in seen_ids:
                continue
            cand_img = resolve_image_path(g.get("image_path"))
            # If candidate is the exact same file path on disk as the new upload, ignore
            if cand_img and incoming_resolved and cand_img.resolve() == incoming_resolved.resolve():
                continue
            if cand_img and cand_img.is_file():
                cand_hash = compute_image_dhash(str(cand_img))
                dist = hamming_distance(incoming_hash, cand_hash)
                if dist <= 6:  # Near-identical or identical photo from previous upload
                    g_dict = dict(g)
                    g_dict["match_reason"] = "identical photo detected"
                    confirmed.append(g_dict)
                    seen_ids.add(g_dict["item_id"])

    # 2. Strong Metadata Deterministic Check from shortlist
    for candidate in shortlist:
        if candidate["item_id"] in seen_ids:
            continue

        candidate["tags"] = _decode_json_list(candidate.get("tags"))
        candidate["accent_colors"] = _decode_json_list(candidate.get("accent_colors"))

        exact_color = _is_similar_label(garment.primary_color, candidate.get("color"))
        exact_subcat = _is_similar_label(garment.sub_category, candidate.get("sub_category"))

        if exact_color and exact_subcat:
            candidate["match_reason"] = "matching color and style"
            confirmed.append(candidate)
            seen_ids.add(candidate["item_id"])
            continue

        # 3. AI Bedrock check for borderline cases
        if check_aws_credentials_configured():
            try:
                identical, reason = await asyncio.to_thread(
                    garments_are_identical, garment, candidate
                )
                if identical:
                    candidate["match_reason"] = reason
                    confirmed.append(candidate)
                    seen_ids.add(candidate["item_id"])
            except Exception:
                logger.warning("Bedrock duplicate check failed; keeping item as new", exc_info=True)

    return confirmed



def _capture_to_extraction(garments: list[dict[str, Any]]) -> GarmentExtractionResult:
    """Rebuild a pending capture's structured metadata for an edit request."""
    first = garments[0]
    extracted: list[ExtractedGarment] = []
    for row in garments:
        extracted.append(ExtractedGarment(
            category=row["category"], sub_category=row["sub_category"],
            primary_color=row["color"], accent_colors=_decode_json_list(row.get("accent_colors")),
            silhouette_fit=row["silhouette_fit"],
            fabric_weight=row["fabric_weight"], formality_tier=row["formality_tier"],
            estimated_brand=row.get("brand"), style_tags=_decode_json_list(row.get("tags")),
            layering_role=row["layering_role"],
        ))
    return GarmentExtractionResult(
        photo_type=PhotoType(first.get("source_type") or "single_item"),
        garments=extracted,
    )


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None or not message.photo:
        return

    photo = message.photo[-1]
    real_user_id = str(user.id)
    chat_id = message.chat_id
    timestamp = int(datetime.now(timezone.utc).timestamp())
    demo_mode = bool(context.user_data.get(_DEMO_MODE_KEY)) if context.user_data else False
    pool_mode = bool(context.user_data.get(_POOL_MODE_KEY)) if context.user_data else False

    # In pool mode, store garments under the shared pool user_id.
    # Real user_id is still used for the batch key so each user's debounce is independent.
    effective_user_id = POOL_USER_ID if pool_mode else real_user_id

    current_count = len(_PHOTO_BATCHES.get(real_user_id, {}).get("photos", [])) + 1
    image_path = IMAGES_DIR / f"{user.id}_{timestamp}_{current_count}.jpg"

    try:
        telegram_file = await context.bot.get_file(photo.file_id)
        await telegram_file.download_to_drive(custom_path=str(image_path))
    except Exception:
        logger.exception("Failed to save photo for user %s", user.id)
        await message.reply_text("⚠️ Sorry, I couldn't save that photo. Please try again.")
        return

    if real_user_id not in _PHOTO_BATCHES:
        _PHOTO_BATCHES[real_user_id] = {
            "photos": [],
            "timer": None,
            "chat_id": chat_id,
            "demo_mode": demo_mode,
            "pool_mode": pool_mode,
            "effective_user_id": effective_user_id,
            "timestamp": timestamp,
        }

    batch = _PHOTO_BATCHES[real_user_id]
    batch["photos"].append((image_path, message.caption))

    # Cancel previous timer if still receiving photos in media group / burst
    if batch["timer"] and not batch["timer"].done():
        batch["timer"].cancel()

    # Schedule debounce task (1.2s delay to collect all photos sent simultaneously)
    batch["timer"] = asyncio.create_task(
        _delayed_process_photo_batch(real_user_id, context, delay=1.2)
    )



async def _delayed_process_photo_batch(
    user_id: str, context: ContextTypes.DEFAULT_TYPE, delay: float = 1.2
) -> None:
    """Process a batch of uploaded photos concurrently after debounce delay."""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return

    batch = _PHOTO_BATCHES.pop(user_id, None)
    if not batch or not batch["photos"]:
        return

    chat_id = batch["chat_id"]
    photos = batch["photos"]
    demo_mode = batch["demo_mode"]
    pool_mode = batch.get("pool_mode", False)
    # Use the effective user_id for all DB operations (pool or real user).
    effective_user_id = batch.get("effective_user_id", user_id)
    timestamp = batch["timestamp"]
    capture_id = f"cap_{timestamp}"

    status_message = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🧪 Processing {len(photos)} pool photo(s)..." if pool_mode
            else f"🧪 Processing {len(photos)} demo photo(s)..." if demo_mode
            else f"🔍 Processing {len(photos)} photo(s) with AI Vision..."
        ),
    )


    extracted_results: list[GarmentExtractionResult] = []
    if demo_mode:
        for img_path, caption in photos:
            try:
                extracted = extract_demo_garment_metadata(caption)
                extracted_results.append(extracted)
            except ValueError as exc:
                await status_message.edit_text(f"⚠️ {exc}")
                return
    elif not check_aws_credentials_configured():
        await status_message.edit_text("📸 Photos saved, but AWS Bedrock credentials are not configured in .env.")
        return
    else:
        try:
            tasks = [
                asyncio.to_thread(extract_garment_metadata, str(img_path), caption)
                for img_path, caption in photos
            ]
            extracted_results = await asyncio.gather(*tasks)
        except CredentialsNotConfiguredError:
            await status_message.edit_text("📸 AWS Bedrock credentials not configured.")
            return
        except (BotoCoreError, ClientError):
            logger.exception("Bedrock extraction failed for %s", capture_id)
            await status_message.edit_text("📸 AI extraction failed due to an AWS error.")
            return
        except Exception:
            logger.exception("Extraction error for %s", capture_id)
            await status_message.edit_text("📸 Unexpected error during AI extraction.")
            return

    all_garments: list[ExtractedGarment] = []
    for res in extracted_results:
        all_garments.extend(res.garments)

    combined_extraction = GarmentExtractionResult(
        photo_type=PhotoType.OOTD if len(extracted_results) > 1 or any(r.photo_type == PhotoType.OOTD for r in extracted_results) else extracted_results[0].photo_type,
        overall_aesthetic="multi-item upload" if len(extracted_results) > 1 else extracted_results[0].overall_aesthetic,
        garments=all_garments,
    )

    item_ids: list[str] = []
    item_img_map: list[str] = []
    for (img_path, caption), res in zip(photos, extracted_results):
        sub_res = GarmentExtractionResult(
            photo_type=res.photo_type,
            overall_aesthetic=res.overall_aesthetic,
            garments=res.garments,
        )
        inserted = insert_capture_garments(
            user_id=effective_user_id,
            image_path=str(img_path),
            capture_id=capture_id,
            extraction=sub_res,
            user_caption=caption,
        )
        item_ids.extend(inserted)
        item_img_map.extend([str(img_path)] * len(inserted))

    exclude_ids = set(item_ids)
    duplicates_by_index = await asyncio.gather(*[
        _confirmed_duplicate_candidates(
            effective_user_id,
            g,
            img_p,
            exclude_capture_id=capture_id,
            exclude_item_ids=exclude_ids,
        )
        for g, img_p in zip(all_garments, item_img_map)
    ])
    duplicates = dict(zip(item_ids, duplicates_by_index, strict=True))
    duplicate_links = {
        item_id: candidates[0]["item_id"]
        for item_id, candidates in duplicates.items()
        if candidates
    }

    if context.user_data is not None:
        context.user_data.setdefault(_DUPLICATE_LINKS_KEY, {})[capture_id] = duplicate_links.copy()
        context.user_data.setdefault("detected_duplicates", {})[capture_id] = duplicate_links.copy()

    # Send preview photos for any detected duplicates (grouped by image path to avoid duplicates!)
    if any(candidates for candidates in duplicates.values()):
        await _send_duplicate_comparison_photos(status_message, context, duplicates)

    if demo_mode:
        confirm_capture(capture_id)
        await status_message.edit_text(
            _format_extraction_summary(item_ids, combined_extraction, duplicates)
            + "\n\n🧪 Demo capture auto-confirmed.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await status_message.edit_text(
        _format_extraction_summary(item_ids, combined_extraction, duplicates),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_verification_keyboard(
            capture_id,
            bool(duplicate_links),
            item_ids,
            detected_links=duplicate_links,
            active_links=duplicate_links.copy(),
        ),
    )


async def verification_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle capture confirmation, duplicate linking, item edits, and wardrobe callbacks."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    user = query.from_user
    if user is None:
        return
    is_pool = bool(context.user_data.get(_POOL_MODE_KEY)) if context.user_data else False
    user_id = POOL_USER_ID if is_pool else str(user.id)
    allowed_user_ids = {str(user.id), POOL_USER_ID} if is_pool else {str(user.id)}

    # A. Wardrobe Category Navigation Callbacks
    if query.data.startswith("wardrobe_cat_"):
        cat = query.data.removeprefix("wardrobe_cat_")
        await _send_wardrobe_category_view(query, context, user_id, cat)
        return

    if query.data == "wardrobe_all":
        await _send_wardrobe_category_view(query, context, user_id, "all")
        return

    if query.data == "wardrobe_menu":
        garments = get_user_garments(user_id, verified_only=True)
        by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for g in garments:
            by_cat[g.get("category") or "top"].append(g)

        tops_cnt = len(by_cat.get("top", []))
        bots_cnt = len(by_cat.get("bottom", []))
        outer_cnt = len(by_cat.get("outerwear", []))
        shoes_cnt = len(by_cat.get("footwear", []))
        acc_cnt = len(by_cat.get("accessory", []))

        title_header = "👚 *Test Pool Wardrobe*" if is_pool else "👚 *Your Wardrobe*"
        summary = (
            f"{title_header} ({len(garments)} verified items)\n\n"
            f"• 👕 *Tops*: {tops_cnt}\n"
            f"• 👖 *Bottoms*: {bots_cnt}\n"
            f"• 🧥 *Outerwear*: {outer_cnt}\n"
            f"• 👟 *Footwear*: {shoes_cnt}\n"
            f"• 🎒 *Accessories*: {acc_cnt}\n\n"
            "Tap a category below to browse photos, delete items, or manage laundry:"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"👕 Tops ({tops_cnt})", callback_data="wardrobe_cat_top"),
                InlineKeyboardButton(f"👖 Bottoms ({bots_cnt})", callback_data="wardrobe_cat_bottom"),
            ],
            [
                InlineKeyboardButton(f"🧥 Outerwear ({outer_cnt})", callback_data="wardrobe_cat_outerwear"),
                InlineKeyboardButton(f"👟 Footwear ({shoes_cnt})", callback_data="wardrobe_cat_footwear"),
            ],
            [
                InlineKeyboardButton(f"🎒 Accessories ({acc_cnt})", callback_data="wardrobe_cat_accessory"),
                InlineKeyboardButton(f"🖼️ View All ({len(garments)})", callback_data="wardrobe_all"),
            ],
            [
                InlineKeyboardButton("⚠️ Clear Entire Wardrobe", callback_data="wardrobe_clear_ask"),
            ],
        ])
        await query.message.reply_text(summary, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        return

    if query.data == "wardrobe_clear_ask":
        garments = get_user_garments(user_id, verified_only=True)
        count = len(garments)
        if count == 0:
            await query.message.reply_text("Your wardrobe is already empty.")
            return

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔴 YES, DELETE EVERYTHING", callback_data="wardrobe_clear_confirm"),
            ],
            [
                InlineKeyboardButton("🟢 Cancel / Keep Wardrobe", callback_data="wardrobe_menu"),
            ],
        ])
        await query.message.reply_text(
            f"⚠️ *Are you sure you want to delete ALL {count} item(s) from your wardrobe?*\n\n"
            "This will permanently delete all your garments, photo appearances, outfit records, and wear history.\n\n"
            "🚨 *This action cannot be undone.*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return

    if query.data == "wardrobe_clear_confirm":
        count, image_paths = delete_all_user_garments(user_id)
        for img_path in image_paths:
            if not image_path_is_referenced(img_path):
                try:
                    resolved = resolve_image_path(img_path)
                    if resolved:
                        resolved.unlink(missing_ok=True)
                except OSError:
                    logger.exception("Failed to delete image file %s", img_path)

        await query.edit_message_text(
            f"🗑️ *Wardrobe Cleared!*\n\n"
            f"Successfully deleted all {count} item(s) and cleared your wardrobe history.\n\n"
            "You can start fresh anytime by sending me photos of new clothing items!",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # 1. Action: Edit Items Menu
    if query.data.startswith("wardrobe_act_edit_") or query.data.startswith("w_act_edit_"):
        cat = query.data.removeprefix("wardrobe_act_edit_").removeprefix("w_act_edit_")
        cat_filter = None if cat == "all" else cat
        garments = get_user_garments(user_id, verified_only=True, category=cat_filter)
        clean_cat_title = "Wardrobe" if cat == "all" else cat.replace("_", " ").title()
        safe_cat_title = escape_markdown(clean_cat_title, version=1)
        if not garments:
            await query.edit_message_text(
                f"No items found in *{safe_cat_title}* to edit.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"wardrobe_back_cat_{cat}")]])
            )
            return
        keyboard = _build_wardrobe_edit_keyboard(garments, cat)
        await query.edit_message_text(
            f"✏️ *Edit {safe_cat_title} Items:*\nTap an item below to update its details (color, fit, brand, etc.):",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return

    # 2. Action: Trigger Item Edit Prompt
    if query.data.startswith("wardrobe_doedit_") or query.data.startswith("w_doedit_"):
        remainder = query.data.removeprefix("wardrobe_doedit_").removeprefix("w_doedit_")
        if "_" in remainder:
            item_id, cat = remainder.rsplit("_", 1)
        else:
            item_id, cat = remainder, "all"
        garment = get_garment_by_id(item_id)
        if not garment or garment.get("user_id") not in allowed_user_ids:
            await query.edit_message_text("This item is no longer available.")
            return
        if context.user_data is not None:
            context.user_data[_PENDING_ITEM_EDIT_KEY] = item_id
        desc = _format_item_title(
            garment.get("sub_category") or "item",
            garment.get("color") or "",
            garment.get("brand"),
        )
        safe_desc = escape_markdown(desc, version=1)
        await query.message.reply_text(
            f"✏️ *Editing `{item_id}` ({safe_desc}):*\n\n"
            "Tell me what needs changing (e.g. “navy linen shirt, oversized” or “brand is Uniqlo”).\n\n"
            "_Send /cancel to abort._",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # 3. Action: Delete Items Menu
    if query.data.startswith("wardrobe_act_del_") or query.data.startswith("w_act_del_"):
        cat = query.data.removeprefix("wardrobe_act_del_").removeprefix("w_act_del_")
        cat_filter = None if cat == "all" else cat
        garments = get_user_garments(user_id, verified_only=True, category=cat_filter)
        clean_cat_title = "Wardrobe" if cat == "all" else cat.replace("_", " ").title()
        safe_cat_title = escape_markdown(clean_cat_title, version=1)
        if not garments:
            await query.edit_message_text(
                f"No items found in *{safe_cat_title}* to delete.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"wardrobe_back_cat_{cat}")]])
            )
            return
        keyboard = _build_wardrobe_delete_keyboard(garments, cat)
        await query.edit_message_text(
            f"🗑️ *Delete {safe_cat_title} Items:*\nTap an item to permanently remove it from your wardrobe:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return

    # 4. Action: Execute Item Deletion & Refresh
    if query.data.startswith("wardrobe_dodel_") or query.data.startswith("w_dodel_"):
        remainder = query.data.removeprefix("wardrobe_dodel_").removeprefix("w_dodel_")
        if "_" in remainder:
            item_id, cat = remainder.rsplit("_", 1)
        else:
            item_id, cat = remainder, "all"
        garment = get_garment_by_id(item_id)
        if garment is None or garment.get("user_id") not in allowed_user_ids:
            await query.edit_message_text("This item is no longer in your wardrobe.")
            return

        image_path = garment.get("image_path")
        delete_garment(item_id)
        if image_path and not image_path_is_referenced(image_path):
            try:
                resolved = resolve_image_path(image_path)
                if resolved:
                    resolved.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to delete image file %s", image_path)

        cat_filter = None if cat == "all" else cat
        remaining = get_user_garments(user_id, verified_only=True, category=cat_filter)
        clean_cat_title = "Wardrobe" if cat == "all" else cat.replace("_", " ").title()
        safe_cat_title = escape_markdown(clean_cat_title, version=1)
        if not remaining:
            await query.edit_message_text(
                f"🗑️ Deleted `{item_id}`.\n\nAll items in *{safe_cat_title}* have been removed.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Categories", callback_data="wardrobe_menu")]]),
            )
            return

        keyboard = _build_wardrobe_delete_keyboard(remaining, cat)
        await query.edit_message_text(
            f"🗑️ Deleted `{item_id}`. Tap another item to delete, or go back:\n\n*Remaining in {safe_cat_title} ({len(remaining)} items):*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return

    # 5. Action: Laundry Status Menu
    if query.data.startswith("wardrobe_act_laun_") or query.data.startswith("w_act_laun_"):
        cat = query.data.removeprefix("wardrobe_act_laun_").removeprefix("w_act_laun_")
        cat_filter = None if cat == "all" else cat
        garments = get_user_garments(user_id, verified_only=True, category=cat_filter)
        clean_cat_title = "Wardrobe" if cat == "all" else cat.replace("_", " ").title()
        safe_cat_title = escape_markdown(clean_cat_title, version=1)
        if not garments:
            await query.edit_message_text(
                f"No items found in *{safe_cat_title}* for laundry.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"wardrobe_back_cat_{cat}")]])
            )
            return
        keyboard = _build_wardrobe_laundry_keyboard(garments, cat)
        await query.edit_message_text(
            f"🧺 *Laundry Status for {safe_cat_title}:*\nTap an item to toggle between Clean and In Laundry:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return

    # 6. Action: Toggle Single Item Laundry & Refresh
    if query.data.startswith("wardrobe_dolaun_") or query.data.startswith("w_dolaun_"):
        remainder = query.data.removeprefix("wardrobe_dolaun_").removeprefix("w_dolaun_")
        if "_" in remainder:
            item_id, cat = remainder.rsplit("_", 1)
        else:
            item_id, cat = remainder, "all"
        garment = get_garment_by_id(item_id)
        if garment and garment.get("user_id") in allowed_user_ids:
            currently_in = bool(garment.get("in_laundry"))
            set_garment_laundry_status(item_id, not currently_in)

        cat_filter = None if cat == "all" else cat
        garments = get_user_garments(user_id, verified_only=True, category=cat_filter)
        clean_cat_title = "Wardrobe" if cat == "all" else cat.replace("_", " ").title()
        safe_cat_title = escape_markdown(clean_cat_title, version=1)
        keyboard = _build_wardrobe_laundry_keyboard(garments, cat)
        await query.edit_message_text(
            f"🧺 *Laundry Status for {safe_cat_title}:*\nTap an item to toggle between Clean and In Laundry:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return

    # 7. Action: Clean All in Category & Refresh
    if query.data.startswith("wardrobe_cleanall_") or query.data.startswith("w_cleanall_"):
        cat = query.data.removeprefix("wardrobe_cleanall_").removeprefix("w_cleanall_")
        cat_filter = None if cat == "all" else cat
        garments = get_user_garments(user_id, verified_only=True, category=cat_filter)
        for g in garments:
            set_garment_laundry_status(g["item_id"], False)

        refreshed = get_user_garments(user_id, verified_only=True, category=cat_filter)
        clean_cat_title = "Wardrobe" if cat == "all" else cat.replace("_", " ").title()
        safe_cat_title = escape_markdown(clean_cat_title, version=1)
        keyboard = _build_wardrobe_laundry_keyboard(refreshed, cat)
        await query.edit_message_text(
            f"🧼 Marked all {safe_cat_title} items as clean!\n\nTap an item if you want to change its status:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return

    # 8. Action: Return to Category 3-Button Menu
    if query.data.startswith("wardrobe_back_cat_") or query.data.startswith("w_back_cat_"):
        cat = query.data.removeprefix("wardrobe_back_cat_").removeprefix("w_back_cat_")
        cat_filter = None if cat == "all" else cat
        garments = get_user_garments(user_id, verified_only=True, category=cat_filter)
        clean_cat_title = "Wardrobe" if cat == "all" else cat.replace("_", " ").title()
        safe_cat_title = escape_markdown(clean_cat_title, version=1)
        await query.edit_message_text(
            f"⚙️ *Manage {safe_cat_title} ({len(garments)} items):*\nChoose an action below to edit, delete, or update laundry status:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_wardrobe_category_menu_keyboard(cat),
        )
        return

    if query.data.startswith("wardrobe_del_"):
        item_id = query.data.removeprefix("wardrobe_del_")
        garment = get_garment_by_id(item_id)
        if garment is None or garment.get("user_id") not in allowed_user_ids:
            await query.edit_message_text("This item is no longer in your wardrobe.")
            return

        image_path = garment.get("image_path")
        delete_garment(item_id)
        if image_path and not image_path_is_referenced(image_path):
            try:
                resolved = resolve_image_path(image_path)
                if resolved:
                    resolved.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to delete image file %s", image_path)

        sub_cat = escape_markdown((garment.get("sub_category") or "item").replace("_", " "), version=1)
        await query.message.reply_text(
            f"🗑️ Item `{item_id}` (*{sub_cat}*) has been removed from your wardrobe.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if query.data.startswith("wardrobe_laun_"):
        # Format: "wardrobe_laun_<item_id>_<category>"
        # item_id itself contains "_" (e.g. "item_101"), so we cannot blindly split("_").
        # Strip the prefix then rsplit once to peel off the category suffix.
        remainder = query.data.removeprefix("wardrobe_laun_")
        if "_" in remainder:
            item_id, cat = remainder.rsplit("_", 1)
        else:
            item_id, cat = remainder, "all"
        garment = get_garment_by_id(item_id)
        if garment and garment.get("user_id") in allowed_user_ids:
            currently_in = bool(garment.get("in_laundry"))
            set_garment_laundry_status(item_id, not currently_in)
            status_str = "🧺 Moved to laundry." if not currently_in else "🧼 Clean and returned to wardrobe."
            await query.message.reply_text(f"Item {item_id}: {status_str}")
        return


    # B. Item-Specific Edit Callback
    if query.data.startswith("edititem_"):
        item_id = query.data.removeprefix("edititem_")
        garment = get_garment_by_id(item_id)
        if not garment or garment.get("user_id") not in allowed_user_ids:
            await query.edit_message_text("This item is no longer available.")
            return
        if context.user_data is not None:
            context.user_data[_PENDING_ITEM_EDIT_KEY] = item_id
        await query.message.reply_text(
            f"✏️ *Editing `{item_id}`:*\n"
            "Tell me what needs changing for this item (e.g. “navy linen shirt, oversized” or “burgundy chinos”).",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # C. Manual 'Already in Wardrobe' Duplicate Linking Callbacks
    if query.data.startswith("manlink_start_"):
        capture_id = query.data.removeprefix("manlink_start_")
        garments = _capture_owned_by(capture_id, user_id, allowed_user_ids=allowed_user_ids)
        if not garments:
            await query.edit_message_text("This pending capture is no longer available.")
            return

        if len(garments) == 1:
            # Only one item, jump straight to category selection
            item_id = garments[0]["item_id"]
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("👕 Tops", callback_data=f"manlink_cat_{item_id}_top"),
                    InlineKeyboardButton("👖 Bottoms", callback_data=f"manlink_cat_{item_id}_bottom"),
                ],
                [
                    InlineKeyboardButton("🧥 Outerwear", callback_data=f"manlink_cat_{item_id}_outerwear"),
                    InlineKeyboardButton("👟 Footwear", callback_data=f"manlink_cat_{item_id}_footwear"),
                ],
                [
                    InlineKeyboardButton("🎒 Accessories", callback_data=f"manlink_cat_{item_id}_accessory"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"confirm_{capture_id}"),
                ],
            ])
            await query.message.reply_text(
                f"🔍 Which category is the saved item for `{item_id}` in?",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            return

        # Multiple items: pick which item to link
        buttons = []
        for g in garments:
            i_id = g["item_id"]
            subcat = (g.get("sub_category") or "piece").replace("_", " ")
            color = (g.get("color") or "").strip()
            buttons.append([InlineKeyboardButton(f"🔗 Link {i_id}: {color} {subcat}", callback_data=f"manlink_catmenu_{i_id}")])
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"confirm_{capture_id}")])

        await query.message.reply_text(
            "Select which uploaded item is already in your wardrobe:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if query.data.startswith("manlink_catmenu_"):
        item_id = query.data.removeprefix("manlink_catmenu_")
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("👕 Tops", callback_data=f"manlink_cat_{item_id}_top"),
                InlineKeyboardButton("👖 Bottoms", callback_data=f"manlink_cat_{item_id}_bottom"),
            ],
            [
                InlineKeyboardButton("🧥 Outerwear", callback_data=f"manlink_cat_{item_id}_outerwear"),
                InlineKeyboardButton("👟 Footwear", callback_data=f"manlink_cat_{item_id}_footwear"),
            ],
            [
                InlineKeyboardButton("🎒 Accessories", callback_data=f"manlink_cat_{item_id}_accessory"),
            ],
        ])
        await query.message.reply_text(
            f"🔍 Which category is the saved item for `{item_id}` in?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return

    if query.data.startswith("manlink_cat_"):
        parts = query.data.split("_")
        item_id = parts[2]
        cat = parts[3]
        garments = get_user_garments(user_id, verified_only=True, category=cat)
        cat_title = escape_markdown(cat.title(), version=1)
        if not garments:
            await query.message.reply_text(f"You don't have any saved items in *{cat_title}* yet.", parse_mode=ParseMode.MARKDOWN)
            return

        # Send unique preview photos for items in that category
        photos_by_path: dict[Path, list[str]] = defaultdict(list)
        for g in garments:
            resolved = resolve_image_path(g.get("image_path"))
            if resolved and resolved.is_file():
                g_cat = (g.get("category") or "item").replace("_", " ").title()
                desc = _format_item_title(
                    g.get("sub_category") or "piece",
                    g.get("color") or "",
                    g.get("brand"),
                )
                photos_by_path[resolved].append(f"Saved {g['item_id']}: {desc} ({g_cat})")

        labeled_buffers: list[io.BytesIO] = []
        try:
            for image_path, labels in photos_by_path.items():
                buf = await asyncio.to_thread(create_labeled_image_bytes, image_path, labels)
                labeled_buffers.append(buf)

            if labeled_buffers:
                for i in range(0, len(labeled_buffers), 10):
                    chunk = labeled_buffers[i:i + 10]
                    if len(chunk) == 1:
                        await context.bot.send_photo(chat_id=query.message.chat_id, photo=chunk[0])
                    else:
                        media_group = [InputMediaPhoto(media=b) for b in chunk]
                        await context.bot.send_media_group(chat_id=query.message.chat_id, media=media_group)
        except Exception:
            logger.exception("Failed to send candidate photos for manual link")
        finally:
            for buf in labeled_buffers:
                buf.close()

        buttons = []
        for g in garments:
            g_id = g["item_id"]
            desc = _format_item_title(
                g.get("sub_category") or "piece",
                g.get("color") or "",
                g.get("brand"),
            )
            buttons.append([InlineKeyboardButton(f"🔗 Match with {g_id} ({desc})", callback_data=f"manlink_do_{item_id}_{g_id}")])

        await query.message.reply_text(
            f"Select which saved *{cat_title}* item corresponds to `{item_id}`:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if query.data.startswith("manlink_do_"):
        parts = query.data.split("_")
        pending_item_id = parts[2]
        existing_id = parts[3]

        garment = get_garment_by_id(pending_item_id)
        if not garment or garment.get("user_id") not in allowed_user_ids:
            await query.message.reply_text("That upload item is no longer available.")
            return

        target_uid = garment.get("user_id") or user_id
        try:
            link_garment_to_existing(
                pending_item_id,
                existing_id,
                target_uid,
                garment["image_path"],
                garment.get("user_caption"),
            )
            await query.message.reply_text(
                f"✅ Linked `{pending_item_id}` to saved `{existing_id}`! New appearance recorded in your wardrobe history.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            logger.exception("Manual link failed for %s -> %s", pending_item_id, existing_id)
            await query.message.reply_text("⚠️ Could not link to that item.")
        return

    # Toggle individual duplicate item link status
    if query.data.startswith("confirm_toggle_"):
        remainder = query.data.removeprefix("confirm_toggle_")
        if "_cap_" in remainder:
            item_id, cap_tail = remainder.split("_cap_", 1)
            capture_id = f"cap_{cap_tail}"
        else:
            item_id, capture_id = remainder.rsplit("_", 1)

        stored_det = context.user_data.setdefault("detected_duplicates", {}).get(capture_id, {})
        active_links = context.user_data.setdefault(_DUPLICATE_LINKS_KEY, {}).setdefault(capture_id, stored_det.copy())

        if item_id in active_links:
            active_links.pop(item_id, None)
        else:
            if item_id in stored_det:
                active_links[item_id] = stored_det[item_id]

        garments = _capture_owned_by(capture_id, user_id, allowed_user_ids=allowed_user_ids)
        all_item_ids = [g["item_id"] for g in garments] if garments else []
        keyboard = _verification_keyboard(
            capture_id,
            has_duplicates=bool(stored_det),
            item_ids=all_item_ids,
            detected_links=stored_det,
            active_links=active_links,
        )
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return

    # D. Confirming an upload where duplicates were detected and linked
    if query.data.startswith("confirm_apply_cap_") or query.data.startswith("confirm_link_cap_"):
        capture_id = (
            query.data.removeprefix("confirm_apply_")
            if query.data.startswith("confirm_apply_")
            else query.data.removeprefix("confirm_link_")
        )
        garments = _capture_owned_by(capture_id, user_id, allowed_user_ids=allowed_user_ids)
        if not garments:
            await query.edit_message_text("This pending capture is no longer available.")
            return

        target_uid = garments[0]["user_id"]
        links = context.user_data.get(_DUPLICATE_LINKS_KEY, {}).get(capture_id)
        if links is None:
            links = await _duplicate_links_for_capture(context, capture_id, target_uid, garments)

        linked_count = 0
        final_item_ids: list[str] = []
        for garment in garments:
            i_id = garment["item_id"]
            existing_item_id = links.get(i_id)
            if existing_item_id:
                try:
                    link_garment_to_existing(
                        i_id,
                        existing_item_id,
                        target_uid,
                        garment["image_path"],
                        garment.get("user_caption"),
                    )
                    linked_count += 1
                    final_item_ids.append(existing_item_id)
                except ValueError:
                    logger.warning("Could not link duplicate %s", i_id)
                    final_item_ids.append(i_id)
            else:
                final_item_ids.append(i_id)

        confirm_capture(capture_id)
        _clear_duplicate_links(context, capture_id)
        if context.user_data is not None:
            context.user_data.get("detected_duplicates", {}).pop(capture_id, None)

        # Check if the confirmed photo was an OOTD
        is_ootd = any(g.get("source_type") == "ootd" for g in garments)
        if is_ootd and context.user_data is not None:
            context.user_data["pending_ootd_combo"] = final_item_ids
            msg = (
                f"✅ Linked {linked_count} duplicate item(s); remaining items added to your wardrobe!\n\n"
                if linked_count > 0
                else f"✅ {len(garments)} item(s) confirmed and added to your wardrobe!\n\n"
            )
            await query.edit_message_text(
                msg
                + "✨ *What occasion did you wear this outfit for?*\n"
                + "(e.g. `football practice`, `weekend coffee date`, `smart casual office`)\n\n"
                + "_Reply with the occasion and I'll remember it as your preferred style!_",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if linked_count > 0:
            await query.edit_message_text(
                f"✅ Linked {linked_count} duplicate item(s); remaining items were added to your wardrobe."
            )
        else:
            await query.edit_message_text(
                f"✅ {len(garments)} item(s) confirmed and added to your wardrobe!"
            )
        return

    # E. Confirming an upload normally (all items kept as new)
    if query.data.startswith("confirm_cap_"):
        capture_id = query.data.removeprefix("confirm_")
        garments = _capture_owned_by(capture_id, user_id, allowed_user_ids=allowed_user_ids)
        if not garments:
            await query.edit_message_text("This pending capture is no longer available.")
            return

        confirm_capture(capture_id)
        _clear_duplicate_links(context, capture_id)

        # Check if the confirmed photo was an OOTD
        is_ootd = any(g.get("source_type") == "ootd" for g in garments)
        if is_ootd and context.user_data is not None:
            context.user_data["pending_ootd_combo"] = [g["item_id"] for g in garments]
            await query.edit_message_text(
                f"✅ {len(garments)} item(s) confirmed and added to your wardrobe!\n\n"
                "✨ *What occasion did you wear this outfit for?*\n"
                "(e.g. `football practice`, `weekend coffee date`, `smart casual office`)\n\n"
                "_Reply with the occasion and I'll remember it as your preferred style!_",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await query.edit_message_text(
            f"✅ {len(garments)} item(s) confirmed and added to your wardrobe!"
        )
        return

    # F. Let the owner correct the extraction in plain language (capture-wide).
    if query.data.startswith("edit_cap_"):
        capture_id = query.data.removeprefix("edit_")
        garments = _capture_owned_by(capture_id, user_id, allowed_user_ids=allowed_user_ids)
        if not garments:
            await query.edit_message_text("This pending capture is no longer available.")
            return
        context.user_data[_PENDING_CORRECTION_KEY] = capture_id
        await query.edit_message_text(
            "✏️ Tell me what needs changing in your own words. For example: "
            "“this is a burgundy crewneck, not navy”, “it is oversized”, or "
            "“these are two separate items”. I’ll revise the details for review."
        )
        return

    # G. Discarding a capture
    if query.data.startswith("delete_cap_"):
        capture_id = query.data.removeprefix("delete_")
        garments = _capture_owned_by(capture_id, user_id, allowed_user_ids=allowed_user_ids)
        if not garments:
            await query.edit_message_text("This pending capture is no longer available.")
            return

        image_paths = {garment["image_path"] for garment in garments}
        delete_capture(capture_id)
        _clear_duplicate_links(context, capture_id)
        for image_path in image_paths:
            if not image_path_is_referenced(image_path):
                try:
                    resolved = resolve_image_path(image_path)
                    if resolved:
                        resolved.unlink(missing_ok=True)
                except OSError:
                    logger.exception("Failed to remove image file for capture %s", capture_id)
        await query.edit_message_text("🗑️ Capture discarded.")
        return

    # H. Deleting an individual item from wardrobe list
    if query.data.startswith("delitem_"):
        item_id = query.data.removeprefix("delitem_")
        garment = get_garment_by_id(item_id)
        if garment is None or garment.get("user_id") not in allowed_user_ids:
            await query.edit_message_text("This item is no longer in your wardrobe.")
            return

        image_path = garment.get("image_path")
        delete_garment(item_id)

        if image_path and not image_path_is_referenced(image_path):
            try:
                resolved = resolve_image_path(image_path)
                if resolved:
                    resolved.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to delete image file %s", image_path)

        sub_cat = escape_markdown((garment.get("sub_category") or "item").replace("_", " "), version=1)
        await query.edit_message_text(
            f"🗑️ Item `{item_id}` (*{sub_cat}*) has been removed from your wardrobe.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # I. Fallback single-item actions
    action, _, item_id = query.data.partition("_")

    if action == "confirm":
        garment = get_garment_by_id(item_id)
        if garment is None or garment.get("user_id") not in allowed_user_ids:
            await query.edit_message_text("This item is no longer available.")
            return
        mark_garment_verified(item_id, True)
        await query.edit_message_text(
            f"✅ Item #{item_id} confirmed and added to your wardrobe!"
        )
        return

    if action == "delete":
        garment = get_garment_by_id(item_id)
        if garment is None or garment.get("user_id") not in allowed_user_ids:
            await query.edit_message_text("This item is no longer available.")
            return
        deleted = delete_garment(item_id)
        if deleted and garment is not None:
            if not image_path_is_referenced(garment["image_path"]):
                try:
                    Path(garment["image_path"]).unlink(missing_ok=True)
                except OSError:
                    logger.exception("Failed to remove image file for %s", item_id)
        await query.edit_message_text(f"🗑️ Item #{item_id} deleted.")
        return


    logger.warning("Unrecognized verification callback data: %s", query.data)


async def laundry_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return

    is_pool = bool(context.user_data.get(_POOL_MODE_KEY)) if context.user_data else False
    user_id = POOL_USER_ID if is_pool else str(user.id)
    items = get_user_laundry_items(user_id)
    if not items:
        empty_msg = (
            "🧺 *Test pool laundry basket is empty!*\nAll verified clothes are clean and ready to wear."
            if is_pool
            else "🧺 *Your laundry basket is empty!*\nAll verified clothes are clean and ready to wear."
        )
        await message.reply_text(
            empty_msg,
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    title_hdr = "🧺 *Test Pool Clothes in Laundry*" if is_pool else "🧺 *Clothes Currently in Laundry*"
    lines = [f"{title_hdr} ({len(items)}):\n"]
    for item in items:
        cat = (item.get("category") or "piece").replace("_", " ").title()
        subcat = (item.get("sub_category") or "item").replace("_", " ")
        color = item.get("color") or ""
        lines.append(f"• `{item['item_id']}`: {color} {subcat} ({cat})")

    lines.append("\nTap below once you've done laundry to return these to your rotation:")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧼 Clean All / Laundry Done", callback_data="laun_clean_all")]
    ])
    await message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)


async def style_action_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle interactive styling actions: wearing today, more options, laundry, dislike."""
    query = update.callback_query
    if query is None or query.data is None or query.message is None:
        return
    await query.answer()

    user = query.from_user
    if user is None:
        return
    is_pool = bool(context.user_data.get(_POOL_MODE_KEY)) if context.user_data else False
    user_id = POOL_USER_ID if is_pool else str(user.id)
    data = query.data

    session = context.user_data.get("style_session") if context.user_data is not None else None

    # 1. User confirms wearing this today
    if data == "act_wear":
        if not session or not session.get("current_items"):
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("✅ Outfit logged as worn today!")
            return

        log_outfit_wear(
            user_id,
            session["current_items"],
            session.get("occasion", "everyday"),
            action="worn",
        )
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "🎉 *Logged as worn today!*\n"
            "I've updated your wardrobe rotation so you won't get the same outfit tomorrow.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # 2. User asks for more options / next candidate
    if data == "act_more":
        if not session:
            await query.message.reply_text("Please type `/style` to start a new outfit search.")
            return

        seen_combos = session.setdefault("seen_combos", [])
        if session.get("current_items") and session["current_items"] not in seen_combos:
            seen_combos.append(session["current_items"])

        await query.edit_message_reply_markup(reply_markup=None)
        safe_occ = escape_markdown(session.get('occasion', 'today'), version=1)
        status = await query.message.reply_text(
            f"🔄 Finding another look for *{safe_occ}*...",
            parse_mode=ParseMode.MARKDOWN,
        )

        try:
            recommendation = await asyncio.to_thread(
                run_stylist_workflow,
                user_id,
                session.get("occasion", "everyday"),
                latitude=session.get("latitude", SINGAPORE_LATITUDE),
                longitude=session.get("longitude", SINGAPORE_LONGITUDE),
                location_name=session.get("location_name", SINGAPORE_LOCATION_NAME),
                target_time=session.get("target_time"),
                time_label=session.get("time_str", "now"),
                excluded_item_ids=session.get("excluded_items", []),
                excluded_combos=seen_combos,
            )
        except StylistWorkflowError as exc:
            await status.edit_text(f"⚠️ {exc}")
            return
        except Exception:
            logger.exception("More options styling failed for %s", user_id)
            await status.edit_text("⚠️ Something went wrong while generating another option.")
            return


        current_item_ids = [item.item_id for item in recommendation.items]
        session["current_items"] = current_item_ids
        if current_item_ids not in seen_combos:
            seen_combos.append(current_item_ids)

        await _send_outfit_photos(query.message, context, user_id, recommendation)
        await status.edit_text("✨ Here's another combination!")
        await query.message.reply_text(
            _format_style_recommendation(recommendation),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_style_action_keyboard(recommendation.items),
        )
        return

    # 3. User indicates an item is in laundry
    if data == "act_laundry":
        if not session or not session.get("current_items"):
            await query.message.reply_text("Please type `/style` to generate an outfit first.")
            return

        buttons = []
        for item_id in session["current_items"]:
            g = get_garment_by_id(item_id)
            desc = f"{g.get('color', '')} {g.get('sub_category', 'piece')}".strip() if g else item_id
            cat = f"({g.get('category', 'item')})".title() if g else ""
            buttons.append([
                InlineKeyboardButton(f"🧺 {desc} {cat}", callback_data=f"laun_set_{item_id}")
            ])
        buttons.append([InlineKeyboardButton("🧺 Whole Outfit", callback_data="laun_set_all")])
        buttons.append([InlineKeyboardButton("⬅️ Cancel", callback_data="laun_cancel")])

        await query.message.reply_text(
            "🧺 *Which piece is in the laundry / not washed?*\n"
            "Select an item below and I'll immediately find a clean replacement:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    # 4. Laundry item selection callback
    if data.startswith("laun_set_"):
        target = data.removeprefix("laun_set_")
        if not session:
            await query.edit_message_text("Session expired. Type `/style` to start again.")
            return

        excluded = session.setdefault("excluded_items", [])
        if target == "all":
            for item_id in session.get("current_items", []):
                set_garment_laundry_status(item_id, True)
                if item_id not in excluded:
                    excluded.append(item_id)
        else:
            set_garment_laundry_status(target, True)
            if target not in excluded:
                excluded.append(target)

        await query.edit_message_text("🧺 Piece marked as dirty! Finding clean replacement...")

        seen_combos = session.get("seen_combos", [])
        try:
            recommendation = await asyncio.to_thread(
                run_stylist_workflow,
                user_id,
                session.get("occasion", "everyday"),
                latitude=session.get("latitude", SINGAPORE_LATITUDE),
                longitude=session.get("longitude", SINGAPORE_LONGITUDE),
                location_name=session.get("location_name", SINGAPORE_LOCATION_NAME),
                target_time=session.get("target_time"),
                time_label=session.get("time_str", "now"),
                excluded_item_ids=excluded,
                excluded_combos=seen_combos,
            )
        except StylistWorkflowError as exc:
            await query.message.reply_text(f"⚠️ {exc}")
            return
        except Exception:
            logger.exception("Laundry swap styling failed for %s", user_id)
            await query.message.reply_text("⚠️ Something went wrong while swapping.")
            return

        current_item_ids = [item.item_id for item in recommendation.items]
        session["current_items"] = current_item_ids
        if current_item_ids not in seen_combos:
            seen_combos.append(current_item_ids)

        await _send_outfit_photos(query.message, context, user_id, recommendation)
        await query.message.reply_text(
            _format_style_recommendation(recommendation),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_style_action_keyboard(recommendation.items),
        )
        return

    # 5. Cancel laundry picker
    if data == "laun_cancel":
        await query.edit_message_text("Laundry selection canceled.")
        return

    # 6. Clean all laundry
    if data == "laun_clean_all":
        count = clear_user_laundry(user_id)
        await query.edit_message_text(f"🧼 Done! {count} clothing item(s) marked as clean and ready to wear.")
        return

    # 7. Dislike outfit callback
    if data == "act_dislike":
        if not session or not session.get("current_items"):
            await query.message.reply_text("Session expired. Type `/style` to get a fresh outfit.")
            return

        log_outfit_wear(
            user_id,
            session["current_items"],
            session.get("occasion", "everyday"),
            action="rejected",
        )
        seen_combos = session.setdefault("seen_combos", [])
        if session["current_items"] not in seen_combos:
            seen_combos.append(session["current_items"])

        await query.edit_message_reply_markup(reply_markup=None)
        status = await query.message.reply_text(
            "👎 Got it, I'll avoid that combination! Searching for a different direction...",
            parse_mode=ParseMode.MARKDOWN,
        )

        try:
            recommendation = await asyncio.to_thread(
                run_stylist_workflow,
                user_id,
                session.get("occasion", "everyday"),
                latitude=session.get("latitude", SINGAPORE_LATITUDE),
                longitude=session.get("longitude", SINGAPORE_LONGITUDE),
                location_name=session.get("location_name", SINGAPORE_LOCATION_NAME),
                target_time=session.get("target_time"),
                time_label=session.get("time_str", "now"),
                excluded_item_ids=session.get("excluded_items", []),
                excluded_combos=seen_combos,
            )
        except StylistWorkflowError as exc:
            await status.edit_text(f"⚠️ {exc}")
            return
        except Exception:
            logger.exception("Dislike alternative failed for %s", user_id)
            await status.edit_text("⚠️ Something went wrong while generating an alternative.")
            return

        current_item_ids = [item.item_id for item in recommendation.items]
        session["current_items"] = current_item_ids
        if current_item_ids not in seen_combos:
            seen_combos.append(current_item_ids)

        await _send_outfit_photos(query.message, context, user_id, recommendation)
        await status.edit_text("✨ Here's a new style direction:")
        await query.message.reply_text(
            _format_style_recommendation(recommendation),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_style_action_keyboard(recommendation.items),
        )
        return


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text("⚠️ Something went wrong on my end. Please try again.")
        except Exception:
            pass


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data is not None:
        context.user_data.pop("awaiting_style_input", None)
        context.user_data.pop("style_user_id", None)
        context.user_data.pop("profile_draft", None)
        context.user_data.pop("style_session", None)
        context.user_data.pop(_PENDING_CORRECTION_KEY, None)
        # Cancel any pending password or pool profile prompts.
        # Note: pool_mode itself is NOT cleared here — use /adminlive to exit pool mode.
        context.user_data.pop(_AWAITING_ADMIN_PW_KEY, None)
        context.user_data.pop(_AWAITING_POOL_PROFILE_KEY, None)
    if update.message is not None:
        await update.message.reply_text("All active operations canceled.")



__all__ = [
    "start_command",
    "wardrobe_command",
    "delete_command",
    "style_command",
    "laundry_command",
    "style_action_callback_handler",
    "help_command",
    "admin_test_command",
    "admin_live_command",
    "photo_handler",
    "verification_callback_handler",
    "text_handler",
    "error_handler",
    "format_profile_summary",
]
