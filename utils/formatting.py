from datetime import datetime
from zoneinfo import ZoneInfo


def format_datetime(timestamp: str) -> str:
    """Convert an ISO timestamp into readable Belfast local time."""

    if not timestamp:
        return "Unknown"

    parsed = datetime.fromisoformat(timestamp)

    return parsed.astimezone(
        ZoneInfo("Europe/London")
    ).strftime("%d %b %Y at %H:%M")
