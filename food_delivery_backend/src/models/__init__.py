"""
ORM models package aligned to the seeded Postgres schema.

Seeded public tables:
- users (UUID PK, role enum)
- restaurants, menus, menu_items
- orders, order_items
- delivery_assignments
- payments
- tracking_events
"""

from src.models.auth import Base, User, UserRole
from src.models.orders import (
    DeliveryAssignment,
    DeliveryStatus,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentStatus,
    TrackingEvent,
)
from src.models.restaurants import Menu, MenuItem, Restaurant

__all__ = [
    "Base",
    "User",
    "UserRole",
    "Restaurant",
    "Menu",
    "MenuItem",
    "Order",
    "OrderItem",
    "OrderStatus",
    "DeliveryAssignment",
    "DeliveryStatus",
    "Payment",
    "PaymentStatus",
    "TrackingEvent",
]
