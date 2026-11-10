"""Runtime configuration, loaded from ``PAPAVER_*`` environment variables and ``.env``."""

import re
from functools import lru_cache
from typing import Final

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from papaver import __version__

_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Settings(BaseSettings):
    """Application settings.

    Every field can be overridden by an environment variable with the ``PAPAVER_``
    prefix, e.g. ``PAPAVER_CONTACT_EMAIL`` or ``PAPAVER_HTTP_TIMEOUT``.
    """

    model_config = SettingsConfigDict(
        env_prefix="PAPAVER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    contact_email: str | None = Field(
        default=None,
        description="Contact email for the Crossref/OpenAlex polite pool.",
    )
    s2_api_key: SecretStr | None = Field(
        default=None,
        description="Semantic Scholar API key (optional).",
    )
    db_url: str = Field(
        default="sqlite+aiosqlite:///./papaver_cache.db",
        description="SQLAlchemy async database URL for the local cache.",
    )

    http_timeout: float = Field(default=15.0, gt=0, description="Per-request timeout, seconds.")
    max_retries: int = Field(
        default=2,
        ge=0,
        description="Retries after a timeout, network error, HTTP 429 or 5xx.",
    )
    retry_backoff: float = Field(
        default=0.5,
        ge=0,
        description="Base delay for exponential backoff between retries, seconds.",
    )

    crossref_base_url: str = "https://api.crossref.org"
    openalex_base_url: str = "https://api.openalex.org"

    @field_validator("contact_email", mode="before")
    @classmethod
    def _validate_contact_email(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            if not _EMAIL_PATTERN.fullmatch(value):
                raise ValueError(f"not a valid email address: {value!r}")
        return value

    @field_validator("s2_api_key", mode="before")
    @classmethod
    def _empty_key_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("crossref_base_url", "openalex_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    def user_agent(self) -> str:
        """Build the ``User-Agent`` header, e.g. ``Papaver/0.1.0 (mailto:me@example.org)``."""
        base = f"Papaver/{__version__}"
        if self.contact_email:
            return f"{base} (mailto:{self.contact_email})"
        return base


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance (read once, then cached)."""
    return Settings()
