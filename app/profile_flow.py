"""Interactive `/profile` setup with expanded geometry and silhouette toggles."""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .config import POOL_USER_ID
from .database import get_user_profile, upsert_user_profile
from .models import BodyBuild, GenderFrame, Proportions, ThermalPreference, UserProfileInput

logger = logging.getLogger(__name__)

(
    MEASUREMENTS,
    GENDER_FRAME,
    BODY_BUILD,
    PROPORTIONS,
    SILHOUETTES,
    THERMAL,
) = range(6)

_DRAFT_KEY = "profile_draft"

AVAILABLE_SILHOUETTES = [
    ("fitted_top_wide_bottom", "Fitted Top + Wide Pants"),
    ("boxy_top_straight_bottom", "Boxy Top + Straight Pants"),
    ("relaxed_oversized", "Full Relaxed / Oversized"),
    ("clean_tailored", "Clean Regular / Tailored"),
    ("cropped_top_high_waist", "Cropped Top + High Waist"),
]


def format_profile_summary(profile: dict[str, Any]) -> str:
    """Render a saved profile dict as a Markdown summary for chat."""
    frame = (profile.get("gender_frame") or "not set").title()
    height = f"{profile['height_cm']} cm" if profile.get("height_cm") else "not set"
    weight = f"{profile['weight_kg']} kg" if profile.get("weight_kg") else "not set"
    build = (profile.get("body_build") or "not set").replace("_", " ").title()
    props = (profile.get("proportions") or "not set").replace("_", " ").title()
    
    raw_sil = profile.get("favorite_silhouettes") or "[]"
    sil_list = json.loads(raw_sil) if isinstance(raw_sil, str) else (raw_sil or [])
    sil_names = [name for code, name in AVAILABLE_SILHOUETTES if code in sil_list]
    sil_text = ", ".join(sil_names) if sil_names else "not set"

    thermal = "Needs layer for AC ❄️" if profile.get("thermal_preference") == "needs_ac_layer" else "Runs hot / Single layer 🔥"

    return (
        "👤 *Your Style Profile*\n\n"
        f"• *Style Frame:* {frame}\n"
        f"• *Body Metrics:* {height}, {weight}\n"
        f"• *Upper Body Build:* {build}\n"
        f"• *Vertical Proportions:* {props}\n"
        f"• *Favorite Silhouettes:* {sil_text}\n"
        f"• *Thermal Preference:* {thermal}\n\n"
        "Tap the button below or send `/profile edit` to redo your measurements."
    )


def _gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Masculine", callback_data="gender:masculine"),
            InlineKeyboardButton("Feminine", callback_data="gender:feminine"),
        ],
        [InlineKeyboardButton("Androgynous", callback_data="gender:androgynous")],
    ])


def _build_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Slim / Narrow", callback_data="build:slim"),
            InlineKeyboardButton("Athletic / Broad", callback_data="build:athletic_broad"),
        ],
        [
            InlineKeyboardButton("Muscular Frame", callback_data="build:muscular"),
            InlineKeyboardButton("Average / Regular", callback_data="build:average"),
        ],
        [InlineKeyboardButton("Stocky / Strong", callback_data="build:stocky")],
    ])


def _proportions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Longer Torso (Shorter Legs)", callback_data="props:long_torso")],
        [InlineKeyboardButton("Balanced Proportions", callback_data="props:balanced")],
        [InlineKeyboardButton("Longer Legs (Shorter Torso)", callback_data="props:long_legs")],
    ])


def _silhouettes_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    buttons = []
    for code, label in AVAILABLE_SILHOUETTES:
        check = "✅ " if code in selected else "▫️ "
        buttons.append([InlineKeyboardButton(f"{check}{label}", callback_data=f"sil:{code}")])
    buttons.append([InlineKeyboardButton("Done ➡️", callback_data="sil:done")])
    return InlineKeyboardMarkup(buttons)


def _thermal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❄️ Needs Layer (Chills easily indoors)", callback_data="thermal:needs_ac_layer")],
        [InlineKeyboardButton("🔥 Runs Hot (Prefers breathable single layer)", callback_data="thermal:runs_hot")],
    ])


async def profile_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return ConversationHandler.END

    args = context.args or []
    force_edit = any(arg.lower() in ("edit", "redo", "reset", "update") for arg in args)

    is_pool = bool(context.user_data.get("pool_mode")) if context.user_data else False
    target_user_id = POOL_USER_ID if is_pool else str(user.id)

    profile = get_user_profile(target_user_id)
    if profile is not None and profile.get("height_cm") is not None and not force_edit:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Redo Profile", callback_data="profile:redo")]
        ])
        header = "🧪 *Test Style Profile (Pool Mode)*" if is_pool else "👤 *Your Style Profile*"
        summary_text = format_profile_summary(profile)
        if is_pool:
            summary_text = summary_text.replace("👤 *Your Style Profile*", header)
        await message.reply_text(
            summary_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return ConversationHandler.END

    context.user_data[_DRAFT_KEY] = {"favorite_silhouettes": []}
    prompt_header = "Let's set up the *Test Style Profile* for Pool Mode! 📏" if is_pool else "Let's set up your personalized style profile! 📏"
    await message.reply_text(
        f"{prompt_header}\n\n"
        "Send the *height* and *weight* separated by a space:\n"
        "(e.g. `162 52` for 162 cm, 52 kg)\n\n"
        "Send /cancel anytime to abort.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return MEASUREMENTS



async def redo_profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.message is None:
        return ConversationHandler.END

    await query.answer()
    context.user_data[_DRAFT_KEY] = {"favorite_silhouettes": []}
    await query.message.reply_text(
        "Let's update your style profile! 📏\n\n"
        "Send your *height* and *weight* separated by a space:\n"
        "(e.g. `178 72` for 178 cm, 72 kg)\n\n"
        "_Send /cancel anytime to abort._",
        parse_mode=ParseMode.MARKDOWN,
    )
    return MEASUREMENTS


async def receive_measurements(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    if message is None or message.text is None:
        return MEASUREMENTS

    parts = message.text.strip().split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await message.reply_text(
            "Please enter two numbers separated by a space: *height (cm)* and *weight (kg)*.\n"
            "Example: `178 72`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return MEASUREMENTS

    height, weight = int(parts[0]), int(parts[1])
    if not (100 <= height <= 250 and 30 <= weight <= 250):
        await message.reply_text("Please enter realistic values (height 100-250 cm, weight 30-250 kg).")
        return MEASUREMENTS

    draft = context.user_data.setdefault(_DRAFT_KEY, {})
    draft["height_cm"] = height
    draft["weight_kg"] = weight

    await message.reply_text("Select your baseline styling frame:", reply_markup=_gender_keyboard())
    return GENDER_FRAME


async def receive_gender_frame(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None or query.message is None:
        return GENDER_FRAME

    await query.answer()
    value = query.data.split(":", 1)[1]
    context.user_data.setdefault(_DRAFT_KEY, {})["gender_frame"] = value

    await query.edit_message_text(f"Style Frame: {value.title()} ✅")
    await query.message.reply_text("What is your upper-body frame & build?", reply_markup=_build_keyboard())
    return BODY_BUILD


async def receive_body_build(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None or query.message is None:
        return BODY_BUILD

    await query.answer()
    value = query.data.split(":", 1)[1]
    context.user_data.setdefault(_DRAFT_KEY, {})["body_build"] = value

    await query.edit_message_text(f"Upper Build: {value.replace('_', ' ').title()} ✅")
    await query.message.reply_text("How would you describe your vertical proportions?", reply_markup=_proportions_keyboard())
    return PROPORTIONS


async def receive_proportions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None or query.message is None:
        return PROPORTIONS

    await query.answer()
    value = query.data.split(":", 1)[1]
    context.user_data.setdefault(_DRAFT_KEY, {})["proportions"] = value

    await query.edit_message_text(f"Proportions: {value.replace('_', ' ').title()} ✅")
    selected = context.user_data.setdefault(_DRAFT_KEY, {}).setdefault("favorite_silhouettes", [])
    await query.message.reply_text(
        "Select your preferred outfit silhouettes *(tap multiple if you like variety, then tap Done)*:",
        reply_markup=_silhouettes_keyboard(selected),
        parse_mode=ParseMode.MARKDOWN,
    )
    return SILHOUETTES


async def receive_silhouettes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None or query.message is None:
        return SILHOUETTES

    await query.answer()
    action = query.data.split(":", 1)[1]
    draft = context.user_data.setdefault(_DRAFT_KEY, {})
    selected: list[str] = draft.setdefault("favorite_silhouettes", [])

    if action == "done":
        if not selected:
            selected.append("clean_tailored")
        await query.edit_message_text("Silhouette Preferences Saved ✅")
        await query.message.reply_text(
            "Lastly, what is your indoor air conditioning & thermal preference?",
            reply_markup=_thermal_keyboard(),
        )
        return THERMAL

    if action in selected:
        selected.remove(action)
    else:
        selected.append(action)

    await query.edit_message_reply_markup(reply_markup=_silhouettes_keyboard(selected))
    return SILHOUETTES


async def receive_thermal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None or query.message is None or query.from_user is None:
        return THERMAL

    await query.answer()
    value = query.data.split(":", 1)[1]
    draft = context.user_data.setdefault(_DRAFT_KEY, {})
    draft["thermal_preference"] = value

    try:
        validated = UserProfileInput(**draft)
    except ValidationError:
        logger.exception("Invalid profile draft for user %s: %s", query.from_user.id, draft)
        await query.edit_message_text("Validation error. Please try /profile again.")
        context.user_data.pop(_DRAFT_KEY, None)
        return ConversationHandler.END

    is_pool = bool(context.user_data.get("pool_mode")) if context.user_data else False
    target_user_id = POOL_USER_ID if is_pool else str(query.from_user.id)
    upsert_user_profile(target_user_id, validated.to_db_dict())
    context.user_data.pop(_DRAFT_KEY, None)

    await query.edit_message_text("Thermal Preference Saved ✅")
    success_text = (
        "🎉 *Test Style Profile is saved!*\n\n"
        "Wardrobe recommendations via /style in pool mode will now use this profile."
        if is_pool
        else "🎉 *Your Style Profile is complete!*\n\n"
        "Use `/wardrobe` to check cataloged items or `/style <occasion>` to generate an outfit."
    )
    await query.message.reply_text(
        success_text,
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END



async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(_DRAFT_KEY, None)
    if update.message is not None:
        await update.message.reply_text("Profile setup canceled. Run /profile to start again.")
    return ConversationHandler.END


profile_conversation_handler = ConversationHandler(
    entry_points=[
        CommandHandler("profile", profile_entry),
        CallbackQueryHandler(redo_profile_callback, pattern=r"^profile:redo$"),
    ],
    states={
        MEASUREMENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_measurements)],
        GENDER_FRAME: [CallbackQueryHandler(receive_gender_frame, pattern=r"^gender:")],
        BODY_BUILD: [CallbackQueryHandler(receive_body_build, pattern=r"^build:")],
        PROPORTIONS: [CallbackQueryHandler(receive_proportions, pattern=r"^props:")],
        SILHOUETTES: [CallbackQueryHandler(receive_silhouettes, pattern=r"^sil:")],
        THERMAL: [CallbackQueryHandler(receive_thermal, pattern=r"^thermal:")],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    name="profile_setup",
    persistent=False,
    per_message=False,
)