import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Customer, LeagueAttendance, LeagueSession


class LeagueDatabaseService:
    @staticmethod
    async def get_or_create_customer(
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        discord_user_id: int,
        display_name: str,
    ) -> Customer:
        customer = (
            await session.execute(
                select(Customer).where(
                    Customer.tenant_id == tenant_id,
                    Customer.discord_user_id == str(discord_user_id),
                )
            )
        ).scalar_one_or_none()

        if customer is not None:
            return customer

        customer = Customer(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            display_name=display_name,
            discord_user_id=str(discord_user_id),
        )

        session.add(customer)
        await session.flush()

        return customer

    @staticmethod
    async def get_active_session(
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        store_id: uuid.UUID,
    ) -> LeagueSession:
        league_session = (
            await session.execute(
                select(LeagueSession).where(
                    LeagueSession.tenant_id == tenant_id,
                    LeagueSession.store_id == store_id,
                    LeagueSession.status == "active",
                )
            )
        ).scalar_one_or_none()

        if league_session is None:
            raise ValueError(
                "There is no active PostgreSQL League session."
            )

        return league_session

    @staticmethod
    async def check_in_customer(
        session: AsyncSession,
        *,
        league_session_id: uuid.UUID,
        customer_id: uuid.UUID,
        checkin_method: str = "discord",
    ) -> LeagueAttendance:
        league_session = (
            await session.execute(
                select(LeagueSession).where(
                    LeagueSession.id == league_session_id
                )
            )
        ).scalar_one_or_none()

        if league_session is None:
            raise ValueError("League session not found.")

        if league_session.status != "active":
            raise ValueError("League session is not active.")

        customer = (
            await session.execute(
                select(Customer).where(
                    Customer.id == customer_id
                )
            )
        ).scalar_one_or_none()

        if customer is None:
            raise ValueError("Customer not found.")

        existing = (
            await session.execute(
                select(LeagueAttendance).where(
                    LeagueAttendance.league_session_id
                    == league_session.id,
                    LeagueAttendance.customer_id == customer.id,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            raise ValueError(
                "Customer is already checked into this league session."
            )

        attendance = LeagueAttendance(
            id=uuid.uuid4(),
            tenant_id=league_session.tenant_id,
            store_id=league_session.store_id,
            league_session_id=league_session.id,
            customer_id=customer.id,
            checkin_method=checkin_method,
            status="checked_in",
        )

        session.add(attendance)
        await session.flush()

        return attendance