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

    def test_owner_correction_persists_user_caption(self) -> None:
        item_id = self._insert_confirmed(garment(color="black"))
        update_garment_extracted_data(
            item_id,
            garment(color="black", accent_colors=["gold"]),
            user_caption="leather hand bag, and gold with just zipper not the bag itself",
        )

        item = get_garment_by_id(item_id)
        self.assertEqual(item["user_caption"], "leather hand bag, and gold with just zipper not the bag itself")
        self.assertEqual(item["accent_colors"], '["gold"]')

    def test_format_extraction_summary_preserves_full_title(self) -> None:
        from app.handlers import _format_extraction_summary, _format_item_title
        long_subcat = "leather shoulder bag with gold zipper accents"
        g = ExtractedGarment(
            category="accessory",
            sub_category=long_subcat,
            primary_color="black",
            accent_colors=["gold"],
            silhouette_fit="regular",
            fabric_weight="medium",
            formality_tier=3,
            style_tags=["leather", "luxury"],
            layering_role="standalone",
        )
        res = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[g])
        summary = _format_extraction_summary(["item_1"], res, {})
        # Should contain full title without 4-word truncation
        self.assertIn("black leather shoulder bag with gold zipper accents", summary)
        # Whereas default _format_item_title truncates to max_words=4
        truncated = _format_item_title(long_subcat, "black", max_words=4)
        self.assertEqual(len(truncated.split()), 4)

    def test_verification_keyboard_retains_active_links(self) -> None:
        from app.handlers import _verification_keyboard
        det = {"item_10": "item_1"}
        # When user active_links is empty (untoggled / keep new)
        kb_keep_new = _verification_keyboard("cap_1", has_duplicates=True, item_ids=["item_10", "item_11"], detected_links=det, active_links={})
        btn_text = kb_keep_new.inline_keyboard[0][0].text
        self.assertIn("Keep as New", btn_text)

        # When user active_links has item_10 linked
        kb_linked = _verification_keyboard("cap_1", has_duplicates=True, item_ids=["item_10", "item_11"], detected_links=det, active_links={"item_10": "item_1"})
        btn_text_linked = kb_linked.inline_keyboard[0][0].text
        self.assertIn("Link item_10 → item_1", btn_text_linked)


if __name__ == "__main__":
    unittest.main()
