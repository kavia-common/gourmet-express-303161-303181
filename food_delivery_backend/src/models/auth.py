from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for ORM models."""


class UserRole(str, Enum):
    """Seeded Postgres enum `user_role`."""

    customer = "customer"
    restaurant_admin = "restaurant_admin"
    delivery_person = "delivery_person"
    admin = "admin"


class User(Base):
    """Application user (seeded schema: UUID PK + enum role)."""

    __tablename__ = "users"

    # Postgres seeded schema: id uuid PRIMARY KEY DEFAULT gen_random_uuid()
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)

    # Seeded schema uses password_hash (nullable) and full_name/phone.
    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role", native_enum=True),
        nullable=False,
        server_default=UserRole.customer.value,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# -----------------------------------------------------------------------------
# Backwards-compatibility shims
# -----------------------------------------------------------------------------
# Older code used a roles table + user_roles join. The seeded DB does not have
# those tables. We keep these names importable so any leftover references fail
# fast with a clear message instead of an ImportError.
class Role:  # pragma: no cover
    """Compatibility shim: seeded DB does not have a roles table."""

    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError("Seeded Postgres schema does not include a roles table; use users.role enum.")


class UserRoleLink:  # pragma: no cover
    """Compatibility shim for legacy user_roles join table."""

    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError("Seeded Postgres schema does not include a user_roles join table; use users.role enum.")
