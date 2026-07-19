import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def require_env(name: str) -> str:
    """Return a required environment variable or raise an error."""

    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(f"{name} is missing from .env")

    return value


def require_int_env(name: str) -> int:
    """Return a required integer environment variable."""

    try:
        return int(require_env(name))
    except ValueError as exc:
        raise RuntimeError(
            f"{name} must contain numbers only"
        ) from exc


DISCORD_BOT_TOKEN = require_env("DISCORD_BOT_TOKEN")
GOOGLE_SHEET_ID = require_env("GOOGLE_SHEET_ID")

STAFF_CHANNEL_ID = require_int_env("STAFF_CHANNEL_ID")
STAFF_ROLE_ID = require_int_env("STAFF_ROLE_ID")


GOOGLE_CREDENTIALS_FILE = Path(
    os.getenv(
        "GOOGLE_CREDENTIALS_FILE",
        "secrets/google-service-account.json",
    )
)

if not GOOGLE_CREDENTIALS_FILE.is_file():
    raise RuntimeError(
        f"Google credentials file not found: "
        f"{GOOGLE_CREDENTIALS_FILE}"
    )
