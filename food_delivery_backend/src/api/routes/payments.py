from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.session import get_db_session
from src.deps.auth import get_current_user
from src.models import Order, User, UserRole
from src.schemas.payments import (
    PaymentConfirmRequest,
    PaymentConfirmResponse,
    PaymentIntentCreateRequest,
    PaymentIntentCreateResponse,
)
from src.services.payments import confirm_payment_stub, create_payment_intent_stub

router = APIRouter(prefix="/payments", tags=["payments"])


def _is_admin(user: User) -> bool:
    return (user.role.value if hasattr(user.role, "value") else str(user.role)) == UserRole.admin.value


async def _get_order_or_404(session: AsyncSession, order_id: UUID) -> Order:
    order = await session.get(Order, order_id, options=(selectinload(Order.items),))
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _assert_customer_owns_order(order: Order, user: User) -> None:
    """
    Customers may act only on their own orders. Admin may act on any order.

    Note: restaurant_admin/delivery_person are not permitted to pay/confirm payment for customer orders.
    """
    if _is_admin(user):
        return
    if order.customer_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not permitted to access this order")


# PUBLIC_INTERFACE
@router.post(
    "/intent",
    response_model=PaymentIntentCreateResponse,
    status_code=status.HTTP_200_OK,
    summary="Create a payment intent (stub)",
    description=(
        "Simulates creating a payment intent for an order (to be swapped with Stripe later). "
        "Validates the order is in pending status and the provided amount matches order.total_cents. "
        "Returns a fake client_secret."
    ),
)
async def create_payment_intent(
    payload: PaymentIntentCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> PaymentIntentCreateResponse:
    """
    Create a stubbed payment intent for an order.

    Security:
    - Requires authentication.
    - Customer can only create intents for their own orders.
    - Admin may create intents for any order.
    """
    order = await _get_order_or_404(session, payload.order_id)
    _assert_customer_owns_order(order, current_user)

    client_secret, amount_cents, currency = await create_payment_intent_stub(
        session=session,
        order_id=order.id,
        amount_cents=payload.amount_cents,
    )

    return PaymentIntentCreateResponse(
        order_id=order.id,
        amount_cents=amount_cents,
        currency=currency,
        client_secret=client_secret,
        provider="stub",
    )


# PUBLIC_INTERFACE
@router.post(
    "/confirm",
    response_model=PaymentConfirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirm a payment (stub)",
    description=(
        "Simulates confirmation of a payment intent (to be swapped with Stripe later). "
        "Validates client_secret and marks the order as confirmed (if currently pending). "
        "Idempotent when order is already confirmed or beyond."
    ),
)
async def confirm_payment(
    payload: PaymentConfirmRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> PaymentConfirmResponse:
    """
    Confirm payment (stub).

    Security:
    - Requires authentication.
    - Customer can only confirm payment for their own orders.
    - Admin may confirm for any order.
    """
    order = await _get_order_or_404(session, payload.order_id)
    _assert_customer_owns_order(order, current_user)

    updated = await confirm_payment_stub(
        session=session,
        order_id=order.id,
        client_secret=payload.client_secret,
    )

    return PaymentConfirmResponse(
        order_id=updated.id,
        status="succeeded",
        order_status=updated.status.value,
        provider="stub",
    )
