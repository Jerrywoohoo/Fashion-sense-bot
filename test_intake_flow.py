"""Offline regression tests for wardrobe intake safeguards."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import (
    confirm_capture,
    find_potential_duplicates,
    get_garment_by_id,
    init_db,
    insert_capture_garments,
    update_garment_extracted_data,
)
from app.models import ExtractedGarment, GarmentExtractionResult, PhotoType


def garment(*, color: str, accent_colors: list[str] | None = None) -> ExtractedGarment:
    return ExtractedGarment(
        category="top",
        sub_category="crewneck tee",
        primary_color=color,
        accent_colors=accent_colors or [],
        silhouette_fit="regular",
        fabric_weight="medium",
        formality_tier=2,
        style_tags=["casual", "minimal"],
        layering_role="standalone",
    )


class IntakeFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        init_db(str(Path(self.temp_dir.name) / "wardrobe.db"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _insert_confirmed(self, item: ExtractedGarment) -> str:
        item_id = insert_capture_garments(
            "user-1", "one.jpg", f"capture-{item.primary_color}",
            GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[item]),
        )[0]
        confirm_capture(f"capture-{item.primary_color}")
        return item_id

    def test_same_style_different_colour_is_not_a_duplicate_candidate(self) -> None:
        self._insert_confirmed(garment(color="navy"))

        self.assertEqual(find_potential_duplicates("user-1", garment(color="red")), [])

    def test_matching_colour_and_style_is_shortlisted_for_llm_review(self) -> None:
        existing_id = self._insert_confirmed(garment(color="navy"))

        candidates = find_potential_duplicates("user-1", garment(color="navy"))

        self.assertEqual([candidate["item_id"] for candidate in candidates], [existing_id])

    def test_owner_correction_persists_accent_colours(self) -> None:
        item_id = self._insert_confirmed(garment(color="red"))
        update_garment_extracted_data(item_id, garment(color="burgundy", accent_colors=["cream stripe"]))

        item = get_garment_by_id(item_id)
        self.assertEqual(item["color"], "burgundy")
        self.assertEqual(item["accent_colors"], '["cream stripe"]')


if __name__ == "__main__":
    unittest.main()
