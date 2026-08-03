from pathlib import Path
import asyncio
import json
import time
import logging
import discord
from config import DISCORD_BOT_TOKEN, LEAGUE_GUILD_ID
from utils.logging_setup import configure_logging
configure_logging(Path(__file__).resolve().parent)
from app.runtime import bot, pending_requests
import cogs.league as league
import cogs.preorders
import views.robincon
import cogs.robincon_customer
import cogs.robincon_staff
import cogs.health
logger=logging.getLogger(__name__)
HEARTBEAT_FILE = Path("/app/data/discord_bot_heartbeat.json")

async def discord_heartbeat() -> None:
    """Write a shared heartbeat used by the dashboard health check."""
    while not bot.is_closed():
        try:
            HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = HEARTBEAT_FILE.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "connected": bot.is_ready(),
                        "user_id": bot.user.id if bot.user else None,
                        "username": str(bot.user) if bot.user else None,
                        "updated_at": time.time(),
                    }
                ),
                encoding="utf-8",
            )
            temporary.replace(HEARTBEAT_FILE)
        except Exception:
            logger.exception("Failed to update Discord heartbeat")
        await asyncio.sleep(30)

slash_commands_synced=False
@bot.event
async def on_ready():
    global slash_commands_synced
    logger.info('Logged in as %s',bot.user); logger.info('User ID: %s',bot.user.id); logger.info('Pending approvals available after startup: %s',len(pending_requests))
    if not slash_commands_synced:
        try:
            global_commands=await bot.tree.sync(); guild_commands=await bot.tree.sync(guild=discord.Object(id=LEAGUE_GUILD_ID))
            logger.info('Synced %s global and %s guild slash command(s)',len(global_commands),len(guild_commands)); slash_commands_synced=True
        except discord.HTTPException: logger.exception('Failed to sync slash commands')
    if not league.reconcile_league_roles.is_running():
        league.reconcile_league_roles.start()
    if not hasattr(bot, "heartbeat_task"):
        bot.heartbeat_task = asyncio.create_task(discord_heartbeat())
bot.run(DISCORD_BOT_TOKEN)
