from __future__ import annotations

import asyncio
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db_session
from src.deps.auth import get_current_user
from src.models import DeliveryAssignment, Order, OrderStatus, Restaurant, TrackingEvent, User, UserRole
from src.schemas.tracking import TrackingEventCreateRequest, TrackingEventOut
from src.security.auth import decode_access_token
from src.services.tracking_broker import publish_tracking_event, subscribe_order, unsubscribe_order

router = APIRouter(prefix="/tracking", tags=["tracking"])


def _is_admin(user: User) -> bool:
    return (user.role.value if hasattr(user.role, "value") else str(user.role)) == UserRole.admin.value


async def _get_order_or_404(session: AsyncSession, order_id: UUID) -> Order:
    order = await session.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


async def _get_assignment(session: AsyncSession, order_id: UUID) -> Optional[DeliveryAssignment]:
    stmt = select(DeliveryAssignment).where(DeliveryAssignment.order_id == order_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def _assert_can_subscribe(session: AsyncSession, order: Order, user: User) -> None:
    """
    Subscribers allowed:
    - customer who owns the order
    - assigned delivery_person (delivery_assignments.delivery_user_id)
    - restaurant_admin who owns the restaurant for the order
    - admin
    """
    if _is_admin(user):
        return

    if order.customer_user_id == user.id:
        return

    assignment = await _get_assignment(session, order.id)
    if assignment and assignment.delivery_user_id and assignment.delivery_user_id == user.id:
        return

    if (user.role.value if hasattr(user.role, "value") else str(user.role)) == UserRole.restaurant_admin.value:
        restaurant = await session.get(Restaurant, order.restaurant_id)
        if restaurant and restaurant.owner_user_id and restaurant.owner_user_id == user.id:
            return

    raise HTTPException(status_code=403, detail="Not permitted to subscribe to this order's tracking stream")


async def _assert_can_publish(session: AsyncSession, order: Order, user: User) -> None:
    """
    Publishers allowed:
    - assigned delivery_person
    - restaurant_admin (owner of restaurant)
    - admin
    """
    if _is_admin(user):
        return

    assignment = await _get_assignment(session, order.id)
    if assignment and assignment.delivery_user_id and assignment.delivery_user_id == user.id:
        return

    if (user.role.value if hasattr(user.role, "value") else str(user.role)) == UserRole.restaurant_admin.value:
        restaurant = await session.get(Restaurant, order.restaurant_id)
        if restaurant and restaurant.owner_user_id and restaurant.owner_user_id == user.id:
            return

    raise HTTPException(status_code=403, detail="Not permitted to publish tracking updates for this order")


async def _ws_get_current_user(session: AsyncSession, token: str) -> User:
    """
    WebSocket-compatible auth helper.

    JWT subject contains the UUID user id as string.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")

    try:
        payload = decode_access_token(token)
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid token")
        user_id = UUID(str(sub))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await session.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Inactive user")
    return user


# PUBLIC_INTERFACE
@router.get(
    "/orders/{order_id}/events",
    response_model=list[TrackingEventOut],
    summary="List tracking events for an order",
    description="Fetch persisted tracking events for an order. Same authorization rules as streaming subscription.",
)
async def list_tracking_events(
    order_id: UUID,
    limit: int = Query(50, ge=1, le=500, description="Max number of events to return."),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[TrackingEventOut]:
    """List tracking events (history) for an order."""
    order = await _get_order_or_404(session, order_id)
    await _assert_can_subscribe(session, order, current_user)

    stmt = (
        select(TrackingEvent)
        .where(TrackingEvent.order_id == order_id)
        .order_by(TrackingEvent.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    events = list(result.scalars().all())
    events.reverse()
    return [TrackingEventOut.model_validate(e) for e in events]


# PUBLIC_INTERFACE
@router.post(
    "/orders/{order_id}/events",
    response_model=TrackingEventOut,
    status_code=status.HTTP_201_CREATED,
    summary="Append a tracking event and publish it",
    description=(
        "Appends a tracking event (status + optional location) and publishes to real-time subscribers. "
        "Allowed for assigned delivery_person, restaurant_admin for the order's restaurant, or admin."
    ),
)
async def append_tracking_event(
    order_id: UUID,
    payload: TrackingEventCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> TrackingEventOut:
    """
    Append and publish a tracking event.

    Seeded DB uses `tracking_events` with:
      - event_type (text)
      - status (order_status enum, nullable)
      - message (text)
    """
    order = await _get_order_or_404(session, order_id)
    await _assert_can_publish(session, order, current_user)

    # Try to map provided status string to order_status enum; if not possible keep it as a message-only event.
    mapped_status: Optional[OrderStatus] = None
    try:
        mapped_status = OrderStatus(payload.status.strip().lower())
    except Exception:
        mapped_status = None

    ev = TrackingEvent(
        order_id=order_id,
        event_type="status_update" if mapped_status else "note",
        status=mapped_status,
        message=payload.note or payload.status.strip(),
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    session.add(ev)
    await session.commit()
    await session.refresh(ev)

    out = TrackingEventOut.model_validate(ev)

    await publish_tracking_event(order_id=order_id, event_payload=out.model_dump())
    return out


# PUBLIC_INTERFACE
@router.websocket("/ws/orders/{order_id}")
async def ws_order_tracking(websocket: WebSocket, order_id: UUID) -> None:
    """
    WebSocket real-time order tracking stream.

    Authentication:
      - Pass JWT access token as query param:  ws://.../tracking/ws/orders/{order_id}?token=...
      - Or Authorization: Bearer <token> header.

    Authorization:
      - customer who owns the order
      - assigned delivery_person
      - restaurant_admin owner of the order's restaurant
      - admin

    Message format (server -> client):
      {
        "type": "tracking_event",
        "payload": { ... TrackingEventOut ... }
      }
    """
    await websocket.accept()

    token: Optional[str] = websocket.query_params.get("token")
    if not token:
        auth_header = websocket.headers.get("authorization") or websocket.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()

    gen = get_db_session()
    session: AsyncSession = await gen.__anext__()  # type: ignore[misc]

    try:
        try:
            current_user = await _ws_get_current_user(session, token or "")
        except HTTPException:
            await websocket.close(code=4401)
            return

        order = await _get_order_or_404(session, order_id)
        try:
            await _assert_can_subscribe(session, order, current_user)
        except HTTPException:
            await websocket.close(code=4403)
            return

        q = await subscribe_order(order_id)

        await websocket.send_json({"type": "connected", "payload": {"order_id": str(order_id)}})

        while True:
            try:
                msg = await q.get()
                await websocket.send_text(msg.to_json())
            except WebSocketDisconnect:
                break
            except asyncio.CancelledError:
                break
            except Exception:
                break

        await unsubscribe_order(order_id, q)

    finally:
        try:
            await gen.aclose()
        except Exception:
            pass
