"""Shared async HTTP plumbing for the API clients.

:class:`ApiService` gives every client the same behaviour: polite ``User-Agent``,
retries with exponential backoff (honouring ``Retry-After``), optional client-side
rate limiting, an optional response cache, and errors mapped onto
:mod:`papaver.core.exceptions`.
"""

import asyncio
import logging
import random
from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Final, Protocol, Self

import httpx

from papaver.core.config import Settings, get_settings
from papaver.core.exceptions import PaperNotFoundError, UpstreamServiceError, UpstreamTimeoutError
from papaver.core.schemas import DataSource
from papaver.utils.payload import JSONObject

__all__ = ["ApiService", "RateLimiter", "ResponseCacheProtocol", "RetryPolicy", "create_http_client"]

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_DELAY: Final[float] = 60.0


def create_http_client(settings: Settings) -> httpx.AsyncClient:
    """An ``AsyncClient`` configured for Papaver.

    Building one is not free (loading the CA bundle takes a noticeable fraction of a
    second on Windows), so pipelines create one and share it between services.
    """
    return httpx.AsyncClient(timeout=httpx.Timeout(settings.http_timeout), follow_redirects=True)


class ResponseCacheProtocol(Protocol):
    """What :class:`ApiService` needs from a cache (see :class:`papaver.db.cache.ResponseCache`)."""

    async def get(self, source: str, key: str) -> JSONObject | None: ...

    async def set(self, source: str, key: str, payload: JSONObject) -> None: ...


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How often to retry transient failures and how long to back off."""

    max_retries: int
    backoff: float

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        """Delay before retry number ``attempt`` (1-based), never below ``Retry-After``."""
        delay = self.backoff * 2 ** (attempt - 1)
        if retry_after is not None:
            delay = max(delay, retry_after)
        return min(delay, _MAX_RETRY_DELAY)


class RateLimiter:
    """Spaces request *starts* at least ``min_interval`` seconds apart."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._next_slot = 0.0

    async def wait(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            delay = self._next_slot - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_slot = loop.time() + self._min_interval


class ApiService:
    """Base class for async JSON API clients.

    Use subclasses as async context managers so the connection pool is closed. An
    injected ``httpx.AsyncClient`` is left open on exit, since the caller owns it.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        cache: ResponseCacheProtocol | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._cache = cache
        self._headers: dict[str, str] = {
            "User-Agent": self._settings.user_agent(),
            "Accept": "application/json",
        }
        self._retry = RetryPolicy(self._settings.max_retries, self._settings.retry_backoff)
        self._rate_limiter: RateLimiter | None = None
        self._owns_client = client is None
        self._client = client or create_http_client(self._settings)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client if this service created it."""
        if self._owns_client:
            await self._client.aclose()

    async def _get_json(
        self,
        source: DataSource,
        url: str,
        *,
        doi: str,
        params: Mapping[str, str | int] | None = None,
    ) -> JSONObject:
        """GET a JSON object, served from the cache when fresh.

        Raises:
            PaperNotFoundError: HTTP 404 (``doi`` is used in the message).
            UpstreamTimeoutError: every attempt timed out.
            UpstreamServiceError: other HTTP errors, network errors, non-JSON bodies.
        """
        request_url = httpx.URL(url, params=params)
        cache_key = str(request_url)
        if self._cache is not None and (cached := await self._cache.get(source, cache_key)) is not None:
            logger.debug("%s cache hit: %s", source, cache_key)
            return cached

        payload = await self._fetch_json(source, request_url, doi=doi)
        if self._cache is not None:
            await self._cache.set(source, cache_key, payload)
        return payload

    async def _fetch_json(self, source: DataSource, url: httpx.URL, *, doi: str) -> JSONObject:
        attempts = self._retry.max_retries + 1
        for attempt in range(1, attempts + 1):
            is_last = attempt == attempts
            if self._rate_limiter is not None:
                await self._rate_limiter.wait()
            try:
                response = await self._client.get(url, headers=self._headers)
            except httpx.TimeoutException as exc:
                if is_last:
                    raise UpstreamTimeoutError(source, self._settings.http_timeout, attempts) from exc
                logger.debug("%s timed out (attempt %d/%d)", source, attempt, attempts)
                await self._sleep(self._retry.delay(attempt))
                continue
            except httpx.TransportError as exc:
                if is_last:
                    raise UpstreamServiceError(source, f"network error: {exc!r}") from exc
                logger.debug("%s network error (attempt %d/%d): %r", source, attempt, attempts, exc)
                await self._sleep(self._retry.delay(attempt))
                continue

            status = response.status_code
            if status == httpx.codes.NOT_FOUND:
                raise PaperNotFoundError(doi, source)
            if status in _RETRYABLE_STATUS and not is_last:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                delay = self._retry.delay(attempt, retry_after)
                logger.debug(
                    "%s returned HTTP %d (attempt %d/%d), retrying in %.1fs",
                    source, status, attempt, attempts, delay,
                )
                await self._sleep(delay)
                continue
            if status == httpx.codes.TOO_MANY_REQUESTS:
                raise UpstreamServiceError(
                    source, f"rate limited (HTTP 429) after {attempts} attempt(s)", status
                )
            if response.is_error:
                raise UpstreamServiceError(source, _describe_error(response), status)

            try:
                payload = response.json()
            except ValueError as exc:
                raise UpstreamServiceError(source, "response is not valid JSON", status) from exc
            if not isinstance(payload, dict):
                raise UpstreamServiceError(source, "expected a JSON object", status)
            return payload

        raise AssertionError("unreachable: the last attempt always returns or raises")

    @staticmethod
    async def _sleep(delay: float) -> None:
        if delay > 0:
            await asyncio.sleep(delay + random.uniform(0, delay / 10))


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None  # HTTP-date form: fall back to plain backoff
    return seconds if seconds >= 0 else None


def _describe_error(response: httpx.Response) -> str:
    """``HTTP 400: <API error message>`` when the API explains itself in JSON."""
    message = f"HTTP {response.status_code}"
    try:
        body = response.json()
    except ValueError:
        return message
    if isinstance(body, dict):
        detail = body.get("error") or body.get("message")
        if isinstance(detail, str) and detail.strip():
            return f"{message}: {detail.strip()[:200]}"
    return message
