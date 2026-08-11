import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from models import AuditLog


class AuditService:
    @staticmethod
    async def record(
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID | None = None,
        store_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        old_values: dict[str, Any] | None = None,
        new_values: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            store_id=store_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_values=old_values,
            new_values=new_values,
            ip_address=ip_address,
        )

        session.add(entry)
        await session.flush()
        return entry