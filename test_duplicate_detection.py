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


if __name__ == "__main__":
    unittest.main()

