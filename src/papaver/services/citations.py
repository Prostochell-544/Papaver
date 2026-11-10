"""Citation contexts from the Semantic Scholar Graph API.

Endpoint and payload (verified against live responses)::

    GET {s2}/paper/DOI:{doi}/citations?fields=...&offset=0&limit=1000

    {"offset": 0, "next": 1000,            # "next" is absent on the last page
     "data": [{"contexts": [str], "intents": [str], "isInfluential": bool,
               "citingPaper": {"paperId": str, "title": str, "year": int,
                               "authors": [{"authorId": str, "name": str}],
                               "externalIds": {"DOI": str, "PubMed": str, ...}}}]}

Limits: at most 1000 items per page, and ``offset + limit`` must stay below 10000
(HTTP 400 otherwise). Without an API key requests share a public pool that often
answers 429, so pages are fetched sequentially, spaced by ``s2_min_interval``, and
429s are retried with backoff. Many citing papers come without contexts (publisher
licensing): about two thirds for the Wakefield paper.
"""

from typing import Final

import httpx

from papaver.core.config import Settings
from papaver.core.schemas import (
    CitationContextItem,
    CitationIntent,
    CitationScan,
    CitingPaper,
    DataSource,
)
from papaver.services.http import ApiService, RateLimiter, ResponseCacheProtocol, RetryPolicy
from papaver.utils.doi import normalize_doi, quote_doi, try_normalize_doi
from papaver.utils.payload import as_dict, as_list, as_year, clean_text, dedupe, text

__all__ = ["CitationService", "clean_contexts", "parse_citation"]

_FIELDS: Final[str] = ",".join(
    (
        "contexts",
        "intents",
        "isInfluential",
        "citingPaper.title",
        "citingPaper.year",
        "citingPaper.authors",
        "citingPaper.externalIds",
    )
)
_PAGE_LIMIT: Final[int] = 1000
_WINDOW_LIMIT: Final[int] = 10_000  # S2 requires offset + limit < 10000

#: Contexts shorter than this, or with fewer letters, are extraction debris
#: ("[12]", "(ibid.)", "...") rather than sentences.
_MIN_CONTEXT_CHARS: Final[int] = 20
_MIN_CONTEXT_LETTERS: Final[int] = 10


class CitationService(ApiService):
    """Fetches the citing papers of a DOI together with their citation sentences.

    ::

        async with CitationService() as s2:
            items = await s2.fetch_citation_contexts("10.1016/S0140-6736(97)11096-0")
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        cache: ResponseCacheProtocol | None = None,
    ) -> None:
        super().__init__(settings, client=client, cache=cache)
        self._retry = RetryPolicy(self._settings.s2_max_retries, self._settings.s2_retry_backoff)
        self._rate_limiter = RateLimiter(self._settings.s2_min_interval)
        if self._settings.s2_api_key is not None:
            self._headers["x-api-key"] = self._settings.s2_api_key.get_secret_value()

    async def fetch_citation_contexts(
        self, doi: str, *, max_citations: int | None = None
    ) -> list[CitationContextItem]:
        """Citing papers that have at least one usable context (see :meth:`scan_citations`)."""
        scan = await self.scan_citations(doi, max_citations=max_citations)
        return scan.items

    async def scan_citations(self, doi: str, *, max_citations: int | None = None) -> CitationScan:
        """Page through the citations of ``doi`` and keep the ones with usable contexts.

        Args:
            doi: DOI in any form accepted by :func:`normalize_doi`.
            max_citations: citing papers to examine (default ``settings.s2_max_citations``);
                ``0`` skips the request entirely.

        Raises:
            InvalidDOIError: ``doi`` is not a DOI.
            ValueError: ``max_citations`` is outside ``0..9999``.
            PaperNotFoundError: Semantic Scholar does not know the DOI.
            UpstreamTimeoutError | UpstreamServiceError: the API failed, including
                persistent rate limiting after all retries.
        """
        doi = normalize_doi(doi)
        budget = self._settings.s2_max_citations if max_citations is None else max_citations
        if not 0 <= budget < _WINDOW_LIMIT:
            raise ValueError(f"max_citations must be between 0 and {_WINDOW_LIMIT - 1}, got {budget}")
        if budget == 0:
            return CitationScan()

        url = f"{self._settings.s2_base_url}/paper/DOI:{quote_doi(doi)}/citations"
        items: list[CitationContextItem] = []
        scanned = 0
        offset = 0
        has_more = True
        while has_more and scanned < budget:
            limit = min(_PAGE_LIMIT, budget - scanned, _WINDOW_LIMIT - 1 - offset)
            if limit <= 0:
                break
            page = await self._get_json(
                DataSource.SEMANTIC_SCHOLAR,
                url,
                doi=doi,
                params={"fields": _FIELDS, "offset": offset, "limit": limit},
            )
            data = as_list(page.get("data"))
            scanned += len(data)
            items.extend(item for raw in data if (item := parse_citation(raw)) is not None)

            next_offset = page.get("next")
            has_more = (
                bool(data)
                and isinstance(next_offset, int)
                and not isinstance(next_offset, bool)
                and next_offset > offset
            )
            if has_more:
                offset = next_offset

        return CitationScan(items=items, scanned=scanned, truncated=has_more)


def parse_citation(raw: object) -> CitationContextItem | None:
    """Turn one S2 citation record into a model; ``None`` if it has no usable context."""
    if not isinstance(raw, dict):
        return None
    contexts = clean_contexts(raw.get("contexts"))
    if not contexts:
        return None

    citing = as_dict(raw.get("citingPaper"))
    external_ids = as_dict(citing.get("externalIds"))
    authors = (clean_text(author.get("name")) for author in as_list(citing.get("authors")) if isinstance(author, dict))
    intents = (
        CitationIntent(intent)
        for intent in as_list(raw.get("intents"))
        if isinstance(intent, str) and intent.strip()
    )
    return CitationContextItem(
        citing_paper=CitingPaper(
            s2_paper_id=text(citing.get("paperId")),
            title=clean_text(citing.get("title")),
            year=as_year(citing.get("year")),
            doi=try_normalize_doi(text(external_ids.get("DOI"))),
            authors=[name for name in authors if name],
        ),
        contexts=contexts,
        intents=dedupe(intents),
        is_influential=raw.get("isInfluential") is True,
    )


def clean_contexts(raw: object) -> list[str]:
    """Normalize whitespace, drop debris and duplicates (case-insensitive), keep order.

    Markup is *not* stripped: contexts are plain text where ``<`` usually means "less than".
    """
    contexts: list[str] = []
    seen: set[str] = set()
    for value in as_list(raw):
        context = clean_text(value, strip_markup=False)
        if context is None or not _is_meaningful(context):
            continue
        key = context.casefold()
        if key not in seen:
            seen.add(key)
            contexts.append(context)
    return contexts


def _is_meaningful(context: str) -> bool:
    letters = sum(char.isalpha() for char in context)
    return len(context) >= _MIN_CONTEXT_CHARS and letters >= _MIN_CONTEXT_LETTERS
