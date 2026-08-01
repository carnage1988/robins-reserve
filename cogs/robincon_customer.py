from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord

from app.runtime import bot, robincon_service
from views.robincon import (
    RobinConLinkModal,
    RobinConManagedTicketSelectView,
    start_robincon_registration,
)

logger = logging.getLogger(__name__)


@bot.tree.command(
    name="robincon-register",
    description="Register one of the RobinCon tickets managed by your Discord account.",
)
async def robincon_register(interaction: discord.Interaction) -> None:
    """Choose and register one ticket managed by the current Discord account."""

    if robincon_service is None:
        await interaction.response.send_message(
            "❌ RobinCon is temporarily unavailable.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    try:
        tickets = await asyncio.to_thread(
            robincon_service.get_linked_tickets_with_rows,
            interaction.user.id,
        )
    except Exception:
        logger.exception("Unable to retrieve managed RobinCon tickets")
        await interaction.followup.send(
            "❌ Your RobinCon tickets could not be retrieved.",
            ephemeral=True,
        )
        return

    if not tickets:
        await interaction.followup.send(
            "❌ You must link a RobinCon ticket before registering. "
            "Use `/robincon-link` first.",
            ephemeral=True,
        )
        return

    if len(tickets) == 1:
        await start_robincon_registration(
            interaction,
            tickets[0],
            edit_original=False,
        )
        return

    await interaction.followup.send(
        (
            "**Choose an attendee to register**\n\n"
            "Your Discord account manages more than one RobinCon ticket. "
            "Select the ticket you want to view or complete."
        ),
        view=RobinConManagedTicketSelectView(
            tickets=tickets,
            owner_id=interaction.user.id,
        ),
        ephemeral=True,
    )


@bot.tree.command(
    name="robincon-link",
    description="Link a paid RobinCon ticket for yourself or another attendee.",
)
async def robincon_link(interaction: discord.Interaction) -> None:
    """Open the private RobinCon order-verification modal."""

    if robincon_service is None:
        await interaction.response.send_message(
            "❌ RobinCon is temporarily unavailable.",
            ephemeral=True,
        )
        return

    await interaction.response.send_modal(RobinConLinkModal())


@bot.tree.command(
    name="robincon-status",
    description="Check the RobinCon service connection.",
)
async def robincon_status(interaction: discord.Interaction) -> None:
    """Show the current RobinCon workbook connection status."""

    if robincon_service is None:
        await interaction.response.send_message(
            "❌ RobinCon is not connected. Check the bot logs.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    try:
        status: dict[str, Any] = await asyncio.to_thread(robincon_service.get_status)
    except Exception:
        logger.exception("Unable to retrieve RobinCon status")
        await interaction.followup.send(
            "❌ The RobinCon workbook could not be read. Check the bot logs.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="🎟️ RobinCon Service Status",
        description=f"**{status['robincon_name']}**",
    )
    embed.add_field(name="Workbook", value="✅ Connected", inline=True)
    embed.add_field(
        name="Configuration Values",
        value=str(status["configuration_count"]),
        inline=True,
    )
    embed.add_field(
        name="Active Ticket Types",
        value=str(status["ticket_type_count"]),
        inline=True,
    )
    embed.add_field(
        name="Enabled T-Shirt Sizes",
        value=str(status["tshirt_size_count"]),
        inline=True,
    )
    embed.add_field(
        name="Saturday Premium Events",
        value=str(status["saturday_event_count"]),
        inline=True,
    )
    embed.add_field(
        name="Sunday Premium Events",
        value=str(status["sunday_event_count"]),
        inline=True,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)
