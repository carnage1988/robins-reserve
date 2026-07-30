import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    DISCORD_BOT_TOKEN,
    LEAGUE_GUILD_ID,
    LEAGUE_CHANNEL_ID,
    LEAGUE_EVENT_DURATION_HOURS,
    LEAGUE_ROLE_ID,
    LEAGUE_WINDOW_DAYS,
    STAFF_CHANNEL_ID,
    STAFF_ROLE_ID,
)
from league_service import LeagueService
from sheets_service import SheetsService

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"

LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / "robins_reserve.log"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(LOG_FORMAT))

file_handler = RotatingFileHandler(
    LOG_FILE,
    mode="a",
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        console_handler,
        file_handler,
    ],
)

logger = logging.getLogger(__name__)


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    status=discord.Status.online,
    activity=discord.Game(name="Managing preorders"),
)

# Approval-message details awaiting a staff reaction.
# These are persisted so pending approvals survive bot restarts.
PENDING_REQUESTS_FILE = DATA_DIR / "pending_requests.json"
pending_requests: dict[int, dict[str, Any]] = {}


def load_pending_requests() -> dict[int, dict[str, Any]]:
    """Load pending approval requests from disk."""

    if not PENDING_REQUESTS_FILE.exists():
        return {}

    try:
        raw_data = json.loads(
            PENDING_REQUESTS_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(raw_data, dict):
            raise ValueError("Pending request data must be a JSON object")

        loaded_requests = {
            int(message_id): request
            for message_id, request in raw_data.items()
            if isinstance(request, dict)
        }

        logger.info(
            "Loaded %s pending approval request(s)",
            len(loaded_requests),
        )
        return loaded_requests

    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        logger.exception(
            "Could not load pending approvals from %s",
            PENDING_REQUESTS_FILE,
        )
        return {}


def save_pending_requests() -> None:
    """Write pending approval requests to disk atomically."""

    temporary_file = PENDING_REQUESTS_FILE.with_suffix(".json.tmp")

    try:
        temporary_file.write_text(
            json.dumps(pending_requests, indent=2),
            encoding="utf-8",
        )
        temporary_file.replace(PENDING_REQUESTS_FILE)

    except OSError:
        logger.exception(
            "Could not save pending approvals to %s",
            PENDING_REQUESTS_FILE,
        )


pending_requests.update(load_pending_requests())

# Customer quantity prompts awaiting a numeric DM response.
pending_quantity_requests: dict[int, dict[str, Any]] = {}

# Customer baskets awaiting DONE.
customer_baskets: dict[int, list[dict[str, Any]]] = {}


try:
    sheets = SheetsService()
    league_service = LeagueService(sheets)
except Exception:
    logger.exception("Google Sheets connection failed")
    sheets = None
    league_service = None

def is_staff_channel(ctx: commands.Context) -> bool:
    """Return True when a command is used in the staff approval channel."""

    return ctx.guild is not None and ctx.channel.id == STAFF_CHANNEL_ID


def format_datetime(timestamp: str) -> str:
    """Convert an ISO timestamp into readable Belfast local time."""

    if not timestamp:
        return "Unknown"

    parsed = datetime.fromisoformat(timestamp)

    return parsed.astimezone(
        ZoneInfo("Europe/London")
    ).strftime("%d %b %Y at %H:%M")


def format_items(items: list[dict[str, Any]]) -> str:
    """Format order items for Discord messages."""

    return "\n".join(
        f"• **{item['quantity']} × {item['product_name']}**"
        for item in items
    )


def basket_quantity_for_product(
    basket: list[dict[str, Any]],
    product_id: str,
) -> int:
    """Return the quantity of a product currently in a basket."""

    return sum(
        int(item["quantity"])
        for item in basket
        if item["product_id"] == product_id
    )


async def add_to_basket(
    message: discord.Message,
    product: dict[str, Any],
    quantity: int,
) -> None:
    """Validate and add one product line to a customer's basket."""

    if sheets is None:
        await message.channel.send(
            "❌ Preorders are temporarily unavailable."
        )
        return

    basket = customer_baskets.setdefault(
        message.author.id,
        [],
    )

    existing_basket_quantity = basket_quantity_for_product(
        basket,
        product["product_id"],
    )
    approved_quantity = sheets.get_customer_product_total(
        message.author.id,
        product["product_id"],
    )

    remaining_limit = (
        product["customer_limit"]
        - approved_quantity
        - existing_basket_quantity
    )

    if remaining_limit <= 0:
        await message.channel.send(
            f"❌ You have reached the limit for "
            f"**{product['product_name']}**."
        )
        return

    if quantity > remaining_limit:
        await message.channel.send(
            f"❌ You may only add **{remaining_limit}** more × "
            f"**{product['product_name']}**."
        )
        return

    if existing_basket_quantity + quantity > product["stock"]:
        available_for_basket = max(
            product["stock"] - existing_basket_quantity,
            0,
        )
        await message.channel.send(
            f"❌ Only **{available_for_basket}** more × "
            f"**{product['product_name']}** are available."
        )
        return

    for item in basket:
        if item["product_id"] == product["product_id"]:
            item["quantity"] += quantity
            break
    else:
        basket.append(
            {
                "product_id": product["product_id"],
                "product_name": product["product_name"],
                "order_code": product["order_code"],
                "quantity": quantity,
                "league_only": product.get("league_only", False),
            }
        )

    await message.channel.send(
        f"🛒 Added **{quantity} × {product['product_name']}**.\n\n"
        f"**Your basket**\n{format_items(basket)}\n\n"
        "Send another order code to add more products, "
        "type **DONE** to submit, or **CANCEL** to clear the basket."
    )


async def submit_basket(message: discord.Message) -> None:
    """Reserve a customer's basket and request staff approval."""

    basket = customer_baskets.get(message.author.id, [])

    if not basket:
        await message.channel.send(
            "Your basket is empty. Send an order code to begin."
        )
        return

    contains_league_product = any(
        item.get("league_only", False)
        for item in basket
    )

    if contains_league_product:
        if not await has_league_role(message.author.id):
            logger.info(
                "Blocked League-only basket submission for user %s",
                message.author.id,
            )
            await message.channel.send(
                "❌ Your basket contains a Pokémon League-only product, "
                "but I could not verify that you currently have the "
                "**Pokémon League** role.\n\n"
                "Your basket has not been submitted."
            )
            return

    if sheets is None:
        await message.channel.send(
            "❌ Preorders are temporarily unavailable."
        )
        return

    staff_channel = bot.get_channel(STAFF_CHANNEL_ID)

    if not isinstance(staff_channel, discord.TextChannel):
        logger.error("Configured staff channel could not be found")
        await message.channel.send(
            "❌ Your preorder could not be submitted."
        )
        return

    try:
        approval_message = await staff_channel.send(
            "⏳ Reserving preorder stock..."
        )
    except discord.HTTPException:
        logger.exception(
            "Could not create the staff approval message"
        )
        await message.channel.send(
            "❌ Your preorder could not be submitted."
        )
        return

    try:
        reserved_order = sheets.reserve_basket(
            discord_username=str(message.author),
            discord_user_id=message.author.id,
            basket=[dict(item) for item in basket],
            approval_message_id=approval_message.id,
        )

    except ValueError as exc:
        try:
            await approval_message.edit(
                content=f"❌ Reservation failed: {exc}"
            )
        except discord.HTTPException:
            logger.warning(
                "Could not update failed reservation message"
            )

        await message.channel.send(
            f"❌ Your preorder could not be reserved: {exc}"
        )
        return

    except Exception:
        logger.exception("Unexpected basket reservation error")

        try:
            await approval_message.edit(
                content="❌ The preorder reservation failed."
            )
        except discord.HTTPException:
            logger.warning(
                "Could not update failed reservation message"
            )

        await message.channel.send(
            "❌ Your preorder could not be reserved."
        )
        return

    request_embed = discord.Embed(
        title="📦 Pending Preorder Reservation",
        description=(
            "Stock has been reserved from the preorder allocation.\n"
            "React with 👍 to approve or 👎 to decline the complete basket."
        ),
    )
    request_embed.add_field(
        name="Customer",
        value=message.author.mention,
        inline=False,
    )
    request_embed.add_field(
        name="Username",
        value=str(message.author),
        inline=False,
    )
    request_embed.add_field(
        name="Products",
        value=format_items(reserved_order["items"]),
        inline=False,
    )
    request_embed.add_field(
        name="Total Items",
        value=str(reserved_order["total_quantity"]),
        inline=True,
    )
    request_embed.add_field(
        name="Status",
        value=reserved_order["status"],
        inline=True,
    )
    request_embed.add_field(
        name="Reservation PIN",
        value=f"`{reserved_order['pickup_pin']}`",
        inline=False,
    )
    request_embed.set_footer(
        text=f"Discord ID: {message.author.id}"
    )

    try:
        await approval_message.edit(
            content=None,
            embed=request_embed,
        )
        await approval_message.add_reaction("👍")
        await approval_message.add_reaction("👎")
    except discord.HTTPException:
        logger.exception(
            "Reservation was created but the approval message "
            "could not be fully updated"
        )

    pending_requests[approval_message.id] = {
        "pickup_pin": reserved_order["pickup_pin"],
    }
    save_pending_requests()

    customer_baskets.pop(message.author.id, None)
    pending_quantity_requests.pop(message.author.id, None)

    await message.channel.send(
        "✅ **Your preorder stock has been reserved.**\n\n"
        f"{format_items(reserved_order['items'])}\n\n"
        "Your reservation is now awaiting staff approval. "
        "Your pickup PIN will be sent once the preorder is approved."
    )


async def process_preorder_dm(message: discord.Message) -> None:
    """Process order codes, quantities and basket commands received by DM."""

    logger.info(
        "Received DM from %s (%s): %r",
        message.author,
        message.author.id,
        message.content,
    )

    if sheets is None:
        await message.channel.send(
            "❌ Preorders are temporarily unavailable."
        )
        return

    content = message.content.strip()
    command = content.casefold()

    cancel_parts = content.split(maxsplit=1)
    is_cancel_command = cancel_parts[0].casefold() == "cancel"

    if is_cancel_command:
        supplied_pin = (
            cancel_parts[1].strip() if len(cancel_parts) == 2 else ""
        )

        if not supplied_pin:
            had_quantity_prompt = (
                pending_quantity_requests.pop(message.author.id, None)
                is not None
            )
            had_basket = (
                customer_baskets.pop(message.author.id, None)
                is not None
            )

            if had_quantity_prompt or had_basket:
                await message.channel.send(
                    "Your preorder basket was cleared."
                )
                return

            try:
                pending_order = sheets.get_pending_reservation_for_customer(
                    message.author.id
                )
            except Exception:
                logger.exception(
                    "Unable to check for a pending customer reservation"
                )
                await message.channel.send(
                    "❌ I could not check your pending reservations."
                )
                return

            if pending_order is None:
                await message.channel.send(
                    "You do not have a basket or pending reservation to "
                    "cancel. For an approved order, send "
                    "**CANCEL <pickup PIN>**."
                )
                return

            supplied_pin = str(pending_order["pickup_pin"])
            allowed_statuses = {"pending"}
        else:
            allowed_statuses = {"pending", "approved"}

        try:
            cancelled_order = sheets.cancel_reservation(
                pickup_pin=supplied_pin,
                discord_user_id=message.author.id,
                cancelled_by=str(message.author),
                reason="Customer request",
                allowed_statuses=allowed_statuses,
            )
        except ValueError as exc:
            await message.channel.send(f"❌ {exc}")
            return
        except Exception:
            logger.exception("Unexpected preorder cancellation error")
            await message.channel.send(
                "❌ Your preorder reservation could not be cancelled."
            )
            return

        approval_message_id_text = str(
            cancelled_order.get("approval_message_id", "")
        ).strip()

        if approval_message_id_text.isdigit():
            approval_message_id = int(approval_message_id_text)
            pending_requests.pop(approval_message_id, None)
            save_pending_requests()

            staff_channel = bot.get_channel(STAFF_CHANNEL_ID)
            if isinstance(staff_channel, discord.TextChannel):
                try:
                    approval_message = await staff_channel.fetch_message(
                        approval_message_id
                    )
                    cancelled_embed = discord.Embed(
                        title="🚫 Preorder Cancelled by Customer",
                        description=(
                            "Reserved stock has been returned and the order "
                            "has been moved to the Cancelled sheet.\n"
                            f"Pickup PIN: `{cancelled_order['pickup_pin']}`"
                        ),
                    )
                    cancelled_embed.add_field(
                        name="Customer",
                        value=str(message.author),
                        inline=False,
                    )
                    cancelled_embed.add_field(
                        name="Products",
                        value=format_items(cancelled_order["items"]),
                        inline=False,
                    )
                    await approval_message.reply(embed=cancelled_embed)
                except discord.HTTPException:
                    logger.warning(
                        "Could not update the cancelled approval message"
                    )

        await message.channel.send(
            "🚫 **Your preorder has been cancelled.**\n\n"
            f"{format_items(cancelled_order['items'])}\n\n"
            "The reserved stock has been returned to the preorder "
            "allocation."
        )
        return

    if command == "done":
        pending_quantity_requests.pop(message.author.id, None)
        await submit_basket(message)
        return

    pending_product = pending_quantity_requests.get(
        message.author.id
    )

    if pending_product is not None:
        try:
            quantity = int(content)
        except ValueError:
            await message.channel.send(
                "Please reply with a whole number, or type **CANCEL**."
            )
            return

        if quantity < 1:
            await message.channel.send(
                "Quantity must be at least 1."
            )
            return

        product = pending_product["product"]

        if quantity > product["customer_limit"]:
            await message.channel.send(
                f"The maximum quantity for "
                f"**{product['product_name']}** is "
                f"**{product['customer_limit']}**."
            )
            return

        pending_quantity_requests.pop(message.author.id, None)
        await add_to_basket(message, product, quantity)
        return

    try:
        product = sheets.find_product_by_order_code(content)
    except Exception:
        logger.exception("Unable to check order code")
        await message.channel.send(
            "❌ I could not check the current preorders."
        )
        return

    if product is not None and product.get("league_only", False):
        if not await has_league_role(message.author.id):
            logger.info(
                "Blocked League-only product access for user %s",
                message.author.id,
            )
            await message.channel.send(
                "❌ This preorder is available only to members with the "
                "**Pokémon League** role."
            )
            return

    if product is not None and not product["preorders_open"]:
        await message.channel.send(
            f"🚫 Preorders for **{product['product_name']}** "
            "are currently closed.\n\n"
            "Please keep an eye on the server announcements "
            "for updates."
        )
        return

    if product is None:
        try:
            matches = sheets.find_products_by_partial_code(content)
        except Exception:
            logger.exception("Unable to search order codes")
            return

        if matches:
            has_role = await has_league_role(message.author.id)
            matches = [
                match
                for match in matches
                if not match.get("league_only", False) or has_role
            ]

        if matches:
            codes = "\n".join(
                f"• `{match['order_code']}`"
                for match in matches[:10]
            )
            await message.channel.send(
                "❓ I found these matching preorder codes:\n\n"
                f"{codes}\n\n"
                "Please send the full order code exactly as shown."
            )
        else:
            await message.channel.send(
                "❌ **That isn't a recognised order code.**\n\n"
                "Please use **`!products`** to see the products currently available for preorder.\n\n"
                "If you think this is an error, please contact a member of staff."
            )

        return

    if product["stock"] <= 0:
        await message.channel.send(
            f"Sorry, **{product['product_name']}** "
            "is now fully allocated."
        )
        return

    if product["customer_limit"] <= 1:
        await add_to_basket(message, product, 1)
        return

    pending_quantity_requests[message.author.id] = {
        "product": product,
    }

    await message.channel.send(
        f"How many **{product['product_name']}** would you like?\n\n"
        f"Maximum per customer: **{product['customer_limit']}**\n"
        f"Available stock: **{product['stock']}**\n\n"
        "Reply with a whole number, or type **CANCEL**."
    )


@bot.event
async def on_message(message: discord.Message) -> None:
    """Process DMs while preserving normal prefix commands."""

    if message.author.bot:
        return
    
    is_command = message.content.lstrip().startswith("!")

    if message.guild is None and not is_command:
        await process_preorder_dm(message)

    await bot.process_commands(message)


@bot.event
async def on_raw_reaction_add(
    payload: discord.RawReactionActionEvent,
) -> None:
    """Approve or decline an existing pending preorder reservation."""

    if bot.user is None or payload.user_id == bot.user.id:
        return

    if payload.channel_id != STAFF_CHANNEL_ID:
        return

    reaction = str(payload.emoji)

    if reaction not in {"👍", "👎"}:
        return

    request = pending_requests.get(payload.message_id)

    if request is None:
        logger.warning(
            "No pending request found for reservation message %s",
            payload.message_id,
        )
        return

    if sheets is None:
        logger.error(
            "Cannot process preorder reservation: Google Sheets unavailable"
        )
        return

    guild = (
        bot.get_guild(payload.guild_id)
        if payload.guild_id
        else None
    )

    if guild is None:
        logger.warning(
            "Could not identify the server for approval attempt"
        )
        return

    approver = guild.get_member(payload.user_id)

    if approver is None:
        try:
            approver = await guild.fetch_member(payload.user_id)
        except discord.HTTPException:
            logger.warning(
                "Could not retrieve member %s for approval check",
                payload.user_id,
            )
            return

    has_staff_role = any(
        role.id == STAFF_ROLE_ID
        for role in approver.roles
    )

    if not has_staff_role:
        logger.warning(
            "Unauthorised reservation action by %s (%s)",
            approver,
            approver.id,
        )

        channel = bot.get_channel(payload.channel_id)

        if isinstance(channel, discord.TextChannel):
            try:
                approval_message = await channel.fetch_message(
                    payload.message_id
                )
                await approval_message.remove_reaction(
                    payload.emoji,
                    approver,
                )
                await channel.send(
                    f"⚠️ {approver.mention} is not authorised "
                    "to approve or decline preorders."
                )
            except discord.HTTPException:
                logger.warning(
                    "Could not remove unauthorised reaction"
                )

        return

    staff_member = approver.display_name
    pickup_pin = str(request["pickup_pin"])

    if reaction == "👍":
        try:
            processed_order = sheets.approve_reservation(
                pickup_pin=pickup_pin,
                approved_by=staff_member,
                approval_message_id=payload.message_id,
            )

        except ValueError as exc:
            channel = bot.get_channel(payload.channel_id)

            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    f"❌ Could not approve preorder: {exc}"
                )
            return

        except Exception:
            logger.exception(
                "Unexpected preorder basket approval error"
            )
            channel = bot.get_channel(payload.channel_id)

            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    "❌ The preorder basket could not be approved."
                )
            return

        try:
            customer = await bot.fetch_user(
                int(processed_order["discord_user_id"])
            )
            await customer.send(
                "✅ **Robin's Reserve Preorder Approved**\n\n"
                f"{format_items(processed_order['items'])}\n\n"
                f"🔐 Pickup PIN: "
                f"**{processed_order['pickup_pin']}**\n\n"
                "Please show this PIN when collecting the order."
            )
        except discord.HTTPException:
            logger.warning(
                "Could not send confirmation DM to user %s",
                processed_order["discord_user_id"],
            )

        channel = bot.get_channel(payload.channel_id)

        if isinstance(channel, discord.TextChannel):
            try:
                approval_message = await channel.fetch_message(
                    payload.message_id
                )
                approved_embed = discord.Embed(
                    title="✅ Preorder Basket Approved",
                    description=(
                        f"Pickup PIN: "
                        f"`{processed_order['pickup_pin']}`"
                    ),
                )
                approved_embed.add_field(
                    name="Products",
                    value=format_items(
                        processed_order["items"]
                    ),
                    inline=False,
                )
                approved_embed.add_field(
                    name="Total Items",
                    value=str(
                        processed_order["total_quantity"]
                    ),
                    inline=True,
                )
                approved_embed.add_field(
                    name="Approved By",
                    value=staff_member,
                    inline=False,
                )

                await approval_message.reply(
                    embed=approved_embed
                )

            except discord.HTTPException:
                logger.exception(
                    "Could not update the approval message"
                )

    else:
        try:
            processed_order = sheets.decline_reservation(
                pickup_pin=pickup_pin,
                declined_by=staff_member,
                approval_message_id=payload.message_id,
            )

        except ValueError as exc:
            channel = bot.get_channel(payload.channel_id)

            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    f"❌ Could not reject preorder: {exc}"
                )
            return

        except Exception:
            logger.exception(
                "Unexpected preorder basket decline error"
            )
            channel = bot.get_channel(payload.channel_id)

            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    "❌ The preorder basket could not be rejected."
                )
            return

        try:
            customer = await bot.fetch_user(
                int(processed_order["discord_user_id"])
            )
            await customer.send(
                "❌ **Robin's Reserve Preorder Rejected**\n\n"
                f"{format_items(processed_order['items'])}\n\n"
                "The reserved stock has been returned to the preorder "
                "allocation. No pickup PIN has been issued."
            )
        except discord.HTTPException:
            logger.warning(
                "Could not send decline DM to user %s",
                processed_order["discord_user_id"],
            )

        channel = bot.get_channel(payload.channel_id)

        if isinstance(channel, discord.TextChannel):
            try:
                approval_message = await channel.fetch_message(
                    payload.message_id
                )
                declined_embed = discord.Embed(
                    title="❌ Preorder Basket Rejected",
                    description=(
                        "Reserved stock has been returned.\n"
                        f"Reservation PIN: "
                        f"`{processed_order['pickup_pin']}`"
                    ),
                )
                declined_embed.add_field(
                    name="Products",
                    value=format_items(
                        processed_order["items"]
                    ),
                    inline=False,
                )
                declined_embed.add_field(
                    name="Total Items",
                    value=str(
                        processed_order["total_quantity"]
                    ),
                    inline=True,
                )
                declined_embed.add_field(
                    name="Rejected By",
                    value=staff_member,
                    inline=False,
                )

                await approval_message.reply(
                    embed=declined_embed
                )

            except discord.HTTPException:
                logger.exception(
                    "Could not update the rejection message"
                )

    pending_requests.pop(payload.message_id, None)
    save_pending_requests()


@bot.command(name="lookup")
async def lookup(
    ctx: commands.Context,
    pickup_pin: str = "",
) -> None:
    """Look up an order across active and archive sheets by PIN."""

    if not is_staff_channel(ctx):
        await ctx.send(
            "❌ This command can only be used in the "
            "staff approval channel."
        )
        return

    if not pickup_pin:
        await ctx.send("Usage: `!lookup <pickup PIN>`")
        return

    if sheets is None:
        await ctx.send(
            "❌ Google Sheets is not connected."
        )
        return

    try:
        order = sheets.lookup_by_pin(pickup_pin)
    except Exception:
        logger.exception("Unable to look up preorder")
        await ctx.send(
            "❌ The preorder lookup failed."
        )
        return

    if order is None:
        await ctx.send(
            f"❌ No preorder was found for PIN "
            f"`{pickup_pin}`."
        )
        return

    embed = discord.Embed(
        title="🔎 Robin's Reserve Lookup",
        description=(
            f"Pickup PIN: `{order['pickup_pin']}`"
        ),
    )
    embed.add_field(
        name="Customer",
        value=(
            order["discord_username"]
            or "Unknown"
        ),
        inline=False,
    )
    embed.add_field(
        name="Products",
        value=format_items(order["items"]),
        inline=False,
    )
    embed.add_field(
        name="Total Items",
        value=str(order["total_quantity"]),
        inline=True,
    )
    embed.add_field(
        name="Status",
        value=order["status"],
        inline=True,
    )
    embed.add_field(
        name="Approved By",
        value=order["approved_by"] or "Unknown",
        inline=False,
    )

    if str(order["status"]).casefold() == "collected":
        embed.add_field(
            name="Collected By",
            value=order["collected_by"] or "Unknown",
            inline=False,
        )
        embed.add_field(
            name="Collected At",
            value=format_datetime(
                order["collected_at"]
            ),
            inline=False,
        )

    elif str(order["status"]).casefold() == "cancelled":
        embed.add_field(
            name="Cancelled By",
            value=order.get("cancelled_by") or "Unknown",
            inline=False,
        )
        embed.add_field(
            name="Cancelled At",
            value=format_datetime(order.get("cancelled_at", "")),
            inline=False,
        )
        embed.add_field(
            name="Reason",
            value=order.get("cancellation_reason") or "Not provided",
            inline=False,
        )
    elif str(order["status"]).casefold() == "rejected":
        embed.add_field(
            name="Rejected By",
            value=order.get("rejected_by") or "Unknown",
            inline=False,
        )
        embed.add_field(
            name="Rejected At",
            value=format_datetime(order.get("rejected_at", "")),
            inline=False,
        )
        embed.add_field(
            name="Reason",
            value=order.get("rejection_reason") or "Not provided",
            inline=False,
        )

    await ctx.send(embed=embed)


@bot.command(name="cancel")
async def staff_cancel(
    ctx: commands.Context,
    pickup_pin: str = "",
    *,
    reason: str = "Customer request received by staff",
) -> None:
    """Cancel a pending or approved order and return reserved stock."""

    if not is_staff_channel(ctx):
        await ctx.send(
            "❌ This command can only be used in the staff approval channel."
        )
        return
    if not pickup_pin:
        await ctx.send("Usage: `!cancel <pickup PIN> [reason]`")
        return
    if sheets is None:
        await ctx.send("❌ Google Sheets is not connected.")
        return

    try:
        order = sheets.cancel_reservation(
            pickup_pin=pickup_pin,
            cancelled_by=str(ctx.author),
            reason=reason,
            allowed_statuses={"pending", "approved"},
        )
    except ValueError as exc:
        await ctx.send(f"❌ {exc}")
        return
    except Exception:
        logger.exception("Unable to cancel preorder")
        await ctx.send("❌ The preorder could not be cancelled.")
        return

    approval_message_id_text = str(
        order.get("approval_message_id", "")
    ).strip()
    if approval_message_id_text.isdigit():
        pending_requests.pop(int(approval_message_id_text), None)
        save_pending_requests()

    try:
        customer = await bot.fetch_user(int(order["discord_user_id"]))
        await customer.send(
            "🚫 **Robin's Reserve Preorder Cancelled**\n\n"
            f"{format_items(order['items'])}\n\n"
            f"Reason: **{reason}**\n\n"
            "The reserved stock has been returned."
        )
    except (ValueError, discord.HTTPException):
        logger.warning(
            "Could not send cancellation notice to user %s",
            order["discord_user_id"],
        )

    embed = discord.Embed(
        title="🚫 Preorder Cancelled by Staff",
        description=f"Pickup PIN: `{order['pickup_pin']}`",
    )
    embed.add_field(
        name="Products", value=format_items(order["items"]), inline=False
    )
    embed.add_field(
        name="Cancelled By", value=str(ctx.author), inline=False
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    await ctx.send(embed=embed)


@bot.command(name="collect")
async def collect(
    ctx: commands.Context,
    pickup_pin: str = "",
) -> None:
    """Collect and archive an entire preorder basket."""

    if not is_staff_channel(ctx):
        await ctx.send(
            "❌ This command can only be used in the "
            "staff approval channel."
        )
        return

    if not pickup_pin:
        await ctx.send("Usage: `!collect <pickup PIN>`")
        return

    if sheets is None:
        await ctx.send(
            "❌ Google Sheets is not connected."
        )
        return

    try:
        order = sheets.collect_order(
            pickup_pin=pickup_pin,
            collected_by=str(ctx.author),
        )

    except ValueError as exc:
        await ctx.send(f"❌ {exc}")
        return

    except Exception:
        logger.exception("Unable to collect preorder")
        await ctx.send(
            "❌ The preorder could not be marked "
            "as collected."
        )
        return

    try:
        customer = await bot.fetch_user(
            int(order["discord_user_id"])
        )
        await customer.send(
            "✅ **Robin's Reserve Collection Complete**\n\n"
            f"{format_items(order['items'])}\n\n"
            f"Collected: "
            f"**{format_datetime(order['collected_at'])}**\n\n"
            "Thank you for ordering with Robin's Reserve!"
        )
    except (ValueError, discord.HTTPException):
        logger.warning(
            "Could not send collection confirmation to user %s",
            order["discord_user_id"],
        )

    embed = discord.Embed(
        title="✅ Collection Complete",
        description=(
            f"Pickup PIN: `{order['pickup_pin']}`"
        ),
    )
    embed.add_field(
        name="Products",
        value=format_items(order["items"]),
        inline=False,
    )
    embed.add_field(
        name="Total Items",
        value=str(order["total_quantity"]),
        inline=True,
    )
    embed.add_field(
        name="Status",
        value=order["status"],
        inline=True,
    )
    embed.add_field(
        name="Collected By",
        value=order["collected_by"],
        inline=False,
    )
    embed.add_field(
        name="Collected At",
        value=format_datetime(
            order["collected_at"]
        ),
        inline=False,
    )

    await ctx.send(embed=embed)


@bot.command(name="ping")
async def ping(ctx: commands.Context) -> None:
    """Confirm that the bot is responsive."""

    await ctx.send(
        f"🏓 Pong! `{round(bot.latency * 1000)} ms`"
    )


@bot.command(name="status")
async def status(ctx: commands.Context) -> None:
    """Display Discord and Google Sheets connection status."""

    staff_channel = bot.get_channel(STAFF_CHANNEL_ID)

    staff_status = (
        f"✅ #{staff_channel.name}"
        if isinstance(
            staff_channel,
            discord.TextChannel,
        )
        else "❌ Not found"
    )

    sheets_status = (
        "✅ Connected"
        if sheets is not None
        else "❌ Not connected"
    )

    await ctx.send(
        "**Robin's Reserve Status**\n"
        "Discord: ✅ Online\n"
        f"Staff channel: {staff_status}\n"
        f"Google Sheets: {sheets_status}"
    )


@bot.command(name="products")
async def products(ctx: commands.Context) -> None:
    """List products currently accepting preorders."""

    if sheets is None:
        await ctx.send(
            "❌ Google Sheets is not connected."
        )
        return

    try:
        available_products = sheets.get_products(
            open_only=True
        )
    except Exception:
        logger.exception("Unable to retrieve products")
        await ctx.send(
            "❌ I could not read the product list."
        )
        return

    if any(
        product.get("league_only", False)
        for product in available_products
    ):
        has_role = await has_league_role(ctx.author.id)
        available_products = [
            product
            for product in available_products
            if not product.get("league_only", False) or has_role
        ]

    if not available_products:
        await ctx.send(
            "There are currently no open preorders."
        )
        return

    lines = [
        "**Robin's Reserve — Current Preorders**"
    ]

    for product in available_products:
        lines.append(
            "\n"
            f"**{product['product_name']}**\n"
            f"Order code: "
            f"`{product['order_code']}`\n"
            f"Stock: {product['stock']}\n"
            f"Customer limit: "
            f"{product['customer_limit']}"
        )

    await ctx.send("\n".join(lines))


@bot.command(name="staff-test")
@commands.has_permissions(administrator=True)
async def staff_test(
    ctx: commands.Context,
) -> None:
    """Send a test message to the configured staff channel."""

    staff_channel = bot.get_channel(STAFF_CHANNEL_ID)

    if not isinstance(
        staff_channel,
        discord.TextChannel,
    ):
        await ctx.send(
            "❌ Staff channel not found."
        )
        return

    await staff_channel.send(
        "✅ **Approval channel test successful**\n"
        f"Requested by: {ctx.author.mention}"
    )

    await ctx.send("✅ Test message sent.")


@staff_test.error
async def staff_test_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:
    """Handle permission errors for the staff test command."""

    if isinstance(
        error,
        commands.MissingPermissions,
    ):
        await ctx.send(
            "❌ Only a server administrator "
            "can run this test."
        )
        return

    logger.exception(
        "The staff-test command failed",
        exc_info=error,
    )
    await ctx.send(
        "❌ The staff-channel test failed."
    )

def get_league_guild() -> discord.Guild | None:
    """Return the configured Robins guild when available."""

    return bot.get_guild(LEAGUE_GUILD_ID)


async def has_league_role(discord_user_id: int) -> bool:
    """Return whether a Discord user currently has the League role."""

    guild = get_league_guild()
    if guild is None:
        logger.error(
            "Could not verify League access because guild %s is unavailable",
            LEAGUE_GUILD_ID,
        )
        return False

    member = guild.get_member(discord_user_id)

    if member is None:
        try:
            member = await guild.fetch_member(discord_user_id)
        except discord.NotFound:
            logger.info(
                "League access denied because user %s is not in the guild",
                discord_user_id,
            )
            return False
        except discord.HTTPException:
            logger.exception(
                "Could not verify League role for user %s",
                discord_user_id,
            )
            return False

    return any(
        role.id == LEAGUE_ROLE_ID
        for role in member.roles
    )


async def validate_league_staff(
    interaction: discord.Interaction,
) -> discord.Member | None:
    """Validate guild, channel, staff role, and League availability."""

    if interaction.guild_id != LEAGUE_GUILD_ID:
        await interaction.response.send_message(
            "❌ This command can only be used in the Robins server.",
            ephemeral=True,
        )
        return None

    if interaction.channel_id != LEAGUE_CHANNEL_ID:
        await interaction.response.send_message(
            "❌ Use this command in the League check-in channel.",
            ephemeral=True,
        )
        return None

    member = interaction.user
    if not isinstance(member, discord.Member):
        await interaction.response.send_message(
            "❌ I could not verify your server roles.",
            ephemeral=True,
        )
        return None

    if not any(role.id == STAFF_ROLE_ID for role in member.roles):
        await interaction.response.send_message(
            "❌ Only Robins staff can use this command.",
            ephemeral=True,
        )
        return None

    if league_service is None:
        await interaction.response.send_message(
            "❌ The League service is currently unavailable.",
            ephemeral=True,
        )
        return None

    return member


async def apply_league_role(discord_user_id: int) -> bool:
    """Add the configured League role to one guild member."""

    guild = get_league_guild()
    if guild is None or league_service is None:
        return False

    role = guild.get_role(LEAGUE_ROLE_ID)
    if role is None:
        logger.error("Configured League role %s was not found", LEAGUE_ROLE_ID)
        return False

    member = guild.get_member(discord_user_id)
    if member is None:
        try:
            member = await guild.fetch_member(discord_user_id)
        except discord.HTTPException:
            logger.warning(
                "Could not retrieve League member %s",
                discord_user_id,
            )
            return False

    if role not in member.roles:
        try:
            await member.add_roles(
                role,
                reason="Robins League attendance check-in",
            )
        except discord.HTTPException:
            logger.exception(
                "Could not add League role to member %s",
                discord_user_id,
            )
            return False

    league_service.set_role_active(discord_user_id, True)
    return True


@app_commands.command(
    name="linkplayer",
    description="Link your Play! Pokémon Player ID to Discord.",
)
@app_commands.describe(player_id="Your Play! Pokémon Player ID")
async def link_player_command(
    interaction: discord.Interaction,
    player_id: str,
) -> None:
    """Link a Discord user to a Play! Pokémon Player ID."""

    if league_service is None:
        await interaction.response.send_message(
            "❌ The League service is currently unavailable.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        linked = league_service.link_player(
            discord_user_id=interaction.user.id,
            discord_name=str(interaction.user),
            player_id=player_id,
        )
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("Failed to link League player")
        await interaction.followup.send(
            "❌ Your Player ID could not be linked.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        (
            "✅ **Player ID linked.**\n\n"
            f"Player ID: `{linked['player_id']}`\n\n"
            "You can now check in when a Robins League event is active."
        ),
        ephemeral=True,
    )


@app_commands.command(
    name="unlinkplayer",
    description="Remove your linked Play! Pokémon Player ID.",
)
async def unlink_player_command(
    interaction: discord.Interaction,
) -> None:
    """Remove a user's Player ID link and League role."""

    if league_service is None:
        await interaction.response.send_message(
            "❌ The League service is currently unavailable.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        unlinked = league_service.unlink_player(interaction.user.id)
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("Failed to unlink League player")
        await interaction.followup.send(
            "❌ Your Player ID could not be unlinked.",
            ephemeral=True,
        )
        return

    guild = get_league_guild()
    if guild is not None:
        role = guild.get_role(LEAGUE_ROLE_ID)
        member = guild.get_member(interaction.user.id)
        if member is None:
            try:
                member = await guild.fetch_member(interaction.user.id)
            except discord.HTTPException:
                member = None

        if role is not None and member is not None and role in member.roles:
            try:
                await member.remove_roles(
                    role,
                    reason="League Player ID unlinked",
                )
            except discord.HTTPException:
                logger.warning(
                    "Could not remove League role from unlinked member %s",
                    interaction.user.id,
                )

    await interaction.followup.send(
        (
            "✅ **Player ID unlinked.**\n\n"
            f"Removed Player ID: `{unlinked.get('Player ID', 'Unknown')}`"
        ),
        ephemeral=True,
    )


@app_commands.command(
    name="leaguecheckin",
    description="Check in to the active Robins League event.",
)
@app_commands.describe(store_code="The store code displayed inside Robins")
async def league_checkin_command(
    interaction: discord.Interaction,
    store_code: str,
) -> None:
    """Check a linked player into an active League event."""

    if league_service is None:
        await interaction.response.send_message(
            "❌ The League service is currently unavailable.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        result = league_service.check_in_player(
            discord_user_id=interaction.user.id,
            store_code=store_code,
        )
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("League check-in failed")
        await interaction.followup.send(
            "❌ Your League check-in could not be completed.",
            ephemeral=True,
        )
        return

    role_added = await apply_league_role(interaction.user.id)
    role_message = (
        "Your League Player role has been added or renewed."
        if role_added
        else "Your attendance was recorded, but the role could not be updated."
    )

    await interaction.followup.send(
        (
            "✅ **League check-in complete.**\n\n"
            f"Event ID: `{result['event_id']}`\n"
            f"Player ID: `{result['player_id']}`\n\n"
            f"{role_message}"
        ),
        ephemeral=True,
    )


@app_commands.command(
    name="leaguestatus",
    description="View your Robins League membership status.",
)
async def player_league_status_command(
    interaction: discord.Interaction,
) -> None:
    """Show a player's linked ID and latest attendance."""

    if league_service is None:
        await interaction.response.send_message(
            "❌ The League service is currently unavailable.",
            ephemeral=True,
        )
        return

    try:
        player = league_service.get_linked_player(interaction.user.id)
    except Exception:
        logger.exception("Could not read player League status")
        await interaction.response.send_message(
            "❌ Your League status could not be retrieved.",
            ephemeral=True,
        )
        return

    if player is None:
        await interaction.response.send_message(
            "You have not linked a Play! Pokémon Player ID yet.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        (
            "**Your Robins League Status**\n\n"
            f"Player ID: `{player.get('Player ID', 'Unknown')}`\n"
            f"Last Attendance: "
            f"`{player.get('Last Attendance') or 'No attendance recorded'}`\n"
            f"Role Active: `{player.get('Role Active', 'FALSE')}`"
        ),
        ephemeral=True,
    )


bot.tree.add_command(link_player_command)
bot.tree.add_command(unlink_player_command)
bot.tree.add_command(league_checkin_command)
bot.tree.add_command(player_league_status_command)


league_group = app_commands.Group(
    name="league",
    description="Manage Robins Pokémon League events.",
    guild_ids=[LEAGUE_GUILD_ID],
)


@league_group.command(
    name="start",
    description="Start a new Robins League event.",
)
async def league_start(
    interaction: discord.Interaction,
) -> None:
    """Start a League event and publish its store check-in code."""

    if await validate_league_staff(interaction) is None:
        return

    await interaction.response.defer(ephemeral=True)

    try:
        event = league_service.start_event()
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("Failed to start League event")
        await interaction.followup.send(
            "❌ The League event could not be started.",
            ephemeral=True,
        )
        return

    channel = bot.get_channel(LEAGUE_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        await interaction.followup.send(
            "❌ The event started, but the League channel was not found.",
            ephemeral=True,
        )
        return

    await channel.send(
        (
            "**League event started.**\n\n"
            f"**Event ID:** `{event['event_id']}`\n"
            f"**Store Code:** `{event['store_code']}`\n\n"
            f"This event expires in {LEAGUE_EVENT_DURATION_HOURS} hours."
        )
    )

    await interaction.followup.send(
        "✅ League event started and the store code was posted.",
        ephemeral=True,
    )


@league_group.command(
    name="end",
    description="End the active Robins League event.",
)
async def league_end(
    interaction: discord.Interaction,
) -> None:
    """End the currently active League event."""

    if await validate_league_staff(interaction) is None:
        return

    await interaction.response.defer(ephemeral=True)

    try:
        event = league_service.close_active_event()
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("Failed to end League event")
        await interaction.followup.send(
            "❌ The League event could not be ended.",
            ephemeral=True,
        )
        return

    channel = bot.get_channel(LEAGUE_CHANNEL_ID)
    if isinstance(channel, discord.TextChannel):
        await channel.send(
            (
                "**League event ended.**\n\n"
                f"**Event ID:** `{event.get('Event ID', 'Unknown')}`\n\n"
                "Players can no longer check in."
            )
        )

    await interaction.followup.send(
        "✅ League event ended.",
        ephemeral=True,
    )


@league_group.command(
    name="status",
    description="Show the current Robins League status.",
)
async def league_status(
    interaction: discord.Interaction,
) -> None:
    """Show event, attendance, and linked-player totals."""

    if await validate_league_staff(interaction) is None:
        return

    await interaction.response.defer(ephemeral=True)

    try:
        status = league_service.get_league_status()
    except Exception:
        logger.exception("Failed to retrieve League status")
        await interaction.followup.send(
            "❌ League status could not be retrieved.",
            ephemeral=True,
        )
        return

    event = status["active_event"]
    if event is None:
        description = (
            "**Robins League Status**\n\n"
            "Active Event: `No`\n"
            f"Linked Players: `{status['linked_players']}`\n"
            f"Active League Players: `{status['active_players']}`"
        )
    else:
        description = (
            "**Robins League Status**\n\n"
            "Active Event: `Yes`\n"
            f"Event ID: `{event.get('Event ID', 'Unknown')}`\n"
            f"Store Code: `{event.get('Store Code', 'Unknown')}`\n"
            f"Players Checked In: `{status['attendance_count']}`\n"
            f"Linked Players: `{status['linked_players']}`\n"
            f"Active League Players: `{status['active_players']}`\n"
            f"Closes: `{event.get('End Time', 'Unknown')}`"
        )

    await interaction.followup.send(description, ephemeral=True)


@league_group.command(
    name="checkin",
    description="Manually check a member into the active League event.",
)
@app_commands.describe(member="The Discord member attending League")
async def league_staff_checkin(
    interaction: discord.Interaction,
    member: discord.Member,
) -> None:
    """Allow staff to record attendance without a store code."""

    if await validate_league_staff(interaction) is None:
        return

    player = league_service.get_linked_player(member.id)
    if player is None:
        await interaction.response.send_message(
            "❌ That member has not linked a Player ID.",
            ephemeral=True,
        )
        return

    active_event = league_service.get_active_event()
    if active_event is None:
        await interaction.response.send_message(
            "❌ There is no active League event.",
            ephemeral=True,
        )
        return

    store_code = str(active_event.get("Store Code", ""))
    await interaction.response.defer(ephemeral=True)

    try:
        result = league_service.check_in_player(member.id, store_code)
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("Staff League check-in failed")
        await interaction.followup.send(
            "❌ The member could not be checked in.",
            ephemeral=True,
        )
        return

    role_added = await apply_league_role(member.id)
    await interaction.followup.send(
        (
            f"✅ {member.mention} checked in to event "
            f"`{result['event_id']}`.\n"
            f"Role updated: `{'Yes' if role_added else 'No'}`"
        ),
        ephemeral=True,
    )


bot.tree.add_command(league_group)


@tasks.loop(hours=24)
async def reconcile_league_roles() -> None:
    """Reconcile League Player roles against the rolling attendance window."""

    if league_service is None:
        return

    guild = get_league_guild()
    if guild is None:
        logger.warning("League role reconciliation skipped: guild unavailable")
        return

    role = guild.get_role(LEAGUE_ROLE_ID)
    if role is None:
        logger.error("League role reconciliation skipped: role unavailable")
        return

    try:
        players = league_service.get_role_reconciliation_players()
    except Exception:
        logger.exception("Could not load League players for reconciliation")
        return

    for player in players:
        user_id = player["discord_user_id"]
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                league_service.set_role_active(user_id, False)
                continue
            except discord.HTTPException:
                logger.warning(
                    "Could not retrieve member %s during role reconciliation",
                    user_id,
                )
                continue

        has_role = role in member.roles
        should_have_role = player["should_have_role"]

        try:
            if should_have_role and not has_role:
                await member.add_roles(
                    role,
                    reason=(
                        f"League attendance within {LEAGUE_WINDOW_DAYS} days"
                    ),
                )
            elif not should_have_role and has_role:
                await member.remove_roles(
                    role,
                    reason=(
                        f"No League attendance within {LEAGUE_WINDOW_DAYS} days"
                    ),
                )
        except discord.HTTPException:
            logger.exception(
                "Could not reconcile League role for member %s",
                user_id,
            )
            continue

        league_service.set_role_active(user_id, should_have_role)


@reconcile_league_roles.before_loop
async def before_reconcile_league_roles() -> None:
    await bot.wait_until_ready()


slash_commands_synced = False


@bot.event
async def on_ready() -> None:
    global slash_commands_synced

    logger.info("Logged in as %s", bot.user)
    logger.info("User ID: %s", bot.user.id)
    logger.info(
        "Pending approvals available after startup: %s",
        len(pending_requests),
    )

    if not slash_commands_synced:
        guild = discord.Object(id=LEAGUE_GUILD_ID)

        try:
            global_commands = await bot.tree.sync()
            guild_commands = await bot.tree.sync(guild=guild)
            logger.info(
                "Synced %s global and %s guild slash command(s)",
                len(global_commands),
                len(guild_commands),
            )
            slash_commands_synced = True
        except discord.HTTPException:
            logger.exception("Failed to sync slash commands")

    if not reconcile_league_roles.is_running():
        reconcile_league_roles.start()


bot.run(DISCORD_BOT_TOKEN)
