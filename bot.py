from pathlib import Path
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
    if not league.reconcile_league_roles.is_running(): league.reconcile_league_roles.start()
bot.run(DISCORD_BOT_TOKEN)
