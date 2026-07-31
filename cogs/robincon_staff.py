from __future__ import annotations

import discord
from discord import app_commands

from app.runtime import bot, robincon_service
from config import STAFF_ROLE_ID
from services.robincon_staff_service import RobinConStaffService


def _is_staff(interaction: discord.Interaction) -> bool:
    return (
        interaction.guild is not None
        and isinstance(interaction.user, discord.Member)
        and any(role.id == STAFF_ROLE_ID for role in interaction.user.roles)
    )


async def _guard(interaction: discord.Interaction) -> bool:
    if not _is_staff(interaction):
        await interaction.response.send_message(
            "❌ This command is available to RobinCon staff only.",
            ephemeral=True,
        )
        return False
    if robincon_service is None:
        await interaction.response.send_message(
            "❌ RobinCon is temporarily unavailable.",
            ephemeral=True,
        )
        return False
    return True


def _ticket_embed(ticket: dict) -> discord.Embed:
    embed = discord.Embed(title=f"🎟 {ticket.get('Ticket ID', 'Unknown')}")
    for name, key in (
        ("Holder", "Ticket Holder Name"),
        ("Order", "Order Number"),
        ("Discord", "Discord Username"),
        ("Status", "Ticket Status"),
        ("T-Shirt", "T-Shirt Size"),
        ("Saturday", "Saturday Event Name"),
        ("Sunday", "Sunday Event Name"),
        ("Checked In", "Checked In"),
    ):
        embed.add_field(
            name=name,
            value=str(ticket.get(key, "") or "Not set"),
            inline=name not in {"Saturday", "Sunday"},
        )
    return embed


@bot.tree.command(
    name="robincon-ticket",
    description="Look up a RobinCon ticket by ticket ID.",
)
async def robincon_ticket(
    interaction: discord.Interaction,
    ticket_id: str,
) -> None:
    if not await _guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    ticket = RobinConStaffService(robincon_service).ticket(ticket_id)
    if ticket is None:
        await interaction.followup.send("❌ Ticket not found.", ephemeral=True)
        return
    await interaction.followup.send(embed=_ticket_embed(ticket), ephemeral=True)


@bot.tree.command(name="robincon-find", description="Search RobinCon tickets.")
async def robincon_find(
    interaction: discord.Interaction,
    query: str,
) -> None:
    if not await _guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    matches = RobinConStaffService(robincon_service).find(query)
    if not matches:
        await interaction.followup.send("❌ No matching tickets.", ephemeral=True)
        return
    text = "\n".join(
        f"`{record.get('Ticket ID')}` — "
        f"{record.get('Ticket Holder Name') or 'Unclaimed'} — "
        f"{record.get('Order Number')}"
        for record in matches
    )
    await interaction.followup.send(text, ephemeral=True)


@bot.tree.command(
    name="robincon-tshirts",
    description="Show RobinCon T-shirt totals.",
)
async def robincon_tshirts(interaction: discord.Interaction) -> None:
    if not await _guard(interaction):
        return
    counts = RobinConStaffService(robincon_service).tshirt_counts()
    text = "\n".join(
        f"**{size}:** {count}"
        for size, count in sorted(counts.items())
    ) or "No sizes selected."
    await interaction.response.send_message(text, ephemeral=True)


@bot.tree.command(
    name="robincon-capacity",
    description="Show premium event capacities.",
)
async def robincon_capacity(interaction: discord.Interaction) -> None:
    if not await _guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    report = RobinConStaffService(robincon_service).capacity()
    parts: list[str] = []
    for day, events in report.items():
        parts.append(f"**{day}**")
        parts.extend(
            f"{event.get('Event Name')}: "
            f"**{event.get('Registered', 0)} / {event.get('Capacity', 0)}**"
            for event in events
        )
    await interaction.followup.send("\n".join(parts), ephemeral=True)


@bot.tree.command(
    name="robincon-attendees",
    description="List registered RobinCon attendees for a day.",
)
@app_commands.choices(
    day=[
        app_commands.Choice(name="Saturday", value="Saturday"),
        app_commands.Choice(name="Sunday", value="Sunday"),
    ]
)
async def robincon_attendees(
    interaction: discord.Interaction,
    day: app_commands.Choice[str],
) -> None:
    if not await _guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    rows = RobinConStaffService(robincon_service).attendees(day.value)
    field = f"{day.value} Event Name"
    text = "\n".join(
        f"{record.get('Ticket Holder Name') or record.get('Discord Username') or record.get('Ticket ID')}"
        f" — {record.get(field)}"
        for record in rows[:50]
    ) or "No attendees registered."
    await interaction.followup.send(text, ephemeral=True)


@bot.tree.command(
    name="robincon-checkin",
    description="Manually check in a RobinCon ticket.",
)
async def robincon_checkin(
    interaction: discord.Interaction,
    ticket_id: str,
) -> None:
    if not await _guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    try:
        ticket = RobinConStaffService(robincon_service).check_in(
            ticket_id,
            interaction.user.id,
            str(interaction.user),
        )
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    await interaction.followup.send(
        f"✅ Ticket `{ticket.get('Ticket ID')}` checked in.",
        ephemeral=True,
    )
