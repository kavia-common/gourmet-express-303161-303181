from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from src.models import OrderStatus


class TrackingEventCreateRequest(BaseModel):
    status: str = Field(
        ...,
        min_length=1,
        description="Tracking status label (will be mapped to order_status enum when possible).",
    )
    note: Optional[str] = Field(None, description="Optional note accompanying the tracking event (stored as message).")
    latitude: Optional[float] = Field(None, description="Optional latitude in decimal degrees.")
    longitude: Optional[float] = Field(None, description="Optional longitude in decimal degrees.")


class TrackingEventOut(BaseModel):
    id: UUID = Field(..., description="Tracking event id (UUID).")
    order_id: UUID = Field(..., description="Order id this tracking event belongs to.")
    event_type: str = Field(..., description="Event type label (e.g. status_update, location_ping).")
    status: Optional[OrderStatus] = Field(None, description="Optional order_status enum value.")
    message: Optional[str] = Field(None, description="Optional message/note.")
    latitude: Optional[float] = Field(None, description="Optional latitude.")
    longitude: Optional[float] = Field(None, description="Optional longitude.")
    created_at: datetime = Field(..., description="Timestamp when the event was created.")

    class Config:
        from_attributes = True
