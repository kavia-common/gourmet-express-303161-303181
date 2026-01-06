from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AssignCourierRequest(BaseModel):
    courier_user_id: int = Field(..., description="User id of the courier to assign.")
    note: Optional[str] = Field(None, description="Optional note/reason for assignment.")


class CourierActionRequest(BaseModel):
    note: Optional[str] = Field(None, description="Optional note (e.g., pickup details, delivery issues).")


class DeliveryAssignmentOut(BaseModel):
    order_id: UUID = Field(..., description="Order id.")
    courier_user_id: int = Field(..., description="Assigned courier user id.")

