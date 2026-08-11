import uuid
from decimal import Decimal

import discord

from services.database import AsyncSessionLocal
from services.payment_service import PaymentService


class LeaguePaymentView(discord.ui.View):
    def __init__(
        self,
        *,
        tenant_id: uuid.UUID,
        store_id: uuid.UUID,
        customer_id: uuid.UUID,
        attendance_id: uuid.UUID,
        amount: Decimal,
        currency: str,
    ) -> None:
        super().__init__(timeout=900)

        self.tenant_id = tenant_id
        self.store_id = store_id
        self.customer_id = customer_id
        self.attendance_id = attendance_id
        self.amount = amount
        self.currency = currency

    @discord.ui.button(
        label="Pay Online",
        emoji="💳",
        style=discord.ButtonStyle.primary,
    )
    async def pay_online(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message(
            "💳 Online payment will be enabled when Stripe is connected.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Pay Cash",
        emoji="💷",
        style=discord.ButtonStyle.secondary,
    )
    async def pay_cash(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        async with AsyncSessionLocal() as session:
            payment = await PaymentService.create_cash_due(
                session,
                tenant_id=self.tenant_id,
                store_id=self.store_id,
                customer_id=self.customer_id,
                context_type="league_attendance",
                context_id=self.attendance_id,
                amount=self.amount,
                currency=self.currency,
            )

            await session.commit()

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=(
                "✅ **League check-in confirmed.**\n\n"
                f"Entry Fee: **£{self.amount:.2f}**\n"
                "Payment: 💷 **Cash Due**\n\n"
                "Please pay at the counter. "
                "Your payment will be marked as paid once received.\n\n"
                f"Payment Reference: `{payment.id}`"
            ),
            view=self,
        )