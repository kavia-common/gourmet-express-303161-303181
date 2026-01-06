from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.auth import Base


class OrderStatus(str, Enum):
    """Seeded Postgres enum `order_status`."""

    pending = "pending"
    confirmed = "confirmed"
    preparing = "preparing"
    ready_for_pickup = "ready_for_pickup"
    picked_up = "picked_up"
    delivered = "delivered"
    cancelled = "cancelled"


class DeliveryStatus(str, Enum):
    """Seeded Postgres enum `delivery_status`."""

    unassigned = "unassigned"
    assigned = "assigned"
    picked_up = "picked_up"
    delivered = "delivered"
    cancelled = "cancelled"


class PaymentStatus(str, Enum):
    """Seeded Postgres enum `payment_status`."""

    pending = "pending"
    authorized = "authorized"
    paid = "paid"
    failed = "failed"
    refunded = "refunded"


class Order(Base):
    """Order aggregate (seeded schema: UUID ids, enum status, placed_at + updated_at)."""

    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)

    customer_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    restaurant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("restaurants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, name="order_status", native_enum=True),
        nullable=False,
        server_default=OrderStatus.pending.value,
        index=True,
    )

    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    delivery_fee_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tax_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="USD")

    delivery_address_line1: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delivery_address_line2: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delivery_city: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delivery_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delivery_postal_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class OrderItem(Base):
    """Line item for an order (seeded schema snapshot fields)."""

    __tablename__ = "order_items"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)

    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    menu_item_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("menu_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    price_cents_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped["Order"] = relationship("Order", back_populates="items")


class DeliveryAssignment(Base):
    """Seeded schema `delivery_assignments` (one per order)."""

    __tablename__ = "delivery_assignments"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    delivery_user_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status: Mapped[DeliveryStatus] = mapped_column(
        SAEnum(DeliveryStatus, name="delivery_status", native_enum=True),
        nullable=False,
        server_default=DeliveryStatus.unassigned.value,
        index=True,
    )

    assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    picked_up_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Payment(Base):
    """Seeded schema `payments` (unique per order)."""

    __tablename__ = "payments"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="mock")
    provider_payment_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status", native_enum=True),
        nullable=False,
        server_default=PaymentStatus.pending.value,
        index=True,
    )

    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="USD")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TrackingEvent(Base):
    """Seeded schema `tracking_events`."""

    __tablename__ = "tracking_events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[Optional[OrderStatus]] = mapped_column(
        SAEnum(OrderStatus, name="order_status", native_enum=True),
        nullable=True,
    )
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    latitude: Mapped[Optional[float]] = mapped_column(nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
