"""Application configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass

from pathlib import Path
from dotenv import load_dotenv

_env_local = Path(__file__).resolve().parent.parent / ".env"
if _env_local.exists():
    load_dotenv(_env_local, override=True)
else:
    load_dotenv(override=True)

_default_db = "../data/wardrobe.db" if Path("../data/wardrobe.db").exists() else "data/wardrobe.db"
DEFAULT_DATABASE_PATH = _default_db
ADMIN_TELEGRAM_USER_ID_ENV = "ADMIN_TELEGRAM_USER_ID"

# The env var name to set your pool-mode password (same pattern as AWS_ACCESS_KEY_ID).
# Add  ADMIN_TEST_PASSWORD=<your-secret>  to your .env file.
ADMIN_TEST_PASSWORD_ENV = "ADMIN_TEST_PASSWORD"

# Virtual user ID used to store all pool-mode garments, shared across all contributors.
POOL_USER_ID = "POOL_TEST_USER"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the bot."""

    telegram_bot_token: str
    database_path: str = DEFAULT_DATABASE_PATH


def load_settings() -> Settings:
    """Load and validate required environment variables.

    ``DATABASE_PATH`` is optional and defaults to ``data/wardrobe.db``.

    Returns:
        A populated ``Settings`` instance.

    Raises:
        RuntimeError: If ``TELEGRAM_BOT_TOKEN`` is not set in the environment
            or ``.env`` file.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Create a .env file (see "
            ".env.example) in the project root and set "
            "TELEGRAM_BOT_TOKEN=<your-bot-token-from-BotFather>."
        )
    database_path = os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH)
    return Settings(telegram_bot_token=token, database_path=database_path)


def is_configured_admin(user_id: str) -> bool:
    """Return whether ``user_id`` matches the optional demo-admin setting.

    Keeping this outside ``Settings`` lets message handlers check authorisation
    without requiring the bot token to be loaded again.
    """
    admin_user_id = os.getenv(ADMIN_TELEGRAM_USER_ID_ENV, "").strip()
    return bool(admin_user_id) and admin_user_id == user_id


def get_admin_test_password() -> str | None:
    """Return the ADMIN_TEST_PASSWORD from the environment, or None if not set.

    This is the shared secret that gates entry into pool-mode testing.
    Set  ADMIN_TEST_PASSWORD=<your-secret>  in your .env file.
    """
    pw = os.getenv(ADMIN_TEST_PASSWORD_ENV, "").strip()
    return pw if pw else None
