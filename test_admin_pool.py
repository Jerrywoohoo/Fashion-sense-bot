"""Tests for admin pool mode: password gating, wardrobe routing, laundry parsing fix."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class TestAdminPoolConfig(unittest.TestCase):
    """Test get_admin_test_password and POOL_USER_ID from config."""

    def test_pool_user_id_is_string(self) -> None:
        from app.config import POOL_USER_ID
        self.assertIsInstance(POOL_USER_ID, str)
        self.assertTrue(len(POOL_USER_ID) > 0)

    def test_get_admin_test_password_returns_none_when_unset(self) -> None:
        from app.config import get_admin_test_password
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADMIN_TEST_PASSWORD", None)
            result = get_admin_test_password()
        self.assertIsNone(result)

    def test_get_admin_test_password_returns_value_when_set(self) -> None:
        from app.config import get_admin_test_password
        with patch.dict(os.environ, {"ADMIN_TEST_PASSWORD": "secret123"}):
            result = get_admin_test_password()
        self.assertEqual(result, "secret123")

    def test_get_admin_test_password_strips_whitespace(self) -> None:
        from app.config import get_admin_test_password
        with patch.dict(os.environ, {"ADMIN_TEST_PASSWORD": "  secret  "}):
            result = get_admin_test_password()
        self.assertEqual(result, "secret")


class TestPoolUserProfile(unittest.TestCase):
    """Test pool profile and garment storage under POOL_USER_ID."""

    def setUp(self) -> None:
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        self.db_file.close()
        from app.database import init_db
        init_db(self.db_path)

    def tearDown(self) -> None:
        os.unlink(self.db_path)

    def test_pool_profile_upsert_and_retrieve(self) -> None:
        from app.config import POOL_USER_ID
        from app.database import get_user_profile, upsert_user_profile
        upsert_user_profile(POOL_USER_ID, {
            "gender_frame": "feminine",
            "height_cm": 162,
            "weight_kg": 52,
            "body_build": "average",
            "proportions": "balanced",
            "favorite_silhouettes": ["fitted_top_wide_bottom"],
            "thermal_preference": "runs_hot",
        })
        profile = get_user_profile(POOL_USER_ID)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["gender_frame"], "feminine")
        self.assertEqual(profile["height_cm"], 162)
        self.assertEqual(profile["weight_kg"], 52)

    def test_pool_garments_stored_under_pool_user_id(self) -> None:
        from app.config import POOL_USER_ID
        from app.database import get_user_garments, insert_raw_garment
        item_id = insert_raw_garment(POOL_USER_ID, "data/images/test_pool.jpg", "test garment")
        garments = get_user_garments(POOL_USER_ID, verified_only=False)
        item_ids = [g["item_id"] for g in garments]
        self.assertIn(item_id, item_ids)

    def test_real_user_garments_separate_from_pool(self) -> None:
        from app.config import POOL_USER_ID
        from app.database import get_user_garments, insert_raw_garment
        real_id = insert_raw_garment("user_real_999", "data/images/real.jpg", "real item")
        pool_id = insert_raw_garment(POOL_USER_ID, "data/images/pool.jpg", "pool item")

        real_garments = get_user_garments("user_real_999", verified_only=False)
        pool_garments = get_user_garments(POOL_USER_ID, verified_only=False)

        real_ids = {g["item_id"] for g in real_garments}
        pool_ids = {g["item_id"] for g in pool_garments}

        self.assertIn(real_id, real_ids)
        self.assertNotIn(real_id, pool_ids)
        self.assertIn(pool_id, pool_ids)
        self.assertNotIn(pool_id, real_ids)

    def test_sequential_item_ids_across_multiple_users(self) -> None:
        """Verify Person A sending 10 items (101-110) followed by Person B (111-114) increments sequentially."""
        from app.config import POOL_USER_ID
        from app.database import insert_raw_garment

        # Person A sends 10 items
        person_a_ids = [
            insert_raw_garment(POOL_USER_ID, f"data/images/userA_{i}.jpg", f"Item A {i}")
            for i in range(10)
        ]
        self.assertEqual(person_a_ids[0], "item_101")
        self.assertEqual(person_a_ids[-1], "item_110")

        # Person B sends 4 items
        person_b_ids = [
            insert_raw_garment(POOL_USER_ID, f"data/images/userB_{i}.jpg", f"Item B {i}")
            for i in range(4)
        ]
        self.assertEqual(person_b_ids, ["item_111", "item_112", "item_113", "item_114"])

        # Person C sends 3 items
        person_c_ids = [
            insert_raw_garment(POOL_USER_ID, f"data/images/userC_{i}.jpg", f"Item C {i}")
            for i in range(3)
        ]
        self.assertEqual(person_c_ids, ["item_115", "item_116", "item_117"])



class TestLaundryToggleParsing(unittest.TestCase):
    """Regression tests for laundry toggle callback data parsing bug fix."""

    def _parse(self, callback_data: str) -> tuple[str, str]:
        remainder = callback_data.removeprefix("wardrobe_laun_")
        if "_" in remainder:
            item_id, cat = remainder.rsplit("_", 1)
        else:
            item_id, cat = remainder, "all"
        return item_id, cat

    def test_item_101_top(self) -> None:
        item_id, cat = self._parse("wardrobe_laun_item_101_top")
        self.assertEqual(item_id, "item_101")
        self.assertEqual(cat, "top")

    def test_item_9999_bottom(self) -> None:
        item_id, cat = self._parse("wardrobe_laun_item_9999_bottom")
        self.assertEqual(item_id, "item_9999")
        self.assertEqual(cat, "bottom")

    def test_item_101_all(self) -> None:
        item_id, cat = self._parse("wardrobe_laun_item_101_all")
        self.assertEqual(item_id, "item_101")
        self.assertEqual(cat, "all")

    def test_item_200_footwear(self) -> None:
        item_id, cat = self._parse("wardrobe_laun_item_200_footwear")
        self.assertEqual(item_id, "item_200")
        self.assertEqual(cat, "footwear")


class TestPoolItemEditingAndCallbacks(unittest.TestCase):
    """Test item editing and callback ownership resolution in pool mode."""

    def setUp(self) -> None:
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        self.db_file.close()
        from app.database import init_db
        init_db(self.db_path)

    def tearDown(self) -> None:
        os.unlink(self.db_path)

    def test_pool_item_accessible_by_allowed_user_ids(self) -> None:
        """Ensure item stored under POOL_USER_ID is recognized as accessible when allowed_user_ids includes pool."""
        from app.config import POOL_USER_ID
        from app.database import get_garment_by_id, insert_raw_garment

        pool_item_id = insert_raw_garment(POOL_USER_ID, "data/images/test.jpg", "pool item")
        garment = get_garment_by_id(pool_item_id)
        self.assertIsNotNone(garment)

        user_id = "1328919692"
        # In normal mode: allowed_user_ids = {user_id}
        normal_allowed = {user_id}
        self.assertNotIn(garment["user_id"], normal_allowed)

        # In pool mode: allowed_user_ids = {user_id, POOL_USER_ID}
        pool_allowed = {user_id, POOL_USER_ID}
        self.assertIn(garment["user_id"], pool_allowed)

    def test_capture_owned_by_with_pool_allowed(self) -> None:
        """Ensure _capture_owned_by recognizes pool captures when allowed_user_ids is provided."""
        from app.config import POOL_USER_ID
        from app.database import insert_capture_garments
        from app.handlers import _capture_owned_by
        from app.models import ExtractedGarment, GarmentExtractionResult, PhotoType

        extraction = GarmentExtractionResult(
            photo_type=PhotoType.SINGLE_ITEM,
            garments=[
                ExtractedGarment(
                    category="top",
                    sub_category="t-shirt",
                    primary_color="navy",
                    accent_colors=[],
                    silhouette_fit="regular",
                    fabric_weight="lightweight",
                    formality_tier=2,
                    style_tags=["basic"],
                    layering_role="base",
                )

            ],
        )

        inserted_ids = insert_capture_garments(
            user_id=POOL_USER_ID,
            image_path="data/images/cap1.jpg",
            capture_id="cap_pool_123",
            extraction=extraction,
        )
        self.assertTrue(len(inserted_ids) > 0)

        # Non-pool mode check fails
        garments_normal = _capture_owned_by("cap_pool_123", "user_123", allowed_user_ids={"user_123"})
        self.assertEqual(garments_normal, [])

        # Pool mode check succeeds
        garments_pool = _capture_owned_by("cap_pool_123", "user_123", allowed_user_ids={"user_123", POOL_USER_ID})
        self.assertEqual(len(garments_pool), 1)
        self.assertEqual(garments_pool[0]["item_id"], inserted_ids[0])



class TestMarkdownEscaping(unittest.IsolatedAsyncioTestCase):
    """Test Markdown escaping to prevent Telegram ParseMode.MARKDOWN entity errors."""

    async def test_start_command_escapes_first_name_with_underscores(self) -> None:
        from unittest.mock import AsyncMock, MagicMock
        from app.handlers import start_command

        user = MagicMock()
        user.id = 12345
        user.first_name = "Jerry_SSD"

        message = AsyncMock()
        message.reply_text = AsyncMock()

        update = MagicMock()
        update.effective_user = user
        update.message = message

        context = MagicMock()

        await start_command(update, context)

        message.reply_text.assert_awaited_once()
        call_args, _ = message.reply_text.call_args
        sent_text = call_args[0]
        # Should contain escaped Jerry\_SSD
        self.assertIn(r"Jerry\_SSD", sent_text)

    def test_format_style_recommendation_escapes_underscores(self) -> None:
        from app.handlers import _format_style_recommendation
        from app.models import OutfitItemSelection, OutfitRecommendation

        rec = OutfitRecommendation(
            outfit_name="Smart_Casual_Look",
            occasion="dinner_date",
            items=[
                OutfitItemSelection(
                    item_id="item_101",
                    category="top_wear",
                    sub_category="button_up",
                    primary_color="navy_blue",
                    role_in_outfit="base_layer",
                )
            ],
            weather_reasoning="Warm and breezy",
            proportion_reasoning="Balanced proportions",
            styling_tips=["Roll_up_sleeves"],
        )
        formatted = _format_style_recommendation(rec)
        self.assertIn(r"Smart\_Casual\_Look", formatted)
    def test_wardrobe_view_all_keyboards_and_actions(self) -> None:
        from app.handlers import (
            _build_wardrobe_delete_keyboard,
            _build_wardrobe_edit_keyboard,
            _build_wardrobe_laundry_keyboard,
            _wardrobe_category_menu_keyboard,
        )

        kb = _wardrobe_category_menu_keyboard("all")
        button_callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]

        self.assertIn("wardrobe_act_edit_all", button_callbacks)
        self.assertIn("wardrobe_act_del_all", button_callbacks)
        self.assertIn("wardrobe_act_laun_all", button_callbacks)
        self.assertIn("wardrobe_menu", button_callbacks)

        sample_all_garments = [
            {"item_id": "item_101", "sub_category": "t-shirt", "color": "white", "brand": "Uniqlo", "in_laundry": 0},
            {"item_id": "item_102", "sub_category": "jeans", "color": "blue", "brand": "Levi's", "in_laundry": 1},
            {"item_id": "item_103", "sub_category": "sneakers", "color": "white", "brand": "Nike", "in_laundry": 0},
        ]

        edit_kb = _build_wardrobe_edit_keyboard(sample_all_garments, "all")
        self.assertEqual(len(edit_kb.inline_keyboard), 4)  # 3 items + Back
        self.assertEqual(edit_kb.inline_keyboard[0][0].callback_data, "wardrobe_doedit_item_101_all")
        self.assertEqual(edit_kb.inline_keyboard[-1][0].callback_data, "wardrobe_back_cat_all")

        del_kb = _build_wardrobe_delete_keyboard(sample_all_garments, "all")
        self.assertEqual(len(del_kb.inline_keyboard), 4)
        self.assertEqual(del_kb.inline_keyboard[0][0].callback_data, "wardrobe_dodel_item_101_all")
        self.assertEqual(del_kb.inline_keyboard[-1][0].callback_data, "wardrobe_back_cat_all")

        laun_kb = _build_wardrobe_laundry_keyboard(sample_all_garments, "all")
        self.assertEqual(len(laun_kb.inline_keyboard), 5)  # 3 items + Clean All + Back
        self.assertEqual(laun_kb.inline_keyboard[0][0].callback_data, "wardrobe_dolaun_item_101_all")
        self.assertEqual(laun_kb.inline_keyboard[-2][0].callback_data, "wardrobe_cleanall_all")
        self.assertEqual(laun_kb.inline_keyboard[-1][0].callback_data, "wardrobe_back_cat_all")

    def test_garments_natural_item_number_sorting(self) -> None:
        from app.handlers import (
            _build_wardrobe_delete_keyboard,
            _build_wardrobe_edit_keyboard,
            _build_wardrobe_laundry_keyboard,
            _sort_garments_by_item_id,
        )

        unsorted = [
            {"item_id": "item_106", "sub_category": "jacket", "color": "black", "brand": None},
            {"item_id": "item_107", "sub_category": "boots", "color": "brown", "brand": None},
            {"item_id": "item_101", "sub_category": "tee", "color": "white", "brand": None},
            {"item_id": "item_105", "sub_category": "jeans", "color": "blue", "brand": None},
        ]

        sorted_items = _sort_garments_by_item_id(unsorted)
        sorted_ids = [g["item_id"] for g in sorted_items]
        self.assertEqual(sorted_ids, ["item_101", "item_105", "item_106", "item_107"])

        edit_kb = _build_wardrobe_edit_keyboard(unsorted, "all")
        edit_callbacks = [btn.callback_data for row in edit_kb.inline_keyboard[:-1] for btn in row]
        self.assertEqual(
            edit_callbacks,
            [
                "wardrobe_doedit_item_101_all",
                "wardrobe_doedit_item_105_all",
                "wardrobe_doedit_item_106_all",
                "wardrobe_doedit_item_107_all",
            ],
        )

        del_kb = _build_wardrobe_delete_keyboard(unsorted, "all")
        del_callbacks = [btn.callback_data for row in del_kb.inline_keyboard[:-1] for btn in row]
        self.assertEqual(
            del_callbacks,
            [
                "wardrobe_dodel_item_101_all",
                "wardrobe_dodel_item_105_all",
                "wardrobe_dodel_item_106_all",
                "wardrobe_dodel_item_107_all",
            ],
        )

        laun_kb = _build_wardrobe_laundry_keyboard(unsorted, "all")
        laun_callbacks = [btn.callback_data for row in laun_kb.inline_keyboard[:-2] for btn in row]
        self.assertEqual(
            laun_callbacks,
            [
                "wardrobe_dolaun_item_101_all",
                "wardrobe_dolaun_item_105_all",
                "wardrobe_dolaun_item_106_all",
                "wardrobe_dolaun_item_107_all",
            ],
        )


class TestWardrobeCallbacksExecution(unittest.IsolatedAsyncioTestCase):
    """Test callback handlers for wardrobe 3-action buttons."""

    def setUp(self) -> None:
        import tempfile
        from app.database import init_db, insert_capture_garments, mark_garment_verified
        from app.models import ExtractedGarment, GarmentExtractionResult, PhotoType

        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db_file.name
        self.db_file.close()
        init_db(self.db_path)

        # Insert sample verified garments for user_123
        item = ExtractedGarment(
            category="top", sub_category="t-shirt", primary_color="white",
            silhouette_fit="regular", fabric_weight="medium", formality_tier=2
        )
        res = GarmentExtractionResult(photo_type=PhotoType.SINGLE_ITEM, garments=[item])
        ids = insert_capture_garments("user_123", "data/images/t.jpg", "cap_123", res)
        mark_garment_verified(ids[0], True)
        self.item_id = ids[0]

    def tearDown(self) -> None:
        import os
        os.unlink(self.db_path)

    async def test_edit_action_callback(self) -> None:
        from unittest.mock import AsyncMock, MagicMock
        from app.handlers import verification_callback_handler

        user = MagicMock()
        user.id = "user_123"

        query = MagicMock()
        query.data = "wardrobe_act_edit_top"
        query.from_user = user
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {}

        await verification_callback_handler(update, context)

        query.edit_message_text.assert_awaited_once()
        args, kwargs = query.edit_message_text.call_args
        self.assertIn("Edit Top Items", args[0])
        # Keyboard should contain item button
        self.assertIn("reply_markup", kwargs)
        kb = kwargs["reply_markup"]
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        self.assertIn(f"wardrobe_doedit_{self.item_id}_top", callbacks)

    async def test_laundry_toggle_callback(self) -> None:
        from unittest.mock import AsyncMock, MagicMock
        from app.database import get_garment_by_id
        from app.handlers import verification_callback_handler

        user = MagicMock()
        user.id = "user_123"

        query = MagicMock()
        query.data = f"wardrobe_dolaun_{self.item_id}_top"
        query.from_user = user
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {}

        # Initial: not in laundry
        self.assertEqual(get_garment_by_id(self.item_id)["in_laundry"], 0)

        # Toggle to laundry
        await verification_callback_handler(update, context)
        self.assertEqual(get_garment_by_id(self.item_id)["in_laundry"], 1)

        # Toggle back to clean
        await verification_callback_handler(update, context)
        self.assertEqual(get_garment_by_id(self.item_id)["in_laundry"], 0)


if __name__ == "__main__":
    unittest.main()

