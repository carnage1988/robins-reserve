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


DISCORD_BOT_TOKEN = require_env("DISCORD_BOT_TOKEN")
GOOGLE_SHEET_ID = require_env("GOOGLE_SHEET_ID")

try:
    STAFF_CHANNEL_ID = int(require_env("STAFF_CHANNEL_ID"))
except ValueError as exc:
    raise RuntimeError("STAFF_CHANNEL_ID must contain numbers only") from exc


GOOGLE_CREDENTIALS_FILE = Path(
    os.getenv(
        "GOOGLE_CREDENTIALS_FILE",
        "secrets/google-service-account.json",
    )
)

if not GOOGLE_CREDENTIALS_FILE.is_file():
    raise RuntimeError(
        f"Google credentials file not found: {GOOGLE_CREDENTIALS_FILE}"
    )
