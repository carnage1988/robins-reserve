"""Central retry, caching and diagnostics for gspread operations."""
from __future__ import annotations

import copy
import logging
import random
import threading
import time
from dataclasses import asdict, dataclass
from functools import wraps
from typing import Any, Callable

import gspread
from gspread.exceptions import APIError
from gspread.http_client import HTTPClient

logger = logging.getLogger(__name__)
_lock = threading.RLock()
_cache: dict[tuple[int, str, tuple[Any, ...]], tuple[float, Any]] = {}
_installed = False


@dataclass
class SheetsMetrics:
    requests: int = 0
    retries: int = 0
    rate_limits: int = 0
    failures: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    invalidations: int = 0
    last_success_monotonic: float = 0.0
    last_failure_monotonic: float = 0.0
    last_status: int = 0


_metrics = SheetsMetrics()
_started_monotonic = time.monotonic()


def _status(exc: APIError) -> int:
    response = getattr(exc, "response", None)
    return int(getattr(response, "status_code", 0) or 0)


def _retryable(exc: APIError) -> bool:
    return _status(exc) in {429, 500, 502, 503, 504}


def _retry_after(exc: APIError) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _call_with_retry(
    func: Callable[..., Any],
    *args: Any,
    max_attempts: int = 7,
    **kwargs: Any,
) -> Any:
    delay = 2.0
    for attempt in range(1, max_attempts + 1):
        with _lock:
            _metrics.requests += 1
        try:
            value = func(*args, **kwargs)
            with _lock:
                _metrics.last_success_monotonic = time.monotonic()
                _metrics.last_status = 200
            return value
        except APIError as exc:
            status = _status(exc)
            with _lock:
                _metrics.last_failure_monotonic = time.monotonic()
                _metrics.last_status = status
                if status == 429:
                    _metrics.rate_limits += 1

            if not _retryable(exc) or attempt >= max_attempts:
                with _lock:
                    _metrics.failures += 1
                raise

            with _lock:
                _metrics.retries += 1
            server_delay = _retry_after(exc)
            sleep_for = (
                server_delay
                if server_delay is not None
                else min(delay, 30.0) + random.uniform(0, 0.5)
            )
            logger.warning(
                "Google Sheets request failed with HTTP %s "
                "(attempt %s/%s); retrying in %.2fs",
                status,
                attempt,
                max_attempts,
                sleep_for,
            )
            time.sleep(sleep_for)
            delay *= 2

    raise RuntimeError("Google Sheets retry loop exited unexpectedly")


def _key(
    worksheet: gspread.Worksheet,
    name: str,
    args: tuple[Any, ...],
) -> tuple[int, str, tuple[Any, ...]]:
    return (id(worksheet), name, args)


def _invalidate(worksheet: gspread.Worksheet) -> None:
    identity = id(worksheet)
    removed = 0
    with _lock:
        for key in [key for key in _cache if key[0] == identity]:
            _cache.pop(key, None)
            removed += 1
        if removed:
            _metrics.invalidations += 1


def clear_sheets_cache() -> int:
    """Clear all cached worksheet reads and return the removed entry count."""
    with _lock:
        count = len(_cache)
        _cache.clear()
        if count:
            _metrics.invalidations += 1
        return count


def get_sheets_diagnostics() -> dict[str, Any]:
    """Return a safe snapshot for health/status commands."""
    now = time.monotonic()
    with _lock:
        data = asdict(_metrics)
        data["cache_entries"] = len(_cache)
    data["installed"] = _installed
    data["uptime_seconds"] = round(now - _started_monotonic, 1)
    success = data.pop("last_success_monotonic")
    failure = data.pop("last_failure_monotonic")
    data["last_success_age_seconds"] = (
        round(now - success, 1) if success else None
    )
    data["last_failure_age_seconds"] = (
        round(now - failure, 1) if failure else None
    )
    return data


def _cached_wrapper(
    original: Callable[..., Any],
    name: str,
    ttl: float,
) -> Callable[..., Any]:
    @wraps(original)
    def wrapped(
        worksheet: gspread.Worksheet,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        try:
            frozen = (args, tuple(sorted(kwargs.items())))
            hash(frozen)
        except Exception:
            return original(worksheet, *args, **kwargs)

        cache_key = _key(worksheet, name, frozen)
        now = time.monotonic()
        with _lock:
            hit = _cache.get(cache_key)
            if hit and hit[0] > now:
                _metrics.cache_hits += 1
                return copy.deepcopy(hit[1])
            _metrics.cache_misses += 1

        value = original(worksheet, *args, **kwargs)
        with _lock:
            _cache[cache_key] = (now + ttl, copy.deepcopy(value))
        return value

    return wrapped


def _write_wrapper(original: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(original)
    def wrapped(
        worksheet: gspread.Worksheet,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        value = original(worksheet, *args, **kwargs)
        _invalidate(worksheet)
        return value

    return wrapped


def install_gspread_resilience() -> None:
    """Install retries before any spreadsheet is opened, plus read caching."""
    global _installed
    if _installed:
        return

    original_request = HTTPClient.request

    @wraps(original_request)
    def resilient_request(client: HTTPClient, *args: Any, **kwargs: Any) -> Any:
        return _call_with_retry(original_request, client, *args, **kwargs)

    HTTPClient.request = resilient_request

    worksheet_class = gspread.Worksheet
    worksheet_class.row_values = _cached_wrapper(
        worksheet_class.row_values, "row_values", 900.0
    )
    worksheet_class.get_all_records = _cached_wrapper(
        worksheet_class.get_all_records, "get_all_records", 30.0
    )
    worksheet_class.get_all_values = _cached_wrapper(
        worksheet_class.get_all_values, "get_all_values", 30.0
    )
    worksheet_class.get = _cached_wrapper(
        worksheet_class.get, "get", 30.0
    )

    for name in (
        "update_cell",
        "update",
        "batch_update",
        "append_row",
        "append_rows",
        "delete_rows",
        "clear",
    ):
        if hasattr(worksheet_class, name):
            setattr(
                worksheet_class,
                name,
                _write_wrapper(getattr(worksheet_class, name)),
            )

    _installed = True
    logger.info("Installed resilient Google Sheets access layer")
