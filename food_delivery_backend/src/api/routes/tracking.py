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
from src.models.auth import Role, User, UserRole
from src.models.orders import Order
from src.models.restaurants import Restaurant
from src.models.tracking import OrderTrackingEvent
from src.schemas.tracking import TrackingEventCreateRequest, TrackingEventOut
from src.security.auth import decode_access_token
from src.services.tracking_broker import publish_tracking_event, subscribe_order, unsubscribe_order

router = APIRouter(prefix="/tracking", tags=["tracking"])


def _has_role(user: User, role_name: str) -> bool:
    roles: list[Role] = getattr(user, "_role_objects", [])
    return any(r.name == role_name for r in roles)


def _is_admin(user: User) -> bool:
    return _has_role(user, "admin")


async def _get_order_or_404(session: AsyncSession, order_id: UUID) -> Order:
    order = await session.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


async def _assert_can_subscribe(session: AsyncSession, order: Order, user: User) -> None:
    """
    Subscribers allowed:
    - customer who owns the order
    - assigned courier (order.courier_user_id)
    - restaurant_owner who owns the restaurant for the order
    - admin
    """
    if _is_admin(user):
        return

    if int(order.customer_user_id) == int(user.id):
        return

    if order.courier_user_id is not None and int(order.courier_user_id) == int(user.id):
        return

    # restaurant_owner must own the restaurant record
    if _has_role(user, "restaurant_owner"):
        restaurant = await session.get(Restaurant, order.restaurant_id)
        if restaurant and restaurant.owner_user_id is not None and str(restaurant.owner_user_id) == str(user.id):
            return

    raise HTTPException(status_code=403, detail="Not permitted to subscribe to this order's tracking stream")


async def _assert_can_publish(session: AsyncSession, order: Order, user: User) -> None:
    """
    Publishers allowed:
    - assigned courier
    - restaurant_owner who owns the restaurant
    - admin

    Customers are not allowed to push tracking events.
    """
    if _is_admin(user):
        return

    if order.courier_user_id is not None and int(order.courier_user_id) == int(user.id):
        return

    if _has_role(user, "restaurant_owner"):
        restaurant = await session.get(Restaurant, order.restaurant_id)
        if restaurant and restaurant.owner_user_id is not None and str(restaurant.owner_user_id) == str(user.id):
            return

    raise HTTPException(status_code=403, detail="Not permitted to publish tracking updates for this order")


async def _load_user_with_roles(session: AsyncSession, user_id: int) -> Optional[User]:
    """Load User and attach roles to `_role_objects` (mirrors deps.auth behavior, but WS-friendly)."""
    user = await session.get(User, user_id)
    if not user:
        return None
    result = await session.execute(
        select(Role).join(UserRole, Role.id == UserRole.role_id).where(UserRole.user_id == user.id)
    )
    setattr(user, "_role_objects", result.scalars().all())
    return user


async def _ws_get_current_user(session: AsyncSession, token: str) -> User:
    """
    WebSocket-compatible auth helper.

    The REST dependencies use OAuth2PasswordBearer which relies on Request.
    For WebSockets, we accept the JWT as:
      - query param ?token=...
      - OR Authorization: Bearer <token> header
    """
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")

    try:
        payload = decode_access_token(token)
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid token")
        user_id = int(sub)
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await _load_user_with_roles(session, user_id)
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
        select(OrderTrackingEvent)
        .where(OrderTrackingEvent.order_id == order_id)
        .order_by(OrderTrackingEvent.id.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    events = list(result.scalars().all())
    events.reverse()  # oldest -> newest for frontend convenience
    return [TrackingEventOut.model_validate(e) for e in events]


# PUBLIC_INTERFACE
@router.post(
    "/orders/{order_id}/events",
    response_model=TrackingEventOut,
    status_code=status.HTTP_201_CREATED,
    summary="Append a tracking event and publish it",
    description=(
        "Appends a tracking event (status + optional location) and publishes to real-time subscribers. "
        "Allowed for assigned courier, restaurant_owner for the order's restaurant, or admin."
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

    Frontend usage:
    - Courier/restaurant app calls this endpoint on state transitions or periodically (location pings).
    - Customer UI listens via WebSocket and also can fetch /tracking/orders/{order_id}/events for history.
    """
    order = await _get_order_or_404(session, order_id)
    await _assert_can_publish(session, order, current_user)

    ev = OrderTrackingEvent(
        order_id=order_id,
        status=payload.status.strip(),
        note=payload.note,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    session.add(ev)
    await session.commit()
    await session.refresh(ev)

    out = TrackingEventOut.model_validate(ev)

    # Publish to subscribers (best-effort).
    await publish_tracking_event(order_id=order_id, event_payload=out.model_dump())

    return out


# PUBLIC_INTERFACE
@router.websocket("/ws/orders/{order_id}")
async def ws_order_tracking(websocket: WebSocket, order_id: UUID) -> None:
    """
    WebSocket real-time order tracking stream.

    Authentication:
      - Pass JWT access token as query param:  ws://.../tracking/ws/orders/{order_id}?token=...
        (recommended for browsers)
      - Alternatively, send `Authorization: Bearer <token>` header if your WS client supports it.

    Authorization:
      - customer who owns the order
      - assigned courier
      - restaurant_owner of the order's restaurant
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

    # Manually acquire a session from dependency generator.
    gen = get_db_session()
    session: AsyncSession = await gen.__anext__()  # type: ignore[misc]

    try:
        try:
            current_user = await _ws_get_current_user(session, token or "")
        except HTTPException:
            await websocket.close(code=4401)  # unauthorized
            return

        order = await _get_order_or_404(session, order_id)
        try:
            await _assert_can_subscribe(session, order, current_user)
        except HTTPException:
            await websocket.close(code=4403)  # forbidden
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
