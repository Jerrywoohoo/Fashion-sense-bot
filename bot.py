"""Entry point for the styling assistant Telegram bot.

Usage:
    python bot.py

Requires a ``.env`` file (see ``.env.example``) with ``TELEGRAM_BOT_TOKEN``
set to a token issued by @BotFather.
"""
from __future__ import annotations

import logging
import signal

from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from app.config import load_settings
from app.database import init_db
from app.extractor import check_aws_credentials_configured
from app.handlers import (
    admin_live_command,
    admin_test_command,
    cancel_command,
    delete_command,
    error_handler,
    help_command,
    laundry_command,
    photo_handler,
    start_command,
    style_action_callback_handler,
    style_command,
    text_handler,
    verification_callback_handler,
    wardrobe_command,
)
from app.paths import ensure_data_directories
from app.profile_flow import profile_conversation_handler

BOT_COMMANDS = [
    BotCommand("start", "Welcome message and quick intro"),
    BotCommand("profile", "View or set up your style profile"),
    BotCommand("wardrobe", "View your cataloged items"),
    BotCommand("style", "Get an outfit suggestion for today"),
    BotCommand("laundry", "Check or clean clothes in laundry"),
    BotCommand("delete", "Remove an item from your wardrobe"),
    BotCommand("help", "Show available commands"),
    BotCommand("admintest", "Enter shared pool mode for wardrobe testing"),
    BotCommand("adminlive", "Exit pool mode and return to your own wardrobe"),
    BotCommand("cancel", "Cancel an in-progress operation"),
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# The HTTP client used internally by python-telegram-bot is chatty at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def build_application() -> Application:
    settings = load_settings()
    ensure_data_directories()
    init_db(settings.database_path)

    request_config = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
    )

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .request(request_config)
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(profile_conversation_handler)
    application.add_handler(CommandHandler("wardrobe", wardrobe_command))
    application.add_handler(CommandHandler("style", style_command))
    application.add_handler(CommandHandler("laundry", laundry_command))
    application.add_handler(CommandHandler(["delete", "remove"], delete_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("admintest", admin_test_command))
    application.add_handler(CommandHandler("adminlive", admin_live_command))
    application.add_handler(CommandHandler("cancel", cancel_command))

    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    application.add_handler(
        CallbackQueryHandler(
            style_action_callback_handler, pattern=r"^(act|laun)_"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            verification_callback_handler, pattern=r"^(confirm|delete|delitem|edit|edititem|manlink|wardrobe|w)_"
        )
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Catches handler exceptions as well as network/polling errors raised by
    # the Updater, so a dropped connection doesn't crash the process.
    application.add_error_handler(error_handler)

    return application


async def _on_startup(application: Application) -> None:
    """Push the command list to Telegram so it shows in the '/' menu."""
    await application.bot.set_my_commands(BOT_COMMANDS)
    logger.info("Registered %d bot commands with Telegram.", len(BOT_COMMANDS))


async def _on_shutdown(application: Application) -> None:
    """Log a clean confirmation once the application has fully stopped."""
    logger.info("Shutdown complete. Goodbye!")


def main() -> None:
    """Build the application and run it with polling until interrupted.

    Polling errors (dropped connections, timeouts, etc.) are handled by
    ``error_handler`` and do not stop the bot; PTB retries automatically.
    A SIGINT or SIGTERM triggers ``Application.run_polling``'s built-in
    graceful shutdown sequence (stop polling, cancel pending updates,
    call ``post_shutdown``).
    """
    try:
        application = build_application()
    except RuntimeError:
        logger.exception("Failed to start: configuration error.")
        raise

    logger.info("Starting bot polling...")
    application.run_polling(
        allowed_updates=None,
        stop_signals=(signal.SIGINT, signal.SIGTERM),
        close_loop=True,
    )


if __name__ == "__main__":
    main()
