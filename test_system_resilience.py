"""Tests for system responsiveness: HTTPX polling resilience, ConversationHandler routing, and state cleanup."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.ext import ConversationHandler

from app.handlers import (
    _AWAITING_ADMIN_PW_KEY,
    _AWAITING_POOL_PROFILE_KEY,
    _PENDING_CORRECTION_KEY,
    _PENDING_ITEM_EDIT_KEY,
    _clear_transient_chat_state,
)
from app.profile_flow import (
    _DRAFT_KEY,
    fallback_command_router,
    profile_conversation_handler,
)


class TestProfileConversationHandlerResilience(unittest.IsolatedAsyncioTestCase):
    """Test conversation handler configuration and fallback command routing."""

    def test_handler_allows_reentry_and_timeout(self) -> None:
        self.assertTrue(
            profile_conversation_handler.allow_reentry,
            "profile_conversation_handler must have allow_reentry=True so users can restart /profile.",
        )
        self.assertEqual(
            profile_conversation_handler.conversation_timeout,
            300.0,
            "profile_conversation_handler must have a 5-minute timeout to avoid trapping users.",
        )

    async def test_fallback_router_routes_wardrobe_command(self) -> None:
        update = MagicMock()
        update.message = MagicMock()
        update.message.text = "/wardrobe"
        context = MagicMock()
        context.user_data = {_DRAFT_KEY: {"favorite_silhouettes": []}}

        with patch("app.handlers.wardrobe_command", new_callable=AsyncMock) as mock_wardrobe:
            res = await fallback_command_router(update, context)

        self.assertEqual(res, ConversationHandler.END)
        self.assertNotIn(_DRAFT_KEY, context.user_data)
        mock_wardrobe.assert_awaited_once_with(update, context)

    async def test_fallback_router_routes_style_command(self) -> None:
        update = MagicMock()
        update.message = MagicMock()
        update.message.text = "/style"
        context = MagicMock()
        context.user_data = {_DRAFT_KEY: {}}

        with patch("app.handlers.style_command", new_callable=AsyncMock) as mock_style:
            res = await fallback_command_router(update, context)

        self.assertEqual(res, ConversationHandler.END)
        mock_style.assert_awaited_once_with(update, context)

    async def test_fallback_router_routes_cancel(self) -> None:
        update = MagicMock()
        update.message = MagicMock()
        update.message.text = "/cancel"
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.user_data = {_DRAFT_KEY: {}}

        res = await fallback_command_router(update, context)
        self.assertEqual(res, ConversationHandler.END)
        self.assertNotIn(_DRAFT_KEY, context.user_data)
        update.message.reply_text.assert_awaited_once()


class TestTransientChatStateCleanup(unittest.TestCase):
    """Test transient state flags are cleanly wiped when navigating between commands."""

    def test_clear_transient_chat_state(self) -> None:
        context = MagicMock()
        context.user_data = {
            "awaiting_style_input": True,
            "style_user_id": "123",
            _PENDING_ITEM_EDIT_KEY: "item_999",
            _PENDING_CORRECTION_KEY: "cap_123",
            _AWAITING_ADMIN_PW_KEY: True,
            _AWAITING_POOL_PROFILE_KEY: True,
            "keep_this": "preserved",
        }

        _clear_transient_chat_state(context)

        self.assertNotIn("awaiting_style_input", context.user_data)
        self.assertNotIn("style_user_id", context.user_data)
        self.assertNotIn(_PENDING_ITEM_EDIT_KEY, context.user_data)
        self.assertNotIn(_PENDING_CORRECTION_KEY, context.user_data)
        self.assertNotIn(_AWAITING_ADMIN_PW_KEY, context.user_data)
        self.assertNotIn(_AWAITING_POOL_PROFILE_KEY, context.user_data)
        self.assertEqual(context.user_data.get("keep_this"), "preserved")


class TestNetworkAndPollingConfiguration(unittest.TestCase):
    """Test HTTPX request configuration and polling resilience in bot.py."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db_path = self.temp_db.name
        self.temp_db.close()

    def tearDown(self) -> None:
        if os.path.exists(self.temp_db_path):
            os.unlink(self.temp_db_path)

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"})
    def test_build_application_request_configs(self) -> None:
        with patch("app.config.load_settings") as mock_settings:
            settings_obj = MagicMock()
            settings_obj.telegram_bot_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
            settings_obj.database_path = self.temp_db_path
            mock_settings.return_value = settings_obj

            from bot import build_application
            app = build_application()

            # app.bot._request has (get_updates_request, main_request)
            get_updates_req = app.bot._request[0]
            main_req = app.bot._request[1]

            # Polling request: fast read timeout, smaller pool
            self.assertEqual(get_updates_req.read_timeout, 15.0)
            self.assertEqual(get_updates_req._client_kwargs["timeout"].connect, 10.0)
            self.assertEqual(get_updates_req._client_kwargs["timeout"].pool, 5.0)

            # Main request: large pool, high media write timeout
            self.assertEqual(main_req._media_write_timeout, 60.0)
            self.assertEqual(main_req.read_timeout, 20.0)
            self.assertEqual(main_req._client_kwargs["timeout"].connect, 10.0)
            self.assertEqual(main_req._client_kwargs["timeout"].pool, 5.0)


if __name__ == "__main__":
    unittest.main()
