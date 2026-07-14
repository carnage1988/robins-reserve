import logging

import discord
from discord.ext import commands

from config import DISCORD_BOT_TOKEN, STAFF_CHANNEL_ID
from sheets_service import SheetsService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)


try:
    sheets = SheetsService()
except Exception as exc:
    logging.error("Google Sheets connection failed: %s", exc)
    sheets = None


@bot.event
async def on_ready() -> None:
    print("=" * 60)
    print(f"Connected as: {bot.user}")
    print(f"Servers:      {len(bot.guilds)}")

    staff_channel = bot.get_channel(STAFF_CHANNEL_ID)

    if staff_channel is None:
        print("Staff channel: NOT FOUND")
    else:
        print(f"Staff channel: #{staff_channel.name}")

    if sheets is None:
        print("Google Sheets: NOT CONNECTED")
    else:
        try:
            status = sheets.connection_status()
            print(f"Google Sheet: {status['title']}")
            print(f"Products:     {status['product_count']}")
        except Exception as exc:
            logging.exception(
                "Unable to read Google Sheets",
                exc_info=exc,
            )

    print("=" * 60)

async def process_preorder_dm(message: discord.Message) -> None:
    """Check a DM for an active preorder trigger phrase."""

    print(f"Received message: {message.content}")


    if sheets is None:
        await message.channel.send(
            "❌ Preorders are temporarily unavailable."
        )
        return

    try:
        product = sheets.find_product_by_trigger(message.content)
    except Exception:
        logging.exception("Unable to check preorder trigger")
        await message.channel.send(
            "❌ I could not check the current preorders."
        )
        return

    if product is None:
        return

    if product["stock"] <= 0:
        await message.channel.send(
            f"Sorry, **{product['product_name']}** is now fully allocated."
        )
        return

    staff_channel = bot.get_channel(STAFF_CHANNEL_ID)

    if staff_channel is None:
        logging.error("Configured staff channel could not be found")
        await message.channel.send(
            "❌ Your preorder could not be submitted."
        )
        return

    approval_message = await staff_channel.send(
        "📦 **New Preorder Request**\n"
        f"Customer: {message.author.mention}\n"
        f"Username: `{message.author}`\n"
        f"Discord ID: `{message.author.id}`\n"
        f"Product: **{product['product_name']}**\n"
        f"Product ID: `{product['product_id']}`\n"
        f"Stock remaining: `{product['stock']}`\n\n"
        "React with 👍 to approve."
    )

    await approval_message.add_reaction("👍")

    await message.channel.send(
        f"✅ Your request for **{product['product_name']}** "
        "has been sent for approval."
    )

@bot.event
async def on_message(message: discord.Message) -> None:
    """Process customer DMs while keeping normal commands working."""

    if message.author.bot:
        return

    if message.guild is None:
        await process_preorder_dm(message)

    await bot.process_commands(message)

@bot.command(name="ping")
async def ping(ctx: commands.Context) -> None:
    await ctx.send(f"🏓 Pong! `{round(bot.latency * 1000)} ms`")


@bot.command(name="status")
async def status(ctx: commands.Context) -> None:
    staff_channel = bot.get_channel(STAFF_CHANNEL_ID)

    staff_status = (
        f"✅ #{staff_channel.name}"
        if staff_channel
        else "❌ Not found"
    )

    sheets_status = (
        "✅ Connected"
        if sheets
        else "❌ Not connected"
    )

    await ctx.send(
        "**Preorder Bot Status**\n"
        f"Discord: ✅ Online\n"
        f"Staff channel: {staff_status}\n"
        f"Google Sheets: {sheets_status}"
    )


@bot.command(name="products")
async def products(ctx: commands.Context) -> None:
    """List products currently accepting preorders."""

    if sheets is None:
        await ctx.send("❌ Google Sheets is not connected.")
        return

    try:
        available_products = sheets.get_products(open_only=True)
    except Exception:
        logging.exception("Unable to retrieve products")
        await ctx.send("❌ I could not read the product list.")
        return

    if not available_products:
        await ctx.send("There are currently no open preorders.")
        return

    lines = ["**Current Preorders**"]

    for product in available_products:
        lines.append(
            "\n"
            f"**{product['product_name']}**\n"
            f"Product ID: `{product['product_id']}`\n"
            f"Stock: {product['stock']}\n"
            f"Customer limit: {product['customer_limit']}"
        )

    await ctx.send("\n".join(lines))


@bot.command(name="staff-test")
@commands.has_permissions(administrator=True)
async def staff_test(ctx: commands.Context) -> None:
    staff_channel = bot.get_channel(STAFF_CHANNEL_ID)

    if staff_channel is None:
        await ctx.send("❌ Staff channel not found.")
        return

    await staff_channel.send(
        "✅ **Approval channel test successful**\n"
        f"Requested by: {ctx.author.mention}"
    )

    await ctx.send("✅ Test message sent.")


bot.run(DISCORD_BOT_TOKEN)
