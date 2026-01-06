from __future__ import annotations

from typing import Callable, List, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db_session
from src.models.auth import Role, User, UserRole
from src.security.auth import decode_access_token


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def _load_user_with_roles(session: AsyncSession, user_id: int) -> Optional[User]:
    user = await session.get(User, user_id)
    if not user:
        return None
    # Load roles via explicit query to avoid lazy-loading issues in async contexts.
    result = await session.execute(
        select(Role).join(UserRole, Role.id == UserRole.role_id).where(UserRole.user_id == user.id)
    )
    role_rows = result.scalars().all()
    # Attach a synthetic attribute for response building.
    setattr(user, "_role_objects", role_rows)
    return user


# PUBLIC_INTERFACE
async def get_current_user(
    token: str = Depends(oauth2_scheme), session: AsyncSession = Depends(get_db_session)
) -> User:
    """Dependency that validates JWT and returns the current User."""
    try:
        payload = decode_access_token(token)
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        user_id = int(sub)
    except (JWTError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = await _load_user_with_roles(session, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")
    return user


# PUBLIC_INTERFACE
def require_roles(required: List[str]) -> Callable:
    """Factory for a dependency that enforces the current user has at least one of required roles."""

    async def _checker(user: User = Depends(get_current_user)) -> User:
        roles: List[Role] = getattr(user, "_role_objects", [])
        role_names = {r.name for r in roles}
        if not role_names.intersection(required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(required)}",
            )
        return user

    return _checker
