from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict
from uuid import UUID


@dataclass(frozen=True)
class BrokerMessage:
    """
    Message published to subscribers.

    We keep this JSON-serializable so it can be pushed to WebSocket and SSE uniformly.
    """

    type: str
    payload: Dict[str, Any]

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "payload": self.payload}, default=str)


class OrderEventBroker:
    """
    Simple in-memory broker keyed by order_id.

    Implementation details:
    - Each subscriber gets its own asyncio.Queue (fan-out).
    - publish(order_id, msg) puts the message on every active subscriber queue.

    Note:
    - This is suitable for single-process dev/demo deployments.
    - For multi-instance production, replace with Redis pub/sub, NATS, etc.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._subscribers: dict[UUID, set[asyncio.Queue[BrokerMessage]]] = {}

    async def subscribe(self, order_id: UUID) -> asyncio.Queue[BrokerMessage]:
        async with self._lock:
            q: asyncio.Queue[BrokerMessage] = asyncio.Queue(maxsize=100)
            self._subscribers.setdefault(order_id, set()).add(q)
            return q

    async def unsubscribe(self, order_id: UUID, q: asyncio.Queue[BrokerMessage]) -> None:
        async with self._lock:
            if order_id not in self._subscribers:
                return
            self._subscribers[order_id].discard(q)
            if not self._subscribers[order_id]:
                self._subscribers.pop(order_id, None)

    async def publish(self, order_id: UUID, msg: BrokerMessage) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(order_id, set()))

        # Publish outside lock. If a queue is full, drop oldest by get_nowait to keep stream live.
        for q in queues:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                try:
                    _ = q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    # Give up on this queue for now.
                    pass


# A module-level singleton broker for the app process.
BROKER = OrderEventBroker()


# PUBLIC_INTERFACE
async def publish_tracking_event(order_id: UUID, event_payload: Dict[str, Any]) -> None:
    """Publish a tracking event payload to all subscribers of the given order."""
    await BROKER.publish(order_id, BrokerMessage(type="tracking_event", payload=event_payload))


# PUBLIC_INTERFACE
async def subscribe_order(order_id: UUID) -> asyncio.Queue[BrokerMessage]:
    """Subscribe to an order's event stream; returns a per-subscriber asyncio.Queue."""
    return await BROKER.subscribe(order_id)


# PUBLIC_INTERFACE
async def unsubscribe_order(order_id: UUID, q: asyncio.Queue[BrokerMessage]) -> None:
    """Unsubscribe a queue from an order's event stream."""
    await BROKER.unsubscribe(order_id, q)
