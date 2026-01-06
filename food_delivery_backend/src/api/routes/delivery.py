from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.session import get_db_session
from src.deps.auth import require_roles
from src.models.auth import Role, User
from src.models.orders import Order, OrderStatus
from src.models.restaurants import Restaurant
from src.schemas.delivery import AssignCourierRequest, CourierActionRequest, DeliveryAssignmentOut
from src.schemas.orders import OrderOut


router = APIRouter(prefix="/delivery", tags=["delivery"])


def _has_role(user: User, role_name: str) -> bool:
    roles: list[Role] = getattr(user, "_role_objects", [])
    return any(r.name == role_name for r in roles)


def _is_admin(user: User) -> bool:
    return _has_role(user, "admin")


async def _get_order_or_404(session: AsyncSession, order_id: UUID) -> Order:
    order = await session.get(Order, order_id, options=(selectinload(Order.items),))
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


async def _assert_restaurant_manage_access(session: AsyncSession, restaurant_id: UUID, user: User) -> None:
    """
    Restaurant updates are allowed for:
    - admin
    - restaurant_owner who owns the restaurant
    """
    if _is_admin(user):
        return
    restaurant = await session.get(Restaurant, restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    if restaurant.owner_user_id is None or str(restaurant.owner_user_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Not permitted to manage deliveries for this restaurant")


def _order_to_out(order: Order) -> OrderOut:
    return OrderOut.model_validate(order)


def _assert_courier_assigned(order: Order, courier_user: User) -> None:
    if order.courier_user_id is None:
        raise HTTPException(status_code=400, detail="Order has no assigned courier")
    if int(order.courier_user_id) != int(courier_user.id):
        raise HTTPException(status_code=403, detail="Not the assigned courier for this order")


def _assert_status(order: Order, expected: OrderStatus) -> None:
    current = OrderStatus(order.status)
    if current != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid order status for this action (expected {expected.value}, got {current.value})",
        )


# PUBLIC_INTERFACE
@router.post(
    "/orders/{order_id}/assign",
    response_model=DeliveryAssignmentOut,
    status_code=status.HTTP_200_OK,
    summary="Assign an order to a courier",
    description=(
        "Assign a courier to an order. Allowed for admin or restaurant_owner of the restaurant for the order. "
        "Typically done when the order is READY_FOR_PICKUP (or earlier operationally). "
        "This does not automatically change order status."
    ),
)
async def assign_order_to_courier(
    order_id: UUID,
    payload: AssignCourierRequest,
    current_user: User = Depends(require_roles(["restaurant_owner", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> DeliveryAssignmentOut:
    """
    Assign an order to a courier.

    Permissions:
    - admin: can assign any order
    - restaurant_owner: can assign only orders for restaurants they own

    Validations:
    - Order must exist
    - Courier user id must be positive
    - If not admin, restaurant_owner must own the restaurant for the order
    """
    if payload.courier_user_id <= 0:
        raise HTTPException(status_code=422, detail="courier_user_id must be a positive integer")

    order = await _get_order_or_404(session, order_id)
    await _assert_restaurant_manage_access(session, order.restaurant_id, current_user)

    # Set/replace assignment. (Operationally allowed to reassign; can be tightened later.)
    order.courier_user_id = int(payload.courier_user_id)

    await session.commit()
    return DeliveryAssignmentOut(order_id=order.id, courier_user_id=int(order.courier_user_id))


# PUBLIC_INTERFACE
@router.post(
    "/orders/{order_id}/accept",
    response_model=OrderOut,
    summary="Courier accepts an assignment",
    description=(
        "Courier confirms they accept the delivery assignment. "
        "Allowed only for the assigned courier. Does not change order status."
    ),
)
async def courier_accept_assignment(
    order_id: UUID,
    _: CourierActionRequest,
    current_user: User = Depends(require_roles(["courier", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Courier accepts assignment.

    Permissions:
    - courier: only if assigned to the order
    - admin: allowed but still must be assigned (prevents arbitrary acceptance)
    """
    order = await _get_order_or_404(session, order_id)
    _assert_courier_assigned(order, current_user)
    return _order_to_out(order)


# PUBLIC_INTERFACE
@router.post(
    "/orders/{order_id}/picked-up",
    response_model=OrderOut,
    summary="Courier marks order as picked up",
    description=(
        "Transition order READY_FOR_PICKUP -> OUT_FOR_DELIVERY. "
        "Allowed only for the assigned courier."
    ),
)
async def courier_mark_picked_up(
    order_id: UUID,
    _: CourierActionRequest,
    current_user: User = Depends(require_roles(["courier", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Mark an order as picked up.

    Validations:
    - Order must be READY_FOR_PICKUP
    - Current user must be assigned courier
    """
    order = await _get_order_or_404(session, order_id)
    _assert_courier_assigned(order, current_user)
    _assert_status(order, OrderStatus.READY_FOR_PICKUP)

    order.status = OrderStatus.OUT_FOR_DELIVERY.value
    await session.commit()

    order = await _get_order_or_404(session, order_id)
    return _order_to_out(order)


# PUBLIC_INTERFACE
@router.post(
    "/orders/{order_id}/delivered",
    response_model=OrderOut,
    summary="Courier marks order as delivered",
    description=(
        "Transition order OUT_FOR_DELIVERY -> DELIVERED. "
        "Allowed only for the assigned courier."
    ),
)
async def courier_mark_delivered(
    order_id: UUID,
    _: CourierActionRequest,
    current_user: User = Depends(require_roles(["courier", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Mark an order as delivered.

    Validations:
    - Order must be OUT_FOR_DELIVERY
    - Current user must be assigned courier
    """
    order = await _get_order_or_404(session, order_id)
    _assert_courier_assigned(order, current_user)
    _assert_status(order, OrderStatus.OUT_FOR_DELIVERY)

    order.status = OrderStatus.DELIVERED.value
    await session.commit()

    order = await _get_order_or_404(session, order_id)
    return _order_to_out(order)

