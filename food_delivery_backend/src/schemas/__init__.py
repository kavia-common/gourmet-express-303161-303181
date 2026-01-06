"""
Pydantic schemas package.
"""

from src.schemas.auth import (
    AttachRoleRequest,
    LoginRequest,
    RegisterRequest,
    RoleOut,
    TokenResponse,
    UserOut,
)
from src.schemas.restaurants import (
    MenuCreate,
    MenuItemCreate,
    MenuItemOut,
    MenuItemUpdate,
    MenuOut,
    MenuUpdate,
    RestaurantCreate,
    RestaurantOut,
    RestaurantUpdate,
)

__all__ = [
    "RegisterRequest",
    "LoginRequest",
    "TokenResponse",
    "RoleOut",
    "UserOut",
    "AttachRoleRequest",
    "RestaurantCreate",
    "RestaurantUpdate",
    "RestaurantOut",
    "MenuCreate",
    "MenuUpdate",
    "MenuOut",
    "MenuItemCreate",
    "MenuItemUpdate",
    "MenuItemOut",
]
