from __future__ import annotations
import logging
import asyncio
import discord
from app.runtime import bot, robincon_service
from views.robincon import RobinConLinkModal, RobinConRegistrationState, RobinConTShirtStepView, build_robincon_registration_complete_embed
logger=logging.getLogger(__name__)

@bot.tree.command(
    name="robincon-register",
    description="Complete registration for your linked RobinCon ticket.",
)
async def robincon_register(interaction: discord.Interaction) -> None:
    """Open the complete private RobinCon registration wizard."""

    if robincon_service is None:
        await interaction.response.send_message(
            "❌ RobinCon is temporarily unavailable.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    try:
        ticket = await asyncio.to_thread(
            robincon_service.get_linked_ticket_with_row,
            interaction.user.id,
        )
        if ticket is None:
            raise ValueError(
                "You must link a RobinCon ticket before registering. "
                "Use `/robincon-link` first."
            )
        if str(ticket.get("Registration Complete", "")).strip().upper() == "TRUE":
            await interaction.followup.send(
                embed=build_robincon_registration_complete_embed(ticket),
                ephemeral=True,
            )
            return
        if not await asyncio.to_thread(
            robincon_service.is_tshirt_selection_open
        ):
            raise ValueError("RobinCon T-shirt selection is currently closed.")
        if not await asyncio.to_thread(
            robincon_service.is_premium_event_registration_open
        ):
            raise ValueError(
                "RobinCon premium-event registration is currently closed."
            )
        sizes, saturday_events, sunday_events = await asyncio.gather(
            asyncio.to_thread(robincon_service.get_enabled_tshirt_sizes),
            asyncio.to_thread(robincon_service.get_active_saturday_events),
            asyncio.to_thread(robincon_service.get_active_sunday_events),
        )
        if not sizes:
            raise ValueError("No RobinCon T-shirt sizes are currently available.")
        if not saturday_events:
            raise ValueError("No Saturday premium events are currently available.")
        if not sunday_events:
            raise ValueError("No Sunday premium events are currently available.")
        if len(sizes) > 25 or len(saturday_events) > 25 or len(sunday_events) > 25:
            raise ValueError(
                "A registration list contains more than Discord's 25-option limit."
            )
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("Unable to start RobinCon registration")
        await interaction.followup.send(
            "❌ RobinCon registration could not be started.",
            ephemeral=True,
        )
        return

    state = RobinConRegistrationState(
        owner_id=interaction.user.id,
        ticket=ticket,
        sizes=sizes,
        saturday_events=saturday_events,
        sunday_events=sunday_events,
    )
    current_size = str(ticket.get("T-Shirt Size", "")).strip()
    message = (
        "**RobinCon Registration — Step 1 of 4**\n\n"
        f"✅ Ticket verified: `{ticket.get('Ticket ID', 'Unknown')}`\n\n"
        "Choose the T-shirt size included with your ticket."
    )
    if current_size:
        message += f"\n\nCurrent saved size: **{current_size}**"

    await interaction.followup.send(
        message,
        view=RobinConTShirtStepView(state),
        ephemeral=True,
    )


@bot.tree.command(
    name="robincon-link",
    description="Link a paid RobinCon ticket to your Discord account.",
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
async def robincon_status(
    interaction: discord.Interaction,
) -> None:
    """Show the current RobinCon workbook connection status."""

    if robincon_service is None:
        await interaction.response.send_message(
            "❌ RobinCon is not connected. Check the bot logs.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        status = robincon_service.get_status()
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
    embed.add_field(
        name="Workbook",
        value="✅ Connected",
        inline=True,
    )
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

    await interaction.followup.send(
        embed=embed,
        ephemeral=True,
    )


