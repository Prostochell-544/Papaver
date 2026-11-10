"""Async engine and session factory."""

from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from papaver.db.models import Base


def create_engine(db_url: str) -> AsyncEngine:
    """Create an async engine; for file-based SQLite the parent folder is created first."""
    url = make_url(db_url)
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(url)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_models(engine: AsyncEngine) -> None:
    """Create missing tables. (Schema migrations will move to Alembic once the schema settles.)"""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
