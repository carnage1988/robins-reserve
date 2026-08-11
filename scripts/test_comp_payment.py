import asyncio
import uuid

from sqlalchemy import select

from models import Payment, User
from services.database import AsyncSessionLocal
from services.payment_service import PaymentService


PAYMENT_ID = uuid.UUID("ab5b8007-6a11-4681-a938-b964166135e3")


async def main() -> None:
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(
                select(User).where(
                    User.display_name == "hex: Carnage"
                )
            )
        ).scalar_one()

        payment = await PaymentService.comp_payment(
            session,
            payment_id=PAYMENT_ID,
            confirmed_by=user.id,
            reason="Goodwill",
        )

        await session.commit()

        print(f"Payment: {payment.id}")
        print(f"Status: {payment.status}")
        print(f"Confirmed by: {payment.confirmed_by}")
        print(f"Confirmed at: {payment.confirmed_at}")


if __name__ == "__main__":
    asyncio.run(main())