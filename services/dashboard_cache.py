"""Thread-safe in-memory cache for RobinHub dashboard API data.

This module deliberately has no FastAPI or Google Sheets dependencies.  It can
therefore be introduced and syntax-tested before wiring it into dashboard_api.
"""

from __future__ import annotations

import copy
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Callable, Generic, TypeVar


logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(slots=True)
class CacheEntry(Generic[T]):
    """One cached value and its timing metadata."""

    value: T
    stored_monotonic: float
    stored_at: datetime


@dataclass(slots=True)
class CacheResult(Generic[T]):
    """Value returned to an API endpoint with cache metadata."""

    value: T
    cache_status: str
    age_seconds: float
    refreshed_at: str


class DashboardCache:
    """Small thread-safe cache with TTL and stale-data fallback.

    A per-key lock prevents multiple simultaneous dashboard requests from
    rebuilding the same Google Sheets-backed payload at once.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        ttl_seconds: int = 60,
        stale_seconds: int = 300,
    ) -> None:
        self.enabled = bool(enabled)
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.stale_seconds = max(self.ttl_seconds, int(stale_seconds))

        self._entries: dict[str, CacheEntry[Any]] = {}
        self._entries_lock = threading.RLock()
        self._key_locks: dict[str, threading.Lock] = {}

        self._hits = 0
        self._misses = 0
        self._refreshes = 0
        self._stale_fallbacks = 0
        self._invalidations = 0
        self._last_error: str = ""

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")

    def _get_key_lock(self, key: str) -> threading.Lock:
        with self._entries_lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    def _entry_age(self, entry: CacheEntry[Any]) -> float:
        return max(0.0, monotonic() - entry.stored_monotonic)

    def _get_entry(self, key: str) -> CacheEntry[Any] | None:
        with self._entries_lock:
            return self._entries.get(key)

    def _store(self, key: str, value: T) -> CacheEntry[T]:
        entry: CacheEntry[T] = CacheEntry(
            value=copy.deepcopy(value),
            stored_monotonic=monotonic(),
            stored_at=self._utc_now(),
        )
        with self._entries_lock:
            self._entries[key] = entry
            self._refreshes += 1
            self._last_error = ""
        return entry

    def get_or_load(
        self,
        key: str,
        loader: Callable[[], T],
        *,
        force_refresh: bool = False,
    ) -> CacheResult[T]:
        """Return a fresh cached value, loading it once when required.

        If loading fails and an entry is no older than ``stale_seconds``, that
        stale value is returned so a temporary Sheets quota/network failure does
        not take the dashboard down.
        """

        clean_key = str(key or "").strip()
        if not clean_key:
            raise ValueError("A non-empty cache key is required.")

        if not self.enabled:
            loaded = loader()
            now = self._utc_now()
            return CacheResult(
                value=loaded,
                cache_status="disabled",
                age_seconds=0.0,
                refreshed_at=self._iso(now),
            )

        entry = self._get_entry(clean_key)
        if entry is not None and not force_refresh:
            age = self._entry_age(entry)
            if age < self.ttl_seconds:
                with self._entries_lock:
                    self._hits += 1
                return CacheResult(
                    value=copy.deepcopy(entry.value),
                    cache_status="hit",
                    age_seconds=round(age, 3),
                    refreshed_at=self._iso(entry.stored_at),
                )

        key_lock = self._get_key_lock(clean_key)
        with key_lock:
            # Another request may have refreshed while this request waited.
            entry = self._get_entry(clean_key)
            if entry is not None and not force_refresh:
                age = self._entry_age(entry)
                if age < self.ttl_seconds:
                    with self._entries_lock:
                        self._hits += 1
                    return CacheResult(
                        value=copy.deepcopy(entry.value),
                        cache_status="hit_after_wait",
                        age_seconds=round(age, 3),
                        refreshed_at=self._iso(entry.stored_at),
                    )

            with self._entries_lock:
                self._misses += 1

            try:
                loaded = loader()
            except Exception as exc:
                stale_entry = self._get_entry(clean_key)
                if stale_entry is not None:
                    stale_age = self._entry_age(stale_entry)
                    if stale_age <= self.stale_seconds:
                        with self._entries_lock:
                            self._stale_fallbacks += 1
                            self._last_error = f"{type(exc).__name__}: {exc}"
                        logger.warning(
                            "Cache loader failed for %s; serving %.1fs stale data: %s",
                            clean_key,
                            stale_age,
                            exc,
                        )
                        return CacheResult(
                            value=copy.deepcopy(stale_entry.value),
                            cache_status="stale_fallback",
                            age_seconds=round(stale_age, 3),
                            refreshed_at=self._iso(stale_entry.stored_at),
                        )

                with self._entries_lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                raise

            new_entry = self._store(clean_key, loaded)
            return CacheResult(
                value=copy.deepcopy(new_entry.value),
                cache_status="refreshed",
                age_seconds=0.0,
                refreshed_at=self._iso(new_entry.stored_at),
            )

    def set(self, key: str, value: T) -> None:
        """Explicitly replace one cached value after a successful write."""

        clean_key = str(key or "").strip()
        if not clean_key:
            raise ValueError("A non-empty cache key is required.")
        self._store(clean_key, value)

    def invalidate(self, *keys: str) -> int:
        """Remove selected keys, returning the number actually removed."""

        clean_keys = {str(key or "").strip() for key in keys}
        clean_keys.discard("")
        removed = 0

        with self._entries_lock:
            for key in clean_keys:
                if self._entries.pop(key, None) is not None:
                    removed += 1
            if clean_keys:
                self._invalidations += 1

        if clean_keys:
            logger.info(
                "Dashboard cache invalidated keys=%s removed=%s",
                sorted(clean_keys),
                removed,
            )
        return removed

    def invalidate_all(self) -> int:
        """Clear every cached entry."""

        with self._entries_lock:
            removed = len(self._entries)
            self._entries.clear()
            self._invalidations += 1
        logger.info("Dashboard cache cleared removed=%s", removed)
        return removed

    def health(self) -> dict[str, Any]:
        """Return operational cache metrics suitable for /health."""

        with self._entries_lock:
            entries = {
                key: {
                    "age_seconds": round(self._entry_age(entry), 3),
                    "refreshed_at": self._iso(entry.stored_at),
                    "fresh": self._entry_age(entry) < self.ttl_seconds,
                }
                for key, entry in self._entries.items()
            }
            return {
                "enabled": self.enabled,
                "ttl_seconds": self.ttl_seconds,
                "stale_seconds": self.stale_seconds,
                "entry_count": len(entries),
                "entries": entries,
                "hits": self._hits,
                "misses": self._misses,
                "refreshes": self._refreshes,
                "stale_fallbacks": self._stale_fallbacks,
                "invalidations": self._invalidations,
                "last_error": self._last_error or None,
            }
