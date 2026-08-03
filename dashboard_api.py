"""HTTP API for the RobinsReserve Budibase staff portal.

This deliberately reuses the existing SheetsService and LeagueService so the
Discord bot and dashboard read and modify the same RobinsReserve data.
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode
from collections import defaultdict
from datetime import datetime
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, Field
import requests

from services.sheets_resilience import install_gspread_resilience
from services.league_service import LeagueService
from services.sheets_service import SheetsService
from services.robincon_service import RobinConService
from services.robincon_staff_service import RobinConStaffService

import logging

logger = logging.getLogger(__name__)

install_gspread_resilience()


APP_NAME = "Robins Reserve Operations API"
APP_VERSION = "1.4.0-dev"
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
    return await call_next(request)

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
    allow_headers=["Content-Type", "X-API-Key"],
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
    }


@app.get("/api/service-health")
def service_health() -> dict[str, Any]:
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


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
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


@app.get("/api/league/status")
def league_status() -> dict[str, Any]:
    _, league_service = require_services()
    return league_service.get_league_status()


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


@app.post("/api/league/start")
def start_league(staff: dict[str, Any] = Depends(require_staff)) -> dict[str, Any]:
    _, league_service = require_services()
    try:
        event = league_service.start_event()
        event["notification_warnings"] = _notify_league_started(event, _actor(staff))
        return event
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Dashboard League start failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/league/end")
def end_league(staff: dict[str, Any] = Depends(require_staff)) -> dict[str, Any]:
    _, league_service = require_services()
    try:
        event = league_service.close_active_event()
        event["notification_warnings"] = _notify_league_ended(event, _actor(staff))
        return event
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Dashboard League end failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

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
