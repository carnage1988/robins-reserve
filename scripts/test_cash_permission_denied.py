import asyncio
import uuid

from sqlalchemy import select

from models import (
    Customer,
    LeagueAttendance,
    LeagueSession,
    Payment,
    Role,
    Store,
    Tenant,
    User,
    UserRole,
)
from services.database import AsyncSessionLocal
from services.payment_service import PaymentService


async def main() -> None:
    async with AsyncSessionLocal() as session:
        tenant = (
            await session.execute(
                select(Tenant).where(Tenant.slug == "robins")
            )
        ).scalar_one()

        store = (
            await session.execute(
                select(Store).where(
                    Store.tenant_id == tenant.id,
                    Store.code == "BELFAST",
                )
            )
        ).scalar_one()

        staff_role = (
            await session.execute(
                select(Role).where(
                    Role.tenant_id == tenant.id,
                    Role.name == "Staff",
                )
            )
        ).scalar_one_or_none()

        if staff_role is None:
            staff_role = Role(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                name="Staff",
                description="Standard RobinHub staff access.",
            )
            session.add(staff_role)
            await session.flush()

        staff_user = (
            await session.execute(
                select(User).where(
                    User.tenant_id == tenant.id,
                    User.display_name == "Test Staff",
                )
            )
        ).scalar_one_or_none()

        if staff_user is None:
            staff_user = User(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                display_name="Test Staff",
                active=True,
            )
            session.add(staff_user)
            await session.flush()

        existing_role = (
            await session.execute(
                select(UserRole).where(
                    UserRole.user_id == staff_user.id,
                    UserRole.role_id == staff_role.id,
                )
            )
        ).scalar_one_or_none()

        if existing_role is None:
            session.add(
                UserRole(
                    id=uuid.uuid4(),
                    tenant_id=tenant.id,
                    user_id=staff_user.id,
                    role_id=staff_role.id,
                )
            )

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

        attendance = (
            await session.execute(
                select(LeagueAttendance).where(
                    LeagueAttendance.league_session_id
                    == league_session.id,
                    LeagueAttendance.customer_id == customer.id,
                )
            )
        ).scalar_one()

        # Use a fresh payment so this test can be repeated.
        payment = Payment(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            store_id=store.id,
            customer_id=customer.id,
            context_type="league_attendance",
            context_id=attendance.id,
            amount=league_session.entry_fee,
            currency=league_session.currency,
            method="cash",
            status="cash_due",
        )

        session.add(payment)
        await session.flush()

        print(f"Test Staff UUID: {staff_user.id}")
        print(f"Payment UUID: {payment.id}")
        print("Attempting unauthorised cash confirmation...")

        try:
            await PaymentService.confirm_cash_payment(
                session,
                payment_id=payment.id,
                confirmed_by=staff_user.id,
            )
        except PermissionError as exc:
            print()
            print("PASS: permission correctly denied")
            print(f"Reason: {exc}")

            await session.rollback()
            return

        await session.rollback()

        raise RuntimeError(
            "FAIL: standard staff was able to confirm a cash payment."
        )


if __name__ == "__main__":
    asyncio.run(main())