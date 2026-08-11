import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Payment, Permission, RolePermission, UserRole
from services.audit_service import AuditService


class PaymentService:
    @staticmethod
    async def create_cash_due(
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        store_id: uuid.UUID,
        customer_id: uuid.UUID,
        context_type: str,
        context_id: uuid.UUID,
        amount: Decimal,
        currency: str = "GBP",
    ) -> Payment:
        payment = Payment(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            store_id=store_id,
            customer_id=customer_id,
            context_type=context_type,
            context_id=context_id,
            amount=amount,
            currency=currency,
            method="cash",
            status="cash_due",
        )

        session.add(payment)
        await session.flush()

        await AuditService.record(
            session,
            tenant_id=tenant_id,
            store_id=store_id,
            action="payment.cash_due.created",
            entity_type="payment",
            entity_id=payment.id,
            new_values={
                "amount": str(amount),
                "currency": currency,
                "status": "cash_due",
            },
        )

        return payment

    @staticmethod
    async def _user_has_permission(
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        permission_code: str,
    ) -> bool:
        result = await session.execute(
            select(Permission.id)
            .join(
                RolePermission,
                RolePermission.permission_id == Permission.id,
            )
            .join(
                UserRole,
                UserRole.role_id == RolePermission.role_id,
            )
            .where(
                UserRole.user_id == user_id,
                Permission.code == permission_code,
            )
        )

        return result.scalar_one_or_none() is not None

    @classmethod
    async def confirm_cash_payment(
        cls,
        session: AsyncSession,
        *,
        payment_id: uuid.UUID,
        confirmed_by: uuid.UUID,
    ) -> Payment:
        allowed = await cls._user_has_permission(
            session,
            user_id=confirmed_by,
            permission_code="payments.cash.confirm",
        )

        if not allowed:
            raise PermissionError(
                "User does not have permission to confirm cash payments."
            )

        payment = (
            await session.execute(
                select(Payment).where(Payment.id == payment_id)
            )
        ).scalar_one_or_none()

        if payment is None:
            raise ValueError("Payment not found.")

        if payment.method != "cash":
            raise ValueError("Payment is not a cash payment.")

        if payment.status != "cash_due":
            raise ValueError(
                f"Cash payment cannot be confirmed from status {payment.status}."
            )

        old_status = payment.status
        now = datetime.now(timezone.utc)

        payment.status = "paid"
        payment.paid_at = now
        payment.confirmed_by = confirmed_by
        payment.confirmed_at = now

        await AuditService.record(
            session,
            tenant_id=payment.tenant_id,
            store_id=payment.store_id,
            user_id=confirmed_by,
            action="payment.cash.confirmed",
            entity_type="payment",
            entity_id=payment.id,
            old_values={"status": old_status},
            new_values={"status": "paid"},
        )

        await session.flush()
        return payment