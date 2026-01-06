from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.models.auth import Base


class OrderTrackingEvent(Base):
    """
    Tracking events appended over the lifetime of an order.

    This table is the source-of-truth for real-time order tracking streams (WebSocket/SSE).
    When a new row is inserted, the API publishes a corresponding event to active subscribers.
    """

    __tablename__ = "order_tracking_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Human-readable status (e.g., "PICKED_UP", "EN_ROUTE", "DELIVERED").
    # Note: this is intentionally separate from Order.status (lifecycle enum), since tracking can be richer.
    status: Mapped[str] = mapped_column(String(64), nullable=False)

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    latitude: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
