from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db_session
from src.deps.auth import get_current_user, require_roles
from src.models.auth import User, UserRole
from src.models.restaurants import Menu, MenuItem, Restaurant
from src.schemas.restaurants import (
    MenuItemCreate,
    MenuItemOut,
    MenuItemUpdate,
    MenuOut,
    MenuUpdate,
    RestaurantCreate,
    RestaurantOut,
    RestaurantUpdate,
)

router = APIRouter(prefix="/restaurants", tags=["restaurants"])


def _is_admin(user: User) -> bool:
    return (user.role.value if hasattr(user.role, "value") else str(user.role)) == UserRole.admin.value


async def _get_restaurant_or_404(session: AsyncSession, restaurant_id: UUID) -> Restaurant:
    restaurant = await session.get(Restaurant, restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return restaurant


async def _require_restaurant_manage_access(
    session: AsyncSession, restaurant: Restaurant, user: User
) -> None:
    """
    Enforce that the user can manage the restaurant.
    - admin can manage anything
    - restaurant_admin can manage only their own restaurants
    """
    if _is_admin(user):
        return
    if restaurant.owner_user_id is None or str(restaurant.owner_user_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Not permitted to manage this restaurant")


async def _get_menu_or_404(session: AsyncSession, menu_id: UUID) -> Menu:
    menu = await session.get(Menu, menu_id)
    if not menu:
        raise HTTPException(status_code=404, detail="Menu not found")
    return menu


async def _get_menu_item_or_404(session: AsyncSession, item_id: UUID) -> MenuItem:
    item = await session.get(MenuItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found")
    return item


@router.get(
    "",
    response_model=list[RestaurantOut],
    summary="Browse restaurants",
    description="List restaurants with pagination and basic search across name/city.",
)
async def list_restaurants(
    q: Optional[str] = Query(None, description="Search query (matches name or city)."),
    city: Optional[str] = Query(None, description="Filter by city (case-insensitive exact match)."),
    limit: int = Query(20, ge=1, le=100, description="Max number of results to return."),
    offset: int = Query(0, ge=0, description="Offset for pagination."),
    include_inactive: bool = Query(
        False,
        description="If true, include inactive restaurants (requires admin).",
    ),
    current_user: Optional[User] = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[RestaurantOut]:
    """
    Browse restaurants for customers.

    Notes:
    - By default returns only active restaurants.
    - include_inactive=true is allowed only for admins.
    """
    if include_inactive and not (_is_admin(current_user)):
        raise HTTPException(status_code=403, detail="include_inactive requires admin role")

    stmt = select(Restaurant)

    if not include_inactive:
        stmt = stmt.where(Restaurant.is_active.is_(True))

    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Restaurant.name.ilike(like), Restaurant.city.ilike(like)))

    if city:
        stmt = stmt.where(func.lower(Restaurant.city) == city.strip().lower())

    stmt = stmt.order_by(Restaurant.name.asc()).limit(limit).offset(offset)

    result = await session.execute(stmt)
    restaurants = result.scalars().all()
    return [RestaurantOut.model_validate(r) for r in restaurants]


@router.get(
    "/{restaurant_id}",
    response_model=RestaurantOut,
    summary="Get a restaurant by id",
    description="Fetch a single restaurant by its UUID.",
)
async def get_restaurant(
    restaurant_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> RestaurantOut:
    restaurant = await _get_restaurant_or_404(session, restaurant_id)
    return RestaurantOut.model_validate(restaurant)


@router.post(
    "",
    response_model=RestaurantOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a restaurant",
    description="Create a new restaurant (role: restaurant_admin or admin).",
)
async def create_restaurant(
    payload: RestaurantCreate,
    current_user: User = Depends(require_roles(["restaurant_admin", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> RestaurantOut:
    owner_user_id = payload.owner_user_id
    if owner_user_id is None and not _is_admin(current_user):
        # restaurant_admin creating their own restaurant
        owner_user_id = UUID(str(current_user.id))
    if owner_user_id is not None and (not _is_admin(current_user)) and str(owner_user_id) != str(
        current_user.id
    ):
        raise HTTPException(status_code=403, detail="Cannot create restaurant for another owner")

    restaurant = Restaurant(
        owner_user_id=owner_user_id,
        name=payload.name,
        description=payload.description,
        phone=payload.phone,
        address_line1=payload.address_line1,
        address_line2=payload.address_line2,
        city=payload.city,
        state=payload.state,
        postal_code=payload.postal_code,
        latitude=payload.latitude,
        longitude=payload.longitude,
        is_active=payload.is_active,
    )
    session.add(restaurant)
    await session.commit()
    await session.refresh(restaurant)
    return RestaurantOut.model_validate(restaurant)


@router.put(
    "/{restaurant_id}",
    response_model=RestaurantOut,
    summary="Update a restaurant",
    description="Update a restaurant (role: restaurant_admin or admin). Owners can only update their own restaurants.",
)
async def update_restaurant(
    restaurant_id: UUID,
    payload: RestaurantUpdate,
    current_user: User = Depends(require_roles(["restaurant_admin", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> RestaurantOut:
    restaurant = await _get_restaurant_or_404(session, restaurant_id)
    await _require_restaurant_manage_access(session, restaurant, current_user)

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(restaurant, k, v)

    await session.commit()
    await session.refresh(restaurant)
    return RestaurantOut.model_validate(restaurant)


@router.delete(
    "/{restaurant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a restaurant",
    description="Delete a restaurant (role: restaurant_admin or admin). Owners can only delete their own restaurants.",
)
async def delete_restaurant(
    restaurant_id: UUID,
    current_user: User = Depends(require_roles(["restaurant_admin", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    restaurant = await _get_restaurant_or_404(session, restaurant_id)
    await _require_restaurant_manage_access(session, restaurant, current_user)

    await session.execute(delete(Restaurant).where(Restaurant.id == restaurant_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{restaurant_id}/menus",
    response_model=list[MenuOut],
    summary="List menus by restaurant",
    description="List menus for a restaurant. By default returns only active menus.",
)
async def list_menus_by_restaurant(
    restaurant_id: UUID,
    include_inactive: bool = Query(
        False,
        description="If true, include inactive menus (requires restaurant_admin for that restaurant or admin).",
    ),
    current_user: Optional[User] = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[MenuOut]:
    restaurant = await _get_restaurant_or_404(session, restaurant_id)

    if include_inactive:
        # only restaurant owner (of this restaurant) or admin
        if not _is_admin(current_user):
            await _require_restaurant_manage_access(session, restaurant, current_user)

    stmt = select(Menu).where(Menu.restaurant_id == restaurant_id)
    if not include_inactive:
        stmt = stmt.where(Menu.is_active.is_(True))
    stmt = stmt.order_by(Menu.name.asc())

    result = await session.execute(stmt)
    menus = result.scalars().all()
    return [MenuOut.model_validate(m) for m in menus]


@router.post(
    "/{restaurant_id}/menus",
    response_model=MenuOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a menu for a restaurant",
    description="Create a menu under a restaurant (role: restaurant_admin for that restaurant or admin).",
)
async def create_menu(
    restaurant_id: UUID,
    payload: MenuUpdate,  # same fields, no restaurant_id in body for this endpoint
    current_user: User = Depends(require_roles(["restaurant_admin", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> MenuOut:
    restaurant = await _get_restaurant_or_404(session, restaurant_id)
    await _require_restaurant_manage_access(session, restaurant, current_user)

    # If name omitted in MenuUpdate, we still need it here.
    if payload.name is None:
        raise HTTPException(status_code=422, detail="Field 'name' is required")

    menu = Menu(
        restaurant_id=restaurant_id,
        name=payload.name,
        description=payload.description,
        is_active=True if payload.is_active is None else payload.is_active,
    )
    session.add(menu)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=400, detail="Menu name must be unique per restaurant")
    await session.refresh(menu)
    return MenuOut.model_validate(menu)


@router.put(
    "/{restaurant_id}/menus/{menu_id}",
    response_model=MenuOut,
    summary="Update a menu",
    description="Update a menu (role: restaurant_admin for that restaurant or admin).",
)
async def update_menu(
    restaurant_id: UUID,
    menu_id: UUID,
    payload: MenuUpdate,
    current_user: User = Depends(require_roles(["restaurant_admin", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> MenuOut:
    restaurant = await _get_restaurant_or_404(session, restaurant_id)
    await _require_restaurant_manage_access(session, restaurant, current_user)

    menu = await _get_menu_or_404(session, menu_id)
    if menu.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="Menu not found for this restaurant")

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(menu, k, v)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=400, detail="Menu name must be unique per restaurant")
    await session.refresh(menu)
    return MenuOut.model_validate(menu)


@router.delete(
    "/{restaurant_id}/menus/{menu_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a menu",
    description="Delete a menu (role: restaurant_admin for that restaurant or admin).",
)
async def delete_menu(
    restaurant_id: UUID,
    menu_id: UUID,
    current_user: User = Depends(require_roles(["restaurant_admin", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    restaurant = await _get_restaurant_or_404(session, restaurant_id)
    await _require_restaurant_manage_access(session, restaurant, current_user)

    menu = await _get_menu_or_404(session, menu_id)
    if menu.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="Menu not found for this restaurant")

    await session.execute(delete(Menu).where(Menu.id == menu_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{restaurant_id}/menus/{menu_id}/items",
    response_model=list[MenuItemOut],
    summary="List menu items",
    description="List items for a menu. By default returns only available items.",
)
async def list_menu_items(
    restaurant_id: UUID,
    menu_id: UUID,
    include_unavailable: bool = Query(
        False,
        description="If true, include unavailable items (requires restaurant_admin for that restaurant or admin).",
    ),
    current_user: Optional[User] = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[MenuItemOut]:
    restaurant = await _get_restaurant_or_404(session, restaurant_id)
    menu = await _get_menu_or_404(session, menu_id)

    if menu.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="Menu not found for this restaurant")

    if include_unavailable and not _is_admin(current_user):
        await _require_restaurant_manage_access(session, restaurant, current_user)

    stmt = select(MenuItem).where(
        MenuItem.menu_id == menu_id,
        MenuItem.restaurant_id == restaurant_id,
    )
    if not include_unavailable:
        stmt = stmt.where(MenuItem.is_available.is_(True))

    stmt = stmt.order_by(MenuItem.name.asc())
    result = await session.execute(stmt)
    items = result.scalars().all()
    return [MenuItemOut.model_validate(i) for i in items]


@router.post(
    "/{restaurant_id}/menus/{menu_id}/items",
    response_model=MenuItemOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a menu item",
    description="Create a menu item under a menu (role: restaurant_admin for that restaurant or admin).",
)
async def create_menu_item(
    restaurant_id: UUID,
    menu_id: UUID,
    payload: MenuItemCreate,
    current_user: User = Depends(require_roles(["restaurant_admin", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> MenuItemOut:
    restaurant = await _get_restaurant_or_404(session, restaurant_id)
    await _require_restaurant_manage_access(session, restaurant, current_user)

    menu = await _get_menu_or_404(session, menu_id)
    if menu.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="Menu not found for this restaurant")

    if payload.menu_id != menu_id:
        raise HTTPException(status_code=400, detail="menu_id in body must match path")

    item = MenuItem(
        menu_id=menu_id,
        restaurant_id=restaurant_id,
        name=payload.name,
        description=payload.description,
        price_cents=payload.price_cents,
        currency=payload.currency,
        image_url=payload.image_url,
        is_available=payload.is_available,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return MenuItemOut.model_validate(item)


@router.put(
    "/{restaurant_id}/menus/{menu_id}/items/{item_id}",
    response_model=MenuItemOut,
    summary="Update a menu item",
    description="Update a menu item (role: restaurant_admin for that restaurant or admin).",
)
async def update_menu_item(
    restaurant_id: UUID,
    menu_id: UUID,
    item_id: UUID,
    payload: MenuItemUpdate,
    current_user: User = Depends(require_roles(["restaurant_admin", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> MenuItemOut:
    restaurant = await _get_restaurant_or_404(session, restaurant_id)
    await _require_restaurant_manage_access(session, restaurant, current_user)

    menu = await _get_menu_or_404(session, menu_id)
    if menu.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="Menu not found for this restaurant")

    item = await _get_menu_item_or_404(session, item_id)
    if item.menu_id != menu_id or item.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="Menu item not found for this menu/restaurant")

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(item, k, v)

    await session.commit()
    await session.refresh(item)
    return MenuItemOut.model_validate(item)


@router.delete(
    "/{restaurant_id}/menus/{menu_id}/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a menu item",
    description="Delete a menu item (role: restaurant_admin for that restaurant or admin).",
)
async def delete_menu_item(
    restaurant_id: UUID,
    menu_id: UUID,
    item_id: UUID,
    current_user: User = Depends(require_roles(["restaurant_admin", "admin"])),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    restaurant = await _get_restaurant_or_404(session, restaurant_id)
    await _require_restaurant_manage_access(session, restaurant, current_user)

    menu = await _get_menu_or_404(session, menu_id)
    if menu.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="Menu not found for this restaurant")

    item = await _get_menu_item_or_404(session, item_id)
    if item.menu_id != menu_id or item.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="Menu item not found for this menu/restaurant")

    await session.execute(delete(MenuItem).where(MenuItem.id == item_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
