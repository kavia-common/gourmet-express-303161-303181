from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from src.models import OrderStatus


class OrderItemOut(BaseModel):
    id: UUID = Field(..., description="Order item id.")
    menu_item_id: UUID = Field(..., description="Menu item id.")
    name: str = Field(..., description="Item name snapshot.")
    quantity: int = Field(..., ge=1, description="Quantity (>= 1).")
    unit_price_cents: int = Field(..., ge=0, description="Unit price in cents at time of ordering.")
    currency: str = Field(..., min_length=1, description="Currency code for the item price snapshot.")

    class Config:
        from_attributes = True


class OrderOut(BaseModel):
    id: UUID = Field(..., description="Order id.")
    customer_user_id: UUID = Field(..., description="Customer user id who owns the order.")
    restaurant_id: UUID = Field(..., description="Restaurant id for the order.")
    status: OrderStatus = Field(..., description="Current order status.")

    currency: str = Field(..., description="Order currency.")
    subtotal_cents: int = Field(..., ge=0, description="Subtotal in cents.")
    delivery_fee_cents: int = Field(..., ge=0, description="Delivery fee in cents.")
    total_cents: int = Field(..., ge=0, description="Total in cents.")

    courier_user_id: Optional[UUID] = Field(None, description="Assigned courier user id (if any).")
    placed_at: Optional[datetime] = Field(None, description="Timestamp when the order was placed.")

    items: List[OrderItemOut] = Field(default_factory=list, description="Order items.")

    created_at: datetime = Field(..., description="Created timestamp.")
    updated_at: datetime = Field(..., description="Updated timestamp.")

    class Config:
        from_attributes = True


class CartCreateRequest(BaseModel):
    restaurant_id: UUID = Field(..., description="Restaurant id that the cart belongs to.")


class CartItemUpsertRequest(BaseModel):
    menu_item_id: UUID = Field(..., description="Menu item id to add/update in cart.")
    quantity: int = Field(..., ge=1, description="Quantity to set for this item in the cart (>=1).")


class CartItemRemoveRequest(BaseModel):
    menu_item_id: UUID = Field(..., description="Menu item id to remove from the cart.")


class PlaceOrderRequest(BaseModel):
    idempotency_key: Optional[str] = Field(
        None,
        min_length=1,
        description=(
            "Optional idempotency key for place operation. If repeated, the server returns the "
            "current state of the already-placed order."
        ),
    )


class UpdateOrderStatusRequest(BaseModel):
    status: OrderStatus = Field(..., description="New status for the order.")
