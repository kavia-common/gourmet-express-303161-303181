from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.auth import Base


class OrderStatus(str, Enum):
    """Order lifecycle states."""

    CREATED = "CREATED"  # cart created, editable
    PAID = "PAID"  # payment confirmed
    PREPARING = "PREPARING"  # restaurant accepted and is preparing
    READY_FOR_PICKUP = "READY_FOR_PICKUP"  # ready for courier pickup
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"  # courier en route
    DELIVERED = "DELIVERED"  # delivered to customer
    CANCELED = "CANCELED"  # terminal


class Order(Base):
    """Order aggregate (cart + placed order + delivery tracking state)."""

    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True)

    # Customer who owns the order/cart
    customer_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Restaurant that fulfills the order
    restaurant_id: Mapped[UUID] = mapped_column(
        ForeignKey("restaurants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False, default=OrderStatus.CREATED.value)

    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_fee_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Assigned courier (optional, can be assigned later)
    courier_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    placed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[List["OrderItem"]] = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class OrderItem(Base):
    """Line item for an order (captures price at time of ordering)."""

    __tablename__ = "order_items"
    __table_args__ = (UniqueConstraint("order_id", "menu_item_id", name="uq_order_item_unique"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)

    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    menu_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("menu_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Snapshot of menu item data at time of placing/updates in cart
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    order: Mapped["Order"] = relationship("Order", back_populates="items")
