"""SQLAlchemy 2.0 ORM models."""

import datetime as dt
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all Papaver tables."""


class ApiCacheEntry(Base):
    """A raw JSON response from an external API, keyed by source and full request URL."""

    __tablename__ = "api_cache"

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)

    def __repr__(self) -> str:
        return f"ApiCacheEntry(source={self.source!r}, key={self.key!r}, fetched_at={self.fetched_at!r})"
