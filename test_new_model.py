"""Unit tests for new-model features: wear history, laundry management, variety."""
import os
import tempfile
import unittest

from app.database import (
    clear_user_laundry,
    get_recently_worn_item_ids,
    get_user_garments,
    get_user_laundry_items,
    init_db,
    insert_capture_garments,
    log_outfit_wear,
    mark_garment_verified,
    set_garment_laundry_status,
)
from app.models import ExtractedGarment, GarmentExtractionResult, PhotoType
from app.stylist_graph import run_stylist_workflow


class TestNewModelFeatures(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_wardrobe.db")
        init_db(self.db_path)
        self.user_id = "test_user_999"

        # Insert test garments
        top1 = ExtractedGarment(
            category="top", sub_category="striped tee", primary_color="navy",
            silhouette_fit="regular", fabric_weight="lightweight_breathable", formality_tier=2
        )
        top2 = ExtractedGarment(
            category="top", sub_category="oxford shirt", primary_color="white",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=3
        )
        bot1 = ExtractedGarment(
            category="bottom", sub_category="chinos", primary_color="olive",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=3
        )
        bot2 = ExtractedGarment(
            category="bottom", sub_category="jeans", primary_color="denim",
            silhouette_fit="regular", fabric_weight="heavy_structured", formality_tier=2
        )
        shoes1 = ExtractedGarment(
            category="footwear", sub_category="sneakers", primary_color="white",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=2
        )

        res = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[top1, top2, bot1, bot2, shoes1])
        self.item_ids = insert_capture_garments(
            self.user_id, "dummy.jpg", "cap_test_1", res
        )
        for i_id in self.item_ids:
            mark_garment_verified(i_id, True)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_wear_history_logging(self):
        # Log wear
        log_outfit_wear(self.user_id, [self.item_ids[0], self.item_ids[2]], "university", "worn")
        recent = get_recently_worn_item_ids(self.user_id, days=2)
        self.assertIn(self.item_ids[0], recent)
        self.assertIn(self.item_ids[2], recent)
        self.assertNotIn(self.item_ids[1], recent)

    def test_laundry_management(self):
        # Mark item in laundry
        set_garment_laundry_status(self.item_ids[0], True)
        laundry_items = get_user_laundry_items(self.user_id)
        self.assertEqual(len(laundry_items), 1)
        self.assertEqual(laundry_items[0]["item_id"], self.item_ids[0])

        # Clean garments query excludes laundry
        clean = get_user_garments(self.user_id, verified_only=True, exclude_laundry=True)
        self.assertEqual(len(clean), len(self.item_ids) - 1)
        self.assertNotIn(self.item_ids[0], [g["item_id"] for g in clean])

        # Clear laundry
        cleared_count = clear_user_laundry(self.user_id)
        self.assertEqual(cleared_count, 1)
        self.assertEqual(len(get_user_laundry_items(self.user_id)), 0)

    def test_variety_and_exclusion(self):
        # First recommendation
        rec1 = run_stylist_workflow(self.user_id, "university lecture")
        combo1 = [item.item_id for item in rec1.items]
        self.assertTrue(len(combo1) >= 2)

        # More options request with excluded_combos
        rec2 = run_stylist_workflow(
            self.user_id, "university lecture", excluded_combos=[combo1]
        )
        combo2 = [item.item_id for item in rec2.items]
        # Should return a different combo
        self.assertNotEqual(combo1, combo2)

        # Put an item from combo2 into laundry
        set_garment_laundry_status(combo2[0], True)
        rec3 = run_stylist_workflow(
            self.user_id, "university lecture", excluded_combos=[combo1, combo2]
        )
        combo3 = [item.item_id for item in rec3.items]
        self.assertNotIn(combo2[0], combo3)


if __name__ == "__main__":
    unittest.main()

