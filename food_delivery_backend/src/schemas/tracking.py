from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TrackingEventCreateRequest(BaseModel):
    status: str = Field(..., min_length=1, description="Tracking status label (e.g., PICKED_UP, EN_ROUTE).")
    note: Optional[str] = Field(None, description="Optional note accompanying the tracking event.")
    latitude: Optional[float] = Field(None, description="Optional latitude in decimal degrees.")
    longitude: Optional[float] = Field(None, description="Optional longitude in decimal degrees.")


class TrackingEventOut(BaseModel):
    id: int = Field(..., description="Tracking event id.")
    order_id: UUID = Field(..., description="Order id this tracking event belongs to.")
    status: str = Field(..., description="Tracking status label.")
    note: Optional[str] = Field(None, description="Optional note.")
    latitude: Optional[float] = Field(None, description="Optional latitude.")
    longitude: Optional[float] = Field(None, description="Optional longitude.")
    created_at: datetime = Field(..., description="Timestamp when the event was created.")

    class Config:
        from_attributes = True
