import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

for mod in ("telegram", "telegram.constants", "telegram.ext"):
    if mod not in sys.modules:
        try:
            __import__(mod)
        except ImportError:
            sys.modules[mod] = MagicMock()

from PIL import Image

from app.database import (
    find_potential_duplicates,
    init_db,
    insert_capture_garments,
    mark_garment_verified,
)
from app.extractor import compute_image_dhash, hamming_distance
from app.handlers import _confirmed_duplicate_candidates
from app.models import ExtractedGarment, GarmentExtractionResult, PhotoType


class TestDuplicateDetection(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_wardrobe.db")
        init_db(self.db_path)
        self.user_id = "test_dup_user"

        # Create two sample images (img1 and img2 are identical, img3 is different)
        self.img1_path = os.path.join(self.tmp_dir.name, "photo1.jpg")
        self.img2_path = os.path.join(self.tmp_dir.name, "photo2.jpg")
        self.img3_path = os.path.join(self.tmp_dir.name, "photo3.jpg")

        img_a = Image.new("RGB", (200, 200))
        for x in range(200):
            for y in range(200):
                img_a.putpixel((x, y), (x % 255, y % 255, (x + y) % 255))
        img_a.save(self.img1_path)
        img_a.save(self.img2_path)

        img_b = Image.new("RGB", (200, 200))
        for x in range(200):
            for y in range(200):
                img_b.putpixel((x, y), (255 - (x % 255), (y * 2) % 255, 100))
        img_b.save(self.img3_path)

        # Insert item 1 into DB and verify it
        g1 = ExtractedGarment(
            category="bottom", sub_category="straight leg jeans", primary_color="black",
            silhouette_fit="regular", fabric_weight="heavy_structured", formality_tier=2
        )
        res1 = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[g1])
        item_ids = insert_capture_garments(self.user_id, self.img1_path, "cap_1", res1)
        mark_garment_verified(item_ids[0], True)
        self.saved_item_id = item_ids[0]

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_visual_hash_duplicate_detection(self):
        # Incoming item has same photo as saved item
        incoming = ExtractedGarment(
            category="bottom", sub_category="jeans", primary_color="dark",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=2
        )
        candidates = asyncio.run(
            _confirmed_duplicate_candidates(self.user_id, incoming, self.img2_path)
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["item_id"], self.saved_item_id)
        self.assertEqual(candidates[0]["match_reason"], "identical photo detected")

    def test_metadata_duplicate_detection_with_normalization(self):
        # Incoming item has different photo but same normalized color & style (e.g. straight_leg_jeans)
        incoming = ExtractedGarment(
            category="bottom", sub_category="straight_leg_jeans", primary_color="black",
            silhouette_fit="regular", fabric_weight="heavy_structured", formality_tier=2
        )
        candidates = asyncio.run(
            _confirmed_duplicate_candidates(self.user_id, incoming, self.img3_path)
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["item_id"], self.saved_item_id)
    def test_new_upload_does_not_match_itself_in_pending_capture(self):
        # A completely new item is uploaded with img3 and inserted as pending cap_new
        new_g = ExtractedGarment(
            category="top", sub_category="linen shirt", primary_color="white",
            silhouette_fit="regular", fabric_weight="lightweight_breathable", formality_tier=2
        )
        new_res = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[new_g])
        new_ids = insert_capture_garments(self.user_id, self.img3_path, "cap_new", new_res)
        pending_id = new_ids[0]

        # Duplicate candidates must NOT match pending_id or its own capture
        candidates = asyncio.run(
            _confirmed_duplicate_candidates(
                self.user_id,
                new_g,
                self.img3_path,
                exclude_capture_id="cap_new",
                exclude_item_ids={pending_id},
            )
        )
        self.assertEqual(len(candidates), 0)

    def test_confirm_apply_ootd_with_linked_duplicates(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.handlers import verification_callback_handler, _DUPLICATE_LINKS_KEY

        # Insert an OOTD capture with 2 items
        g1 = ExtractedGarment(
            category="bottom", sub_category="straight leg jeans", primary_color="black",
            silhouette_fit="regular", fabric_weight="heavy_structured", formality_tier=2
        )
        g2 = ExtractedGarment(
            category="top", sub_category="white tee", primary_color="white",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=1
        )
        ootd_res = GarmentExtractionResult(photo_type=PhotoType.OOTD, garments=[g1, g2])
        ootd_ids = insert_capture_garments(self.user_id, self.img2_path, "cap_ootd", ootd_res)

        user = MagicMock()
        user.id = self.user_id

        query = MagicMock()
        query.data = "confirm_apply_cap_ootd"
        query.from_user = user
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {
            _DUPLICATE_LINKS_KEY: {"cap_ootd": {ootd_ids[0]: self.saved_item_id}}
        }

        asyncio.run(verification_callback_handler(update, context))

        self.assertEqual(context.user_data.get("pending_ootd_combo"), [self.saved_item_id, ootd_ids[1]])
        self.assertEqual(context.user_data.get("pending_ootd_linked"), [self.saved_item_id])
        query.edit_message_text.assert_awaited_once()

        # Verify outfit was immediately registered in user_outfits
        from app.database import get_user_outfits
        outfits = get_user_outfits(self.user_id)
        self.assertEqual(len(outfits), 1)
        self.assertEqual(outfits[0]["item_ids"], [self.saved_item_id, ootd_ids[1]])
        self.assertEqual(outfits[0]["linked_item_ids"], [self.saved_item_id])
        self.assertEqual(outfits[0]["image_path"], self.img2_path)
        self.assertEqual(context.user_data.get("pending_ootd_outfit_id"), outfits[0]["outfit_id"])

        # Test subsequent occasion reply via text_handler
        from app.handlers import text_handler
        msg = MagicMock()
        msg.text = "smart casual office"
        msg.reply_text = AsyncMock()
        update_text = MagicMock()
        update_text.message = msg
        update_text.effective_user = user

        asyncio.run(text_handler(update_text, context))
        updated_outfits = get_user_outfits(self.user_id)
        self.assertEqual(len(updated_outfits), 1)
        self.assertEqual(updated_outfits[0]["occasion"], "smart casual office")
        msg.reply_text.assert_awaited_once()
        sent_reply = msg.reply_text.await_args[0][0]
        self.assertIn("Got it!* OOTD is saved as *smart casual office*", sent_reply)
        self.assertIn("OOTD #1* saved to your OOTD collection:", sent_reply)

    def test_confirm_cap_ootd_without_duplicates(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.handlers import verification_callback_handler
        from app.database import get_user_outfits

        g1 = ExtractedGarment(
            category="top", sub_category="linen shirt", primary_color="navy",
            silhouette_fit="regular", fabric_weight="lightweight_breathable", formality_tier=2
        )
        g2 = ExtractedGarment(
            category="bottom", sub_category="chinos", primary_color="khaki",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=2
        )
        ootd_res = GarmentExtractionResult(photo_type=PhotoType.OOTD, garments=[g1, g2])
        ootd_ids = insert_capture_garments(self.user_id, self.img3_path, "cap_plain_ootd", ootd_res)

        user = MagicMock()
        user.id = self.user_id

        query = MagicMock()
        query.data = "confirm_cap_plain_ootd"
        query.from_user = user
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {}

        asyncio.run(verification_callback_handler(update, context))

        outfits = get_user_outfits(self.user_id)
        self.assertEqual(len(outfits), 1)
        self.assertEqual(outfits[0]["item_ids"], ootd_ids)
        self.assertEqual(outfits[0]["image_path"], self.img3_path)
        self.assertEqual(context.user_data.get("pending_ootd_outfit_id"), outfits[0]["outfit_id"])

        # Test prompt msg tracking
        self.assertEqual(context.user_data.get("pending_ootd_prompt_msg_id"), query.message.message_id)

    def test_text_handler_occasion_confirmation_fallback(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.database import save_user_outfit, get_user_outfits
        from app.handlers import text_handler

        # User has an outfit saved with default occasion "ootd", but user_data state is empty (e.g. restart)
        save_user_outfit(self.user_id, "ootd", ["item_101", "item_102"])

        msg = MagicMock()
        msg.text = "weekend coffee date"
        msg.reply_text = AsyncMock()
        update = MagicMock()
        update.message = msg
        user = MagicMock()
        user.id = int(self.user_id.split("_")[-1]) if self.user_id.split("_")[-1].isdigit() else 9999
        update.effective_user = user

        context = MagicMock()
        context.user_data = {}  # Empty context (lost in-memory state)

        # Map user to self.user_id if pool mode or non-digit
        with unittest.mock.patch("app.handlers.POOL_USER_ID", self.user_id):
            context.user_data["pool_mode"] = True
            asyncio.run(text_handler(update, context))

        outfits = get_user_outfits(self.user_id)
        self.assertEqual(outfits[-1]["occasion"], "weekend coffee date")
        msg.reply_text.assert_awaited_once()
        sent_reply = msg.reply_text.await_args[0][0]
        self.assertIn("Got it!* OOTD is saved as *weekend coffee date*", sent_reply)
        self.assertIn("saved to your OOTD collection", sent_reply)


    def test_backfill_confirmed_ootds(self):
        from app.database import get_user_outfits, _connect
        # Simulate a verified OOTD capture in garments table that has no user_outfits row
        g1 = ExtractedGarment(
            category="top", sub_category="silk blouse", primary_color="red",
            silhouette_fit="regular", fabric_weight="lightweight_breathable", formality_tier=3
        )
        g2 = ExtractedGarment(
            category="bottom", sub_category="pencil skirt", primary_color="black",
            silhouette_fit="slim", fabric_weight="medium", formality_tier=3
        )
        res = GarmentExtractionResult(photo_type=PhotoType.OOTD, garments=[g1, g2])
        item_ids = insert_capture_garments("bf_user", self.img1_path, "cap_backfill", res, user_caption="cocktail party")
        for i_id in item_ids:
            mark_garment_verified(i_id, True)

        # Before backfill, user_outfits has 0 rows for bf_user
        self.assertEqual(len(get_user_outfits("bf_user")), 0)

        # Re-running init_db triggers backfill_confirmed_ootds
        init_db(self.db_path)

        bf_outfits = get_user_outfits("bf_user")
        self.assertEqual(len(bf_outfits), 1)
        self.assertEqual(bf_outfits[0]["item_ids"], item_ids)
        self.assertEqual(bf_outfits[0]["occasion"], "cocktail party")
        self.assertEqual(bf_outfits[0]["image_path"], self.img1_path)

    def test_save_user_outfit_deduplication(self):
        from app.database import save_user_outfit, get_user_outfits
        uid = "dedup_test_user"
        # First save creates outfit #1
        id1 = save_user_outfit(uid, "smart casual", ["item_a", "item_b"], image_path=self.img1_path)
        outfits = get_user_outfits(uid)
        self.assertEqual(len(outfits), 1)

        # Saving again with identical items updates outfit #1 without creating a second outfit
        id2 = save_user_outfit(uid, "business casual", ["item_a", "item_b"], image_path=self.img1_path)
        self.assertEqual(id1, id2)
        outfits_after = get_user_outfits(uid)
        self.assertEqual(len(outfits_after), 1)
        self.assertEqual(outfits_after[0]["occasion"], "business casual")

    def test_deduplicate_user_outfits_cleanup(self):
        from app.database import _connect, _ensure_user_exists, deduplicate_user_outfits, get_user_outfits
        import json
        uid = "cleanup_user"
        with _connect() as conn:
            _ensure_user_exists(conn, uid)
            # Insert duplicate 1 with non-existent image
            conn.execute(
                "INSERT INTO user_outfits (user_id, occasion, item_ids, image_path) VALUES (?, ?, ?, ?)",
                (uid, "ootd", json.dumps(["i1", "i2"]), "non_existent_file.jpg"),
            )
            # Insert duplicate 2 with valid image
            conn.execute(
                "INSERT INTO user_outfits (user_id, occasion, item_ids, image_path) VALUES (?, ?, ?, ?)",
                (uid, "ootd", json.dumps(["i1", "i2"]), self.img1_path),
            )
            deduplicate_user_outfits(conn)

        outfits = get_user_outfits(uid)
        self.assertEqual(len(outfits), 1)
        self.assertEqual(outfits[0]["image_path"], self.img1_path)

    def test_wlink_tgt_callback_merges_and_updates_ootd(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.handlers import verification_callback_handler
        from app.database import (
            insert_capture_garments, mark_garment_verified, save_user_outfit,
            get_garment_by_id, get_user_outfits
        )

        # Create new item to merge (item_b) and companion item (item_c)
        gb = ExtractedGarment(
            category="bottom", sub_category="black jeans", primary_color="black",
            silhouette_fit="regular", fabric_weight="heavy_structured", formality_tier=2
        )
        gc = ExtractedGarment(
            category="top", sub_category="navy blazer", primary_color="navy",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=3
        )
        res = GarmentExtractionResult(photo_type=PhotoType.OOTD, garments=[gb, gc])
        new_ids = insert_capture_garments(self.user_id, self.img2_path, "cap_merge", res)
        for i_id in new_ids:
            mark_garment_verified(i_id, True)

        src_id = new_ids[0]
        companion_id = new_ids[1]

        # Register an outfit with [src_id, companion_id]
        outfit_id = save_user_outfit(self.user_id, "smart casual", [src_id, companion_id], image_path=self.img2_path)

        # Execute wlink_tgt with colon delimiter
        user = MagicMock()
        user.id = self.user_id

        query = MagicMock()
        query.data = f"wlink_tgt:{src_id}:{self.saved_item_id}"
        query.from_user = user
        query.answer = AsyncMock()
        query.message.reply_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {}

        asyncio.run(verification_callback_handler(update, context))

        # Check reply message
        query.message.reply_text.assert_awaited_once()
        reply_call = query.message.reply_text.await_args
        self.assertIn("Successfully Linked Duplicate", reply_call[0][0])

        # Verify src_id is removed from garments
        self.assertIsNone(get_garment_by_id(src_id))
        self.assertIsNotNone(get_garment_by_id(self.saved_item_id))

        # Verify outfit was updated with saved_item_id replacing src_id
        outfits = get_user_outfits(self.user_id)
        matching_outfit = next(o for o in outfits if o["outfit_id"] == outfit_id)
        self.assertEqual(matching_outfit["item_ids"], [self.saved_item_id, companion_id])
        self.assertIn(self.saved_item_id, matching_outfit["linked_item_ids"])

    def test_wlink_tgt_legacy_underscore_fallback(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.handlers import verification_callback_handler
        from app.database import (
            insert_capture_garments, mark_garment_verified, save_user_outfit,
            get_garment_by_id, get_user_outfits
        )

        gb = ExtractedGarment(
            category="bottom", sub_category="vintage jeans", primary_color="black",
            silhouette_fit="regular", fabric_weight="heavy_structured", formality_tier=2
        )
        res = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[gb])
        new_ids = insert_capture_garments(self.user_id, self.img2_path, "cap_legacy", res)
        mark_garment_verified(new_ids[0], True)
        src_id = new_ids[0]

        # Use legacy underscore format wlink_tgt_<src>_<tgt>
        user = MagicMock()
        user.id = self.user_id

        query = MagicMock()
        query.data = f"wlink_tgt_{src_id}_{self.saved_item_id}"
        query.from_user = user
        query.answer = AsyncMock()
        query.message.reply_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {}

        asyncio.run(verification_callback_handler(update, context))

        self.assertIsNone(get_garment_by_id(src_id))
        self.assertIsNotNone(get_garment_by_id(self.saved_item_id))

    def test_manlink_do_callback_pending_capture(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.handlers import verification_callback_handler, _DUPLICATE_LINKS_KEY
        from app.database import insert_capture_garments, get_garment_by_id

        gb = ExtractedGarment(
            category="bottom", sub_category="pending pants", primary_color="black",
            silhouette_fit="regular", fabric_weight="heavy_structured", formality_tier=2
        )
        res = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[gb])
        new_ids = insert_capture_garments(self.user_id, self.img2_path, "cap_pending", res)
        src_id = new_ids[0]

        user = MagicMock()
        user.id = self.user_id

        query = MagicMock()
        query.data = f"manlink_do:{src_id}:{self.saved_item_id}"
        query.from_user = user
        query.answer = AsyncMock()
        query.message.reply_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {}

        asyncio.run(verification_callback_handler(update, context))

        # Item should still exist (not prematurely deleted) and registered in pending links
        self.assertIsNotNone(get_garment_by_id(src_id))
        self.assertEqual(context.user_data[_DUPLICATE_LINKS_KEY]["cap_pending"][src_id], self.saved_item_id)

    def test_confirm_toggle_callback(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.handlers import verification_callback_handler, _DUPLICATE_LINKS_KEY
        from app.database import insert_capture_garments

        gb = ExtractedGarment(
            category="bottom", sub_category="toggle pants", primary_color="black",
            silhouette_fit="regular", fabric_weight="heavy_structured", formality_tier=2
        )
        res = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[gb])
        new_ids = insert_capture_garments(self.user_id, self.img2_path, "cap_toggle", res)
        src_id = new_ids[0]

        user = MagicMock()
        user.id = self.user_id

        query = MagicMock()
        query.data = f"confirm_toggle:{src_id}:cap_toggle"
        query.from_user = user
        query.answer = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {
            "detected_duplicates": {"cap_toggle": {src_id: self.saved_item_id}},
            _DUPLICATE_LINKS_KEY: {"cap_toggle": {src_id: self.saved_item_id}},
        }

        # First toggle: should remove from active_links
        asyncio.run(verification_callback_handler(update, context))
        self.assertNotIn(src_id, context.user_data[_DUPLICATE_LINKS_KEY]["cap_toggle"])

        # Second toggle: should re-add to active_links
        asyncio.run(verification_callback_handler(update, context))
        self.assertEqual(context.user_data[_DUPLICATE_LINKS_KEY]["cap_toggle"][src_id], self.saved_item_id)

    def test_cleanup_duplicate_comparison_photos_success(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.handlers import (
            _cleanup_duplicate_comparison_photos,
            _DUPLICATE_COMPARISON_MSG_IDS_KEY,
            _DUPLICATE_PHOTOS_SENT_KEY,
        )

        context = MagicMock()
        context.bot.delete_message = AsyncMock()
        context.user_data = {
            _DUPLICATE_COMPARISON_MSG_IDS_KEY: {"cap_test": [101, 102]},
            _DUPLICATE_PHOTOS_SENT_KEY: {"cap_test": True},
        }

        res = asyncio.run(_cleanup_duplicate_comparison_photos(chat_id=12345, capture_id="cap_test", context=context))
        self.assertTrue(res)
        self.assertEqual(context.bot.delete_message.await_count, 2)
        context.bot.delete_message.assert_any_await(chat_id=12345, message_id=101)
        context.bot.delete_message.assert_any_await(chat_id=12345, message_id=102)
        self.assertNotIn("cap_test", context.user_data[_DUPLICATE_COMPARISON_MSG_IDS_KEY])
        self.assertNotIn("cap_test", context.user_data[_DUPLICATE_PHOTOS_SENT_KEY])

    def test_cleanup_duplicate_comparison_photos_failure(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.handlers import (
            _cleanup_duplicate_comparison_photos,
            _DUPLICATE_COMPARISON_MSG_IDS_KEY,
            _DUPLICATE_PHOTOS_SENT_KEY,
        )

        context = MagicMock()
        context.bot.delete_message = AsyncMock(side_effect=Exception("Message cannot be deleted"))
        context.user_data = {
            _DUPLICATE_COMPARISON_MSG_IDS_KEY: {"cap_test": [101]},
            _DUPLICATE_PHOTOS_SENT_KEY: {"cap_test": True},
        }

        res = asyncio.run(_cleanup_duplicate_comparison_photos(chat_id=12345, capture_id="cap_test", context=context))
        self.assertFalse(res)
        context.bot.delete_message.assert_awaited_once_with(chat_id=12345, message_id=101)

    def test_cleanup_duplicate_comparison_photos_no_photos_sent(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.handlers import _cleanup_duplicate_comparison_photos

        context = MagicMock()
        context.bot.delete_message = AsyncMock()
        context.user_data = {}

        res = asyncio.run(_cleanup_duplicate_comparison_photos(chat_id=12345, capture_id="cap_none", context=context))
        self.assertTrue(res)
        self.assertEqual(context.bot.delete_message.await_count, 0)

    def test_confirm_apply_ootd_resends_prompt_if_cleanup_fails(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.handlers import (
            verification_callback_handler,
            _DUPLICATE_LINKS_KEY,
            _DUPLICATE_COMPARISON_MSG_IDS_KEY,
            _DUPLICATE_PHOTOS_SENT_KEY,
        )
        from app.database import insert_capture_garments

        # Insert 2 garments as OOTD
        g1 = ExtractedGarment(
            category="top", sub_category="navy shirt", primary_color="navy",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=2
        )
        g2 = ExtractedGarment(
            category="bottom", sub_category="khaki pants", primary_color="khaki",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=2
        )
        res = GarmentExtractionResult(photo_type=PhotoType.OOTD, garments=[g1, g2])
        item_ids = insert_capture_garments(self.user_id, self.img1_path, "cap_confirm_fail", res)

        user = MagicMock()
        user.id = self.user_id

        query = MagicMock()
        query.data = "confirm_apply_cap_confirm_fail"
        query.from_user = user
        query.message.chat_id = 99999
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message.reply_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        # Failing delete_message
        context.bot.delete_message = AsyncMock(side_effect=Exception("Cannot delete"))
        context.user_data = {
            "detected_duplicates": {"cap_confirm_fail": {item_ids[0]: self.saved_item_id}},
            _DUPLICATE_LINKS_KEY: {"cap_confirm_fail": {item_ids[0]: self.saved_item_id}},
            _DUPLICATE_COMPARISON_MSG_IDS_KEY: {"cap_confirm_fail": [555]},
            _DUPLICATE_PHOTOS_SENT_KEY: {"cap_confirm_fail": True},
        }

        asyncio.run(verification_callback_handler(update, context))

        # query.edit_message_text was called with a short summary
        query.edit_message_text.assert_awaited()
        # query.message.reply_text was called with the full occasion prompt!
        query.message.reply_text.assert_awaited()
        reply_call_args = query.message.reply_text.call_args[0][0]
        self.assertIn("What occasion did you wear this outfit for?", reply_call_args)
        self.assertIn("Saved to your OOTD collection!", reply_call_args)

    def test_confirm_apply_ootd_edits_in_place_if_cleanup_succeeds(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.handlers import (
            verification_callback_handler,
            _DUPLICATE_LINKS_KEY,
            _DUPLICATE_COMPARISON_MSG_IDS_KEY,
            _DUPLICATE_PHOTOS_SENT_KEY,
        )
        from app.database import insert_capture_garments

        # Insert 2 garments as OOTD
        g1 = ExtractedGarment(
            category="top", sub_category="navy shirt", primary_color="navy",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=2
        )
        g2 = ExtractedGarment(
            category="bottom", sub_category="khaki pants", primary_color="khaki",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=2
        )
        res = GarmentExtractionResult(photo_type=PhotoType.OOTD, garments=[g1, g2])
        item_ids = insert_capture_garments(self.user_id, self.img1_path, "cap_confirm_succ", res)

        user = MagicMock()
        user.id = self.user_id

        query = MagicMock()
        query.data = "confirm_apply_cap_confirm_succ"
        query.from_user = user
        query.message.chat_id = 99999
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message.reply_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        # Successful delete_message
        context.bot.delete_message = AsyncMock()
        context.user_data = {
            "detected_duplicates": {"cap_confirm_succ": {item_ids[0]: self.saved_item_id}},
            _DUPLICATE_LINKS_KEY: {"cap_confirm_succ": {item_ids[0]: self.saved_item_id}},
            _DUPLICATE_COMPARISON_MSG_IDS_KEY: {"cap_confirm_succ": [555]},
            _DUPLICATE_PHOTOS_SENT_KEY: {"cap_confirm_succ": True},
        }

        asyncio.run(verification_callback_handler(update, context))

        # Photo was deleted
        context.bot.delete_message.assert_awaited_once_with(chat_id=99999, message_id=555)
        # query.edit_message_text was called with the full occasion prompt!
        query.edit_message_text.assert_awaited()
        edit_call_args = query.edit_message_text.call_args[0][0]
        self.assertIn("What occasion did you wear this outfit for?", edit_call_args)
        self.assertIn("Saved to your OOTD collection!", edit_call_args)
        # query.message.reply_text was NOT needed because cleanup succeeded
        query.message.reply_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()



