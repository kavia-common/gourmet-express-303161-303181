from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., description="Unique email address for the user.")
    password: str = Field(..., min_length=8, description="Plaintext password (min 8 chars).")
    full_name: Optional[str] = Field(None, description="Optional full name.")


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="User email.")
    password: str = Field(..., description="User password.")


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="JWT access token.")
    token_type: str = Field("bearer", description="Token type (always 'bearer').")


class RoleOut(BaseModel):
    id: int = Field(..., description="Role id.")
    name: str = Field(..., description="Role name.")
    description: Optional[str] = Field(None, description="Role description.")


class UserOut(BaseModel):
    id: int = Field(..., description="User id.")
    email: EmailStr = Field(..., description="User email.")
    full_name: Optional[str] = Field(None, description="Full name.")
    is_active: bool = Field(..., description="Whether the user is active.")
    roles: List[RoleOut] = Field(default_factory=list, description="Roles attached to the user.")


class AttachRoleRequest(BaseModel):
    role_name: str = Field(..., min_length=1, description="Role name to attach (case-sensitive).")
