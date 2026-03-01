"""
app/db.py
─────────────────────────────────────────────────────────────────
Async SQLAlchemy engine, session factory, and application
lifecycle hooks for creating / disposing the engine.
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.settings import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    # Required for SQLite to allow multi-threaded access
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency that yields a database session."""
    async with async_session_factory() as session:
        yield session


async def create_tables() -> None:
    """Create all tables (for quick dev bootstrap; use Alembic in prod)."""
    from app.models import Base  # noqa: F811

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    await engine.dispose()
