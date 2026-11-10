"""Response cache for external API calls.

Caching is best-effort: any database failure is logged and treated as a cache miss,
so a locked or read-only cache file never blocks an audit.
"""

import datetime as dt
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papaver.core.config import Settings
from papaver.db.models import ApiCacheEntry
from papaver.db.session import create_engine, create_session_factory, init_models
from papaver.utils.payload import JSONObject

__all__ = ["ResponseCache", "open_response_cache"]

logger = logging.getLogger(__name__)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class ResponseCache:
    """Stores raw JSON responses with a time-to-live."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], ttl: dt.timedelta) -> None:
        self._sessions = sessions
        self._ttl = ttl

    async def get(self, source: str, key: str) -> JSONObject | None:
        """Return a fresh cached payload, or ``None`` (miss, expired or DB error)."""
        if self._ttl <= dt.timedelta(0):
            return None
        try:
            async with self._sessions() as session:
                entry = await session.get(ApiCacheEntry, (str(source), key))
        except SQLAlchemyError as exc:
            logger.warning("Cache read failed, fetching live: %s", exc)
            return None
        if entry is None:
            return None
        fetched_at = entry.fetched_at
        if fetched_at.tzinfo is None:  # SQLite drops the timezone; we always store UTC
            fetched_at = fetched_at.replace(tzinfo=dt.UTC)
        if _utcnow() - fetched_at > self._ttl:
            return None
        return entry.payload

    async def set(self, source: str, key: str, payload: JSONObject) -> None:
        """Insert or refresh a cached payload."""
        try:
            async with self._sessions.begin() as session:
                await session.merge(
                    ApiCacheEntry(source=str(source), key=key, payload=payload, fetched_at=_utcnow())
                )
        except SQLAlchemyError as exc:
            logger.warning("Cache write failed: %s", exc)

    async def purge_expired(self) -> int:
        """Delete entries older than the TTL; returns the number of rows removed."""
        cutoff = _utcnow() - self._ttl
        async with self._sessions.begin() as session:
            result = await session.execute(delete(ApiCacheEntry).where(ApiCacheEntry.fetched_at < cutoff))
        return int(getattr(result, "rowcount", 0) or 0)


@asynccontextmanager
async def open_response_cache(settings: Settings, *, enabled: bool = True) -> AsyncIterator[ResponseCache | None]:
    """Open the cache database from ``settings.db_url``.

    Yields ``None`` when caching is disabled or the database cannot be opened, so
    callers can pass the result straight to the services.
    """
    if not enabled:
        yield None
        return

    try:
        engine = create_engine(settings.db_url)
    except (SQLAlchemyError, OSError, ValueError) as exc:
        logger.warning("Response cache disabled, invalid database %s: %s", settings.db_url, exc)
        yield None
        return
    try:
        await init_models(engine)
    except (SQLAlchemyError, OSError) as exc:
        await engine.dispose()
        logger.warning("Response cache disabled, cannot open %s: %s", settings.db_url, exc)
        yield None
        return

    try:
        yield ResponseCache(create_session_factory(engine), dt.timedelta(hours=settings.cache_ttl_hours))
    finally:
        await engine.dispose()
