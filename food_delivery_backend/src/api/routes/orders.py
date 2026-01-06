from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.session import get_db_session
from src.deps.auth import get_current_user, require_roles
from src.models.auth import Role, User
from src.models.orders import Order, OrderItem, OrderStatus
from src.models.restaurants import MenuItem, Restaurant
from src.schemas.orders import (
    CartCreateRequest,
    CartItemRemoveRequest,
    CartItemUpsertRequest,
    OrderOut,
    PlaceOrderRequest,
    UpdateOrderStatusRequest,
)


router = APIRouter(prefix="/orders", tags=["orders"])


def _has_role(user: User, role_name: str) -> bool:
    roles: list[Role] = getattr(user, "_role_objects", [])
    return any(r.name == role_name for r in roles)


def _is_admin(user: User) -> bool:
    return _has_role(user, "admin")


async def _get_order_or_404(session: AsyncSession, order_id: UUID) -> Order:
    order = await session.get(
        Order,
        order_id,
        options=(selectinload(Order.items),),
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _assert_customer_owns_order(order: Order, user: User) -> None:
    if order.customer_user_id != user.id and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Not permitted to access this order")


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
        raise HTTPException(status_code=403, detail="Not permitted to manage orders for this restaurant")


def _recompute_totals(order: Order) -> None:
    subtotal = 0
    # Always recompute from items
    for it in order.items:
        subtotal += int(it.unit_price_cents) * int(it.quantity)
    order.subtotal_cents = subtotal
    # Delivery fee placeholder (future integration)
    order.delivery_fee_cents = 0
    order.total_cents = int(order.subtotal_cents) + int(order.delivery_fee_cents)


async def _load_menu_item_for_cart(
    session: AsyncSession, menu_item_id: UUID, restaurant_id: UUID
) -> MenuItem:
    item = await session.get(MenuItem, menu_item_id)
    if not item or item.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="Menu item not found for this restaurant")
    if not item.is_available:
        raise HTTPException(status_code=400, detail="Menu item is not available")
    if item.price_cents < 0:
        raise HTTPException(status_code=400, detail="Invalid menu item price")
    return item


async def _validate_restaurant_is_orderable(session: AsyncSession, restaurant_id: UUID) -> None:
    restaurant = await session.get(Restaurant, restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    if not restaurant.is_active:
        raise HTTPException(status_code=400, detail="Restaurant is not accepting orders")


def _allowed_next_statuses_for_actor(order: Order, actor: str) -> set[OrderStatus]:
    """
    Actor is one of: customer, restaurant, courier, admin.
    Returns allowed *target* statuses from current order.status.
    """
    current = OrderStatus(order.status)

    if actor == "admin":
        # Admin can set any status (including cancel) for operational recovery.
        return set(OrderStatus)

    if current == OrderStatus.CANCELED or current == OrderStatus.DELIVERED:
        return set()

    if actor == "customer":
        # Customer can cancel only before it is out for delivery/delivered.
        if current in {OrderStatus.CREATED, OrderStatus.PAID, OrderStatus.PREPARING, OrderStatus.READY_FOR_PICKUP}:
            return {OrderStatus.CANCELED}
        return set()

    if actor == "restaurant":
        # Restaurant moves forward after payment:
        if current == OrderStatus.PAID:
            return {OrderStatus.PREPARING, OrderStatus.CANCELED}
        if current == OrderStatus.PREPARING:
            return {OrderStatus.READY_FOR_PICKUP, OrderStatus.CANCELED}
        return set()

    if actor == "courier":
        if current == OrderStatus.READY_FOR_PICKUP:
            return {OrderStatus.OUT_FOR_DELIVERY}
        if current == OrderStatus.OUT_FOR_DELIVERY:
            return {OrderStatus.DELIVERED}
        return set()

    return set()


def _actor_for_user(user: User) -> str:
    # Prefer admin override first
    if _is_admin(user):
        return "admin"
    if _has_role(user, "courier"):
        return "courier"
    if _has_role(user, "restaurant_owner"):
        return "restaurant"
    return "customer"


def _order_to_out(order: Order) -> OrderOut:
    # Ensure enum conversion
    return OrderOut.model_validate(order)


@router.post(
    "/cart",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a cart",
    description="Create a new cart (order with status CREATED) for the authenticated customer.",
)
async def create_cart(
    payload: CartCreateRequest,
    current_user: User = Depends(require_roles(["customer", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Create a new cart for a restaurant.

    A cart is an Order in status CREATED.
    """
    await _validate_restaurant_is_orderable(session, payload.restaurant_id)

    order = Order(
        id=uuid4(),
        customer_user_id=current_user.id,
        restaurant_id=payload.restaurant_id,
        status=OrderStatus.CREATED.value,
        currency="USD",
        subtotal_cents=0,
        delivery_fee_cents=0,
        total_cents=0,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    # Reload items relationship (empty) for consistent response
    order = await _get_order_or_404(session, order.id)
    return _order_to_out(order)


@router.get(
    "/{order_id}",
    response_model=OrderOut,
    summary="Get an order/cart by id",
    description="Fetch an order by id. Customers may only access their own orders; admins can access any.",
)
async def get_order(
    order_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    order = await _get_order_or_404(session, order_id)
    _assert_customer_owns_order(order, current_user)
    return _order_to_out(order)


@router.post(
    "/{order_id}/cart/items",
    response_model=OrderOut,
    summary="Add/update an item in the cart",
    description="Upsert a line item in a cart (status CREATED). Idempotent: setting same quantity yields same cart.",
)
async def upsert_cart_item(
    order_id: UUID,
    payload: CartItemUpsertRequest,
    current_user: User = Depends(require_roles(["customer", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Upsert a cart item by menu_item_id (unique per order).

    Validations:
    - Order must be in CREATED status
    - Customer must own the order
    - Menu item must be available and belong to the same restaurant
    - Price snapshot is refreshed from current menu item price
    """
    order = await _get_order_or_404(session, order_id)
    _assert_customer_owns_order(order, current_user)

    if OrderStatus(order.status) != OrderStatus.CREATED:
        raise HTTPException(status_code=400, detail="Cart is not editable")

    menu_item = await _load_menu_item_for_cart(session, payload.menu_item_id, order.restaurant_id)

    existing = next((it for it in order.items if it.menu_item_id == payload.menu_item_id), None)
    if existing:
        existing.quantity = payload.quantity
        existing.unit_price_cents = int(menu_item.price_cents)
        existing.currency = menu_item.currency
        existing.name = menu_item.name
    else:
        order.items.append(
            OrderItem(
                id=uuid4(),
                order_id=order.id,
                menu_item_id=payload.menu_item_id,
                quantity=payload.quantity,
                unit_price_cents=int(menu_item.price_cents),
                currency=menu_item.currency,
                name=menu_item.name,
            )
        )

    _recompute_totals(order)
    await session.commit()

    # reload to reflect latest
    order = await _get_order_or_404(session, order_id)
    return _order_to_out(order)


@router.delete(
    "/{order_id}/cart/items",
    response_model=OrderOut,
    summary="Remove an item from the cart",
    description="Remove a line item from a cart (status CREATED). Idempotent: removing missing item returns cart unchanged.",
)
async def remove_cart_item(
    order_id: UUID,
    payload: CartItemRemoveRequest,
    current_user: User = Depends(require_roles(["customer", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Remove an item from the cart (idempotent).

    If the item is not in the cart, the server returns the current cart unchanged.
    """
    order = await _get_order_or_404(session, order_id)
    _assert_customer_owns_order(order, current_user)

    if OrderStatus(order.status) != OrderStatus.CREATED:
        raise HTTPException(status_code=400, detail="Cart is not editable")

    before = len(order.items)
    order.items = [it for it in order.items if it.menu_item_id != payload.menu_item_id]
    after = len(order.items)

    if before != after:
        _recompute_totals(order)
        await session.commit()

    order = await _get_order_or_404(session, order_id)
    return _order_to_out(order)


@router.post(
    "/{order_id}/place",
    response_model=OrderOut,
    summary="Place an order",
    description=(
        "Place a cart (CREATED -> PAID placeholder). Validates menu item availability and "
        "re-snapshots pricing. Idempotent: if already placed, returns current order state."
    ),
)
async def place_order(
    order_id: UUID,
    payload: PlaceOrderRequest,
    current_user: User = Depends(require_roles(["customer", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Place an order.

    For now, this endpoint marks the order as PAID to represent a successful payment step.
    (Payment integration can later replace this with real payment confirmation.)

    Idempotency:
    - If order already moved past CREATED, return current state.
    """
    order = await _get_order_or_404(session, order_id)
    _assert_customer_owns_order(order, current_user)

    current_status = OrderStatus(order.status)
    if current_status != OrderStatus.CREATED:
        # Already placed or beyond; return current state (idempotent).
        return _order_to_out(order)

    if not order.items:
        raise HTTPException(status_code=400, detail="Cannot place an empty cart")

    # Validate restaurant still active
    await _validate_restaurant_is_orderable(session, order.restaurant_id)

    # Re-validate each item availability and refresh pricing snapshot
    for it in order.items:
        menu_item = await _load_menu_item_for_cart(session, it.menu_item_id, order.restaurant_id)
        it.unit_price_cents = int(menu_item.price_cents)
        it.currency = menu_item.currency
        it.name = menu_item.name

    _recompute_totals(order)

    # Transition to PAID as part of "place" placeholder.
    order.status = OrderStatus.PAID.value
    order.placed_at = datetime.now(timezone.utc)

    await session.commit()
    order = await _get_order_or_404(session, order_id)
    return _order_to_out(order)


@router.get(
    "",
    response_model=list[OrderOut],
    summary="List my orders",
    description="List orders for the current user (customers see their own; admin can filter by customer_user_id).",
)
async def list_orders(
    status_filter: Optional[OrderStatus] = Query(None, description="Optional status filter."),
    customer_user_id: Optional[int] = Query(
        None, description="Admin-only: filter by customer user id."
    ),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[OrderOut]:
    actor = _actor_for_user(current_user)

    stmt = select(Order).options(selectinload(Order.items)).order_by(Order.created_at.desc())

    if actor != "admin":
        stmt = stmt.where(Order.customer_user_id == current_user.id)
    else:
        if customer_user_id is not None:
            stmt = stmt.where(Order.customer_user_id == customer_user_id)

    if status_filter is not None:
        stmt = stmt.where(Order.status == status_filter.value)

    result = await session.execute(stmt)
    orders = result.scalars().all()
    return [OrderOut.model_validate(o) for o in orders]


@router.post(
    "/{order_id}/status",
    response_model=OrderOut,
    summary="Update order status",
    description=(
        "Transition an order to a new status with role checks:\n"
        "- customer: cancel before delivery\n"
        "- restaurant_owner: PAID->PREPARING->READY_FOR_PICKUP (or cancel)\n"
        "- courier: READY_FOR_PICKUP->OUT_FOR_DELIVERY->DELIVERED\n"
        "- admin: any status\n"
    ),
)
async def update_order_status(
    order_id: UUID,
    payload: UpdateOrderStatusRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Update order status with ownership + role validation.

    Additional restrictions:
    - Restaurant actions require the user be owner of the restaurant for this order (unless admin).
    - Courier actions require 'courier' role; assignment is best-effort: on transition to OUT_FOR_DELIVERY
      the courier_user_id will be set if empty.
    """
    order = await _get_order_or_404(session, order_id)

    actor = _actor_for_user(current_user)
    target = payload.status

    # Ownership check for customer access; admin bypasses.
    if actor == "customer":
        _assert_customer_owns_order(order, current_user)

    # Restaurant manage access check
    if actor == "restaurant":
        await _assert_restaurant_manage_access(session, order.restaurant_id, current_user)

    # Courier role check handled by actor selection: if no courier role, actor won't be courier.
    if actor == "courier" and not _has_role(current_user, "courier"):
        raise HTTPException(status_code=403, detail="Requires courier role")

    allowed = _allowed_next_statuses_for_actor(order, actor)
    if target not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status transition from {order.status} to {target.value} for {actor}",
        )

    # Apply transition (idempotent if already same target - but our allowed set excludes same)
    order.status = target.value

    # Side-effects
    if target == OrderStatus.OUT_FOR_DELIVERY and order.courier_user_id is None and actor == "courier":
        order.courier_user_id = current_user.id

    await session.commit()
    order = await _get_order_or_404(session, order_id)
    return _order_to_out(order)
