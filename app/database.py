"""
app/database.py
───────────────
Async SQLAlchemy engine + session factory.

To migrate from SQLite → PostgreSQL:
  1. Set DATABASE_URL=postgresql+asyncpg://user:pass@host/db in .env
  2. Install asyncpg: pip install asyncpg
  3. Run: alembic upgrade head
  Nothing else changes.
"""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


# ── Engine ───────────────────────────────────────────────────
engine = create_async_engine(
    settings.database_url,
    echo=settings.app_debug,       # logs SQL in dev; set APP_DEBUG=false in prod
    connect_args=(
        {"check_same_thread": False}   # SQLite only; ignored by Postgres
        if "sqlite" in settings.database_url
        else {}
    ),
)

# ── Session factory ──────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,    # prevents lazy-load errors post-commit in async context
)


# ── Base class for all ORM models ────────────────────────────
class Base(DeclarativeBase):
    pass


# ── FastAPI dependency ────────────────────────────────────────
async def get_db() -> AsyncSession:
    """
    Yields an async DB session per request.
    Usage in a route:
        async def my_route(db: AsyncSession = Depends(get_db)):
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
