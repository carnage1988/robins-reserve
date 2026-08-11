import asyncio

from sqlalchemy import select

from models import (
    AuditLog,
    Customer,
    LeagueAttendance,
    LeagueSession,
    Payment,
    User,
)
from services.database import AsyncSessionLocal
from services.league_db_service import LeagueDatabaseService
from services.payment_service import PaymentService


async def main() -> None:
    async with AsyncSessionLocal() as session:
        league_session = (
            await session.execute(
                select(LeagueSession).where(
                    LeagueSession.status == "active"
                )
            )
        ).scalar_one()

        customer = (
            await session.execute(
                select(Customer).where(
                    Customer.display_name == "Test Player"
                )
            )
        ).scalar_one()

        bill = (
            await session.execute(
                select(User).where(
                    User.display_name == "Bill"
                )
            )
        ).scalar_one()

        existing_attendance = (
            await session.execute(
                select(LeagueAttendance).where(
                    LeagueAttendance.league_session_id
                    == league_session.id,
                    LeagueAttendance.customer_id == customer.id,
                )
            )
        ).scalar_one_or_none()

        if existing_attendance is None:
            attendance = await LeagueDatabaseService.check_in_customer(
                session,
                league_session_id=league_session.id,
                customer_id=customer.id,
                checkin_method="discord",
            )
        else:
            attendance = existing_attendance

        existing_payment = (
            await session.execute(
                select(Payment).where(
                    Payment.context_type == "league_attendance",
                    Payment.context_id == attendance.id,
                )
            )
        ).scalar_one_or_none()

        if existing_payment is None:
            payment = await PaymentService.create_cash_due(
                session,
                tenant_id=attendance.tenant_id,
                store_id=attendance.store_id,
                customer_id=attendance.customer_id,
                context_type="league_attendance",
                context_id=attendance.id,
                amount=league_session.entry_fee,
                currency=league_session.currency,
            )
        else:
            payment = existing_payment

        print("After check-in/payment choice:")
        print(f"Attendance ID: {attendance.id}")
        print(f"Payment ID: {payment.id}")
        print(f"Payment method: {payment.method}")
        print(f"Payment status: {payment.status}")

        if payment.status == "cash_due":
            payment = await PaymentService.confirm_cash_payment(
                session,
                payment_id=payment.id,
                confirmed_by=bill.id,
            )

        await session.commit()

        print()
        print("After Bill confirms cash:")
        print(f"Payment status: {payment.status}")
        print(f"Confirmed by: {payment.confirmed_by}")
        print(f"Confirmed at: {payment.confirmed_at}")

        audit_entries = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.entity_id == payment.id)
                .order_by(AuditLog.created_at)
            )
        ).scalars().all()

        print()
        print("Audit entries:")

        for entry in audit_entries:
            print(
                f"- {entry.action} "
                f"old={entry.old_values} "
                f"new={entry.new_values}"
            )


if __name__ == "__main__":
    asyncio.run(main())