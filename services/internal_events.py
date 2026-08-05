"""Best-effort notifications from RobinHub workers to the dashboard API."""

from __future__ import annotations

import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def notify_internal_event(event: str, *, timeout_seconds: int = 3) -> bool:
    """Notify the dashboard API after a successful application write.

    Notification failures are logged but deliberately do not interrupt the
    customer or staff workflow. The dashboard cache TTL remains the fallback.
    """

    event_name = str(event or "").strip()
    event_url = os.getenv(
        "INTERNAL_EVENT_URL",
        "http://robins-reserve-api-dev:8000/internal/event",
    ).strip()
    token = os.getenv("INTERNAL_CACHE_TOKEN", "").strip()

    if not event_name:
        logger.warning("Internal dashboard event was not sent: event name is empty")
        return False

    if not event_url or not token:
        logger.warning(
            "Internal dashboard event %s was not sent: URL or token is missing",
            event_name,
        )
        return False

    payload = json.dumps({"event": event_name}).encode("utf-8")
    request = Request(
        event_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Internal-Token": token,
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=max(1, int(timeout_seconds))) as response:
            success = 200 <= response.status < 300
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        logger.warning(
            "Internal dashboard event %s failed with HTTP %s: %s",
            event_name,
            exc.code,
            body[:300],
        )
        return False
    except URLError as exc:
        logger.warning(
            "Internal dashboard event %s could not reach the API: %s",
            event_name,
            exc.reason,
        )
        return False
    except Exception:
        logger.exception("Internal dashboard event %s failed", event_name)
        return False

    if success:
        logger.info("Internal dashboard event sent: %s", event_name)
    return success
