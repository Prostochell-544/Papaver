import asyncio
from typing import Any

import httpx
import pytest
import respx

from papaver.core.config import Settings
from papaver.core.exceptions import PaperNotFoundError, UpstreamServiceError, UpstreamTimeoutError
from papaver.core.schemas import CitationIntent
from papaver.services.citations import CitationService, clean_contexts, parse_citation
from papaver.services.http import RateLimiter
from tests.conftest import WAKEFIELD_DOI, s2_citation

S2_HOST = "api.semanticscholar.org"


def _page(data: list[dict[str, Any]], *, offset: int = 0, next_offset: int | None = None) -> dict[str, Any]:
    page: dict[str, Any] = {"offset": offset, "data": data}
    if next_offset is not None:
        page["next"] = next_offset
    return page


# ------------------------------------------------------------------- parsing


def test_clean_contexts_drops_debris_and_duplicates() -> None:
    raw = [
        "  Wakefield et al.   reported a link\n between MMR and autism [3].  ",
        "Wakefield et al. reported a link between MMR and autism [3].",  # same after cleanup
        "WAKEFIELD ET AL. REPORTED A LINK BETWEEN MMR AND AUTISM [3].",  # same, other case
        "[12]",
        "(1, 2, 3–7)",
        "...",
        "   ",
        None,
        42,
        "Values were p < 0.05 and q > 3 in the retracted study.",
    ]
    assert clean_contexts(raw) == [
        "Wakefield et al. reported a link between MMR and autism [3].",
        "Values were p < 0.05 and q > 3 in the retracted study.",  # "<...>" is not markup here
    ]
    assert clean_contexts(None) == []


def test_parse_citation_maps_fields() -> None:
    raw = s2_citation(
        title="  The   MMR <i>controversy</i> ",
        contexts=["Following the retracted report by Wakefield, vaccine uptake fell sharply [6]."],
        intents=["Background", "methodology", "background", "brand-new-intent", ""],
        influential=True,
        doi="10.1007/S10654-020-00632-5",
        year=2020,
    )
    item = parse_citation(raw)

    assert item is not None
    assert item.citing_paper.title == "The MMR controversy"
    assert item.citing_paper.doi == "10.1007/s10654-020-00632-5"
    assert item.citing_paper.year == 2020
    assert item.citing_paper.authors == ["A. Author", "B. Author"]
    assert item.citing_paper.s2_paper_id is not None
    assert item.intents == [CitationIntent.BACKGROUND, CitationIntent.METHODOLOGY, CitationIntent.OTHER]
    assert item.is_influential is True


@pytest.mark.parametrize(
    "raw",
    [
        s2_citation(title="No contexts", contexts=[]),
        s2_citation(title="Only debris", contexts=["[4]", "  "]),
        {"contexts": None, "citingPaper": None},
        "not-a-dict",
    ],
)
def test_parse_citation_skips_items_without_usable_context(raw: object) -> None:
    assert parse_citation(raw) is None


def test_parse_citation_tolerates_missing_paper_fields() -> None:
    item = parse_citation({"contexts": ["A perfectly fine citation sentence about the paper."]})
    assert item is not None
    assert item.citing_paper.title is None
    assert item.citing_paper.doi is None
    assert item.intents == []
    assert item.is_influential is False


# --------------------------------------------------------------- HTTP client


async def test_scan_single_page(
    settings: Settings, respx_mock: respx.MockRouter, s2_wakefield_page: dict[str, Any]
) -> None:
    route = respx_mock.get(host=S2_HOST).respond(json=s2_wakefield_page)

    async with CitationService(settings) as s2:
        scan = await s2.scan_citations("https://doi.org/10.1016/S0140-6736(97)11096-0")

    assert scan.scanned == 2
    assert scan.truncated is False
    assert len(scan.items) == 1
    assert scan.influential == scan.items
    request = route.calls.last.request
    assert request.url.path == f"/graph/v1/paper/DOI:{WAKEFIELD_DOI}/citations"
    assert request.url.params["fields"] == (
        "contexts,intents,isInfluential,citingPaper.title,citingPaper.year,"
        "citingPaper.authors,citingPaper.externalIds"
    )
    assert (request.url.params["offset"], request.url.params["limit"]) == ("0", "1000")
    assert "x-api-key" not in request.headers
    assert request.headers["User-Agent"] == "Papaver/0.1.0 (mailto:tests@example.org)"


async def test_fetch_citation_contexts_returns_items(
    settings: Settings, respx_mock: respx.MockRouter, s2_wakefield_page: dict[str, Any]
) -> None:
    respx_mock.get(host=S2_HOST).respond(json=s2_wakefield_page)

    async with CitationService(settings) as s2:
        items = await s2.fetch_citation_contexts(WAKEFIELD_DOI)

    assert [item.citing_paper.doi for item in items] == ["10.3390/vaccines11020288"]


async def test_paginates_until_next_is_missing(settings: Settings, respx_mock: respx.MockRouter) -> None:
    good = "A citation sentence that is long enough to be useful."
    route = respx_mock.get(host=S2_HOST)
    route.side_effect = [
        httpx.Response(200, json=_page([s2_citation(title="p1", contexts=[good])], next_offset=1000)),
        httpx.Response(200, json=_page([s2_citation(title="p2", contexts=[good])], offset=1000)),
    ]

    async with CitationService(settings) as s2:
        scan = await s2.scan_citations(WAKEFIELD_DOI)

    assert [call.request.url.params["offset"] for call in route.calls] == ["0", "1000"]
    assert [item.citing_paper.title for item in scan.items] == ["p1", "p2"]
    assert (scan.scanned, scan.truncated) == (2, False)


async def test_stops_at_budget_and_reports_truncation(settings: Settings, respx_mock: respx.MockRouter) -> None:
    data = [s2_citation(title=f"t{i}", contexts=[]) for i in range(3)]
    route = respx_mock.get(host=S2_HOST).respond(json=_page(data, next_offset=3))

    async with CitationService(settings) as s2:
        scan = await s2.scan_citations(WAKEFIELD_DOI, max_citations=3)

    assert route.call_count == 1
    assert route.calls.last.request.url.params["limit"] == "3"
    assert (scan.scanned, scan.truncated, scan.items) == (3, True, [])


async def test_never_exceeds_s2_offset_window(settings: Settings, respx_mock: respx.MockRouter) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        assert offset + limit < 10_000  # S2 answers HTTP 400 otherwise
        data = [{"contexts": []}] * limit
        return httpx.Response(200, json=_page(data, offset=offset, next_offset=offset + limit))

    route = respx_mock.get(host=S2_HOST).mock(side_effect=respond)

    async with CitationService(settings) as s2:
        scan = await s2.scan_citations(WAKEFIELD_DOI, max_citations=9999)

    assert route.call_count == 10
    assert route.calls.last.request.url.params["limit"] == "999"
    assert (scan.scanned, scan.truncated) == (9999, True)


async def test_zero_budget_makes_no_request(settings: Settings, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(host=S2_HOST)

    async with CitationService(settings) as s2:
        scan = await s2.scan_citations(WAKEFIELD_DOI, max_citations=0)

    assert route.call_count == 0
    assert scan.items == [] and scan.scanned == 0


@pytest.mark.parametrize("budget", [-1, 10_000])
async def test_rejects_out_of_range_budget(settings: Settings, budget: int) -> None:
    async with CitationService(settings) as s2:
        with pytest.raises(ValueError, match="max_citations"):
            await s2.scan_citations(WAKEFIELD_DOI, max_citations=budget)


async def test_sends_api_key_when_configured(
    settings: Settings, respx_mock: respx.MockRouter, s2_wakefield_page: dict[str, Any]
) -> None:
    route = respx_mock.get(host=S2_HOST).respond(json=s2_wakefield_page)
    keyed = Settings(_env_file=None, **(settings.model_dump() | {"s2_api_key": "secret-key"}))  # type: ignore[call-arg]

    async with CitationService(keyed) as s2:
        await s2.scan_citations(WAKEFIELD_DOI)

    assert route.calls.last.request.headers["x-api-key"] == "secret-key"


async def test_retries_429_then_succeeds(
    settings: Settings, respx_mock: respx.MockRouter, s2_wakefield_page: dict[str, Any]
) -> None:
    route = respx_mock.get(host=S2_HOST)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}, json={"message": "Too Many Requests"}),
        httpx.Response(200, json=s2_wakefield_page),
    ]

    async with CitationService(settings) as s2:
        scan = await s2.scan_citations(WAKEFIELD_DOI)

    assert route.call_count == 2
    assert len(scan.items) == 1


async def test_persistent_429_is_reported_as_rate_limit(settings: Settings, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(host=S2_HOST).respond(429, json={"message": "Too Many Requests"})

    async with CitationService(settings) as s2:
        with pytest.raises(UpstreamServiceError, match="rate limited") as excinfo:
            await s2.scan_citations(WAKEFIELD_DOI)

    assert excinfo.value.status_code == 429
    assert route.call_count == 2  # first try + s2_max_retries=1


async def test_unknown_doi_raises_not_found(settings: Settings, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(host=S2_HOST).respond(404, json={"error": "Paper with id DOI:10.1/x not found"})

    async with CitationService(settings) as s2:
        with pytest.raises(PaperNotFoundError):
            await s2.scan_citations("10.1234/unknown")


async def test_bad_request_carries_api_message(settings: Settings, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(host=S2_HOST).respond(400, json={"error": "offset + limit must be < 10000"})

    async with CitationService(settings) as s2:
        with pytest.raises(UpstreamServiceError, match="offset \\+ limit must be < 10000"):
            await s2.scan_citations(WAKEFIELD_DOI)


async def test_timeout(settings: Settings, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(host=S2_HOST).mock(side_effect=httpx.ReadTimeout("slow"))

    async with CitationService(settings) as s2:
        with pytest.raises(UpstreamTimeoutError):
            await s2.scan_citations(WAKEFIELD_DOI)


async def test_rate_limiter_spaces_requests() -> None:
    limiter = RateLimiter(0.05)
    loop = asyncio.get_running_loop()
    start = loop.time()
    for _ in range(3):
        await limiter.wait()
    assert loop.time() - start >= 0.09  # two enforced gaps of ~0.05 s
