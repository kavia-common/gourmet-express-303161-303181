from __future__ import annotations

from typing import Callable, List
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db_session
from src.models.auth import User, UserRole
from src.security.auth import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# PUBLIC_INTERFACE
async def get_current_user(
    token: str = Depends(oauth2_scheme), session: AsyncSession = Depends(get_db_session)
) -> User:
    """Dependency that validates JWT and returns the current User (seeded schema: UUID ids)."""
    try:
        payload = decode_access_token(token)
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        user_id = UUID(str(sub))
    except (JWTError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = await session.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")
    return user


# PUBLIC_INTERFACE
def require_roles(required: List[str]) -> Callable:
    """Factory for a dependency that enforces the current user has one of required roles (via users.role enum)."""

    required_set = {r.strip() for r in required if r and r.strip()}

    async def _checker(user: User = Depends(get_current_user)) -> User:
        if UserRole.admin.value in required_set and user.role == UserRole.admin:
            return user
        if user.role.value not in required_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(required)}",
            )
        return user

    return _checker
