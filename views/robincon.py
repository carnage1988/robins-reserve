from __future__ import annotations
import logging
import asyncio
from typing import Any
from utils.formatting import format_datetime
import discord
from app.runtime import robincon_service
logger=logging.getLogger(__name__)

def build_robincon_linked_embed(ticket: dict[str, Any]) -> discord.Embed:
    """Build the private confirmation shown after a ticket is linked."""

    embed = discord.Embed(
        title="✅ RobinCon Ticket Linked",
        description=(
            "Your Discord account is now linked to this RobinCon ticket."
        ),
    )
    embed.add_field(
        name="Ticket ID",
        value=f"`{ticket.get('Ticket ID', 'Unknown')}`",
        inline=True,
    )
    embed.add_field(
        name="Ticket Type",
        value=str(ticket.get("Ticket Type", "Unknown")),
        inline=True,
    )
    embed.add_field(
        name="Premium Event Allowance",
        value=str(ticket.get("Premium Event Allowance", 0)),
        inline=True,
    )
    embed.add_field(
        name="Next Step",
        value=(
            "Run `/robincon-register` to choose your RobinCon T-shirt size. "
            "Premium-event selection will follow in the next stage."
        ),
        inline=False,
    )
    return embed


class RobinConTicketSelect(discord.ui.Select):
    """Allow a purchaser to choose one unlinked ticket from a larger order."""

    def __init__(
        self,
        *,
        tickets: list[dict[str, Any]],
        owner_id: int,
        holder_name: str,
        holder_email: str,
    ) -> None:
        self.owner_id = owner_id
        self.holder_name = holder_name
        self.holder_email = holder_email

        options = [
            discord.SelectOption(
                label=(
                    f"Ticket {ticket.get('Ticket Number', '?')} — "
                    f"{ticket.get('Ticket Type', 'Ticket')}"
                )[:100],
                value=str(ticket.get("Ticket ID", "")),
                description=f"Ticket ID: {ticket.get('Ticket ID', 'Unknown')}"[:100],
            )
            for ticket in tickets[:25]
        ]

        super().__init__(
            placeholder="Choose the ticket you want to link",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Only the person who started this link can choose a ticket.",
                ephemeral=True,
            )
            return

        if robincon_service is None:
            await interaction.response.send_message(
                "❌ RobinCon is temporarily unavailable.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            ticket = await asyncio.to_thread(
                robincon_service.link_ticket,
                ticket_id=self.values[0],
                discord_user_id=interaction.user.id,
                discord_username=str(interaction.user),
                holder_name=self.holder_name,
                holder_email=self.holder_email,
            )
        except ValueError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        except Exception:
            logger.exception("RobinCon multi-ticket link failed")
            await interaction.followup.send(
                "❌ The selected ticket could not be linked.",
                ephemeral=True,
            )
            return

        await interaction.edit_original_response(
            content=None,
            embed=build_robincon_linked_embed(ticket),
            view=None,
        )


class RobinConTicketSelectView(discord.ui.View):
    """Private selector view for an order containing multiple tickets."""

    def __init__(
        self,
        *,
        tickets: list[dict[str, Any]],
        owner_id: int,
        holder_name: str,
        holder_email: str,
    ) -> None:
        super().__init__(timeout=300)
        self.add_item(
            RobinConTicketSelect(
                tickets=tickets,
                owner_id=owner_id,
                holder_name=holder_name,
                holder_email=holder_email,
            )
        )


class RobinConLinkModal(discord.ui.Modal, title="Link RobinCon Ticket"):
    """Collect private order details used to verify ticket ownership."""

    order_number = discord.ui.TextInput(
        label="Order Number",
        placeholder="Enter the order number from your confirmation email",
        min_length=1,
        max_length=100,
    )
    customer_email = discord.ui.TextInput(
        label="Purchaser Email",
        placeholder="Enter the email used for the purchase",
        min_length=3,
        max_length=254,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if robincon_service is None:
            await interaction.response.send_message(
                "❌ RobinCon is temporarily unavailable.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            order, tickets = await asyncio.to_thread(
                robincon_service.prepare_ticket_link,
                order_number=self.order_number.value,
                customer_email=self.customer_email.value,
                discord_user_id=interaction.user.id,
            )
        except ValueError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        except Exception:
            logger.exception("RobinCon order verification failed")
            await interaction.followup.send(
                "❌ The RobinCon order could not be verified.",
                ephemeral=True,
            )
            return

        holder_name = str(order.get("Customer Name", "")).strip()
        if not holder_name:
            holder_name = interaction.user.display_name
        holder_email = str(order.get("Customer Email", "")).strip()

        if len(tickets) == 1:
            try:
                ticket = await asyncio.to_thread(
                    robincon_service.link_ticket,
                    ticket_id=str(tickets[0].get("Ticket ID", "")),
                    discord_user_id=interaction.user.id,
                    discord_username=str(interaction.user),
                    holder_name=holder_name,
                    holder_email=holder_email,
                )
            except ValueError as exc:
                await interaction.followup.send(f"❌ {exc}", ephemeral=True)
                return
            except Exception:
                logger.exception("RobinCon ticket link failed")
                await interaction.followup.send(
                    "❌ Your RobinCon ticket could not be linked.",
                    ephemeral=True,
                )
                return

            await interaction.followup.send(
                embed=build_robincon_linked_embed(ticket),
                ephemeral=True,
            )
            return

        view = RobinConTicketSelectView(
            tickets=tickets,
            owner_id=interaction.user.id,
            holder_name=holder_name,
            holder_email=holder_email,
        )
        await interaction.followup.send(
            (
                f"✅ Order `{order.get('Order Number', 'Unknown')}` was verified.\n\n"
                "This order contains multiple unlinked tickets. Choose the "
                "ticket you want to link to your Discord account."
            ),
            view=view,
            ephemeral=True,
        )


def build_robincon_registration_complete_embed(
    ticket: dict[str, Any],
) -> discord.Embed:
    """Build the locked registration summary shown to an attendee."""

    embed = discord.Embed(
        title="✅ RobinCon Registration Complete",
        description=(
            f"Ticket `{ticket.get('Ticket ID', 'Unknown')}` is fully "
            "registered and the selections are now locked."
        ),
    )
    embed.add_field(
        name="T-Shirt",
        value=str(ticket.get("T-Shirt Size", "Not selected")) or "Not selected",
        inline=False,
    )
    embed.add_field(
        name="Saturday Premium Event",
        value=str(ticket.get("Saturday Event Name", "Not selected"))
        or "Not selected",
        inline=False,
    )
    embed.add_field(
        name="Sunday Premium Event",
        value=str(ticket.get("Sunday Event Name", "Not selected"))
        or "Not selected",
        inline=False,
    )
    completed_at = str(ticket.get("Registration Completed At", "")).strip()
    if completed_at:
        embed.add_field(
            name="Completed",
            value=format_datetime(completed_at),
            inline=False,
        )
    embed.set_footer(
        text="Contact Robin's staff if a correction is required."
    )
    return embed


class RobinConRegistrationState:
    """Hold one attendee's in-progress registration choices."""

    def __init__(
        self,
        *,
        owner_id: int,
        ticket: dict[str, Any],
        sizes: list[dict[str, Any]],
        saturday_events: list[dict[str, Any]],
        sunday_events: list[dict[str, Any]],
    ) -> None:
        self.owner_id = owner_id
        self.ticket = ticket
        self.sizes = sizes
        self.saturday_events = saturday_events
        self.sunday_events = sunday_events
        self.tshirt_size_id = ""
        self.tshirt_size_name = ""
        self.saturday_event_id = ""
        self.saturday_event_name = ""
        self.sunday_event_id = ""
        self.sunday_event_name = ""


class RobinConRegistrationSelect(discord.ui.Select):
    """Base select component for the guided registration wizard."""

    def __init__(
        self,
        *,
        state: RobinConRegistrationState,
        step: str,
        options: list[discord.SelectOption],
        placeholder: str,
    ) -> None:
        self.state = state
        self.step = step
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.state.owner_id:
            await interaction.response.send_message(
                "❌ Only the attendee who opened this registration can use it.",
                ephemeral=True,
            )
            return

        selected_value = self.values[0]
        if self.step == "tshirt":
            selected = next(
                (
                    item
                    for item in self.state.sizes
                    if str(item.get("Size ID", "")) == selected_value
                ),
                None,
            )
            if selected is None:
                await interaction.response.send_message(
                    "❌ That T-shirt size is no longer available.",
                    ephemeral=True,
                )
                return
            self.state.tshirt_size_id = selected_value
            self.state.tshirt_size_name = str(
                selected.get("Display Name", selected_value)
            )
            await interaction.response.edit_message(
                content=(
                    "**RobinCon Registration — Step 2 of 4**\n\n"
                    f"✅ T-shirt: **{self.state.tshirt_size_name}**\n\n"
                    "Choose one Saturday premium event."
                ),
                embed=None,
                view=RobinConSaturdayEventView(self.state),
            )
            return

        if self.step == "saturday":
            selected = next(
                (
                    item
                    for item in self.state.saturday_events
                    if str(item.get("Event ID", "")) == selected_value
                ),
                None,
            )
            if selected is None:
                await interaction.response.send_message(
                    "❌ That Saturday event is no longer available.",
                    ephemeral=True,
                )
                return
            self.state.saturday_event_id = selected_value
            self.state.saturday_event_name = str(
                selected.get("Event Name", selected_value)
            )
            await interaction.response.edit_message(
                content=(
                    "**RobinCon Registration — Step 3 of 4**\n\n"
                    f"✅ T-shirt: **{self.state.tshirt_size_name}**\n"
                    f"✅ Saturday: **{self.state.saturday_event_name}**\n\n"
                    "Choose one Sunday premium event."
                ),
                embed=None,
                view=RobinConSundayEventView(self.state),
            )
            return

        selected = next(
            (
                item
                for item in self.state.sunday_events
                if str(item.get("Event ID", "")) == selected_value
            ),
            None,
        )
        if selected is None:
            await interaction.response.send_message(
                "❌ That Sunday event is no longer available.",
                ephemeral=True,
            )
            return
        self.state.sunday_event_id = selected_value
        self.state.sunday_event_name = str(
            selected.get("Event Name", selected_value)
        )
        review = discord.Embed(
            title="📋 RobinCon Registration Review",
            description=(
                f"Ticket `{self.state.ticket.get('Ticket ID', 'Unknown')}`\n\n"
                "Review the choices below carefully. Once confirmed, they "
                "will be locked and cannot be changed through Discord."
            ),
        )
        review.add_field(
            name="T-Shirt",
            value=self.state.tshirt_size_name,
            inline=False,
        )
        review.add_field(
            name="Saturday Premium Event",
            value=self.state.saturday_event_name,
            inline=False,
        )
        review.add_field(
            name="Sunday Premium Event",
            value=self.state.sunday_event_name,
            inline=False,
        )
        await interaction.response.edit_message(
            content="**RobinCon Registration — Step 4 of 4**",
            embed=review,
            view=RobinConRegistrationConfirmView(self.state),
        )


class RobinConTShirtStepView(discord.ui.View):
    def __init__(self, state: RobinConRegistrationState) -> None:
        super().__init__(timeout=600)
        current_size = str(state.ticket.get("T-Shirt Size", "")).strip()
        options = [
            discord.SelectOption(
                label=str(size.get("Display Name", size.get("Size ID", "Size")))[:100],
                value=str(size.get("Size ID", "")),
                default=(
                    bool(current_size)
                    and str(size.get("Display Name", "")).strip() == current_size
                ),
            )
            for size in state.sizes
        ]
        self.add_item(
            RobinConRegistrationSelect(
                state=state,
                step="tshirt",
                options=options,
                placeholder="Choose your RobinCon T-shirt size",
            )
        )


class RobinConSaturdayEventView(discord.ui.View):
    def __init__(self, state: RobinConRegistrationState) -> None:
        super().__init__(timeout=600)
        options = [
            discord.SelectOption(
                label=str(event.get("Event Name", "Saturday Event"))[:100],
                value=str(event.get("Event ID", "")),
                description=(
                    f"{event.get('Start Time', '')}–{event.get('End Time', '')}"
                )[:100],
            )
            for event in state.saturday_events
        ]
        self.add_item(
            RobinConRegistrationSelect(
                state=state,
                step="saturday",
                options=options,
                placeholder="Choose one Saturday premium event",
            )
        )


class RobinConSundayEventView(discord.ui.View):
    def __init__(self, state: RobinConRegistrationState) -> None:
        super().__init__(timeout=600)
        options = [
            discord.SelectOption(
                label=str(event.get("Event Name", "Sunday Event"))[:100],
                value=str(event.get("Event ID", "")),
                description=(
                    f"{event.get('Start Time', '')}–{event.get('End Time', '')}"
                )[:100],
            )
            for event in state.sunday_events
        ]
        self.add_item(
            RobinConRegistrationSelect(
                state=state,
                step="sunday",
                options=options,
                placeholder="Choose one Sunday premium event",
            )
        )


class RobinConRegistrationConfirmView(discord.ui.View):
    def __init__(self, state: RobinConRegistrationState) -> None:
        super().__init__(timeout=600)
        self.state = state

    @discord.ui.button(
        label="Confirm and Lock Registration",
        style=discord.ButtonStyle.success,
        emoji="✅",
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.user.id != self.state.owner_id:
            await interaction.response.send_message(
                "❌ Only the attendee who opened this registration can confirm it.",
                ephemeral=True,
            )
            return
        if robincon_service is None:
            await interaction.response.send_message(
                "❌ RobinCon is temporarily unavailable.",
                ephemeral=True,
            )
            return

        button.disabled = True
        await interaction.response.edit_message(view=self)
        try:
            ticket = await asyncio.to_thread(
                robincon_service.complete_registration,
                discord_user_id=interaction.user.id,
                discord_username=str(interaction.user),
                tshirt_size_id=self.state.tshirt_size_id,
                saturday_event_id=self.state.saturday_event_id,
                sunday_event_id=self.state.sunday_event_id,
            )
        except ValueError as exc:
            button.disabled = False
            await interaction.edit_original_response(view=self)
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        except Exception:
            button.disabled = False
            logger.exception("RobinCon registration confirmation failed")
            await interaction.edit_original_response(view=self)
            await interaction.followup.send(
                "❌ Your RobinCon registration could not be completed.",
                ephemeral=True,
            )
            return

        await interaction.edit_original_response(
            content=None,
            embed=build_robincon_registration_complete_embed(ticket),
            view=None,
        )


