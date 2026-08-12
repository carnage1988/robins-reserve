"""HTTP API for the RobinsReserve Budibase staff portal.

This deliberately reuses the existing SheetsService and LeagueService so the
Discord bot and dashboard read and modify the same RobinsReserve data.
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, parse_qs
from collections import defaultdict
from datetime import datetime
from typing import Any, Literal, Callable, TypeVar

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.responses import HTMLResponse
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, Field
import requests

from services.sheets_resilience import install_gspread_resilience
from services.league_service import LeagueService
from services.sheets_service import SheetsService
from services.robincon_service import RobinConService
from services.robincon_staff_service import RobinConStaffService
from services.dashboard_cache import DashboardCache

import uuid

from sqlalchemy import select

from models import (
    Customer,
    LeagueAttendance,
    LeagueSession,
    LeagueTemplate,
    Payment,
    Store,
    Tenant,
    User,
)
from services.database import AsyncSessionLocal
from services.payment_service import PaymentService
from services.league_db_service import LeagueDatabaseService

import logging

logger = logging.getLogger(__name__)

install_gspread_resilience()


APP_NAME = "Robins Reserve Operations API"
APP_VERSION = "1.4.2-dev"
LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD", "5"))
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "").strip()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
STAFF_CHANNEL_ID = os.getenv("STAFF_CHANNEL_ID", "").strip()
LEAGUE_CHANNEL_ID = os.getenv("LEAGUE_CHANNEL_ID", "").strip()
LEAGUE_EVENT_DURATION_HOURS = int(os.getenv("LEAGUE_EVENT_DURATION_HOURS", "4"))

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_REDIRECT_URI = os.getenv(
    "DISCORD_REDIRECT_URI",
    "http://localhost:10000/auth/discord/callback",
).strip()
LEAGUE_DISCORD_REDIRECT_URI = os.getenv(
    "LEAGUE_DISCORD_REDIRECT_URI",
    "http://localhost:10000/league/auth/discord/callback",
).strip()
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", os.getenv("LEAGUE_GUILD_ID", "")).strip()
DISCORD_STAFF_ROLE_IDS = {
    role.strip()
    for role in os.getenv("DISCORD_STAFF_ROLE_IDS", os.getenv("STAFF_ROLE_ID", "")).split(",")
    if role.strip()
}
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-this-session-secret").strip()
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "false").strip().lower() in {"1", "true", "yes"}
SESSION_SAME_SITE = os.getenv("SESSION_SAME_SITE", "lax").strip().lower()
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "robins_staff_session").strip()
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:10000").rstrip("/")
BOT_HEARTBEAT_FILE = Path(os.getenv("BOT_HEARTBEAT_FILE", "/app/data/discord_bot_heartbeat.json"))
BOT_HEARTBEAT_MAX_AGE = int(os.getenv("BOT_HEARTBEAT_MAX_AGE", "120"))

DASHBOARD_CACHE_ENABLED = os.getenv(
    "DASHBOARD_CACHE_ENABLED",
    "true",
).strip().lower() in {"1", "true", "yes", "on"}
DASHBOARD_CACHE_TTL_SECONDS = int(
    os.getenv("DASHBOARD_CACHE_TTL_SECONDS", "60")
)
DASHBOARD_CACHE_STALE_SECONDS = int(
    os.getenv("DASHBOARD_CACHE_STALE_SECONDS", "300")
)
INTERNAL_CACHE_TOKEN = os.getenv("INTERNAL_CACHE_TOKEN", "").strip()

dashboard_cache = DashboardCache(
    enabled=DASHBOARD_CACHE_ENABLED,
    ttl_seconds=DASHBOARD_CACHE_TTL_SECONDS,
    stale_seconds=DASHBOARD_CACHE_STALE_SECONDS,
)

EVENT_CACHE_MAP: dict[str, tuple[str, ...]] = {
    "preorder.awaiting_approval": ("dashboard", "service_health"),
    "preorder.approved": ("dashboard", "service_health"),
    "preorder.declined": ("dashboard", "service_health"),
    "preorder.cancelled": ("dashboard", "service_health"),
    "preorder.collected": ("dashboard", "service_health"),
    "league.started": ("dashboard", "league_status", "service_health"),
    "league.ended": ("dashboard", "league_status", "service_health"),
    "league.checkin": ("dashboard", "league_status"),
    "robincon.updated": ("dashboard", "service_health"),
    "squarespace.imported": ("dashboard", "service_health"),
}

T = TypeVar("T")


def _cached_value(key: str, loader: Callable[[], T]) -> T:
    """Return only the cached payload, preserving existing API response shapes."""

    result = dashboard_cache.get_or_load(key, loader)
    logger.debug(
        "Dashboard cache key=%s status=%s age_seconds=%s",
        key,
        result.cache_status,
        result.age_seconds,
    )
    return result.value


def _invalidate_for_event(event: str) -> int:
    keys = EVENT_CACHE_MAP.get(event, ("dashboard",))
    removed = dashboard_cache.invalidate(*keys)
    logger.info(
        "Internal event %s invalidated cache keys=%s removed=%s",
        event,
        keys,
        removed,
    )
    return removed


def _cors_origins() -> list[str]:
    raw = os.getenv(
        "DASHBOARD_CORS_ORIGINS",
        "http://localhost:10000,http://127.0.0.1:10000",
    )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _allowed_hosts() -> list[str]:
    raw = os.getenv("DASHBOARD_ALLOWED_HOSTS", "localhost,127.0.0.1")
    return [host.strip() for host in raw.split(",") if host.strip()]


def _request_origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin", "").rstrip("/")
    return not origin or origin in {item.rstrip("/") for item in _cors_origins()}


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="Live staff-dashboard API backed by RobinsReserve Google Sheets.",
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts())


@app.middleware("http")
async def protect_staff_api(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _request_origin_allowed(request):
        return JSONResponse(status_code=403, content={"detail": "Untrusted request origin."})
    if request.url.path.startswith("/api/") and not request.session.get("staff_user"):
        return JSONResponse(status_code=401, content={"detail": "Discord login required."})

    response = await call_next(request)

    if response.status_code < 400 and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        path = request.url.path
        if path.startswith("/api/reservations/"):
            action = path.rsplit("/", 1)[-1]
            event = {
                "approve": "preorder.approved",
                "decline": "preorder.declined",
                "reject": "preorder.declined",
                "cancel": "preorder.cancelled",
                "collect": "preorder.collected",
            }.get(action)
            if event:
                _invalidate_for_event(event)
        elif path == "/api/league/start":
            _invalidate_for_event("league.started")
        elif path == "/api/league/end":
            _invalidate_for_event("league.ended")
        elif path.startswith("/api/robincon/"):
            _invalidate_for_event("robincon.updated")

    return response

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie=SESSION_COOKIE_NAME,
    max_age=60 * 60 * 12,
    same_site=SESSION_SAME_SITE,
    https_only=SESSION_HTTPS_ONLY,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "X-Internal-Token"],
)


sheets: SheetsService | None = None
league: LeagueService | None = None
robincon: RobinConService | None = None
robincon_staff: RobinConStaffService | None = None
startup_error: str | None = None
robincon_startup_error: str | None = None


@app.on_event("startup")
def initialise_services() -> None:
    global sheets, league, robincon, robincon_staff
    global startup_error, robincon_startup_error
    try:
        sheets = SheetsService()
        league = LeagueService(sheets)
        startup_error = None
    except Exception as exc:  # surfaced through /health
        sheets = None
        league = None
        startup_error = f"{type(exc).__name__}: {exc}"

    try:
        robincon = RobinConService()
        robincon_staff = RobinConStaffService(robincon)
        robincon_startup_error = None
    except Exception as exc:
        robincon = None
        robincon_staff = None
        robincon_startup_error = f"{type(exc).__name__}: {exc}"


def require_services() -> tuple[SheetsService, LeagueService]:
    if sheets is None or league is None:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "RobinsReserve data services are unavailable.",
                "error": startup_error,
            },
        )
    return sheets, league


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Protect write operations when DASHBOARD_API_KEY is configured."""
    if DASHBOARD_API_KEY and x_api_key != DASHBOARD_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def _money(value: Any) -> float | None:
    text = str(value or "").strip().replace("£", "").replace(",", "")
    if not text:
        return None
    try:
        return round(float(text), 2)
    except (TypeError, ValueError):
        return None


def _currency(value: Any) -> str:
    number = _money(value)
    return f"£{number:.2f}" if number is not None else "Price unavailable"


def _priced_items_text(order: dict[str, Any]) -> str:
    lines: list[str] = []
    for item in order.get("items", []):
        quantity = int(item.get("quantity", 0) or 0)
        name = _clean(item.get("product_name")) or "Unknown product"
        unit_price = item.get("unit_price")
        subtotal = item.get("subtotal")
        if _money(unit_price) is None or _money(subtotal) is None:
            lines.append(f"• **{quantity} × {name}** — Price unavailable")
        else:
            lines.append(
                f"• **{quantity} × {name}** — {_currency(unit_price)} each — "
                f"**{_currency(subtotal)}**"
            )
    return "\n".join(lines)


def _discord_api(method: str, path: str, *, json_body: Any | None = None) -> requests.Response:
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is unavailable to the dashboard API.")
    response = requests.request(
        method,
        f"https://discord.com/api/v10{path}",
        headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"},
        json=json_body,
        timeout=15,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Discord API {method} {path} failed with HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )
    return response


def _discord_react(message_id: str, emoji: str) -> None:
    from urllib.parse import quote
    if not STAFF_CHANNEL_ID or not str(message_id).isdigit():
        raise RuntimeError("The original Discord approval message could not be identified.")
    _discord_api(
        "PUT",
        f"/channels/{STAFF_CHANNEL_ID}/messages/{message_id}/reactions/{quote(emoji)}/@me",
    )


def _discord_dm(user_id: str, content: str) -> None:
    if not str(user_id).isdigit():
        raise RuntimeError("The reservation does not contain a valid Discord user ID.")
    channel = _discord_api("POST", "/users/@me/channels", json_body={"recipient_id": str(user_id)}).json()
    _discord_api("POST", f"/channels/{channel['id']}/messages", json_body={"content": content})


def _discord_reply(message_id: str, content: str) -> None:
    if not STAFF_CHANNEL_ID or not str(message_id).isdigit():
        return
    _discord_api(
        "POST",
        f"/channels/{STAFF_CHANNEL_ID}/messages",
        json_body={
            "content": content,
            "message_reference": {"message_id": str(message_id)},
            "allowed_mentions": {"replied_user": False},
        },
    )




def _discord_channel_message(channel_id: str, content: str) -> None:
    if not str(channel_id).isdigit():
        raise RuntimeError("The configured Discord channel ID is invalid.")
    _discord_api("POST", f"/channels/{channel_id}/messages", json_body={"content": content})


def _notify_league_started(event: dict[str, Any], staff_name: str) -> list[str]:
    warnings: list[str] = []
    try:
        _discord_channel_message(
            LEAGUE_CHANNEL_ID,
            "**League event started.**\n\n"
            f"**Event ID:** `{event.get('event_id', 'Unknown')}`\n"
            f"**Store Code:** `{event.get('store_code', 'Unknown')}`\n\n"
            f"This event expires in {LEAGUE_EVENT_DURATION_HOURS} hours.\n"
            f"Started from the Operations Portal by **{staff_name}**.",
        )
    except Exception as exc:
        logger.exception("League started but Discord announcement failed")
        warnings.append(str(exc))
    return warnings


def _notify_league_ended(event: dict[str, Any], staff_name: str) -> list[str]:
    warnings: list[str] = []
    try:
        _discord_channel_message(
            LEAGUE_CHANNEL_ID,
            "**League event ended.**\n\n"
            f"**Event ID:** `{event.get('Event ID', event.get('event_id', 'Unknown'))}`\n\n"
            "Players can no longer check in.\n"
            f"Ended from the Operations Portal by **{staff_name}**.",
        )
    except Exception as exc:
        logger.exception("League ended but Discord announcement failed")
        warnings.append(str(exc))
    return warnings


def _notify_dashboard_collection(order: dict[str, Any], staff_name: str) -> list[str]:
    warnings: list[str] = []
    try:
        _discord_dm(
            _clean(order.get("discord_user_id")),
            "✅ **Your Robin's Reserve preorder has been collected.**\n\n"
            f"{_priced_items_text(order)}\n\n"
            f"💷 Basket Total: **{_currency(order.get('total_value'))}**\n\n"
            f"Collected by: **{staff_name}**",
        )
    except Exception as exc:
        logger.exception("Dashboard collection succeeded but customer DM failed")
        warnings.append(str(exc))
    return warnings

def _remove_pending_request(message_id: str) -> None:
    """Remove a completed approval from the shared bot persistence file."""

    if not str(message_id).isdigit():
        return
    path = Path(os.getenv("PENDING_REQUESTS_FILE", "/app/data/pending_requests.json"))
    try:
        if not path.exists():
            return
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or str(message_id) not in data:
            return
        data.pop(str(message_id), None)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(path)
    except (OSError, ValueError, TypeError):
        logger.exception("Could not remove completed dashboard approval from pending requests")


def _discord_set_decision_reaction(message_id: str, emoji: str) -> None:
    """Replace the initial choice reactions with the dashboard decision."""

    if not STAFF_CHANNEL_ID or not str(message_id).isdigit():
        raise RuntimeError("The original Discord approval message could not be identified.")
    _discord_api(
        "DELETE",
        f"/channels/{STAFF_CHANNEL_ID}/messages/{message_id}/reactions",
    )
    _discord_react(message_id, emoji)


def _notify_dashboard_approval(order: dict[str, Any], staff_name: str) -> list[str]:
    warnings: list[str] = []
    message_id = _clean(order.get("approval_message_id"))
    try:
        _discord_set_decision_reaction(message_id, "👍")
        _discord_reply(
            message_id,
            f"✅ **Preorder approved from the Operations Portal**\n"
            f"Pickup PIN: `{order['pickup_pin']}`\nApproved by: **{staff_name}**",
        )
    except Exception as exc:
        logger.exception("Dashboard approval succeeded but Discord message update failed")
        warnings.append(str(exc))
    try:
        _discord_dm(
            _clean(order.get("discord_user_id")),
            "✅ **Robin's Reserve Preorder Approved**\n\n"
            f"{_priced_items_text(order)}\n\n"
            f"💷 Basket Total: **{_currency(order.get('total_value'))}**\n\n"
            f"🔐 Pickup PIN: **{order['pickup_pin']}**\n\n"
            "Please show this PIN when collecting the order.",
        )
    except Exception as exc:
        logger.exception("Dashboard approval succeeded but customer DM failed")
        warnings.append(str(exc))
    return warnings


def _notify_dashboard_decline(order: dict[str, Any], staff_name: str, reason: str) -> list[str]:
    warnings: list[str] = []
    message_id = _clean(order.get("approval_message_id"))
    try:
        _discord_set_decision_reaction(message_id, "👎")
        _discord_reply(
            message_id,
            f"❌ **Preorder declined from the Operations Portal**\n"
            f"Reason: {reason}\nDeclined by: **{staff_name}**",
        )
    except Exception as exc:
        logger.exception("Dashboard decline succeeded but Discord message update failed")
        warnings.append(str(exc))
    try:
        _discord_dm(
            _clean(order.get("discord_user_id")),
            "❌ **Robin's Reserve Preorder Rejected**\n\n"
            f"{_priced_items_text(order)}\n\n"
            f"Reason: **{reason}**\n\n"
            "The reserved stock has been returned to the preorder allocation. "
            "No pickup PIN has been issued.",
        )
    except Exception as exc:
        logger.exception("Dashboard decline succeeded but customer DM failed")
        warnings.append(str(exc))
    return warnings


class ActionRequest(BaseModel):
    reason: str = Field(default="", max_length=500)

class LeagueManualCheckInRequest(BaseModel):
    discord_user_id: str = Field(
        min_length=1,
        max_length=30,
    )

def _oauth_configured() -> bool:
    return all(
        [
            DISCORD_CLIENT_ID,
            DISCORD_CLIENT_SECRET,
            DISCORD_REDIRECT_URI,
            DISCORD_GUILD_ID,
            DISCORD_STAFF_ROLE_IDS,
            SESSION_SECRET != "change-this-session-secret",
        ]
    )


def require_staff(request: Request) -> dict[str, Any]:
    user = request.session.get("staff_user")
    if not user:
        raise HTTPException(status_code=401, detail="Discord login required.")
    return user


def _actor(staff: dict[str, Any]) -> str:
    display = staff.get("display_name") or staff.get("username") or "Discord staff"
    return f"{display} (Discord ID: {staff.get('id', 'unknown')})"

async def _resolve_db_staff_user(
    session,
    staff: dict[str, Any],
) -> User:
    discord_user_id = str(staff.get("id", "")).strip()

    if not discord_user_id:
        raise HTTPException(
            status_code=401,
            detail="Discord staff identity is unavailable.",
        )

    user = (
        await session.execute(
            select(User).where(
                User.discord_user_id == discord_user_id
            )
        )
    ).scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "Your Discord account is not linked to a "
                "RobinHub staff user."
            ),
        )

    if not user.active:
        raise HTTPException(
            status_code=403,
            detail="Your RobinHub staff account is inactive.",
        )

    return user

def _discord_avatar_url(user: dict[str, Any]) -> str:
    avatar = user.get("avatar")
    user_id = user.get("id")
    if avatar and user_id:
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png?size=128"
    return ""


def _bot_health() -> dict[str, Any]:
    try:
        age = max(0.0, time.time() - BOT_HEARTBEAT_FILE.stat().st_mtime)
        return {
            "connected": age <= BOT_HEARTBEAT_MAX_AGE,
            "age_seconds": round(age, 1),
        }
    except OSError:
        return {"connected": False, "age_seconds": None}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalise_item(record: dict[str, Any], sheet_name: str) -> dict[str, Any]:
    quantity_raw = record.get("Quantity", 0)
    try:
        quantity = int(quantity_raw or 0)
    except (TypeError, ValueError):
        quantity = 0

    unit_price_raw = record.get("Unit Price", record.get("Price", ""))
    subtotal_raw = record.get("Subtotal", "")

    return {
        "sheet_name": sheet_name,
        "timestamp": _clean(record.get("Timestamp")),
        "discord_username": _clean(record.get("Discord Username")),
        "discord_user_id": _clean(record.get("Discord User ID")),
        "product_id": _clean(record.get("Product ID")),
        "product_name": _clean(record.get("Product Name")),
        "quantity": quantity,
        "status": _clean(record.get("Status")),
        "approved_by": _clean(record.get("Approved By")),
        "pickup_pin": _clean(record.get("Pickup PIN")),
        "unit_price": _money(unit_price_raw),
        "subtotal": _money(subtotal_raw),
        "collected_at": _clean(record.get("Collected At")),
        "collected_by": _clean(record.get("Collected By")),
        "cancelled_at": _clean(record.get("Cancelled At")),
        "cancelled_by": _clean(record.get("Cancelled By")),
        "cancellation_reason": _clean(record.get("Cancellation Reason")),
        "rejected_at": _clean(record.get("Rejected At")),
        "rejected_by": _clean(record.get("Rejected By")),
        "rejection_reason": _clean(record.get("Rejection Reason")),
    }


def _group_reservations(
    service: SheetsService,
    scope: Literal["active", "all"] = "active",
) -> list[dict[str, Any]]:
    worksheet_pairs = [(service.preorders_sheet, "Preorders")]
    if scope == "all":
        worksheet_pairs.extend(
            [
                (service.collected_sheet, "Collected"),
                (service.cancelled_sheet, "Cancelled"),
                (service.rejected_sheet, "Rejected"),
            ]
        )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for worksheet, sheet_name in worksheet_pairs:
        for record in worksheet.get_all_records():
            item = _normalise_item(record, sheet_name)
            if item["pickup_pin"]:
                grouped[item["pickup_pin"]].append(item)

    reservations: list[dict[str, Any]] = []
    for pin, items in grouped.items():
        first = items[0]
        total_value = 0.0
        has_total_value = False
        for item in items:
            raw = item.get("subtotal")
            try:
                total_value += float(str(raw).replace("£", "").replace(",", ""))
                has_total_value = True
            except (TypeError, ValueError):
                pass

        reservations.append(
            {
                "pickup_pin": pin,
                "customer": first["discord_username"],
                "discord_username": first["discord_username"],
                "discord_user_id": first["discord_user_id"],
                "status": first["status"],
                "sheet_name": first["sheet_name"],
                "timestamp": first["timestamp"],
                "approved_by": first["approved_by"],
                "collected_at": first["collected_at"],
                "collected_by": first["collected_by"],
                "cancelled_at": first["cancelled_at"],
                "cancelled_by": first["cancelled_by"],
                "cancellation_reason": first["cancellation_reason"],
                "rejected_at": first["rejected_at"],
                "rejected_by": first["rejected_by"],
                "rejection_reason": first["rejection_reason"],
                "total_items": sum(item["quantity"] for item in items),
                "line_count": len(items),
                "total_value": round(total_value, 2) if has_total_value else None,
                "items": items,
            }
        )

    def sort_key(order: dict[str, Any]) -> str:
        return order.get("timestamp") or ""

    return sorted(reservations, key=sort_key, reverse=True)


class InternalEventRequest(BaseModel):
    event: str = Field(min_length=1, max_length=100)


@app.post("/internal/event")
def internal_event(
    payload: InternalEventRequest,
    x_internal_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Invalidate dashboard cache entries after an application write."""

    if not INTERNAL_CACHE_TOKEN or not secrets.compare_digest(
        x_internal_token or "",
        INTERNAL_CACHE_TOKEN,
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid internal event token.",
        )

    removed = _invalidate_for_event(payload.event)
    return {
        "accepted": True,
        "event": payload.event,
        "invalidated_entries": removed,
    }


@app.get("/auth/discord/login")
def discord_login(request: Request) -> RedirectResponse:
    if not _oauth_configured():
        return RedirectResponse(f"{FRONTEND_URL}/?auth_error=Discord+OAuth+is+not+configured")

    oauth_state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = oauth_state
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds.members.read",
        "state": oauth_state,
    }
    return RedirectResponse(
        "https://discord.com/oauth2/authorize?" + urlencode(params)
    )


@app.get("/auth/discord/callback")
def discord_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    expected_state = request.session.pop("oauth_state", None)
    if not code or not state or state != expected_state:
        return RedirectResponse(f"{FRONTEND_URL}/?auth_error=Invalid+Discord+login+state")

    try:
        token_response = requests.post(
            "https://discord.com/api/v10/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        user_response = requests.get(
            "https://discord.com/api/v10/users/@me", headers=headers, timeout=15
        )
        user_response.raise_for_status()
        user = user_response.json()

        member_response = requests.get(
            f"https://discord.com/api/v10/users/@me/guilds/{DISCORD_GUILD_ID}/member",
            headers=headers,
            timeout=15,
        )
        member_response.raise_for_status()
        member = member_response.json()
    except requests.HTTPError as exc:
        response = exc.response

        logger.error(
            "Discord OAuth HTTP error: status=%s url=%s body=%s",
            response.status_code if response is not None else "unknown",
            response.url if response is not None else "unknown",
            response.text[:1000] if response is not None else str(exc),
        )

        request.session.clear()

        return RedirectResponse(
            f"{FRONTEND_URL}/?auth_error=Discord+login+failed"
        )

    except (requests.RequestException, KeyError, ValueError):
        logger.exception("Unexpected Discord OAuth error")
        request.session.clear()

        return RedirectResponse(
            f"{FRONTEND_URL}/?auth_error=Discord+login+failed"
        )

    member_roles = {str(role_id) for role_id in member.get("roles", [])}
    if not member_roles.intersection(DISCORD_STAFF_ROLE_IDS):
        request.session.clear()
        return RedirectResponse(f"{FRONTEND_URL}/?auth_error=You+do+not+have+a+staff+role")

    request.session["staff_user"] = {
        "id": str(user.get("id", "")),
        "username": user.get("username", "Discord user"),
        "display_name": member.get("nick") or user.get("global_name") or user.get("username"),
        "avatar_url": _discord_avatar_url(user),
        "roles": sorted(member_roles),
    }
    return RedirectResponse(FRONTEND_URL + "/")

@app.get("/league/auth/discord/login")
def league_discord_login(request: Request) -> RedirectResponse:
    """Begin Discord OAuth for a League customer."""

    if not all(
        [
            DISCORD_CLIENT_ID,
            DISCORD_CLIENT_SECRET,
            LEAGUE_DISCORD_REDIRECT_URI,
            DISCORD_GUILD_ID,
        ]
    ):
        return RedirectResponse(
            "/league/checkin?error=Discord+login+is+not+configured"
        )

    oauth_state = secrets.token_urlsafe(32)

    request.session["league_oauth_state"] = oauth_state

    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": LEAGUE_DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds.members.read",
        "state": oauth_state,
    }

    return RedirectResponse(
        "https://discord.com/oauth2/authorize?"
        + urlencode(params)
    )


@app.get("/league/auth/discord/callback")
def league_discord_callback(
    request: Request,
    code: str = "",
    state: str = "",
) -> RedirectResponse:
    """Complete Discord OAuth for a League customer."""

    expected_state = request.session.pop(
        "league_oauth_state",
        None,
    )

    if (
        not code
        or not state
        or state != expected_state
    ):
        return RedirectResponse(
            "/league/checkin?error=Invalid+Discord+login+state"
        )

    try:
        token_response = requests.post(
            "https://discord.com/api/v10/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": LEAGUE_DISCORD_REDIRECT_URI,
            },
            headers={
                "Content-Type":
                    "application/x-www-form-urlencoded"
            },
            timeout=15,
        )

        token_response.raise_for_status()

        access_token = token_response.json()[
            "access_token"
        ]

        headers = {
            "Authorization": f"Bearer {access_token}"
        }

        user_response = requests.get(
            "https://discord.com/api/v10/users/@me",
            headers=headers,
            timeout=15,
        )

        user_response.raise_for_status()
        user = user_response.json()

        member_response = requests.get(
            (
                "https://discord.com/api/v10/users/@me/"
                f"guilds/{DISCORD_GUILD_ID}/member"
            ),
            headers=headers,
            timeout=15,
        )

        member_response.raise_for_status()
        member = member_response.json()

    except requests.HTTPError as exc:
        response = exc.response

        logger.error(
            (
                "League Discord OAuth HTTP error: "
                "status=%s body=%s"
            ),
            (
                response.status_code
                if response is not None
                else "unknown"
            ),
            (
                response.text[:1000]
                if response is not None
                else str(exc)
            ),
        )

        request.session.pop(
            "league_user",
            None,
        )

        return RedirectResponse(
            "/league/checkin?error=Discord+login+failed"
        )

    except (
        requests.RequestException,
        KeyError,
        ValueError,
    ):
        logger.exception(
            "Unexpected League Discord OAuth error"
        )

        request.session.pop(
            "league_user",
            None,
        )

        return RedirectResponse(
            "/league/checkin?error=Discord+login+failed"
        )

    request.session["league_user"] = {
        "id": str(user.get("id", "")),
        "username": user.get(
            "username",
            "Discord user",
        ),
        "display_name": (
            member.get("nick")
            or user.get("global_name")
            or user.get("username")
            or "Discord user"
        ),
        "avatar_url": _discord_avatar_url(user),
    }

    return RedirectResponse(
        "/league/checkin"
    )

@app.get("/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    user = request.session.get("staff_user")
    if not user:
        raise HTTPException(status_code=401, detail="Discord login required.")
    return {"authenticated": True, "user": user}


@app.post("/auth/logout")
def auth_logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"logged_out": True}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    if sheets is None:
        return {
            "status": "unhealthy",
            "google_sheets": False,
            "error": startup_error,
        }
    try:
        connection = sheets.connection_status()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Google Sheets health check failed: {exc}",
        ) from exc
    bot_health = _bot_health()
    return {
        "status": "healthy",
        "api": True,
        "google_sheets": True,
        "discord_bot": bot_health["connected"],
        "discord_bot_heartbeat_age_seconds": bot_health["age_seconds"],
        "spreadsheet": connection,
        "dashboard_cache": dashboard_cache.health(),
    }


def _load_service_health() -> dict[str, Any]:
    sheets_ok = False
    sheets_message = "Connection unavailable"
    if sheets is not None:
        try:
            connection = sheets.connection_status()
            sheets_ok = bool(connection.get("connected", True)) if isinstance(connection, dict) else True
            sheets_message = "Live operational data source"
        except Exception as exc:
            sheets_message = f"Connection unavailable: {type(exc).__name__}"
    bot = _bot_health()
    league_ok = league is not None
    return {
        "api": {"connected": True, "message": "Connected and responding"},
        "google_sheets": {"connected": sheets_ok, "message": sheets_message},
        "discord_bot": {
            "connected": bot["connected"],
            "message": "Shared reservation workflow" if bot["connected"] else "Heartbeat unavailable",
            "heartbeat_age_seconds": bot["age_seconds"],
        },
        "pokemon_league": {
            "connected": league_ok,
            "message": "League service loaded" if league_ok else "League service unavailable",
        },
        "robincon": {
            "connected": robincon is not None,
            "message": "RobinCon workbook loaded" if robincon is not None else "RobinCon unavailable",
        },
    }


@app.get("/api/service-health")
def service_health() -> dict[str, Any]:
    return _cached_value("service_health", _load_service_health)


def _load_dashboard() -> dict[str, Any]:
    service, league_service = require_services()
    reservations = _group_reservations(service, "active")
    all_reservations = _group_reservations(service, "all")
    products = service.get_products(open_only=False)
    league_status = league_service.get_league_status()

    pending = sum(
        1 for order in reservations if order["status"].casefold() == "pending"
    )
    approved = sum(
        1 for order in reservations if order["status"].casefold() == "approved"
    )
    low_stock = sum(
        1 for product in products if int(product.get("stock", 0)) <= LOW_STOCK_THRESHOLD
    )
    today = datetime.now().astimezone().date()

    def occurred_today(value: Any) -> bool:
        text = _clean(value)
        if not text:
            return False
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone().date() == today
        except ValueError:
            return text[:10] == today.isoformat()

    orders_today = sum(1 for order in all_reservations if occurred_today(order.get("timestamp")))
    collections_today = sum(1 for order in all_reservations if occurred_today(order.get("collected_at")))

    return {
        "reservations_waiting": pending,
        "ready_for_collection": approved,
        "league_running": league_status.get("active_event") is not None,
        "league_attendance": league_status.get("attendance_count", 0),
        "low_stock_products": low_stock,
        "low_stock_threshold": LOW_STOCK_THRESHOLD,
        "orders_today": orders_today,
        "collections_today": collections_today,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "league": league_status,
        "pending_orders": [
            order for order in reservations if order["status"].casefold() == "pending"
        ][:12],
        "recent_activity": all_reservations[:12],
    }


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    return _cached_value("dashboard", _load_dashboard)


@app.get("/api/reservations")
def reservations(
    search: str = Query(default="", max_length=100),
    status: str = Query(default="", max_length=30),
    scope: Literal["active", "all"] = "active",
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    service, _ = require_services()
    orders = _group_reservations(service, scope)

    search_term = search.strip().casefold()
    status_term = status.strip().casefold()

    if search_term:
        orders = [
            order
            for order in orders
            if search_term in order["pickup_pin"].casefold()
            or search_term in order["discord_username"].casefold()
            or search_term in order["discord_user_id"].casefold()
            or any(
                search_term in item["product_name"].casefold()
                for item in order["items"]
            )
        ]

    if status_term:
        orders = [
            order for order in orders if order["status"].casefold() == status_term
        ]

    return orders[:limit]


@app.get("/api/reservations/{pickup_pin}")
def reservation_detail(pickup_pin: str) -> dict[str, Any]:
    service, _ = require_services()
    order = service.lookup_by_pin(pickup_pin)
    if order is None:
        raise HTTPException(status_code=404, detail="Reservation not found.")
    return order


@app.post("/api/reservations/{pickup_pin}/approve")
def approve_reservation(
    pickup_pin: str,
    request: ActionRequest,
    staff: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    del request
    service, _ = require_services()
    existing = service.lookup_by_pin(pickup_pin)
    if existing is None:
        raise HTTPException(status_code=404, detail="Reservation not found.")
    message_id = _clean(existing.get("approval_message_id"))
    if not message_id.isdigit():
        raise HTTPException(status_code=409, detail="The reservation has no valid Discord approval message ID.")
    try:
        processed = service.approve_reservation(
            pickup_pin=pickup_pin,
            approved_by=_actor(staff),
            approval_message_id=int(message_id),
        )
        warnings = _notify_dashboard_approval(processed, _actor(staff))
        _remove_pending_request(message_id)
        processed["notification_warnings"] = warnings
        return processed
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Dashboard preorder approval failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/reservations/{pickup_pin}/decline")
def decline_reservation(
    pickup_pin: str,
    request: ActionRequest,
    staff: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    service, _ = require_services()
    existing = service.lookup_by_pin(pickup_pin)
    if existing is None:
        raise HTTPException(status_code=404, detail="Reservation not found.")
    message_id = _clean(existing.get("approval_message_id"))
    if not message_id.isdigit():
        raise HTTPException(status_code=409, detail="The reservation has no valid Discord approval message ID.")
    reason = request.reason.strip() or "Staff declined reservation"
    try:
        processed = service.decline_reservation(
            pickup_pin=pickup_pin,
            declined_by=_actor(staff),
            approval_message_id=int(message_id),
            reason=reason,
        )
        # Archive rows retain the message ID and customer details.
        warnings = _notify_dashboard_decline(processed, _actor(staff), reason)
        _remove_pending_request(message_id)
        processed["notification_warnings"] = warnings
        return processed
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Dashboard preorder decline failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/reservations/{pickup_pin}/collect")
def collect_reservation(
    pickup_pin: str,
    request: ActionRequest,
    staff: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    del request
    service, _ = require_services()
    try:
        processed = service.collect_order(
            pickup_pin=pickup_pin,
            collected_by=_actor(staff),
        )
        processed["notification_warnings"] = _notify_dashboard_collection(
            processed, _actor(staff)
        )
        return processed
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/reservations/{pickup_pin}/cancel")
def cancel_reservation(
    pickup_pin: str,
    request: ActionRequest,
    staff: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    service, _ = require_services()
    try:
        return service.cancel_reservation(
            pickup_pin=pickup_pin,
            cancelled_by=_actor(staff),
            reason=request.reason.strip() or "Staff cancelled reservation",
            allowed_statuses={"pending", "approved"},
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/reservations/{pickup_pin}/reject")
def reject_reservation(
    pickup_pin: str,
    request: ActionRequest,
    staff: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    service, _ = require_services()
    try:
        return service.order_manager.archive(
            pickup_pin=pickup_pin,
            destination_sheet=service.rejected_sheet,
            destination_name="Rejected",
            final_status="Rejected",
            allowed_statuses={"pending", "approved"},
            actor_header="Rejected By",
            actor=_actor(staff),
            timestamp_header="Rejected At",
            reason_header="Rejection Reason",
            reason=request.reason.strip() or "Staff rejected reservation",
            restore_stock=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/products")
def products(
    open_only: bool = False,
    low_stock_only: bool = False,
) -> list[dict[str, Any]]:
    service, _ = require_services()
    rows = service.get_products(open_only=open_only)
    if low_stock_only:
        rows = [
            row for row in rows if int(row.get("stock", 0)) <= LOW_STOCK_THRESHOLD
        ]
    return rows

def _league_public_page(
    *,
    title: str,
    heading: str,
    body: str,
    status_class: str = "",
) -> str:
    return f"""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="theme-color" content="#0b0d12">

    <title>{title}</title>

    <style>
        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
            font-family:
                Inter,
                system-ui,
                -apple-system,
                BlinkMacSystemFont,
                "Segoe UI",
                sans-serif;
            background:
                radial-gradient(circle at top, #222936 0%, #11151d 45%, #090b10 100%);
            color: #f5f7fb;
        }}

        .league-shell {{
            width: 100%;
            max-width: 520px;
        }}

        .league-brand {{
            text-align: center;
            margin-bottom: 22px;
        }}

        .league-brand h1 {{
            margin: 0;
            font-size: 2rem;
            letter-spacing: -0.04em;
        }}

        .league-brand p {{
            margin: 8px 0 0;
            color: #9da7b8;
        }}

        .league-card {{
            background: rgba(20, 24, 33, 0.96);
            border: 1px solid #303848;
            border-radius: 20px;
            padding: 28px;
            box-shadow: 0 18px 50px rgba(0, 0, 0, 0.35);
        }}

        .status {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            border-radius: 999px;
            padding: 7px 12px;
            margin-bottom: 18px;
            font-size: 0.86rem;
            font-weight: 700;
        }}

        .status.open {{
            background: rgba(34, 197, 94, 0.12);
            color: #86efac;
            border: 1px solid rgba(34, 197, 94, 0.30);
        }}

        .status.closed {{
            background: rgba(239, 68, 68, 0.12);
            color: #fca5a5;
            border: 1px solid rgba(239, 68, 68, 0.30);
        }}

        .league-card h2 {{
            margin: 0 0 12px;
            font-size: 1.65rem;
        }}

        .league-card p {{
            color: #b6bfce;
            line-height: 1.55;
        }}

        .league-detail {{
            display: flex;
            justify-content: space-between;
            gap: 20px;
            padding: 14px 0;
            border-bottom: 1px solid #2c3442;
        }}

        .league-detail:last-of-type {{
            border-bottom: 0;
        }}

        .league-detail span {{
            color: #8f9aad;
        }}

        .league-detail strong {{
            text-align: right;
        }}

        .league-code-label {{
            display: block;
            margin-top: 22px;
            margin-bottom: 8px;
            color: #b6bfce;
            font-size: 0.92rem;
            font-weight: 700;
        }}

        .league-code-input {{
            width: 100%;
            padding: 14px 16px;
            border: 1px solid #3a4558;
            border-radius: 12px;
            background: #0f141d;
            color: #f5f7fb;
            font: inherit;
            font-size: 1.1rem;
            font-weight: 700;
            letter-spacing: 0.12em;
            text-align: center;
            text-transform: uppercase;
            outline: none;
        }}

        .league-code-input:focus {{
            border-color: #5865f2;
            box-shadow: 0 0 0 3px rgba(88, 101, 242, 0.18);
        }}

        .league-action {{
            display: block;
            width: 100%;
            margin-top: 24px;
            padding: 14px 18px;
            border: 0;
            border-radius: 12px;
            background: #5865f2;
            color: white;
            font-size: 1rem;
            font-weight: 700;
            text-align: center;
            text-decoration: none;
            cursor: pointer;
        }}

        .league-action:hover {{
            filter: brightness(1.08);
        }}

        .league-success {{
            color: #86efac;
        }}

        .league-warning {{
            color: #fcd34d;
        }}

        .league-footer {{
            margin-top: 18px;
            text-align: center;
            color: #707b8d;
            font-size: 0.82rem;
        }}

        @media (max-width: 520px) {{
            body {{
                padding: 16px;
            }}

            .league-card {{
                padding: 22px;
            }}
        }}
    </style>
</head>
<body>
    <div class="league-shell">
        <div class="league-brand">
            <h1>Robins Hobby Cafe</h1>
            <p>Pokémon League Check-In</p>
        </div>

        <main class="league-card">
            <div class="status {status_class}">
                {heading}
            </div>

            {body}
        </main>

        <div class="league-footer">
            Powered by RobinHub
        </div>
    </div>
</body>
</html>
"""

def _load_league_status() -> dict[str, Any]:
    _, league_service = require_services()
    return league_service.get_league_status()


@app.get("/api/league/status")
def league_status() -> dict[str, Any]:
    return _cached_value("league_status", _load_league_status)

@app.get("/league/checkin", response_class=HTMLResponse)
async def public_league_checkin(
    request: Request,
) -> HTMLResponse:
    """Public League check-in landing page."""

    league_user = request.session.get("league_user")

    try:
        async with AsyncSessionLocal() as session:
            tenant = (
                await session.execute(
                    select(Tenant).where(
                        Tenant.slug == "robins"
                    )
                )
            ).scalar_one_or_none()

            if tenant is None:
                return HTMLResponse(
                    content=_league_public_page(
                        title="League Check-In Unavailable",
                        heading="⚠️ Check-in Unavailable",
                        status_class="closed",
                        body="""
                            <h2>Pokémon League</h2>
                            <p>League check-in is currently unavailable.</p>
                            <p>Please speak to a member of staff.</p>
                        """,
                    ),
                    status_code=503,
                )

            store = (
                await session.execute(
                    select(Store).where(
                        Store.tenant_id == tenant.id,
                        Store.code == "BELFAST",
                    )
                )
            ).scalar_one_or_none()

            if store is None:
                return HTMLResponse(
                    content=_league_public_page(
                        title="League Check-In Unavailable",
                        heading="⚠️ Check-in Unavailable",
                        status_class="closed",
                        body="""
                            <h2>Pokémon League</h2>
                            <p>League check-in is currently unavailable.</p>
                            <p>Please speak to a member of staff.</p>
                        """,
                    ),
                    status_code=503,
                )

            league_session = (
                await session.execute(
                    select(LeagueSession).where(
                        LeagueSession.tenant_id == tenant.id,
                        LeagueSession.store_id == store.id,
                        LeagueSession.status == "active",
                    )
                )
            ).scalar_one_or_none()

            if league_session is None:
                return HTMLResponse(
                    content=_league_public_page(
                        title="League Check-In Closed",
                        heading="🔴 Check-in Closed",
                        status_class="closed",
                        body="""
                            <h2>Pokémon League</h2>
                            <p>There is currently no active League session.</p>
                            <p>Please speak to a member of staff.</p>
                        """,
                    ),
                    status_code=200,
                )

            if league_user is None:
                return HTMLResponse(
                    content=_league_public_page(
                        title="Robins League Check-In",
                        heading="🟢 Check-in Open",
                        status_class="open",
                        body=f"""
                            <h2>Pokémon League</h2>
                            <div class="league-detail">
                                <span>Location</span>
                                <strong>Robins Hobby Cafe — Belfast</strong>
                            </div>
                            <div class="league-detail">
                                <span>Entry Fee</span>
                                <strong>£{league_session.entry_fee:.2f}</strong>
                            </div>
                            <p>Sign in with Discord to continue with your League check-in.</p>
                            <a class="league-action" href="/league/auth/discord/login">
                                Continue with Discord
                            </a>
                        """,
                    ),
                    status_code=200,
                )

            discord_user_id = str(
                league_user.get("id", "")
            ).strip()

            display_name = (
                league_user.get("display_name")
                or league_user.get("username")
                or "Discord user"
            )

            linked_player = None

            if league is not None and discord_user_id.isdigit():
                linked_player = league.get_linked_player(
                    int(discord_user_id)
                )

            if linked_player is None:
                return HTMLResponse(
                    content=_league_public_page(
                        title="League Player ID Required",
                        heading="⚠️ Player ID Required",
                        status_class="closed",
                        body=f"""
                            <h2>Pokémon League</h2>
                            <div class="league-detail">
                                <span>Discord</span>
                                <strong>{display_name}</strong>
                            </div>
                            <p>Your Discord account does not currently have a linked League Player ID.</p>
                            <p>Please link your Player ID in Discord before checking in.</p>
                        """,
                    ),
                    status_code=200,
                )

            player_id = _clean(
                linked_player.get("Player ID")
            )

            return HTMLResponse(
                content=_league_public_page(
                    title="Robins League Check-In",
                    heading="🟢 Check-in Open",
                    status_class="open",
                    body=f"""
                        <h2>Ready to Check In</h2>
                        <div class="league-detail">
                            <span>Location</span>
                            <strong>Robins Hobby Cafe — Belfast</strong>
                        </div>
                        <div class="league-detail">
                            <span>Entry Fee</span>
                            <strong>£{league_session.entry_fee:.2f}</strong>
                        </div>
                        <div class="league-detail">
                            <span>Discord</span>
                            <strong>{display_name}</strong>
                        </div>
                        <div class="league-detail">
                            <span>Player ID</span>
                            <strong>{player_id}</strong>
                        </div>
                        <form method="post" action="/league/checkin">
                            <label class="league-code-label" for="store_code">
                                Current League Store Code
                            </label>
                            <input
                                class="league-code-input"
                                id="store_code"
                                name="store_code"
                                type="text"
                                maxlength="12"
                                autocomplete="off"
                                autocapitalize="characters"
                                spellcheck="false"
                                placeholder="Enter code"
                                required
                            >
                            <button class="league-action" type="submit">Check In</button>
                        </form>
                    """,
                ),
                status_code=200,
            )

    except Exception:
        logger.exception(
            "Public League check-in page failed"
        )

        return HTMLResponse(
            content=_league_public_page(
                title="League Check-In Unavailable",
                heading="⚠️ Check-in Unavailable",
                status_class="closed",
                body="""
                    <h2>Something went wrong</h2>
                    <p>League check-in is temporarily unavailable.</p>
                    <p>Please speak to a member of staff.</p>
                """,
            ),
            status_code=500,
        )


@app.post("/league/checkin", response_class=HTMLResponse)
async def public_league_checkin_submit(
    request: Request,
) -> HTMLResponse:
    """Check the signed-in League player into the active session."""

    league_user = request.session.get("league_user")

    if not league_user:
        return RedirectResponse(
            "/league/auth/discord/login",
            status_code=303,
        )

    discord_user_id = str(
        league_user.get("id", "")
    ).strip()

    display_name = (
        league_user.get("display_name")
        or league_user.get("username")
        or "Discord user"
    )

    raw_body = (await request.body()).decode("utf-8", errors="replace")
    submitted_store_code = _clean(
        parse_qs(raw_body).get("store_code", [""])[0]
    ).upper()

    if not discord_user_id.isdigit():
        return HTMLResponse(
            content=_league_public_page(
                title="League Check-In Unavailable",
                heading="⚠️ Check-in Unavailable",
                status_class="closed",
                body="""
                    <h2>Invalid Discord Identity</h2>
                    <p>We could not identify your Discord account.</p>
                    <p>Please speak to a member of staff.</p>
                """,
            ),
            status_code=400,
        )

    try:
        linked_player = None

        if league is not None:
            linked_player = league.get_linked_player(
                int(discord_user_id)
            )

        if linked_player is None:
            return HTMLResponse(
                content=_league_public_page(
                    title="League Player ID Required",
                    heading="⚠️ Player ID Required",
                    status_class="closed",
                    body=f"""
                        <h2>Pokémon League</h2>
                        <div class="league-detail">
                            <span>Discord</span>
                            <strong>{display_name}</strong>
                        </div>
                        <p>Your Discord account does not have a linked League Player ID.</p>
                        <p>Please link your Player ID in Discord before checking in.</p>
                    """,
                ),
                status_code=409,
            )

        active_event = league.get_active_event() if league is not None else None

        if active_event is None:
            return HTMLResponse(
                content=_league_public_page(
                    title="League Check-In Closed",
                    heading="🔴 Check-in Closed",
                    status_class="closed",
                    body="""
                        <h2>Pokémon League</h2>
                        <p>The active League event has closed.</p>
                        <p>Please speak to a member of staff.</p>
                    """,
                ),
                status_code=409,
            )

        active_store_code = _clean(
            active_event.get("Store Code")
        ).upper()

        if not submitted_store_code or not secrets.compare_digest(
            submitted_store_code,
            active_store_code,
        ):
            return HTMLResponse(
                content=_league_public_page(
                    title="Invalid League Store Code",
                    heading="⚠️ Invalid Store Code",
                    status_class="closed",
                    body=f"""
                        <h2>Pokémon League</h2>
                        <div class="league-detail">
                            <span>Player</span>
                            <strong>{display_name}</strong>
                        </div>
                        <p>That Store Code does not match the current League event.</p>
                        <p>Please enter the code displayed in store and try again.</p>
                        <a class="league-action" href="/league/checkin">Try Again</a>
                    """,
                ),
                status_code=409,
            )

        async with AsyncSessionLocal() as session:
            tenant = (
                await session.execute(
                    select(Tenant).where(
                        Tenant.slug == "robins"
                    )
                )
            ).scalar_one()

            store = (
                await session.execute(
                    select(Store).where(
                        Store.tenant_id == tenant.id,
                        Store.code == "BELFAST",
                    )
                )
            ).scalar_one()

            league_session = (
                await LeagueDatabaseService.get_active_session(
                    session,
                    tenant_id=tenant.id,
                    store_id=store.id,
                )
            )

            customer = (
                await LeagueDatabaseService.get_or_create_customer(
                    session,
                    tenant_id=tenant.id,
                    discord_user_id=int(discord_user_id),
                    display_name=display_name,
                )
            )

            attendance = (
                await LeagueDatabaseService.check_in_customer(
                    session,
                    league_session_id=league_session.id,
                    customer_id=customer.id,
                    checkin_method="public_qr",
                )
            )

            payment = await PaymentService.create_cash_due(
                session,
                tenant_id=tenant.id,
                store_id=store.id,
                customer_id=customer.id,
                context_type="league_attendance",
                context_id=attendance.id,
                amount=league_session.entry_fee,
                currency=league_session.currency,
            )

            try:
                league.check_in_player(
                    discord_user_id=int(discord_user_id),
                    store_code=active_store_code,
                )
            except Exception:
                await session.rollback()
                raise

            await session.commit()

            return HTMLResponse(
                content=_league_public_page(
                    title="League Check-In Complete",
                    heading="✅ Check-In Complete",
                    status_class="open",
                    body=f"""
                        <h2 class="league-success">You're checked in!</h2>
                        <div class="league-detail">
                            <span>Player</span>
                            <strong>{display_name}</strong>
                        </div>
                        <div class="league-detail">
                            <span>Entry Fee</span>
                            <strong>£{payment.amount:.2f}</strong>
                        </div>
                        <div class="league-detail">
                            <span>Payment</span>
                            <strong>Cash Due</strong>
                        </div>
                        <p>Please pay at the counter.</p>
                    """,
                ),
                status_code=200,
            )

    except ValueError as exc:
        return HTMLResponse(
            content=_league_public_page(
                title="League Check-In",
                heading="⚠️ Check-In Not Completed",
                status_class="closed",
                body=f"""
                    <h2>Unable to Check In</h2>
                    <p>{str(exc)}</p>
                """,
            ),
            status_code=409,
        )

    except Exception:
        logger.exception(
            "Public League check-in failed for Discord user %s",
            discord_user_id,
        )

        return HTMLResponse(
            content=_league_public_page(
                title="League Check-In Failed",
                heading="⚠️ Check-In Failed",
                status_class="closed",
                body="""
                    <h2>Something went wrong</h2>
                    <p>We could not complete your League check-in.</p>
                    <p>Please speak to a member of staff.</p>
                """,
            ),
            status_code=500,
        )

@app.get("/api/league/attendance")
def league_attendance(event_id: str = "") -> list[dict[str, Any]]:
    service, league_service = require_services()
    if not event_id:
        active = league_service.get_active_event()
        if active is None:
            return []
        event_id = _clean(active.get("Event ID"))

    player_names = {
        _clean(row.get("Discord User ID")): {
            "discord_name": _clean(row.get("Discord Name")),
            "player_id": _clean(row.get("Player ID")),
        }
        for row in service.league_players_sheet.get_all_records()
    }

    results: list[dict[str, Any]] = []
    for row in service.league_attendance_sheet.get_all_records():
        if _clean(row.get("Event ID")) != event_id:
            continue
        user_id = _clean(row.get("Discord User ID"))
        player = player_names.get(user_id, {})
        results.append(
            {
                "event_id": event_id,
                "discord_user_id": user_id,
                "discord_name": player.get("discord_name", ""),
                "player_id": _clean(row.get("Player ID"))
                or player.get("player_id", ""),
                "checked_in_at": _clean(row.get("Checked In At")),
            }
        )
    return results

@app.get("/api/league/players")
def league_players(
    staff: dict[str, Any] = Depends(require_staff),
) -> list[dict[str, Any]]:
    """Return players who have linked their League Player ID."""

    del staff

    service, _ = require_services()

    players: list[dict[str, Any]] = []

    for row in service.league_players_sheet.get_all_records():
        discord_user_id = _clean(row.get("Discord User ID"))

        if not discord_user_id:
            continue

        players.append(
            {
                "discord_user_id": discord_user_id,
                "discord_name": _clean(row.get("Discord Name")),
                "player_id": _clean(row.get("Player ID")),
                "last_attendance": _clean(row.get("Last Attendance")),
            }
        )

    players.sort(
        key=lambda player: (
            player["discord_name"] or player["discord_user_id"]
        ).casefold()
    )

    return players

@app.get("/api/league/payments")
async def league_payments(
    staff: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    """Return the active PostgreSQL League session and attendee payments."""

    del staff

    try:
        async with AsyncSessionLocal() as session:
            tenant = (
                await session.execute(
                    select(Tenant).where(
                        Tenant.slug == "robins"
                    )
                )
            ).scalar_one_or_none()

            if tenant is None:
                raise HTTPException(
                    status_code=404,
                    detail="RobinHub tenant was not found.",
                )

            store = (
                await session.execute(
                    select(Store).where(
                        Store.tenant_id == tenant.id,
                        Store.code == "BELFAST",
                    )
                )
            ).scalar_one_or_none()

            if store is None:
                raise HTTPException(
                    status_code=404,
                    detail="RobinHub store was not found.",
                )

            league_session = (
                await session.execute(
                    select(LeagueSession).where(
                        LeagueSession.tenant_id == tenant.id,
                        LeagueSession.store_id == store.id,
                        LeagueSession.status == "active",
                    )
                )
            ).scalar_one_or_none()

            if tenant is None:
                return HTMLResponse(
                    content="<h1>League check-in unavailable</h1>",
                    status_code=503,
                )

            store = (
                await session.execute(
                    select(Store).where(
                        Store.tenant_id == tenant.id,
                        Store.code == "BELFAST",
                    )
                )
            ).scalar_one_or_none()

            if store is None:
                return HTMLResponse(
                    content="<h1>League check-in unavailable</h1>",
                    status_code=503,
                )

            league_session = (
                await session.execute(
                    select(LeagueSession).where(
                        LeagueSession.tenant_id == tenant.id,
                        LeagueSession.store_id == store.id,
                        LeagueSession.status == "active",
                    )
                )
            ).scalar_one_or_none()

            if league_session is None:
                return HTMLResponse(
                    content="""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Robins League Check-In</title>
</head>
<body>
    <main>
        <h1>Pokémon League</h1>
        <h2>🔴 Check-in Closed</h2>
        <p>There is currently no active League session.</p>
        <p>Please speak to a member of staff.</p>
    </main>
</body>
</html>
                    """,
                    status_code=200,
                )

            if league_session is None:
                return {
                    "active": False,
                    "session": None,
                    "attendees": [],
                }

            league_template = (
                await session.execute(
                    select(LeagueTemplate).where(
                        LeagueTemplate.id
                        == league_session.league_template_id
                    )
                )
            ).scalar_one_or_none()

            attendance_rows = (
                await session.execute(
                    select(
                        LeagueAttendance,
                        Customer,
                    )
                    .join(
                        Customer,
                        Customer.id == LeagueAttendance.customer_id,
                    )
                    .where(
                        LeagueAttendance.league_session_id
                        == league_session.id
                    )
                    .order_by(
                        LeagueAttendance.checked_in_at
                    )
                )
            ).all()

            attendance_ids = [
                attendance.id
                for attendance, _customer in attendance_rows
            ]

            payments_by_attendance: dict[uuid.UUID, Payment] = {}

            if attendance_ids:
                payments = (
                    await session.execute(
                        select(Payment)
                        .where(
                            Payment.context_type
                            == "league_attendance",
                            Payment.context_id.in_(attendance_ids),
                        )
                        .order_by(
                            Payment.created_at.desc()
                        )
                    )
                ).scalars().all()

                # The newest payment for an attendance wins.
                for payment in payments:
                    if payment.context_id not in payments_by_attendance:
                        payments_by_attendance[
                            payment.context_id
                        ] = payment

            attendees: list[dict[str, Any]] = []

            for attendance, customer in attendance_rows:
                payment = payments_by_attendance.get(
                    attendance.id
                )

                attendees.append(
                    {
                        "attendance_id": str(attendance.id),
                        "customer_id": str(customer.id),
                        "customer": customer.display_name,
                        "discord_user_id": customer.discord_user_id,
                        "checked_in_at": (
                            attendance.checked_in_at.isoformat()
                            if attendance.checked_in_at
                            else None
                        ),
                        "attendance_status": attendance.status,
                        "payment": (
                            {
                                "id": str(payment.id),
                                "amount": float(payment.amount),
                                "currency": payment.currency,
                                "method": payment.method,
                                "status": payment.status,
                                "confirmed_by": (
                                    str(payment.confirmed_by)
                                    if payment.confirmed_by
                                    else None
                                ),
                                "confirmed_at": (
                                    payment.confirmed_at.isoformat()
                                    if payment.confirmed_at
                                    else None
                                ),
                            }
                            if payment
                            else None
                        ),
                    }
                )

            return {
                "active": True,
                "session": {
                    "id": str(league_session.id),
                    "name": (
                        league_template.name
                        if league_template
                        else "League"
                    ),
                    "status": league_session.status,
                    "entry_fee": float(
                        league_session.entry_fee
                    ),
                    "currency": league_session.currency,
                    "starts_at": league_session.starts_at.isoformat(),
                    "ends_at": league_session.ends_at.isoformat(),
                },
                "attendees": attendees,
            }

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Could not load PostgreSQL League payments"
        )
        raise HTTPException(
            status_code=500,
            detail="League payment data could not be loaded.",
        ) from exc

@app.post("/api/league/start")
async def start_league(
    staff: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    _, league_service = require_services()

    try:
        async with AsyncSessionLocal() as session:
            tenant = (
                await session.execute(
                    select(Tenant).where(
                        Tenant.slug == "robins"
                    )
                )
            ).scalar_one()

            store = (
                await session.execute(
                    select(Store).where(
                        Store.tenant_id == tenant.id,
                        Store.code == "BELFAST",
                    )
                )
            ).scalar_one()

            staff_user = await _resolve_db_staff_user(
                session,
                staff,
            )

            db_session = await LeagueDatabaseService.start_session(
                session,
                tenant_id=tenant.id,
                store_id=store.id,
                template_name="Pokémon Weekly League",
                duration_hours=LEAGUE_EVENT_DURATION_HOURS,
                created_by=staff_user.id,
            )

            try:
                event = league_service.start_event()
            except Exception:
                await session.rollback()
                raise

            await session.commit()

            event["postgresql_session_id"] = str(db_session.id)
            event["entry_fee"] = float(db_session.entry_fee)

            event["notification_warnings"] = _notify_league_started(
                event,
                _actor(staff),
            )

            return event

    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception("Dashboard League start failed")

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc

@app.post("/api/league/end")
async def end_league(
    staff: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    _, league_service = require_services()

    try:
        async with AsyncSessionLocal() as session:
            tenant = (
                await session.execute(
                    select(Tenant).where(
                        Tenant.slug == "robins"
                    )
                )
            ).scalar_one()

            store = (
                await session.execute(
                    select(Store).where(
                        Store.tenant_id == tenant.id,
                        Store.code == "BELFAST",
                    )
                )
            ).scalar_one()

            db_session = await LeagueDatabaseService.end_active_session(
                session,
                tenant_id=tenant.id,
                store_id=store.id,
            )

            try:
                event = league_service.close_active_event()
            except Exception:
                await session.rollback()
                raise

            await session.commit()

            event["postgresql_session_id"] = str(db_session.id)

            event["notification_warnings"] = _notify_league_ended(
                event,
                _actor(staff),
            )

            return event

    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception("Dashboard League end failed")

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc
@app.post("/api/league/manual-checkin")
async def manual_league_checkin(
    payload: LeagueManualCheckInRequest,
    staff: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    service, league_service = require_services()

    discord_user_id = payload.discord_user_id.strip()

    if not discord_user_id.isdigit():
        raise HTTPException(
            status_code=422,
            detail="Invalid Discord user ID.",
        )

    try:
        linked_player = league_service.get_linked_player(
            int(discord_user_id)
        )

        if linked_player is None:
            raise HTTPException(
                status_code=404,
                detail="That player has not linked a League Player ID.",
            )

        active_event = league_service.get_active_event()

        if active_event is None:
            raise HTTPException(
                status_code=409,
                detail="There is no active League event.",
            )

        store_code = _clean(
            active_event.get("Store Code")
        )

        async with AsyncSessionLocal() as session:
            tenant = (
                await session.execute(
                    select(Tenant).where(
                        Tenant.slug == "robins"
                    )
                )
            ).scalar_one()

            store = (
                await session.execute(
                    select(Store).where(
                        Store.tenant_id == tenant.id,
                        Store.code == "BELFAST",
                    )
                )
            ).scalar_one()

            db_league_session = (
                await LeagueDatabaseService.get_active_session(
                    session,
                    tenant_id=tenant.id,
                    store_id=store.id,
                )
            )

            display_name = (
                _clean(linked_player.get("Discord Name"))
                or f"Discord {discord_user_id}"
            )

            customer = (
                await LeagueDatabaseService.get_or_create_customer(
                    session,
                    tenant_id=tenant.id,
                    discord_user_id=int(discord_user_id),
                    display_name=display_name,
                )
            )

            attendance = (
                await LeagueDatabaseService.check_in_customer(
                    session,
                    league_session_id=db_league_session.id,
                    customer_id=customer.id,
                    checkin_method="staff_dashboard",
                )
            )

            payment = await PaymentService.create_cash_due(
                session,
                tenant_id=tenant.id,
                store_id=store.id,
                customer_id=customer.id,
                context_type="league_attendance",
                context_id=attendance.id,
                amount=db_league_session.entry_fee,
                currency=db_league_session.currency,
            )

            # Keep the existing Sheets League workflow synchronised.
            try:
                sheet_result = league_service.check_in_player(
                    discord_user_id=int(discord_user_id),
                    store_code=store_code,
                )
            except Exception:
                await session.rollback()
                raise

            await session.commit()

            return {
                "attendance_id": str(attendance.id),
                "customer_id": str(customer.id),
                "player_id": sheet_result["player_id"],
                "discord_user_id": discord_user_id,
                "display_name": display_name,
                "payment_id": str(payment.id),
                "payment_status": payment.status,
                "amount": float(payment.amount),
                "currency": payment.currency,
                "checked_in": True,
            }

    except HTTPException:
        raise

    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Manual League check-in failed for Discord user %s",
            discord_user_id,
        )

        raise HTTPException(
            status_code=500,
            detail="The player could not be checked in.",
        ) from exc


@app.post("/api/payments/{payment_id}/confirm-cash")
async def confirm_cash_payment(
    payment_id: uuid.UUID,
    staff: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    try:
        async with AsyncSessionLocal() as session:
            staff_user = await _resolve_db_staff_user(
                session,
                staff,
            )

            payment = await PaymentService.confirm_cash_payment(
                session,
                payment_id=payment_id,
                confirmed_by=staff_user.id,
            )

            await session.commit()

            return {
                "id": str(payment.id),
                "status": payment.status,
                "method": payment.method,
                "amount": float(payment.amount),
                "currency": payment.currency,
                "confirmed_by": str(payment.confirmed_by),
                "confirmed_at": (
                    payment.confirmed_at.isoformat()
                    if payment.confirmed_at
                    else None
                ),
                "paid_at": (
                    payment.paid_at.isoformat()
                    if payment.paid_at
                    else None
                ),
            }

    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Dashboard cash confirmation failed for payment %s",
            payment_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Cash payment could not be confirmed.",
        ) from exc

@app.post("/api/payments/{payment_id}/comp")
async def comp_payment(
    payment_id: uuid.UUID,
    request: ActionRequest,
    staff: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    reason = request.reason.strip()

    if not reason:
        raise HTTPException(
            status_code=422,
            detail="A comp reason is required.",
        )

    try:
        async with AsyncSessionLocal() as session:
            staff_user = await _resolve_db_staff_user(
                session,
                staff,
            )

            payment = await PaymentService.comp_payment(
                session,
                payment_id=payment_id,
                confirmed_by=staff_user.id,
                reason=reason,
            )

            await session.commit()

            return {
                "id": str(payment.id),
                "status": payment.status,
                "method": payment.method,
                "amount": float(payment.amount),
                "currency": payment.currency,
                "confirmed_by": str(payment.confirmed_by),
                "confirmed_at": (
                    payment.confirmed_at.isoformat()
                    if payment.confirmed_at
                    else None
                ),
                "comp_reason": reason,
            }

    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Dashboard comp failed for payment %s",
            payment_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Entry could not be comped.",
        ) from exc
# ---------------------------------------------------------------------------
# RobinCon Operations Portal API
# ---------------------------------------------------------------------------

class RobinConCheckInRequest(BaseModel):
    ticket_id: str = Field(min_length=1, max_length=80)


class RobinConEditRequest(BaseModel):
    field: Literal["attendee", "tshirt", "saturday", "sunday"]
    value: str = Field(min_length=1, max_length=200)


def require_robincon() -> RobinConStaffService:
    if robincon_staff is None:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "RobinCon services are unavailable.",
                "error": robincon_startup_error,
            },
        )
    return robincon_staff


@app.get("/api/robincon/summary")
def robincon_summary() -> dict[str, Any]:
    return dict(require_robincon().summary())


@app.get("/api/robincon/orders")
def robincon_orders(search: str = "") -> list[dict[str, Any]]:
    service = require_robincon()
    orders = service.orders()
    query = search.strip().casefold()
    if query:
        orders = [
            order for order in orders
            if query in " ".join(str(value) for value in order.values()).casefold()
        ]
    return orders[:500]


@app.get("/api/robincon/orders/{order_number}")
def robincon_order(order_number: str) -> dict[str, Any]:
    service = require_robincon()
    tickets = service.order(order_number)
    orders = service.orders()
    order = next(
        (
            item for item in orders
            if str(item.get("Order Number", "")).strip().casefold()
            == order_number.strip().casefold()
        ),
        None,
    )
    if not tickets and order is None:
        raise HTTPException(status_code=404, detail="RobinCon order not found.")
    return {"order": order or {"Order Number": order_number}, "tickets": tickets}


@app.get("/api/robincon/tickets")
def robincon_tickets(search: str = "") -> list[dict[str, Any]]:
    service = require_robincon()
    return service.find(search)[:500] if search.strip() else service.tickets()[:500]


@app.get("/api/robincon/tickets/{ticket_id}")
def robincon_ticket(ticket_id: str) -> dict[str, Any]:
    ticket = require_robincon().ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="RobinCon ticket not found.")
    return ticket


@app.get("/api/robincon/capacity")
def robincon_capacity() -> dict[str, list[dict[str, Any]]]:
    return require_robincon().capacity()


@app.get("/api/robincon/tshirts")
def robincon_tshirts() -> dict[str, int]:
    return dict(require_robincon().tshirt_counts())


@app.get("/api/robincon/attendees")
def robincon_attendees(day: Literal["Saturday", "Sunday"] = "Saturday") -> list[dict[str, Any]]:
    return require_robincon().attendees(day)


@app.post("/api/robincon/checkin")
def robincon_checkin(
    payload: RobinConCheckInRequest,
    staff: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    try:
        return require_robincon().check_in(
            payload.ticket_id,
            int(staff.get("id", 0) or 0),
            str(staff.get("display_name") or staff.get("username") or "Dashboard Staff"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/robincon/uncheckin")
def robincon_uncheckin(
    payload: RobinConCheckInRequest,
    staff: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    try:
        return require_robincon().uncheck_in(
            payload.ticket_id,
            int(staff.get("id", 0) or 0),
            str(staff.get("display_name") or staff.get("username") or "Dashboard Staff"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/robincon/tickets/{ticket_id}/edit")
def robincon_edit_ticket(
    ticket_id: str,
    payload: RobinConEditRequest,
    staff: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    try:
        return require_robincon().edit_ticket(
            ticket_id=ticket_id,
            field=payload.field,
            value=payload.value,
            staff_id=int(staff.get("id", 0) or 0),
            staff_name=str(staff.get("display_name") or staff.get("username") or "Dashboard Staff"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
