from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands

from app.runtime import bot, robincon_service
from config import STAFF_ROLE_ID
from services.robincon_staff_service import RobinConStaffService
from utils.formatting import format_datetime

logger = logging.getLogger(__name__)


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


def _yes_no(value: object) -> str:
    return (
        "✅ Yes"
        if str(value or "").strip().upper() in {"TRUE", "YES", "Y", "1"}
        else "❌ No"
    )


def _ticket_embed(ticket: dict) -> discord.Embed:
    ticket_id = str(ticket.get("Ticket ID", "Unknown"))
    embed = discord.Embed(title=f"🎟 {ticket_id}")
    fields = (
        ("Holder", ticket.get("Ticket Holder Name") or "Unclaimed", True),
        ("Order", ticket.get("Order Number") or "Unknown", True),
        ("Discord", ticket.get("Discord Username") or "Not linked", True),
        ("Status", ticket.get("Ticket Status") or "Not set", True),
        ("Registered", _yes_no(ticket.get("Registration Complete")), True),
        ("Checked In", _yes_no(ticket.get("Checked In")), True),
        ("T-Shirt", ticket.get("T-Shirt Size") or "Not selected", True),
        ("Saturday", ticket.get("Saturday Event Name") or "Not selected", False),
        ("Sunday", ticket.get("Sunday Event Name") or "Not selected", False),
    )
    for name, value, inline in fields:
        embed.add_field(name=name, value=str(value), inline=inline)
    checked_in_at = str(ticket.get("Checked In At", "")).strip()
    if checked_in_at:
        embed.add_field(
            name="Checked In At",
            value=format_datetime(checked_in_at),
            inline=False,
        )
    return embed


def _service() -> RobinConStaffService:
    if robincon_service is None:
        raise RuntimeError("RobinCon is unavailable.")
    return RobinConStaffService(robincon_service)


async def _unexpected_error(
    interaction: discord.Interaction,
    log_message: str,
) -> None:
    logger.exception(log_message)
    await interaction.followup.send(
        "❌ The RobinCon staff operation could not be completed.",
        ephemeral=True,
    )


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
    try:
        ticket = await asyncio.to_thread(_service().ticket, ticket_id)
    except Exception:
        await _unexpected_error(interaction, "Unable to look up RobinCon ticket")
        return
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
    try:
        matches = await asyncio.to_thread(_service().find, query)
    except Exception:
        await _unexpected_error(interaction, "Unable to search RobinCon tickets")
        return
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
    name="robincon-order",
    description="Show every RobinCon ticket belonging to an order.",
)
async def robincon_order(
    interaction: discord.Interaction,
    order_number: str,
) -> None:
    if not await _guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    try:
        tickets = await asyncio.to_thread(_service().order, order_number)
    except Exception:
        await _unexpected_error(interaction, "Unable to look up RobinCon order")
        return
    if not tickets:
        await interaction.followup.send(
            "❌ No tickets were found for that order.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"🧾 RobinCon Order {order_number.strip()}",
        description=f"**{len(tickets)} ticket(s)**",
    )
    for ticket in tickets[:20]:
        holder = ticket.get("Ticket Holder Name") or "Unclaimed"
        state = (
            "✅ Registered"
            if _yes_no(ticket.get("Registration Complete")).startswith("✅")
            else "⏳ Registration required"
        )
        linked = _yes_no(ticket.get("Linked"))
        embed.add_field(
            name=str(ticket.get("Ticket ID", "Unknown")),
            value=f"**{holder}**\n{state}\nLinked: {linked}",
            inline=False,
        )
    if len(tickets) > 20:
        embed.set_footer(text=f"Showing 20 of {len(tickets)} tickets")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(
    name="robincon-tshirts",
    description="Show RobinCon T-shirt totals.",
)
async def robincon_tshirts(interaction: discord.Interaction) -> None:
    if not await _guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    try:
        counts = await asyncio.to_thread(_service().tshirt_counts)
    except Exception:
        await _unexpected_error(interaction, "Unable to calculate T-shirt totals")
        return
    text = "\n".join(
        f"**{size}:** {count}" for size, count in sorted(counts.items())
    ) or "No sizes selected."
    await interaction.followup.send(text, ephemeral=True)


@bot.tree.command(
    name="robincon-capacity",
    description="Show premium event capacities.",
)
async def robincon_capacity(interaction: discord.Interaction) -> None:
    if not await _guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    try:
        report = await asyncio.to_thread(_service().capacity)
    except Exception:
        await _unexpected_error(interaction, "Unable to calculate event capacities")
        return
    parts: list[str] = []
    for day, events in report.items():
        parts.append(f"**{day}**")
        if not events:
            parts.append("No active events.")
            continue
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
    try:
        rows = await asyncio.to_thread(_service().attendees, day.value)
    except Exception:
        await _unexpected_error(interaction, "Unable to list RobinCon attendees")
        return
    field = f"{day.value} Event Name"
    text = "\n".join(
        f"{record.get('Ticket Holder Name') or record.get('Discord Username') or record.get('Ticket ID')}"
        f" — {record.get(field)}"
        for record in rows[:50]
    ) or "No attendees registered."
    if len(rows) > 50:
        text += f"\n\nShowing 50 of {len(rows)} attendees."
    await interaction.followup.send(text, ephemeral=True)


@bot.tree.command(
    name="robincon-summary",
    description="Show overall RobinCon operational totals.",
)
async def robincon_summary(interaction: discord.Interaction) -> None:
    if not await _guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    try:
        summary = await asyncio.to_thread(_service().summary)
    except Exception:
        await _unexpected_error(interaction, "Unable to build RobinCon summary")
        return

    embed = discord.Embed(title="📊 RobinCon Summary")
    embed.add_field(name="Orders", value=str(summary["orders"]), inline=True)
    embed.add_field(name="Tickets", value=str(summary["tickets"]), inline=True)
    embed.add_field(name="Unclaimed", value=str(summary["unclaimed"]), inline=True)
    embed.add_field(name="Linked", value=str(summary["linked"]), inline=True)
    embed.add_field(
        name="Registered", value=str(summary["registered"]), inline=True
    )
    embed.add_field(
        name="Checked In", value=str(summary["checked_in"]), inline=True
    )
    for day, totals in summary["days"].items():
        registered = int(totals["registered"])
        capacity = int(totals["capacity"])
        percentage = round((registered / capacity) * 100) if capacity else 0
        embed.add_field(
            name=f"{day} Events",
            value=f"**{registered} / {capacity}** ({percentage}%)",
            inline=True,
        )
    tshirts = summary["tshirts"]
    embed.add_field(
        name="T-Shirts",
        value=(
            "\n".join(f"**{size}:** {count}" for size, count in sorted(tshirts.items()))
            or "No selections yet."
        ),
        inline=False,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


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
        ticket = await asyncio.to_thread(
            _service().check_in,
            ticket_id,
            interaction.user.id,
            str(interaction.user),
        )
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        await _unexpected_error(interaction, "Unable to check in RobinCon ticket")
        return
    await interaction.followup.send(
        f"✅ Ticket `{ticket.get('Ticket ID')}` checked in for "
        f"**{ticket.get('Ticket Holder Name') or 'Unclaimed attendee'}**.",
        ephemeral=True,
    )


@bot.tree.command(
    name="robincon-uncheckin",
    description="Reverse an accidental RobinCon ticket check-in.",
)
async def robincon_uncheckin(
    interaction: discord.Interaction,
    ticket_id: str,
) -> None:
    if not await _guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    try:
        ticket = await asyncio.to_thread(
            _service().uncheck_in,
            ticket_id,
            interaction.user.id,
            str(interaction.user),
        )
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        await _unexpected_error(
            interaction, "Unable to reverse RobinCon ticket check-in"
        )
        return
    await interaction.followup.send(
        f"↩️ Check-in reversed for ticket `{ticket.get('Ticket ID')}`.",
        ephemeral=True,
    )


@bot.tree.command(
    name="robincon-edit",
    description="Correct an attendee name, shirt size, or premium event.",
)
@app_commands.choices(
    field=[
        app_commands.Choice(name="Attendee name", value="attendee-name"),
        app_commands.Choice(name="T-shirt size", value="tshirt"),
        app_commands.Choice(name="Saturday event", value="saturday"),
        app_commands.Choice(name="Sunday event", value="sunday"),
    ]
)
async def robincon_edit(
    interaction: discord.Interaction,
    ticket_id: str,
    field: app_commands.Choice[str],
    value: str,
) -> None:
    if not await _guard(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    try:
        ticket = await asyncio.to_thread(
            _service().edit_ticket,
            ticket_id=ticket_id,
            field=field.value,
            value=value,
            staff_id=interaction.user.id,
            staff_name=str(interaction.user),
        )
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        await _unexpected_error(interaction, "Unable to edit RobinCon ticket")
        return
    await interaction.followup.send(
        content="✅ Ticket updated and the change was added to the audit log.",
        embed=_ticket_embed(ticket),
        ephemeral=True,
    )
