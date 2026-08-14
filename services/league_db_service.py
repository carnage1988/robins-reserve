import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Customer,
    LeagueAttendance,
    LeagueSession,
    LeagueTemplate,
)


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
    async def _expire_stale_sessions(
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        store_id: uuid.UUID,
    ) -> int:
        """
        Close any PostgreSQL League sessions that are still marked active
        even though their configured end time has passed.
        """

        now = datetime.now(timezone.utc)

        stale_sessions = (
            await session.execute(
                select(LeagueSession).where(
                    LeagueSession.tenant_id == tenant_id,
                    LeagueSession.store_id == store_id,
                    LeagueSession.status == "active",
                    LeagueSession.ends_at <= now,
                )
            )
        ).scalars().all()

        for league_session in stale_sessions:
            league_session.status = "closed"

        if stale_sessions:
            await session.flush()

        return len(stale_sessions)

    @staticmethod
    async def get_active_session(
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        store_id: uuid.UUID,
    ) -> LeagueSession:
        await LeagueDatabaseService._expire_stale_sessions(
            session,
            tenant_id=tenant_id,
            store_id=store_id,
        )

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
    async def start_session(
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        store_id: uuid.UUID,
        template_name: str,
        duration_hours: int,
        created_by: uuid.UUID | None = None,
    ) -> LeagueSession:
        await LeagueDatabaseService._expire_stale_sessions(
            session,
            tenant_id=tenant_id,
            store_id=store_id,
        )

        existing = (
            await session.execute(
                select(LeagueSession).where(
                    LeagueSession.tenant_id == tenant_id,
                    LeagueSession.store_id == store_id,
                    LeagueSession.status == "active",
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            raise ValueError(
                "A PostgreSQL League session is already active."
            )

        league_template = (
            await session.execute(
                select(LeagueTemplate).where(
                    LeagueTemplate.tenant_id == tenant_id,
                    LeagueTemplate.name == template_name,
                )
            )
        ).scalar_one_or_none()

        if league_template is None:
            raise ValueError(
                f"League template '{template_name}' was not found."
            )

        starts_at = datetime.now(timezone.utc)
        ends_at = starts_at + timedelta(hours=duration_hours)

        league_session = LeagueSession(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            store_id=store_id,
            league_template_id=league_template.id,
            starts_at=starts_at,
            ends_at=ends_at,
            entry_fee=league_template.default_entry_fee,
            currency=league_template.currency,
            status="active",
            created_by=created_by,
        )

        session.add(league_session)
        await session.flush()

        return league_session

    @staticmethod
    async def end_active_session(
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        store_id: uuid.UUID,
    ) -> LeagueSession:
        await LeagueDatabaseService._expire_stale_sessions(
            session,
            tenant_id=tenant_id,
            store_id=store_id,
        )

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

        league_session.status = "closed"
        league_session.ends_at = datetime.now(timezone.utc)

        await session.flush()

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

        now = datetime.now(timezone.utc)

        if (
            league_session.status != "active"
            or league_session.ends_at <= now
        ):
            if (
                league_session.status == "active"
                and league_session.ends_at <= now
            ):
                league_session.status = "closed"
                await session.flush()

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