import datetime as dt
from typing import Any

import pytest
import respx

from papaver.core.config import Settings
from papaver.db import cache as cache_module
from papaver.db.cache import open_response_cache
from papaver.services.ingest import IngestionService
from tests.conftest import WAKEFIELD_DOI


async def test_roundtrip_and_overwrite(settings: Settings) -> None:
    async with open_response_cache(settings) as cache:
        assert cache is not None
        assert await cache.get("crossref", "https://x/1") is None
        await cache.set("crossref", "https://x/1", {"title": ["É"], "n": 1})
        assert await cache.get("crossref", "https://x/1") == {"title": ["É"], "n": 1}
        await cache.set("crossref", "https://x/1", {"n": 2})
        assert await cache.get("crossref", "https://x/1") == {"n": 2}
        assert await cache.get("openalex", "https://x/1") is None  # keyed per source

    # Persisted across engine restarts.
    async with open_response_cache(settings) as cache:
        assert cache is not None
        assert await cache.get("crossref", "https://x/1") == {"n": 2}


async def test_entries_expire_after_ttl(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    async with open_response_cache(settings) as cache:
        assert cache is not None
        await cache.set("crossref", "k", {"n": 1})

        later = dt.datetime.now(dt.UTC) + dt.timedelta(hours=settings.cache_ttl_hours + 1)
        monkeypatch.setattr(cache_module, "_utcnow", lambda: later)

        assert await cache.get("crossref", "k") is None
        assert await cache.purge_expired() == 1


async def test_zero_ttl_disables_reads(settings: Settings) -> None:
    no_reads = Settings(_env_file=None, **(settings.model_dump() | {"cache_ttl_hours": 0}))  # type: ignore[call-arg]
    async with open_response_cache(no_reads) as cache:
        assert cache is not None
        await cache.set("crossref", "k", {"n": 1})
        assert await cache.get("crossref", "k") is None


async def test_disabled_or_broken_cache_yields_none(settings: Settings) -> None:
    async with open_response_cache(settings, enabled=False) as cache:
        assert cache is None
    sync_driver = Settings(_env_file=None, **(settings.model_dump() | {"db_url": "sqlite:///x.db"}))  # type: ignore[call-arg]
    async with open_response_cache(sync_driver) as cache:
        assert cache is None


async def test_ingestion_serves_repeat_requests_from_cache(
    settings: Settings,
    respx_mock: respx.MockRouter,
    crossref_wakefield: dict[str, Any],
    openalex_wakefield: dict[str, Any],
) -> None:
    crossref = respx_mock.get(host="api.crossref.org").respond(json=crossref_wakefield)
    openalex = respx_mock.get(host="api.openalex.org").respond(json=openalex_wakefield)

    async with open_response_cache(settings) as cache:
        async with IngestionService(settings, cache=cache) as ingest:
            first = await ingest.get_paper_metadata(WAKEFIELD_DOI)
            second = await ingest.get_paper_metadata(WAKEFIELD_DOI)

    assert first == second
    assert (crossref.call_count, openalex.call_count) == (1, 1)


async def test_errors_are_not_cached(settings: Settings, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(host="api.crossref.org").respond(503)

    async with open_response_cache(settings) as cache:
        async with IngestionService(settings, cache=cache) as ingest:
            for _ in range(2):
                with pytest.raises(Exception):
                    await ingest.fetch_crossref(WAKEFIELD_DOI)

    assert route.call_count == 4  # 2 calls x (1 try + 1 retry), nothing served from cache
