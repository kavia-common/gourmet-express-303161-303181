from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db_session
from src.deps.auth import get_current_user, require_roles
from src.models.auth import User, UserRole
from src.schemas.auth import (
    AttachRoleRequest,
    LoginRequest,
    RegisterRequest,
    RoleOut,
    TokenResponse,
    UserOut,
)
from src.security.auth import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_to_out(user: User) -> UserOut:
    # API schema expects a list of RoleOut; seeded schema uses a single enum role.
    role = RoleOut(id=0, name=user.role.value, description=None)
    return UserOut(
        id=user.id,  # pydantic will serialize UUID
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        roles=[role],
    )


# PUBLIC_INTERFACE
@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description="Creates a new user with a hashed password. By default, assigns the 'customer' role.",
)
async def register(payload: RegisterRequest, session: AsyncSession = Depends(get_db_session)) -> UserOut:
    """
    Register a new user.

    - **email** must be unique
    - **password** is hashed with bcrypt and stored in users.password_hash
    - **role** defaults to customer
    """
    user = User(
        email=str(payload.email).lower(),
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=UserRole.customer,
        is_active=True,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=400, detail="Email already registered")

    await session.refresh(user)
    return _user_to_out(user)


# PUBLIC_INTERFACE
@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and get a JWT access token",
    description="Validates credentials and returns an HS256 JWT access token.",
)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_db_session)) -> TokenResponse:
    """
    Login endpoint.

    Returns a JWT token if the email/password are valid.
    """
    result = await session.execute(select(User).where(User.email == str(payload.email).lower()))
    user = result.scalar_one_or_none()
    if not user or not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")

    token = create_access_token(subject=str(user.id))
    return TokenResponse(access_token=token)


# PUBLIC_INTERFACE
@router.get(
    "/me",
    response_model=UserOut,
    summary="Get the current user",
    description="Returns the currently authenticated user and their roles.",
)
async def me(current_user: User = Depends(get_current_user)) -> UserOut:
    """
    Get current authenticated user.

    Uses OAuth2PasswordBearer token from Authorization header.
    """
    return _user_to_out(current_user)


# PUBLIC_INTERFACE
@router.get(
    "/roles",
    response_model=list[RoleOut],
    summary="List roles",
    description="List all available roles in the system.",
)
async def list_roles(_: User = Depends(get_current_user)) -> list[RoleOut]:
    """
    List available roles.

    Seeded schema uses a Postgres enum, so we return the enum values.
    """
    return [RoleOut(id=0, name=r.value, description=None) for r in UserRole]


# PUBLIC_INTERFACE
@router.post(
    "/roles",
    response_model=RoleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a role",
    description="No-op for seeded schema (roles are an enum). Intended for admins in extensible schemas.",
)
async def create_role(
    payload: RoleOut,
    _: User = Depends(require_roles(["admin"])),
) -> RoleOut:
    """
    Create a role.

    Seeded Postgres schema uses an enum for roles, so new roles cannot be created at runtime.
    """
    raise HTTPException(status_code=400, detail="Roles are fixed by schema (enum); cannot create new roles")


# PUBLIC_INTERFACE
@router.post(
    "/users/{user_id}/roles",
    response_model=UserOut,
    summary="Attach a role to a user",
    description="Set the user's role (seeded schema uses a single enum role). Intended for admins (role: admin).",
)
async def attach_role(
    user_id: str,
    payload: AttachRoleRequest,
    _: User = Depends(require_roles(["admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> UserOut:
    """
    Attach a role to a user.

    Seeded schema is single-role: we update users.role.
    """
    try:
        uid = User.id.type.python_type(user_id)  # type: ignore[attr-defined]
    except Exception:
        # Fallback: parse as UUID string
        from uuid import UUID

        try:
            uid = UUID(str(user_id))
        except ValueError:
            raise HTTPException(status_code=422, detail="user_id must be a UUID")

    user = await session.get(User, uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        new_role = UserRole(payload.role_name)
    except ValueError:
        raise HTTPException(status_code=404, detail="Role not found")

    user.role = new_role
    await session.commit()
    await session.refresh(user)
    return _user_to_out(user)
