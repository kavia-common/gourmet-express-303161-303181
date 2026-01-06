from __future__ import annotations

import os
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orders import Order, OrderStatus


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
    - Order status must be CREATED (cart ready for checkout)
    - Order total must match amount_cents provided by client
    """
    _ = _get_payments_config()  # reserved for provider switching

    order = await _get_order_or_404(session, order_id)

    current = OrderStatus(order.status)
    if current != OrderStatus.CREATED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot create payment intent for order in status {current.value}",
        )

    if int(order.total_cents) < 0:
        raise HTTPException(status_code=400, detail="Invalid order total")

    if int(order.total_cents) != int(amount_cents):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order total mismatch",
        )

    if not order.currency:
        raise HTTPException(status_code=400, detail="Order currency missing")

    # In a real provider we'd persist an intent id and link it to the order.
    # For now we return a deterministic client_secret so confirm can validate it.
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
    - If order is CREATED, transitions it to PAID (mimicking payment success).
    - If order is already PAID (or beyond), is idempotent and returns current order.

    This is designed so the route handler can later be swapped to Stripe with minimal change.
    """
    _ = _get_payments_config()  # reserved for provider switching

    order = await _get_order_or_404(session, order_id)

    expected = _fake_client_secret(order.id)
    if client_secret != expected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid client_secret for this order",
        )

    current = OrderStatus(order.status)

    # Idempotency: if already paid or beyond, do not regress.
    if current == OrderStatus.CREATED:
        order.status = OrderStatus.PAID.value
        await session.commit()
        await session.refresh(order)
        return order

    if current in {
        OrderStatus.PAID,
        OrderStatus.PREPARING,
        OrderStatus.READY_FOR_PICKUP,
        OrderStatus.OUT_FOR_DELIVERY,
        OrderStatus.DELIVERED,
    }:
        return order

    if current == OrderStatus.CANCELED:
        raise HTTPException(status_code=400, detail="Cannot confirm payment for a canceled order")

    # Fallback (should not happen due to enum completeness)
    raise HTTPException(status_code=400, detail=f"Cannot confirm payment for order in status {order.status}")
