from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.session import get_db_session
from src.deps.auth import get_current_user
from src.models import DeliveryAssignment, DeliveryStatus, Order, OrderStatus, Restaurant, User, UserRole
from src.schemas.delivery import AssignCourierRequest, CourierActionRequest, DeliveryAssignmentOut
from src.schemas.orders import OrderOut

router = APIRouter(prefix="/delivery", tags=["delivery"])


def _is_admin(user: User) -> bool:
    return (user.role.value if hasattr(user.role, "value") else str(user.role)) == UserRole.admin.value


async def _get_order_or_404(session: AsyncSession, order_id: UUID) -> Order:
    order = await session.get(Order, order_id, options=(selectinload(Order.items),))
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


async def _get_assignment(session: AsyncSession, order_id: UUID) -> DeliveryAssignment:
    stmt = select(DeliveryAssignment).where(DeliveryAssignment.order_id == order_id)
    res = await session.execute(stmt)
    assignment = res.scalar_one_or_none()
    if assignment is None:
        assignment = DeliveryAssignment(
            id=uuid4(),
            order_id=order_id,
            delivery_user_id=None,
            status=DeliveryStatus.unassigned,
            assigned_at=None,
            picked_up_at=None,
            delivered_at=None,
            updated_at=datetime.now(timezone.utc),
        )
        session.add(assignment)
        await session.commit()
        await session.refresh(assignment)
    return assignment


async def _assert_restaurant_manage_access(session: AsyncSession, restaurant_id: UUID, user: User) -> None:
    """
    Restaurant delivery actions are allowed for:
    - admin
    - restaurant_admin who owns the restaurant
    """
    if _is_admin(user):
        return
    if (user.role.value if hasattr(user.role, "value") else str(user.role)) != UserRole.restaurant_admin.value:
        raise HTTPException(status_code=403, detail="Requires restaurant_admin role")

    restaurant = await session.get(Restaurant, restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    if restaurant.owner_user_id is None or restaurant.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not permitted to manage deliveries for this restaurant")


def _order_to_out(order: Order) -> OrderOut:
    # Mirror conversion logic in orders route (created_at mapped from placed_at).
    return OrderOut(
        id=order.id,
        customer_user_id=order.customer_user_id,
        restaurant_id=order.restaurant_id,
        status=order.status,
        currency=order.currency,
        subtotal_cents=int(order.subtotal_cents),
        delivery_fee_cents=int(order.delivery_fee_cents),
        total_cents=int(order.total_cents),
        courier_user_id=None,
        placed_at=order.placed_at,
        created_at=order.placed_at,
        updated_at=order.updated_at,
        items=[
            {
                "id": it.id,
                "menu_item_id": it.menu_item_id,
                "name": it.name_snapshot,
                "quantity": it.quantity,
                "unit_price_cents": it.price_cents_snapshot,
                "currency": order.currency,
            }
            for it in (order.items or [])
        ],
    )


def _assert_role(user: User, role: UserRole) -> None:
    if _is_admin(user):
        return
    if (user.role.value if hasattr(user.role, "value") else str(user.role)) != role.value:
        raise HTTPException(status_code=403, detail=f"Requires {role.value} role")


def _assert_status(order: Order, expected: OrderStatus) -> None:
    if order.status != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid order status for this action (expected {expected.value}, got {order.status.value})",
        )


async def _assert_delivery_user_assigned(assignment: DeliveryAssignment, courier_user: User) -> None:
    if assignment.delivery_user_id is None:
        raise HTTPException(status_code=400, detail="Order has no assigned delivery user")
    if assignment.delivery_user_id != courier_user.id and not _is_admin(courier_user):
        raise HTTPException(status_code=403, detail="Not the assigned delivery user for this order")


# PUBLIC_INTERFACE
@router.post(
    "/orders/{order_id}/assign",
    response_model=DeliveryAssignmentOut,
    status_code=status.HTTP_200_OK,
    summary="Assign an order to a courier",
    description="Assign a delivery_person to an order. Allowed for admin or restaurant_admin of the order's restaurant.",
)
async def assign_order_to_courier(
    order_id: UUID,
    payload: AssignCourierRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> DeliveryAssignmentOut:
    """
    Assign an order to a delivery_person.

    Seeded schema uses `delivery_assignments.delivery_user_id` (UUID FK).
    """
    if payload.courier_user_id is None:
        raise HTTPException(status_code=422, detail="courier_user_id is required")

    order = await _get_order_or_404(session, order_id)
    await _assert_restaurant_manage_access(session, order.restaurant_id, current_user)

    try:
        courier_id = UUID(str(payload.courier_user_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="courier_user_id must be a UUID")

    courier = await session.get(User, courier_id)
    if not courier:
        raise HTTPException(status_code=404, detail="Courier user not found")
    if (courier.role.value if hasattr(courier.role, "value") else str(courier.role)) != UserRole.delivery_person.value:
        raise HTTPException(status_code=400, detail="User is not a delivery_person")

    assignment = await _get_assignment(session, order_id)
    assignment.delivery_user_id = courier_id
    assignment.status = DeliveryStatus.assigned
    assignment.assigned_at = datetime.now(timezone.utc)
    assignment.updated_at = datetime.now(timezone.utc)
    await session.commit()

    return DeliveryAssignmentOut(order_id=order.id, courier_user_id=courier_id)  # type: ignore[arg-type]


# PUBLIC_INTERFACE
@router.post(
    "/orders/{order_id}/accept",
    response_model=OrderOut,
    summary="Courier accepts an assignment",
    description="Courier confirms they accept the delivery assignment. Allowed only for the assigned delivery_person.",
)
async def courier_accept_assignment(
    order_id: UUID,
    _: CourierActionRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Accept assignment (no status change).
    """
    _assert_role(current_user, UserRole.delivery_person)

    order = await _get_order_or_404(session, order_id)
    assignment = await _get_assignment(session, order_id)
    await _assert_delivery_user_assigned(assignment, current_user)
    return _order_to_out(order)


# PUBLIC_INTERFACE
@router.post(
    "/orders/{order_id}/picked-up",
    response_model=OrderOut,
    summary="Courier marks order as picked up",
    description="Transition order ready_for_pickup -> picked_up. Allowed only for assigned delivery_person.",
)
async def courier_mark_picked_up(
    order_id: UUID,
    _: CourierActionRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Mark an order as picked up.
    """
    _assert_role(current_user, UserRole.delivery_person)

    order = await _get_order_or_404(session, order_id)
    assignment = await _get_assignment(session, order_id)
    await _assert_delivery_user_assigned(assignment, current_user)
    _assert_status(order, OrderStatus.ready_for_pickup)

    order.status = OrderStatus.picked_up
    order.updated_at = datetime.now(timezone.utc)

    assignment.status = DeliveryStatus.picked_up
    assignment.picked_up_at = datetime.now(timezone.utc)
    assignment.updated_at = datetime.now(timezone.utc)

    await session.commit()

    order = await _get_order_or_404(session, order_id)
    return _order_to_out(order)


# PUBLIC_INTERFACE
@router.post(
    "/orders/{order_id}/delivered",
    response_model=OrderOut,
    summary="Courier marks order as delivered",
    description="Transition order picked_up -> delivered. Allowed only for assigned delivery_person.",
)
async def courier_mark_delivered(
    order_id: UUID,
    _: CourierActionRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Mark an order as delivered.
    """
    _assert_role(current_user, UserRole.delivery_person)

    order = await _get_order_or_404(session, order_id)
    assignment = await _get_assignment(session, order_id)
    await _assert_delivery_user_assigned(assignment, current_user)
    _assert_status(order, OrderStatus.picked_up)

    order.status = OrderStatus.delivered
    order.updated_at = datetime.now(timezone.utc)

    assignment.status = DeliveryStatus.delivered
    assignment.delivered_at = datetime.now(timezone.utc)
    assignment.updated_at = datetime.now(timezone.utc)

    await session.commit()

    order = await _get_order_or_404(session, order_id)
    return _order_to_out(order)
