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
from src.models import MenuItem, Order, OrderItem, OrderStatus, Restaurant, User, UserRole
from src.schemas.orders import (
    CartCreateRequest,
    CartItemRemoveRequest,
    CartItemUpsertRequest,
    OrderOut,
    PlaceOrderRequest,
    UpdateOrderStatusRequest,
)

router = APIRouter(prefix="/orders", tags=["orders"])


def _is_admin(user: User) -> bool:
    role_value = user.role.value if hasattr(user.role, "value") else str(user.role)
    return role_value == UserRole.admin.value


async def _get_order_or_404(session: AsyncSession, order_id: UUID) -> Order:
    order = await session.get(Order, order_id, options=(selectinload(Order.items),))
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
        raise HTTPException(status_code=403, detail="Not permitted to manage orders for this restaurant")


def _recompute_totals(order: Order) -> None:
    subtotal = 0
    for it in order.items:
        subtotal += int(it.price_cents_snapshot) * int(it.quantity)
        it.line_total_cents = int(it.price_cents_snapshot) * int(it.quantity)

    order.subtotal_cents = subtotal
    order.delivery_fee_cents = 0
    order.tax_cents = 0
    order.total_cents = int(order.subtotal_cents) + int(order.delivery_fee_cents) + int(order.tax_cents)


async def _load_menu_item_for_cart(session: AsyncSession, menu_item_id: UUID, restaurant_id: UUID) -> MenuItem:
    item = await session.get(MenuItem, menu_item_id)
    if not item or item.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="Menu item not found for this restaurant")
    if not item.is_available:
        raise HTTPException(status_code=400, detail="Menu item is not available")
    return item


async def _validate_restaurant_is_orderable(session: AsyncSession, restaurant_id: UUID) -> None:
    restaurant = await session.get(Restaurant, restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    if not restaurant.is_active:
        raise HTTPException(status_code=400, detail="Restaurant is not accepting orders")


def _order_to_out(order: Order) -> OrderOut:
    """
    Convert ORM Order to API OrderOut.

    Seeded DB does not have orders.created_at; it has placed_at + updated_at.
    For API compatibility we map:
      created_at = placed_at
      courier_user_id = delivery_assignments.delivery_user_id (not loaded here; left null)
    """
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
            # order_items store snapshots; synthesize fields expected by API
            # currency snapshot isn't stored per line in seeded schema; reuse order currency
            # unit_price_cents maps from price_cents_snapshot
            # name maps from name_snapshot
            type("Tmp", (), {})()  # placeholder to keep type checkers quiet
            for _ in []
        ],
    ).model_copy(
        update={
            "items": [
                {
                    "id": it.id,
                    "menu_item_id": it.menu_item_id,
                    "name": it.name_snapshot,
                    "quantity": it.quantity,
                    "unit_price_cents": it.price_cents_snapshot,
                    "currency": order.currency,
                }
                for it in (order.items or [])
            ]
        }
    )


# PUBLIC_INTERFACE
@router.post(
    "/cart",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a cart",
    description="Create a new cart (order with status pending) for the authenticated customer.",
)
async def create_cart(
    payload: CartCreateRequest,
    current_user: User = Depends(require_roles(["customer", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Create a new cart for a restaurant.

    Seeded DB uses `order_status.pending` as the initial state.
    """
    await _validate_restaurant_is_orderable(session, payload.restaurant_id)

    order = Order(
        id=uuid4(),
        customer_user_id=current_user.id,
        restaurant_id=payload.restaurant_id,
        status=OrderStatus.pending,
        currency="USD",
        subtotal_cents=0,
        delivery_fee_cents=0,
        tax_cents=0,
        total_cents=0,
        placed_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(order)
    await session.commit()

    order = await _get_order_or_404(session, order.id)
    return _order_to_out(order)


# PUBLIC_INTERFACE
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


# PUBLIC_INTERFACE
@router.post(
    "/{order_id}/cart/items",
    response_model=OrderOut,
    summary="Add/update an item in the cart",
    description="Upsert a line item in a cart (status pending). Idempotent: setting same quantity yields same cart.",
)
async def upsert_cart_item(
    order_id: UUID,
    payload: CartItemUpsertRequest,
    current_user: User = Depends(require_roles(["customer", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Upsert a cart item by menu_item_id.

    Seeded schema uses:
      - order_items.name_snapshot
      - order_items.price_cents_snapshot
      - order_items.line_total_cents
    """
    order = await _get_order_or_404(session, order_id)
    _assert_customer_owns_order(order, current_user)

    if order.status != OrderStatus.pending:
        raise HTTPException(status_code=400, detail="Cart is not editable")

    menu_item = await _load_menu_item_for_cart(session, payload.menu_item_id, order.restaurant_id)

    existing = next((it for it in order.items if it.menu_item_id == payload.menu_item_id), None)
    if existing:
        existing.quantity = payload.quantity
        existing.price_cents_snapshot = int(menu_item.price_cents)
        existing.name_snapshot = menu_item.name
        existing.line_total_cents = int(menu_item.price_cents) * int(payload.quantity)
    else:
        order.items.append(
            OrderItem(
                id=uuid4(),
                order_id=order.id,
                menu_item_id=payload.menu_item_id,
                quantity=payload.quantity,
                price_cents_snapshot=int(menu_item.price_cents),
                name_snapshot=menu_item.name,
                line_total_cents=int(menu_item.price_cents) * int(payload.quantity),
            )
        )

    _recompute_totals(order)
    order.updated_at = datetime.now(timezone.utc)
    await session.commit()

    order = await _get_order_or_404(session, order_id)
    return _order_to_out(order)


# PUBLIC_INTERFACE
@router.delete(
    "/{order_id}/cart/items",
    response_model=OrderOut,
    summary="Remove an item from the cart",
    description="Remove a line item from a cart (status pending). Idempotent: removing missing item returns cart unchanged.",
)
async def remove_cart_item(
    order_id: UUID,
    payload: CartItemRemoveRequest,
    current_user: User = Depends(require_roles(["customer", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    order = await _get_order_or_404(session, order_id)
    _assert_customer_owns_order(order, current_user)

    if order.status != OrderStatus.pending:
        raise HTTPException(status_code=400, detail="Cart is not editable")

    before = len(order.items or [])
    order.items = [it for it in (order.items or []) if it.menu_item_id != payload.menu_item_id]
    after = len(order.items or [])

    if before != after:
        _recompute_totals(order)
        order.updated_at = datetime.now(timezone.utc)
        await session.commit()

    order = await _get_order_or_404(session, order_id)
    return _order_to_out(order)


# PUBLIC_INTERFACE
@router.post(
    "/{order_id}/place",
    response_model=OrderOut,
    summary="Place an order",
    description="Place a cart (pending -> confirmed). Revalidates pricing snapshots.",
)
async def place_order(
    order_id: UUID,
    payload: PlaceOrderRequest,
    current_user: User = Depends(require_roles(["customer", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Place an order.

    In the seeded DB, the next state after `pending` is `confirmed`.
    """
    order = await _get_order_or_404(session, order_id)
    _assert_customer_owns_order(order, current_user)

    if order.status != OrderStatus.pending:
        return _order_to_out(order)

    if not order.items:
        raise HTTPException(status_code=400, detail="Cannot place an empty cart")

    await _validate_restaurant_is_orderable(session, order.restaurant_id)

    for it in order.items:
        menu_item = await _load_menu_item_for_cart(session, it.menu_item_id, order.restaurant_id)
        it.price_cents_snapshot = int(menu_item.price_cents)
        it.name_snapshot = menu_item.name
        it.line_total_cents = int(menu_item.price_cents) * int(it.quantity)

    _recompute_totals(order)

    order.status = OrderStatus.confirmed
    order.updated_at = datetime.now(timezone.utc)

    await session.commit()
    order = await _get_order_or_404(session, order_id)
    return _order_to_out(order)


# PUBLIC_INTERFACE
@router.get(
    "",
    response_model=list[OrderOut],
    summary="List my orders",
    description="List orders for the current user (customers see their own; admin can filter by customer_user_id).",
)
async def list_orders(
    status_filter: Optional[OrderStatus] = Query(None, description="Optional status filter."),
    customer_user_id: Optional[UUID] = Query(None, description="Admin-only: filter by customer user id."),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[OrderOut]:
    stmt = select(Order).options(selectinload(Order.items)).order_by(Order.updated_at.desc())

    if not _is_admin(current_user):
        stmt = stmt.where(Order.customer_user_id == current_user.id)
    else:
        if customer_user_id is not None:
            stmt = stmt.where(Order.customer_user_id == customer_user_id)

    if status_filter is not None:
        stmt = stmt.where(Order.status == status_filter)

    result = await session.execute(stmt)
    orders = result.scalars().all()
    return [_order_to_out(o) for o in orders]


# PUBLIC_INTERFACE
@router.post(
    "/{order_id}/status",
    response_model=OrderOut,
    summary="Update order status",
    description="Transition an order to a new status with role checks (seeded DB enum).",
)
async def update_order_status(
    order_id: UUID,
    payload: UpdateOrderStatusRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> OrderOut:
    """
    Update order status with ownership + role validation.

    Mapping of roles:
      - customer: can cancel while pending/confirmed/preparing/ready_for_pickup
      - restaurant_admin: confirmed -> preparing -> ready_for_pickup (or cancelled)
      - delivery_person: ready_for_pickup -> picked_up -> delivered
      - admin: any status
    """
    order = await _get_order_or_404(session, order_id)
    target = payload.status

    if not _is_admin(current_user):
        role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)

        if role == UserRole.customer.value:
            _assert_customer_owns_order(order, current_user)
            if target != OrderStatus.cancelled:
                raise HTTPException(status_code=400, detail="Customers may only cancel orders")
            if order.status not in {OrderStatus.pending, OrderStatus.confirmed, OrderStatus.preparing, OrderStatus.ready_for_pickup}:
                raise HTTPException(status_code=400, detail="Cannot cancel at this stage")

        elif role == UserRole.restaurant_admin.value:
            await _assert_restaurant_manage_access(session, order.restaurant_id, current_user)
            if order.status == OrderStatus.confirmed and target not in {OrderStatus.preparing, OrderStatus.cancelled}:
                raise HTTPException(status_code=400, detail="Invalid transition for restaurant_admin")
            if order.status == OrderStatus.preparing and target not in {OrderStatus.ready_for_pickup, OrderStatus.cancelled}:
                raise HTTPException(status_code=400, detail="Invalid transition for restaurant_admin")
            if order.status not in {OrderStatus.confirmed, OrderStatus.preparing}:
                raise HTTPException(status_code=400, detail="Order not in a restaurant-manageable status")

        elif role == UserRole.delivery_person.value:
            # Delivery transitions are enforced via delivery endpoints ideally; keep minimal here.
            if order.status == OrderStatus.ready_for_pickup and target != OrderStatus.picked_up:
                raise HTTPException(status_code=400, detail="Invalid transition for delivery_person")
            if order.status == OrderStatus.picked_up and target != OrderStatus.delivered:
                raise HTTPException(status_code=400, detail="Invalid transition for delivery_person")
            if order.status not in {OrderStatus.ready_for_pickup, OrderStatus.picked_up}:
                raise HTTPException(status_code=400, detail="Order not in a courier-manageable status")
        else:
            raise HTTPException(status_code=403, detail="Not permitted to update order status")

    order.status = target
    order.updated_at = datetime.now(timezone.utc)
    await session.commit()

    order = await _get_order_or_404(session, order_id)
    return _order_to_out(order)
