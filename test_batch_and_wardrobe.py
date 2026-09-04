"""Unit tests for batch intake, wardrobe visual deduplication, and manual linking."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from app.database import (
    confirm_capture,
    delete_all_user_garments,
    delete_garment,
    get_capture_garments,
    get_garment_by_id,
    get_user_garments,
    init_db,
    insert_capture_garments,
    link_garment_to_existing,
    mark_garment_verified,
    set_garment_laundry_status,
)
from app.models import ExtractedGarment, GarmentExtractionResult, PhotoType


class TestBatchAndWardrobe(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_wardrobe.db")
        init_db(self.db_path)
        self.user_id = "test_user_batch"

        # Create sample images
        self.ootd_img = os.path.join(self.tmp_dir.name, "ootd_photo.jpg")
        self.single_img = os.path.join(self.tmp_dir.name, "single_photo.jpg")

        img = Image.new("RGB", (200, 200), color=(100, 150, 200))
        img.save(self.ootd_img)
        img.save(self.single_img)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_shared_ootd_photo_deduplication(self):
        # An OOTD photo containing top, bottom, and footwear
        top = ExtractedGarment(
            category="top", sub_category="linen shirt", primary_color="cream",
            silhouette_fit="boxy", fabric_weight="lightweight_breathable", formality_tier=2
        )
        bot = ExtractedGarment(
            category="bottom", sub_category="chinos", primary_color="olive",
            silhouette_fit="regular", fabric_weight="lightweight_breathable", formality_tier=2
        )
        shoes = ExtractedGarment(
            category="footwear", sub_category="sneakers", primary_color="white",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=2
        )
        ootd_res = GarmentExtractionResult(photo_type=PhotoType.OOTD, garments=[top, bot, shoes])
        item_ids = insert_capture_garments(self.user_id, self.ootd_img, "cap_ootd_1", ootd_res)
        for i_id in item_ids:
            mark_garment_verified(i_id, True)

        # Query all user garments
        garments = get_user_garments(self.user_id, verified_only=True)
        self.assertEqual(len(garments), 3)

        # Check that grouping by image path produces exactly 1 unique image path for all 3 items
        photos_by_path: dict[str, list[str]] = {}
        for g in garments:
            img_p = g["image_path"]
            photos_by_path.setdefault(img_p, []).append(g["item_id"])

        self.assertEqual(len(photos_by_path), 1)
        self.assertEqual(len(photos_by_path[self.ootd_img]), 3)

    def test_manual_duplicate_linking(self):
        # 1. Saved existing top
        saved_top = ExtractedGarment(
            category="top", sub_category="oxford shirt", primary_color="white",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=3
        )
        saved_res = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[saved_top])
        saved_ids = insert_capture_garments(self.user_id, self.single_img, "cap_saved", saved_res)
        mark_garment_verified(saved_ids[0], True)
        existing_id = saved_ids[0]

        # 2. Uploaded pending duplicate
        new_top = ExtractedGarment(
            category="top", sub_category="white shirt", primary_color="white",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=3
        )
        new_res = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[new_top])
        new_ids = insert_capture_garments(self.user_id, self.single_img, "cap_new", new_res)
        pending_id = new_ids[0]

        # Verify pending item is unverified
        self.assertEqual(get_garment_by_id(pending_id)["is_verified"], 0)

        # 3. Manually link pending item to existing item
        link_garment_to_existing(
            pending_id, existing_id, self.user_id, self.single_img, "worn again today"
        )

        # Pending item row is replaced/deleted, existing item remains
        self.assertIsNone(get_garment_by_id(pending_id))
        self.assertIsNotNone(get_garment_by_id(existing_id))

    def test_wardrobe_delete_and_laundry_toggle(self):
        item = ExtractedGarment(
            category="bottom", sub_category="jeans", primary_color="black",
            silhouette_fit="regular", fabric_weight="heavy_structured", formality_tier=2
        )
        res = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[item])
        ids = insert_capture_garments(self.user_id, self.single_img, "cap_test", res)
        item_id = ids[0]
        mark_garment_verified(item_id, True)

        # Laundry toggle
        set_garment_laundry_status(item_id, True)
        self.assertEqual(get_garment_by_id(item_id)["in_laundry"], 1)

        set_garment_laundry_status(item_id, False)
        self.assertEqual(get_garment_by_id(item_id)["in_laundry"], 0)

        # Deletion
        deleted = delete_garment(item_id)
        self.assertTrue(deleted)
        self.assertIsNone(get_garment_by_id(item_id))

    def test_delete_all_user_garments(self):
        top = ExtractedGarment(
            category="top", sub_category="t-shirt", primary_color="blue",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=2
        )
        bot = ExtractedGarment(
            category="bottom", sub_category="shorts", primary_color="black",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=2
        )
        res_a = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[top, bot])
        ids_a = insert_capture_garments(self.user_id, self.single_img, "cap_a", res_a)
        for i_id in ids_a:
            mark_garment_verified(i_id, True)

        res_b = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[top])
        ids_b = insert_capture_garments("other_user", self.single_img, "cap_b", res_b)
        mark_garment_verified(ids_b[0], True)

        # Clear entire wardrobe for user A
        count, image_paths = delete_all_user_garments(self.user_id)
        self.assertEqual(count, 2)
        self.assertIn(self.single_img, image_paths)

        # Verify user A's wardrobe is empty
        self.assertEqual(len(get_user_garments(self.user_id, verified_only=True)), 0)

        # Verify user B's wardrobe is untouched
        self.assertEqual(len(get_user_garments("other_user", verified_only=True)), 1)

    def test_format_item_title(self):
        from app.handlers import _format_item_title

        # Brand + color + subcat (4 words)
        self.assertEqual(
            _format_item_title("polo shirt", "brown", "Uniqlo"),
            "Uniqlo brown polo shirt",
        )
        # Default 4-word cap
        self.assertEqual(
            _format_item_title("away jersey", "yellow", "Arsenal F.C."),
            "Arsenal F.C. yellow away",
        )
        # Unconstrained full title
        self.assertEqual(
            _format_item_title("away jersey", "yellow", "Arsenal F.C.", max_words=None),
            "Arsenal F.C. yellow away jersey",
        )
        # No brand fallback
        self.assertEqual(
            _format_item_title("crewneck tee", "navy", None),
            "navy crewneck tee",
        )
        self.assertEqual(
            _format_item_title("oversized dropped shoulder heavyweight hoodie", "washed charcoal", "Fear of God"),
            "Fear of God washed",
        )

    def test_save_user_outfit_and_wear_fk_auto_satisfaction(self):
        from app.database import get_user_outfits, log_outfit_wear, save_user_outfit

        # Testing with a fresh new user ID that doesn't have a profile yet
        fresh_user_id = "user_brand_new_999"
        outfit_id = save_user_outfit(fresh_user_id, "smart casual party", ["item_101", "item_102"])
        self.assertGreater(outfit_id, 0)

        saved = get_user_outfits(fresh_user_id, occasion_keyword="party")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["item_ids"], ["item_101", "item_102"])

        log_id = log_outfit_wear(fresh_user_id, ["item_101", "item_102"], "party", action="worn")
        self.assertGreater(log_id, 0)

    def test_save_user_outfit_with_linked_items_and_image(self):
        from app.database import delete_user_outfit, get_user_outfit_by_id, get_user_outfits, save_user_outfit

        user_id = "user_ootd_test_1"
        outfit_id = save_user_outfit(
            user_id=user_id,
            occasion="Brunch with friends",
            item_ids=["item_101", "item_102", "item_103"],
            linked_item_ids=["item_101"],
            image_path=self.ootd_img,
        )
        self.assertGreater(outfit_id, 0)

        outfits = get_user_outfits(user_id)
        self.assertEqual(len(outfits), 1)
        self.assertEqual(outfits[0]["outfit_id"], outfit_id)
        self.assertEqual(outfits[0]["occasion"], "brunch with friends")
        self.assertEqual(outfits[0]["item_ids"], ["item_101", "item_102", "item_103"])
        self.assertEqual(outfits[0]["linked_item_ids"], ["item_101"])
        self.assertEqual(outfits[0]["image_path"], self.ootd_img)

        fetched = get_user_outfit_by_id(outfit_id, user_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["outfit_id"], outfit_id)
        self.assertEqual(fetched["linked_item_ids"], ["item_101"])

        deleted = delete_user_outfit(outfit_id, user_id)
        self.assertTrue(deleted)
        self.assertEqual(len(get_user_outfits(user_id)), 0)

    def test_link_garment_to_existing_updates_user_outfits(self):
        from app.database import get_user_outfits, save_user_outfit

        # Create two garments in DB
        top1 = ExtractedGarment(
            category="top", sub_category="linen shirt", primary_color="white",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=2
        )
        top2 = ExtractedGarment(
            category="top", sub_category="white linen shirt", primary_color="white",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=2
        )
        res1 = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[top1])
        res2 = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[top2])

        ids1 = insert_capture_garments(self.user_id, self.single_img, "cap_1", res1)
        ids2 = insert_capture_garments(self.user_id, self.single_img, "cap_2", res2)
        tgt_id = ids1[0]
        src_id = ids2[0]
        mark_garment_verified(tgt_id, True)
        mark_garment_verified(src_id, True)

        # Save an OOTD referencing src_id
        outfit_id = save_user_outfit(
            self.user_id, "Weekend chill", [src_id, "item_999"], image_path=self.ootd_img
        )
        self.assertGreater(outfit_id, 0)

        # Now merge src_id into tgt_id
        ok = link_garment_to_existing(
            new_item_id=src_id,
            existing_item_id=tgt_id,
            user_id=self.user_id,
        )
        self.assertTrue(ok)
        self.assertIsNone(get_garment_by_id(src_id))

        # Check that the OOTD record was updated:
        # src_id replaced with tgt_id, and tgt_id added to linked_item_ids
        outfits = get_user_outfits(self.user_id)
        self.assertEqual(len(outfits), 1)
        self.assertIn(tgt_id, outfits[0]["item_ids"])
        self.assertNotIn(src_id, outfits[0]["item_ids"])
        self.assertIn(tgt_id, outfits[0]["linked_item_ids"])

    def test_verification_keyboard_non_committing_toggles(self):
        from app.handlers import _verification_keyboard

        capture_id = "cap_test_123"
        item_ids = ["item_1", "item_2"]
        detected_links = {"item_1": "item_prev_10"}
        active_links = {"item_1": "item_prev_10"}

        kb = _verification_keyboard(
            capture_id,
            has_duplicates=True,
            item_ids=item_ids,
            detected_links=detected_links,
            active_links=active_links,
        )
        button_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]

        # Duplicate toggle callback exists
        self.assertIn(f"confirm_toggle_item_1_{capture_id}", button_data)
        # Explicit confirmation row exists
        self.assertIn(f"confirm_apply_{capture_id}", button_data)
        self.assertIn(f"confirm_{capture_id}", button_data)
        # Edit buttons exist for all items
        self.assertIn("edititem_item_1", button_data)
        self.assertIn("edititem_item_2", button_data)
        # Manual duplicate link button exists
        self.assertIn(f"manlink_start_{capture_id}", button_data)

    def test_category_emoji_helper(self):
        from app.handlers import _get_category_emoji

        self.assertEqual(_get_category_emoji("top"), "👕")
        self.assertEqual(_get_category_emoji("bottom"), "👖")
        self.assertEqual(_get_category_emoji("outerwear"), "🧥")
        self.assertEqual(_get_category_emoji("footwear"), "👟")
        self.assertEqual(_get_category_emoji("accessory"), "🎒")
        self.assertEqual(_get_category_emoji("something_else"), "👗")

    def test_wardrobe_link_keyboard_formatting(self):
        from app.handlers import _build_wardrobe_link_source_keyboard

        garments = [
            {"item_id": "item_101", "sub_category": "linen shirt", "color": "white", "brand": "Uniqlo", "category": "top"},
            {"item_id": "item_102", "sub_category": "denim jeans", "color": "blue", "brand": "Levi's", "category": "bottom"},
        ]
        kb = _build_wardrobe_link_source_keyboard(garments, "all")
        button_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertIn("item_101: Uniqlo white linen shirt (Top)", button_texts)
        self.assertIn("item_102: Levi's blue denim jeans (Bottom)", button_texts)


if __name__ == "__main__":
    unittest.main()


