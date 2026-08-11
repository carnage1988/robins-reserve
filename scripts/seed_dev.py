import asyncio
import uuid

from sqlalchemy import select

from datetime import timedelta
from decimal import Decimal

from models import (
    AuditLog,
    Customer,
    Game,
    LeagueSession,
    LeagueTemplate,
    Permission,
    Role,
    RolePermission,
    Store,
    Tenant,
    User,
    UserRole,
    UserStoreAccess,
)
from sqlalchemy.sql import func

from services.database import AsyncSessionLocal


PERMISSIONS = [
    (
        "payments.cash.confirm",
        "Confirm that a cash payment has been received.",
    ),
    (
        "financial.reports.view",
        "View financial reporting.",
    ),
    (
        "league.manage",
        "Create and manage league sessions.",
    ),
    (
        "dashboard.configure",
        "Configure dashboard layouts and widgets.",
    ),
]


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        tenant = (
            await session.execute(
                select(Tenant).where(Tenant.slug == "robins")
            )
        ).scalar_one_or_none()

        if tenant is None:
            tenant = Tenant(
                id=uuid.uuid4(),
                name="Robins Hobby Cafe",
                slug="robins",
                status="active",
            )
            session.add(tenant)
            await session.flush()

        store = (
            await session.execute(
                select(Store).where(
                    Store.tenant_id == tenant.id,
                    Store.code == "BELFAST",
                )
            )
        ).scalar_one_or_none()

        if store is None:
            store = Store(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                name="Belfast",
                code="BELFAST",
                timezone="Europe/London",
                active=True,
            )
            session.add(store)
            await session.flush()

        bill = (
            await session.execute(
                select(User).where(
                    User.tenant_id == tenant.id,
                    User.display_name == "Bill",
                )
            )
        ).scalar_one_or_none()

        if bill is None:
            bill = User(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                display_name="Bill",
                active=True,
            )
            session.add(bill)
            await session.flush()

        owner_role = (
            await session.execute(
                select(Role).where(
                    Role.tenant_id == tenant.id,
                    Role.name == "Owner",
                )
            )
        ).scalar_one_or_none()

        if owner_role is None:
            owner_role = Role(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                name="Owner",
                description="Full RobinHub tenant owner access.",
            )
            session.add(owner_role)
            await session.flush()

        for code, description in PERMISSIONS:
            permission = (
                await session.execute(
                    select(Permission).where(
                        Permission.code == code
                    )
                )
            ).scalar_one_or_none()

            if permission is None:
                permission = Permission(
                    id=uuid.uuid4(),
                    code=code,
                    description=description,
                )
                session.add(permission)
                await session.flush()

            existing_role_permission = (
                await session.execute(
                    select(RolePermission).where(
                        RolePermission.role_id == owner_role.id,
                        RolePermission.permission_id == permission.id,
                    )
                )
            ).scalar_one_or_none()

            if existing_role_permission is None:
                session.add(
                    RolePermission(
                        id=uuid.uuid4(),
                        tenant_id=tenant.id,
                        role_id=owner_role.id,
                        permission_id=permission.id,
                    )
                )

        existing_user_role = (
            await session.execute(
                select(UserRole).where(
                    UserRole.user_id == bill.id,
                    UserRole.role_id == owner_role.id,
                )
            )
        ).scalar_one_or_none()

        if existing_user_role is None:
            session.add(
                UserRole(
                    id=uuid.uuid4(),
                    tenant_id=tenant.id,
                    user_id=bill.id,
                    role_id=owner_role.id,
                )
            )

        existing_store_access = (
            await session.execute(
                select(UserStoreAccess).where(
                    UserStoreAccess.user_id == bill.id,
                    UserStoreAccess.store_id == store.id,
                )
            )
        ).scalar_one_or_none()

        if existing_store_access is None:
            session.add(
                UserStoreAccess(
                    id=uuid.uuid4(),
                    tenant_id=tenant.id,
                    user_id=bill.id,
                    store_id=store.id,
                )
            )

        pokemon = (
            await session.execute(
                select(Game).where(
                    Game.tenant_id == tenant.id,
                    Game.slug == "pokemon",
                )
            )
        ).scalar_one_or_none()

        if pokemon is None:
            pokemon = Game(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                name="Pokémon",
                slug="pokemon",
                publisher="The Pokémon Company",
                active=True,
            )
            session.add(pokemon)
            await session.flush()

        league_template = (
            await session.execute(
                select(LeagueTemplate).where(
                    LeagueTemplate.tenant_id == tenant.id,
                    LeagueTemplate.name == "Pokémon Weekly League",
                )
            )
        ).scalar_one_or_none()

        if league_template is None:
            league_template = LeagueTemplate(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                game_id=pokemon.id,
                name="Pokémon Weekly League",
                description="Development Pokémon League template.",
                default_entry_fee=Decimal("5.00"),
                currency="GBP",
            )
            session.add(league_template)
            await session.flush()

        test_customer = (
            await session.execute(
                select(Customer).where(
                    Customer.tenant_id == tenant.id,
                    Customer.discord_user_id == "999999999999999999",
                )
            )
        ).scalar_one_or_none()

        if test_customer is None:
            test_customer = Customer(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                display_name="Test Player",
                first_name="Test",
                last_name="Player",
                discord_user_id="999999999999999999",
            )
            session.add(test_customer)
            await session.flush()

        league_session = (
            await session.execute(
                select(LeagueSession).where(
                    LeagueSession.tenant_id == tenant.id,
                    LeagueSession.store_id == store.id,
                    LeagueSession.league_template_id == league_template.id,
                    LeagueSession.status == "active",
                )
            )
        ).scalar_one_or_none()

        if league_session is None:
            now_result = await session.execute(select(func.now()))
            now = now_result.scalar_one()

            league_session = LeagueSession(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                store_id=store.id,
                league_template_id=league_template.id,
                starts_at=now,
                ends_at=now + timedelta(hours=4),
                entry_fee=Decimal("5.00"),
                currency="GBP",
                status="active",
                created_by=bill.id,
            )
            session.add(league_session)
            await session.flush()

        await session.commit()

        print("Development seed complete.")
        print(f"Tenant: {tenant.name}")
        print(f"Store: {store.name}")
        print(f"User: {bill.display_name}")
        print(f"Role: {owner_role.name}")
        print(f"Game: {pokemon.name}")
        print(f"League: {league_template.name}")
        print(f"Test Customer: {test_customer.display_name}")
        print(f"League Session: {league_session.id}")


if __name__ == "__main__":
    asyncio.run(seed())