from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class RestaurantBase(BaseModel):
    name: str = Field(..., min_length=1, description="Restaurant display name.")
    description: Optional[str] = Field(None, description="Restaurant description.")
    phone: Optional[str] = Field(None, description="Restaurant phone number.")

    address_line1: Optional[str] = Field(None, description="Street address line 1.")
    address_line2: Optional[str] = Field(None, description="Street address line 2.")
    city: Optional[str] = Field(None, description="City.")
    state: Optional[str] = Field(None, description="State/region.")
    postal_code: Optional[str] = Field(None, description="Postal/ZIP code.")

    latitude: Optional[float] = Field(None, description="Latitude in decimal degrees.")
    longitude: Optional[float] = Field(None, description="Longitude in decimal degrees.")

    is_active: bool = Field(True, description="Whether the restaurant is active/visible.")


class RestaurantCreate(RestaurantBase):
    owner_user_id: Optional[UUID] = Field(
        None,
        description=(
            "Owner user id. If omitted, defaults to the authenticated user (restaurant_owner) "
            "or may be set by admin."
        ),
    )


class RestaurantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, description="Restaurant display name.")
    description: Optional[str] = Field(None, description="Restaurant description.")
    phone: Optional[str] = Field(None, description="Restaurant phone number.")

    address_line1: Optional[str] = Field(None, description="Street address line 1.")
    address_line2: Optional[str] = Field(None, description="Street address line 2.")
    city: Optional[str] = Field(None, description="City.")
    state: Optional[str] = Field(None, description="State/region.")
    postal_code: Optional[str] = Field(None, description="Postal/ZIP code.")

    latitude: Optional[float] = Field(None, description="Latitude in decimal degrees.")
    longitude: Optional[float] = Field(None, description="Longitude in decimal degrees.")

    is_active: Optional[bool] = Field(None, description="Whether the restaurant is active/visible.")


class RestaurantOut(RestaurantBase):
    id: UUID = Field(..., description="Restaurant id.")
    owner_user_id: Optional[UUID] = Field(None, description="Owner user id (if assigned).")

    class Config:
        from_attributes = True


class MenuBase(BaseModel):
    name: str = Field(..., min_length=1, description="Menu name (unique per restaurant).")
    description: Optional[str] = Field(None, description="Menu description.")
    is_active: bool = Field(True, description="Whether the menu is active.")


class MenuCreate(MenuBase):
    restaurant_id: UUID = Field(..., description="Parent restaurant id.")


class MenuUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, description="Menu name (unique per restaurant).")
    description: Optional[str] = Field(None, description="Menu description.")
    is_active: Optional[bool] = Field(None, description="Whether the menu is active.")


class MenuOut(MenuBase):
    id: UUID = Field(..., description="Menu id.")
    restaurant_id: UUID = Field(..., description="Parent restaurant id.")

    class Config:
        from_attributes = True


class MenuItemBase(BaseModel):
    name: str = Field(..., min_length=1, description="Menu item name.")
    description: Optional[str] = Field(None, description="Menu item description.")
    price_cents: int = Field(..., ge=0, description="Price in cents (>= 0).")
    currency: str = Field("USD", min_length=1, description="ISO currency code (default USD).")
    image_url: Optional[str] = Field(None, description="Optional image URL.")
    is_available: bool = Field(True, description="Whether the item can be ordered.")


class MenuItemCreate(MenuItemBase):
    menu_id: UUID = Field(..., description="Parent menu id.")


class MenuItemUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, description="Menu item name.")
    description: Optional[str] = Field(None, description="Menu item description.")
    price_cents: Optional[int] = Field(None, ge=0, description="Price in cents (>= 0).")
    currency: Optional[str] = Field(None, min_length=1, description="ISO currency code.")
    image_url: Optional[str] = Field(None, description="Optional image URL.")
    is_available: Optional[bool] = Field(None, description="Whether the item can be ordered.")


class MenuItemOut(MenuItemBase):
    id: UUID = Field(..., description="Menu item id.")
    menu_id: UUID = Field(..., description="Parent menu id.")
    restaurant_id: UUID = Field(..., description="Restaurant id (denormalized, per schema).")

    class Config:
        from_attributes = True
