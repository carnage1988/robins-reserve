import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

from config import (
    DISCORD_BOT_TOKEN,
    STAFF_CHANNEL_ID,
    STAFF_ROLE_ID,
)
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
except Exception:
    logger.exception("Google Sheets connection failed")
    sheets = None


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
            }
        )

    await message.channel.send(
        f"🛒 Added **{quantity} × {product['product_name']}**.\n\n"
        f"**Your basket**\n{format_items(basket)}\n\n"
        "Send another order code to add more products, "
        "type **DONE** to submit, or **CANCEL** to clear the basket."
    )


async def submit_basket(message: discord.Message) -> None:
    """Send a customer's complete basket to the staff approval channel."""

    basket = customer_baskets.get(message.author.id, [])

    if not basket:
        await message.channel.send(
            "Your basket is empty. Send an order code to begin."
        )
        return

    staff_channel = bot.get_channel(STAFF_CHANNEL_ID)

    if not isinstance(staff_channel, discord.TextChannel):
        logger.error("Configured staff channel could not be found")
        await message.channel.send(
            "❌ Your preorder could not be submitted."
        )
        return

    request_embed = discord.Embed(
        title="📦 New Preorder Basket",
        description=(
            "React with 👍 to approve the complete basket."
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
        value=format_items(basket),
        inline=False,
    )
    request_embed.add_field(
        name="Total Items",
        value=str(
            sum(int(item["quantity"]) for item in basket)
        ),
        inline=True,
    )
    request_embed.set_footer(
        text=f"Discord ID: {message.author.id}"
    )

    approval_message = await staff_channel.send(
        embed=request_embed
    )
    await approval_message.add_reaction("👍")

    pending_requests[approval_message.id] = {
        "discord_user_id": message.author.id,
        "discord_username": str(message.author),
        "basket": [dict(item) for item in basket],
    }
    save_pending_requests()

    customer_baskets.pop(message.author.id, None)
    pending_quantity_requests.pop(message.author.id, None)

    await message.channel.send(
        "✅ Your basket has been sent for staff approval.\n\n"
        f"{format_items(basket)}"
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

    if command == "cancel":
        pending_quantity_requests.pop(message.author.id, None)
        customer_baskets.pop(message.author.id, None)
        await message.channel.send(
            "Your preorder basket was cleared."
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

    if message.guild is None:
        await process_preorder_dm(message)

    await bot.process_commands(message)


@bot.event
async def on_raw_reaction_add(
    payload: discord.RawReactionActionEvent,
) -> None:
    """Approve a complete preorder basket with one pickup PIN."""

    if bot.user is None or payload.user_id == bot.user.id:
        return

    if payload.channel_id != STAFF_CHANNEL_ID:
        return

    if str(payload.emoji) != "👍":
        return

    request = pending_requests.get(payload.message_id)

    if request is None:
        logger.warning(
            "No pending request found for approval message %s",
            payload.message_id,
        )
        return

    if sheets is None:
        logger.error(
            "Cannot approve preorder: Google Sheets unavailable"
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
            "Unauthorised approval attempt by %s (%s)",
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
                    "to approve preorders."
                )
            except discord.HTTPException:
                logger.warning(
                    "Could not remove unauthorised reaction"
                )

        return

    approved_by = approver.display_name

    try:
        approved_order = sheets.approve_basket(
            discord_username=str(
                request["discord_username"]
            ),
            discord_user_id=int(
                request["discord_user_id"]
            ),
            basket=list(request["basket"]),
            approved_by=approved_by,
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
            int(request["discord_user_id"])
        )
        await customer.send(
            "✅ **Robin's Reserve Preorder Approved**\n\n"
            f"{format_items(approved_order['items'])}\n\n"
            f"🔐 Pickup PIN: "
            f"**{approved_order['pickup_pin']}**\n\n"
            "Please show this PIN when collecting the order."
        )
    except discord.HTTPException:
        logger.warning(
            "Could not send confirmation DM to user %s",
            request["discord_user_id"],
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
                    f"`{approved_order['pickup_pin']}`"
                ),
            )
            approved_embed.add_field(
                name="Products",
                value=format_items(
                    approved_order["items"]
                ),
                inline=False,
            )
            approved_embed.add_field(
                name="Total Items",
                value=str(
                    approved_order["total_quantity"]
                ),
                inline=True,
            )
            approved_embed.add_field(
                name="Approved By",
                value=approved_by,
                inline=False,
            )

            await approval_message.reply(
                embed=approved_embed
            )

        except discord.HTTPException:
            logger.exception(
                "Could not update the approval message"
            )

    pending_requests.pop(payload.message_id, None)
    save_pending_requests()


@bot.command(name="lookup")
async def lookup(
    ctx: commands.Context,
    pickup_pin: str = "",
) -> None:
    """Look up an active or collected preorder basket by PIN."""

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


@bot.event
async def on_ready():
    logger.info("Logged in as %s", bot.user)
    logger.info("User ID: %s", bot.user.id)
    logger.info(
        "Pending approvals available after startup: %s",
        len(pending_requests),
    )

bot.run(DISCORD_BOT_TOKEN)
