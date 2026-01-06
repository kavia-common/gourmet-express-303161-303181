from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db_session
from src.deps.auth import get_current_user, require_roles
from src.models.auth import Role, User, UserRole
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
    roles = getattr(user, "_role_objects", [])
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        roles=[RoleOut(id=r.id, name=r.name, description=r.description) for r in roles],
    )


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description="Creates a new user with a hashed password. By default, attaches the 'customer' role if it exists.",
)
async def register(payload: RegisterRequest, session: AsyncSession = Depends(get_db_session)) -> UserOut:
    """
    Register a new user.

    - **email** must be unique
    - **password** is hashed with bcrypt
    """
    user = User(
        email=str(payload.email).lower(),
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        is_active=True,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=400, detail="Email already registered")

    await session.refresh(user)

    # Attach default role if available.
    result = await session.execute(select(Role).where(Role.name == "customer"))
    customer_role = result.scalar_one_or_none()
    if customer_role:
        session.add(UserRole(user_id=user.id, role_id=customer_role.id))
        await session.commit()

    # Reload roles for output
    result = await session.execute(
        select(Role).join(UserRole, Role.id == UserRole.role_id).where(UserRole.user_id == user.id)
    )
    setattr(user, "_role_objects", result.scalars().all())
    return _user_to_out(user)


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
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")

    token = create_access_token(subject=str(user.id))
    return TokenResponse(access_token=token)


@router.get(
    "/me",
    response_model=UserOut,
    summary="Get the current user",
    description="Returns the currently authenticated user and their roles.",
)
async def me(current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)) -> UserOut:
    """
    Get current authenticated user.

    Uses OAuth2PasswordBearer token from Authorization header.
    """
    # Ensure roles loaded (dependency sets _role_objects); but refresh if not present.
    if not hasattr(current_user, "_role_objects"):
        result = await session.execute(
            select(Role)
            .join(UserRole, Role.id == UserRole.role_id)
            .where(UserRole.user_id == current_user.id)
        )
        setattr(current_user, "_role_objects", result.scalars().all())
    return _user_to_out(current_user)


@router.get(
    "/roles",
    response_model=list[RoleOut],
    summary="List roles",
    description="List all available roles in the system.",
)
async def list_roles(
    _: User = Depends(get_current_user), session: AsyncSession = Depends(get_db_session)
) -> list[RoleOut]:
    result = await session.execute(select(Role).order_by(Role.name.asc()))
    roles = result.scalars().all()
    return [RoleOut(id=r.id, name=r.name, description=r.description) for r in roles]


@router.post(
    "/roles",
    response_model=RoleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a role",
    description="Create a new role. Intended for admins (role: admin).",
)
async def create_role(
    payload: RoleOut,
    _: User = Depends(require_roles(["admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> RoleOut:
    role = Role(name=payload.name, description=payload.description)
    session.add(role)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=400, detail="Role already exists")
    await session.refresh(role)
    return RoleOut(id=role.id, name=role.name, description=role.description)


@router.post(
    "/users/{user_id}/roles",
    response_model=UserOut,
    summary="Attach a role to a user",
    description="Attach an existing role to a user. Intended for admins (role: admin).",
)
async def attach_role(
    user_id: int,
    payload: AttachRoleRequest,
    _: User = Depends(require_roles(["admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> UserOut:
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = await session.execute(select(Role).where(Role.name == payload.role_name))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    session.add(UserRole(user_id=user.id, role_id=role.id))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # UniqueConstraint prevents duplicates
        raise HTTPException(status_code=400, detail="Role already attached")

    # Reload roles for output
    result = await session.execute(
        select(Role).join(UserRole, Role.id == UserRole.role_id).where(UserRole.user_id == user.id)
    )
    setattr(user, "_role_objects", result.scalars().all())
    return _user_to_out(user)
