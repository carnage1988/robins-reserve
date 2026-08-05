"""Squarespace Commerce Orders API client for RobinHub."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class SquarespaceAPIError(RuntimeError):
    """Raised when Squarespace cannot be queried successfully."""


class SquarespaceService:
    """Read paid orders from the Squarespace Commerce Orders API."""

    BASE_URL = "https://api.squarespace.com/1.0/commerce/orders"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        user_agent: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.api_key = (
            api_key
            or os.getenv("SQUARESPACE_API_KEY", "")
        ).strip()

        if not self.api_key:
            raise ValueError(
                "SQUARESPACE_API_KEY is not configured."
            )

        self.user_agent = (
            user_agent
            or os.getenv(
                "SQUARESPACE_USER_AGENT",
                "RobinHub/1.4 Squarespace RobinCon Importer",
            )
        ).strip()

        if not self.user_agent:
            raise ValueError(
                "Squarespace requires a non-empty User-Agent."
            )

        self.timeout_seconds = max(1, int(timeout_seconds))

    @staticmethod
    def _format_utc(value: datetime) -> str:
        """Return a Squarespace-compatible UTC timestamp."""

        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    @classmethod
    def iso_utc_days_ago(cls, days: int) -> str:
        """Return the UTC timestamp for a number of days ago."""

        value = datetime.now(timezone.utc) - timedelta(
            days=max(0, int(days))
        )
        return cls._format_utc(value)

    @classmethod
    def iso_utc_now(cls) -> str:
        """Return the current UTC timestamp."""

        return cls._format_utc(
            datetime.now(timezone.utc)
        )

    def _request_json(
        self,
        url: str,
    ) -> dict[str, Any]:
        """Perform one authenticated Squarespace GET request."""

        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
            method="GET",
        )

        try:
            with urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                payload = response.read().decode("utf-8")

        except HTTPError as exc:
            body = exc.read().decode(
                "utf-8",
                errors="replace",
            )

            raise SquarespaceAPIError(
                f"Squarespace returned HTTP {exc.code}: "
                f"{body[:1000]}"
            ) from exc

        except URLError as exc:
            raise SquarespaceAPIError(
                "Squarespace could not be reached: "
                f"{exc.reason}"
            ) from exc

        try:
            decoded = json.loads(payload)

        except json.JSONDecodeError as exc:
            raise SquarespaceAPIError(
                "Squarespace returned invalid JSON."
            ) from exc

        if not isinstance(decoded, dict):
            raise SquarespaceAPIError(
                "Squarespace returned an unexpected response."
            )

        return decoded

    def list_paid_orders(
        self,
        *,
        modified_after: str | None = None,
        modified_before: str | None = None,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Retrieve paid Squarespace orders.

        When a modification range is supplied, Squarespace requires both
        modifiedAfter and modifiedBefore. Cursor requests must contain only
        the cursor and cannot include the original filters.
        """

        if bool(modified_after) != bool(modified_before):
            raise ValueError(
                "modified_after and modified_before must "
                "both be supplied together."
            )

        orders: list[dict[str, Any]] = []
        cursor: str | None = None

        for _ in range(max(1, int(max_pages))):
            if cursor:
                parameters: dict[str, str] = {
                    "cursor": cursor,
                }
            else:
                parameters = {
                    "paymentStates": "PAID",
                }

                if modified_after and modified_before:
                    parameters["modifiedAfter"] = (
                        modified_after
                    )
                    parameters["modifiedBefore"] = (
                        modified_before
                    )

            url = (
                f"{self.BASE_URL}?"
                f"{urlencode(parameters)}"
            )

            response = self._request_json(url)

            page_orders = response.get("result", [])

            if not isinstance(page_orders, list):
                raise SquarespaceAPIError(
                    "Squarespace order result was not a list."
                )

            orders.extend(
                order
                for order in page_orders
                if isinstance(order, dict)
            )

            pagination = response.get("pagination", {})

            if not isinstance(pagination, dict):
                break

            if not pagination.get("hasNextPage"):
                break

            cursor = str(
                pagination.get(
                    "nextPageCursor",
                    "",
                )
            ).strip()

            if not cursor:
                break

        return orders

    def list_recent_paid_orders(
        self,
        *,
        days: int = 30,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        """Retrieve paid orders modified during the recent date range."""

        return self.list_paid_orders(
            modified_after=self.iso_utc_days_ago(days),
            modified_before=self.iso_utc_now(),
            max_pages=max_pages,
        )

    def get_order(
        self,
        order_id: str,
    ) -> dict[str, Any]:
        """Retrieve one Squarespace order by its internal order ID."""

        clean_order_id = str(order_id or "").strip()

        if not clean_order_id:
            raise ValueError(
                "A Squarespace order ID is required."
            )

        return self._request_json(
            f"{self.BASE_URL}/{clean_order_id}"
        )
