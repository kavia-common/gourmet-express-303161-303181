from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
import os

from src.api.routes.auth import router as auth_router
from src.api.routes.delivery import router as delivery_router
from src.api.routes.orders import router as orders_router
from src.api.routes.payments import router as payments_router
from src.api.routes.restaurants import router as restaurants_router
from src.api.routes.tracking import router as tracking_router
from src.db.session import get_engine
from src.models import Base  # import package so all model modules are registered on Base.metadata

openapi_tags = [
    {"name": "health", "description": "Health and readiness endpoints."},
    {"name": "auth", "description": "Authentication and role management endpoints."},
    {"name": "restaurants", "description": "Restaurant, menu, and menu item browsing/management endpoints."},
    {"name": "orders", "description": "Cart, order placement, and order status lifecycle endpoints."},
    {"name": "delivery", "description": "Delivery workflow: assignment and courier actions (pickup/deliver)."},
    {"name": "tracking", "description": "Order tracking events + real-time WebSocket streaming."},
    {
        "name": "payments",
        "description": "Payment flow endpoints (currently stubbed; designed to be swapped to Stripe).",
    },
]

# Ensure auth works in local/dev environments even when env vars are not provided.
# In production deployments, JWT_SECRET should always be explicitly set.
os.environ.setdefault("JWT_SECRET", "dev-insecure-change-me")

app = FastAPI(
    title="Gourmet Express API",
    description="Backend API for Gourmet Express food delivery platform (auth, restaurants, orders, tracking).",
    version="0.1.0",
    openapi_tags=openapi_tags,
)

_allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "").strip()
_allowed_origins = [o.strip() for o in _allowed_origins_env.split(",") if o.strip()] if _allowed_origins_env else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    """
    Validate DB connectivity and ensure core auth tables exist.

    If migrations are not applied yet, we create the minimal auth tables (users, roles, user_roles).
    """
    engine = get_engine()
    try:
        async with engine.begin() as conn:
            # Connectivity check
            await conn.execute(text("SELECT 1"))
            # Create tables if needed (minimal safety-net, not a replacement for migrations)
            await conn.run_sync(Base.metadata.create_all)
    except SQLAlchemyError as e:
        # Let the app start but clearly surface the issue in logs.
        # In production you'd typically fail fast.
        print(f"[startup] Database connectivity/table init failed: {e}")


@app.get(
    "/",
    tags=["health"],
    summary="Health check",
    description="Basic health check endpoint.",
)
def health_check():
    """Return basic service health."""
    return {"message": "Healthy"}


@app.get(
    "/health/db",
    tags=["health"],
    summary="Database health check",
    description="Checks database connectivity by executing SELECT 1.",
)
async def db_health_check():
    """Check database connectivity."""
    engine = get_engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except SQLAlchemyError as e:
        return {"status": "error", "detail": str(e)}


@app.get(
    "/docs/realtime",
    tags=["tracking"],
    summary="Real-time tracking usage notes",
    description="Lightweight documentation for consuming real-time tracking WebSocket streams.",
)
def realtime_docs():
    """Provide quick usage notes for real-time tracking (WebSocket)."""
    return {
        "websocket": {
            "url_template": "/tracking/ws/orders/{order_id}?token={jwt}",
            "notes": [
                "Pass the JWT access token as query param `token` (recommended for browsers).",
                "Server messages are JSON strings with shape: {type, payload}.",
                "Use /tracking/orders/{order_id}/events to fetch history (REST).",
            ],
        }
    }


app.include_router(auth_router)
app.include_router(restaurants_router)
app.include_router(orders_router)
app.include_router(delivery_router)
app.include_router(tracking_router)
app.include_router(payments_router)
