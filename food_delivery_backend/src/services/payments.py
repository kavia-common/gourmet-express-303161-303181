from __future__ import annotations

import os
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Order, OrderStatus, Payment, PaymentStatus


@dataclass(frozen=True)
class PaymentsConfig:
    """
    Payments configuration placeholder.

    This is intentionally minimal for now and is meant to be replaced/expanded when integrating Stripe.
    """

    provider: str


def _get_payments_config() -> PaymentsConfig:
    """
    Read configuration for payment provider selection.

    Env vars (optional for now):
    - PAYMENTS_PROVIDER: 'stub' (default) or 'stripe' (future)
    """
    provider = os.getenv("PAYMENTS_PROVIDER", "stub").strip().lower() or "stub"
    return PaymentsConfig(provider=provider)


def _fake_client_secret(order_id: UUID) -> str:
    """
    Create a deterministic fake client secret for an order.

    This is only for non-production stub behavior.
    """
    return f"pi_stub_{order_id}_secret_stub"


async def _get_order_or_404(session: AsyncSession, order_id: UUID) -> Order:
    order = await session.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


async def _get_payment(session: AsyncSession, order_id: UUID) -> Payment | None:
    stmt = select(Payment).where(Payment.order_id == order_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


# PUBLIC_INTERFACE
async def create_payment_intent_stub(
    session: AsyncSession,
    order_id: UUID,
    amount_cents: int,
) -> tuple[str, int, str]:
    """
    Create a stubbed payment intent for the given order.

    Validations:
    - Order exists
    - Order status must be `pending` (cart ready for checkout)
    - Order total must match amount_cents provided by client

    Side effects:
    - Ensures a `payments` row exists with status `pending`
    """
    _ = _get_payments_config()

    order = await _get_order_or_404(session, order_id)

    if order.status != OrderStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot create payment intent for order in status {order.status.value}",
        )

    if int(order.total_cents) < 0:
        raise HTTPException(status_code=400, detail="Invalid order total")

    if int(order.total_cents) != int(amount_cents):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Order total mismatch")

    if not order.currency:
        raise HTTPException(status_code=400, detail="Order currency missing")

    payment = await _get_payment(session, order_id)
    if payment is None:
        payment = Payment(
            id=uuid4(),
            order_id=order_id,
            provider="mock",
            provider_payment_id=None,
            status=PaymentStatus.pending,
            amount_cents=int(order.total_cents),
            currency=str(order.currency),
        )
        session.add(payment)
        await session.commit()
    else:
        # Keep amount in sync
        payment.amount_cents = int(order.total_cents)
        payment.currency = str(order.currency)
        await session.commit()

    return _fake_client_secret(order.id), int(order.total_cents), str(order.currency)


# PUBLIC_INTERFACE
async def confirm_payment_stub(
    session: AsyncSession,
    order_id: UUID,
    client_secret: str,
) -> Order:
    """
    Confirm a stubbed payment for the order.

    Behavior:
    - Validates the client_secret matches the deterministic one we generate for the order.
    - If order is `pending`, transitions it to `confirmed` (mimicking successful authorization/payment).
    - Updates `payments.status` to `paid`.
    - If already confirmed+ (preparing/ready_for_pickup/picked_up/delivered), idempotent and returns current order.
    - If cancelled, rejects.
    """
    _ = _get_payments_config()

    order = await _get_order_or_404(session, order_id)

    expected = _fake_client_secret(order.id)
    if client_secret != expected:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid client_secret for this order")

    if order.status == OrderStatus.cancelled:
        raise HTTPException(status_code=400, detail="Cannot confirm payment for a cancelled order")

    payment = await _get_payment(session, order_id)
    if payment is None:
        payment = Payment(
            id=uuid4(),
            order_id=order_id,
            provider="mock",
            provider_payment_id=None,
            status=PaymentStatus.pending,
            amount_cents=int(order.total_cents),
            currency=str(order.currency),
        )
        session.add(payment)

    # Idempotency: treat confirmed+ as already paid
    if order.status == OrderStatus.pending:
        order.status = OrderStatus.confirmed
        payment.status = PaymentStatus.paid
        await session.commit()
        await session.refresh(order)
        return order

    if order.status in {
        OrderStatus.confirmed,
        OrderStatus.preparing,
        OrderStatus.ready_for_pickup,
        OrderStatus.picked_up,
        OrderStatus.delivered,
    }:
        if payment.status != PaymentStatus.paid:
            payment.status = PaymentStatus.paid
            await session.commit()
        return order

    raise HTTPException(status_code=400, detail=f"Cannot confirm payment for order in status {order.status.value}")
