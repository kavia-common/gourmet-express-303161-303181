import os
from pathlib import Path
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def _parse_psql_command_to_url(psql_cmd: str) -> str:
    """
    Convert 'psql postgresql://user:pass@host:port/db' style into the URL part.
    """
    parts = psql_cmd.strip().split()
    if not parts:
        raise ValueError("db_connection.txt is empty")
    if parts[0] == "psql":
        if len(parts) < 2:
            raise ValueError("db_connection.txt must contain 'psql postgresql://...'")
        return parts[1]
    # If file contains only the URL, accept it.
    return parts[0]


def _to_asyncpg_url(sync_url: str) -> str:
    """
    SQLAlchemy async engine expects postgresql+asyncpg://...
    """
    if sync_url.startswith("postgresql+asyncpg://"):
        return sync_url
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgres://"):
        # Historical scheme; normalize.
        return sync_url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise ValueError(f"Unsupported postgres URL scheme in db_connection.txt: {sync_url!r}")


def _default_db_connection_path() -> Path:
    """
    Best-effort location of the database container's connection helper file.

    Repository layout:
      gourmet-express-303161-303181/food_delivery_backend/src/db/session.py  (this file)
      gourmet-express-303161-303183/database/db_connection.txt              (database container)

    We resolve relative to this file to avoid hard-coded absolute paths.
    """
    # .../gourmet-express-303161-303181/food_delivery_backend/src/db/session.py
    this_file = Path(__file__).resolve()
    # go up: db -> src -> food_delivery_backend -> gourmet-express-303161-303181 -> code-generation root
    codegen_root = this_file.parents[4]
    return codegen_root / "gourmet-express-303161-303183" / "database" / "db_connection.txt"


def load_database_url() -> str:
    """
    Load the PostgreSQL connection URL from db_connection.txt.
    Optionally allow override with DATABASE_URL env var (useful for deployment).
    """
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return _to_asyncpg_url(env_url)

    conn_path = os.getenv("DB_CONNECTION_FILE")
    path = Path(conn_path).expanduser().resolve() if conn_path else _default_db_connection_path()

    if not path.exists():
        raise FileNotFoundError(
            f"DB connection file not found at {path}. "
            f"Set DB_CONNECTION_FILE or DATABASE_URL env var."
        )

    raw = path.read_text(encoding="utf-8").strip()
    sync_url = _parse_psql_command_to_url(raw)
    return _to_asyncpg_url(sync_url)


_ENGINE: Optional[AsyncEngine] = None
_SESSIONMAKER: Optional[async_sessionmaker[AsyncSession]] = None


# PUBLIC_INTERFACE
def get_engine() -> AsyncEngine:
    """Get (and lazily create) the global Async SQLAlchemy engine."""
    global _ENGINE
    if _ENGINE is None:
        database_url = load_database_url()
        _ENGINE = create_async_engine(
            database_url,
            pool_pre_ping=True,
            future=True,
        )
    return _ENGINE


# PUBLIC_INTERFACE
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Get (and lazily create) the global async sessionmaker."""
    global _SESSIONMAKER
    if _SESSIONMAKER is None:
        engine = get_engine()
        _SESSIONMAKER = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return _SESSIONMAKER


# PUBLIC_INTERFACE
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an AsyncSession."""
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        yield session
