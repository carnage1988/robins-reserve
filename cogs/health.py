"""Staff-facing operational health and cache diagnostics."""
from __future__ import annotations

import discord
from discord import app_commands

from app.runtime import bot, league_service, robincon_service, sheets
from config import STAFF_ROLE_ID
from services.sheets_resilience import (
    clear_sheets_cache,
    get_sheets_diagnostics,
)


def _is_staff(interaction: discord.Interaction) -> bool:
    return (
        interaction.guild is not None
        and isinstance(interaction.user, discord.Member)
        and any(role.id == STAFF_ROLE_ID for role in interaction.user.roles)
    )


async def _guard(interaction: discord.Interaction) -> bool:
    if _is_staff(interaction):
        return True
    await interaction.response.send_message(
        "❌ This command is available to staff only.",
        ephemeral=True,
    )
    return False


def _state(value: object | None) -> str:
    return "✅ Available" if value is not None else "❌ Unavailable"


@bot.tree.command(name="health", description="Show Robin's Reserve health status.")
async def health(interaction: discord.Interaction) -> None:
    if not await _guard(interaction):
        return

    diagnostics = get_sheets_diagnostics()
    league_task = None
    try:
        from cogs.league import reconcile_league_roles
        league_task = reconcile_league_roles
    except ImportError:
        pass

    embed = discord.Embed(title="🩺 Robin's Reserve Health")
    embed.add_field(name="Discord", value="✅ Connected", inline=True)
    embed.add_field(name="Preorder Sheets", value=_state(sheets), inline=True)
    embed.add_field(name="League", value=_state(league_service), inline=True)
    embed.add_field(name="RobinCon", value=_state(robincon_service), inline=True)
    embed.add_field(
        name="League Task",
        value=(
            "✅ Running"
            if league_task is not None and league_task.is_running()
            else "⚠️ Not running"
        ),
        inline=True,
    )
    embed.add_field(
        name="Sheets API",
        value=(
            f"Requests: **{diagnostics['requests']}**\n"
            f"Retries: **{diagnostics['retries']}**\n"
            f"429s: **{diagnostics['rate_limits']}**\n"
            f"Failures: **{diagnostics['failures']}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Cache",
        value=(
            f"Entries: **{diagnostics['cache_entries']}**\n"
            f"Hits: **{diagnostics['cache_hits']}**\n"
            f"Misses: **{diagnostics['cache_misses']}**"
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="cache-status", description="Show Google Sheets cache status.")
async def cache_status(interaction: discord.Interaction) -> None:
    if not await _guard(interaction):
        return
    data = get_sheets_diagnostics()
    await interaction.response.send_message(
        "**Google Sheets Cache**\n"
        f"Entries: `{data['cache_entries']}`\n"
        f"Hits: `{data['cache_hits']}`\n"
        f"Misses: `{data['cache_misses']}`\n"
        f"Invalidations: `{data['invalidations']}`\n"
        f"Last HTTP status: `{data['last_status'] or 'None'}`",
        ephemeral=True,
    )


@bot.tree.command(name="cache-clear", description="Clear cached Google Sheets reads.")
@app_commands.default_permissions(administrator=True)
async def cache_clear(interaction: discord.Interaction) -> None:
    if not await _guard(interaction):
        return
    removed = clear_sheets_cache()
    await interaction.response.send_message(
        f"✅ Cleared **{removed}** cached Sheets entries.",
        ephemeral=True,
    )
