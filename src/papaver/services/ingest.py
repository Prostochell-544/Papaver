"""Async ingestion of bibliographic metadata from Crossref and OpenAlex.

Field references (verified against live responses):

* Crossref ``GET /works/{doi}`` -> ``message``:
  ``title``, ``container-title``, ``ISSN``, ``publisher``, ``published``/``issued``,
  ``author``, ``is-referenced-by-count``, ``update-to`` and ``updated-by``.
  ``updated-by`` sits on the *amended* work and lists the notices (retraction,
  correction...) that point at it; ``update-to`` sits on the *notice* and points
  back at the amended work. A retracted paper therefore carries its retraction in
  ``updated-by``, not ``update-to``.
* OpenAlex ``GET /works/https://doi.org/{doi}``:
  ``id``, ``title``, ``type``, ``publication_year``, ``is_retracted``,
  ``open_access.is_oa``, ``primary_location.source``, ``authorships``, ``cited_by_count``.
  Note that OpenAlex flags retraction *notices* themselves as ``is_retracted``.
"""

import asyncio
import datetime as dt
import html
import logging
import random
import re
from collections.abc import Iterable
from types import TracebackType
from typing import Any, Final, Self, TypeAlias, TypeVar

import httpx

from papaver.core.config import Settings, get_settings
from papaver.core.exceptions import (
    IngestionError,
    PaperNotFoundError,
    UpstreamServiceError,
    UpstreamTimeoutError,
)
from papaver.core.schemas import (
    AuthorInfo,
    DataSource,
    PaperMetadata,
    UpdateNotice,
    UpdateType,
    parse_issn,
    parse_openalex_id,
    parse_orcid,
)
from papaver.utils.doi import normalize_doi, quote_doi, try_normalize_doi

__all__ = ["IngestionService", "build_paper_metadata"]

logger = logging.getLogger(__name__)

JSONObject: TypeAlias = dict[str, Any]
_T = TypeVar("_T")

_RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_DELAY: Final[float] = 30.0
_CROSSREF_DATE_FIELDS: Final[tuple[str, ...]] = (
    "published",
    "issued",
    "published-print",
    "published-online",
)
#: Only the OpenAlex fields we actually use (the full work record is ~100 KB).
_OPENALEX_SELECT: Final[str] = ",".join(
    (
        "id",
        "doi",
        "title",
        "type",
        "publication_year",
        "is_retracted",
        "open_access",
        "primary_location",
        "authorships",
        "cited_by_count",
    )
)
#: OpenAlex work types that denote an editorial notice rather than a research paper.
_OPENALEX_NOTICE_TYPES: Final[frozenset[str]] = frozenset({"retraction", "erratum"})
_TAG: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")
_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")


class IngestionService:
    """Fetches and merges paper metadata from Crossref and OpenAlex.

    Use as an async context manager so the connection pool is closed::

        async with IngestionService() as ingest:
            paper = await ingest.get_paper_metadata("10.1016/S0140-6736(97)11096-0")

    An existing ``httpx.AsyncClient`` may be injected (e.g. for tests); it is then
    left open on exit, since the caller owns it.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._headers = {
            "User-Agent": self._settings.user_agent(),
            "Accept": "application/json",
        }
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._settings.http_timeout),
            follow_redirects=True,
        )

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

    # ------------------------------------------------------------------ fetchers

    async def fetch_crossref(self, doi: str) -> JSONObject:
        """Return the Crossref ``message`` object for a DOI.

        Raises:
            InvalidDOIError: ``doi`` is not a DOI.
            PaperNotFoundError: Crossref has no such DOI (HTTP 404).
            UpstreamTimeoutError: no answer within the timeout, after retries.
            UpstreamServiceError: any other HTTP or payload problem.
        """
        doi = normalize_doi(doi)
        url = f"{self._settings.crossref_base_url}/works/{quote_doi(doi)}"
        payload = await self._get_json(DataSource.CROSSREF, doi, url)
        message = payload.get("message")
        if not isinstance(message, dict):
            raise UpstreamServiceError(DataSource.CROSSREF, "response has no 'message' object")
        return message

    async def fetch_openalex(self, doi: str) -> JSONObject:
        """Return the OpenAlex work record for a DOI (same errors as :meth:`fetch_crossref`)."""
        doi = normalize_doi(doi)
        url = f"{self._settings.openalex_base_url}/works/https://doi.org/{quote_doi(doi)}"
        return await self._get_json(
            DataSource.OPENALEX, doi, url, params={"select": _OPENALEX_SELECT}
        )

    async def get_paper_metadata(self, doi: str) -> PaperMetadata:
        """Query Crossref and OpenAlex concurrently and merge the results.

        If one source fails, the record is built from the other and the failure is
        logged as a warning (``PaperMetadata.sources`` shows who contributed).

        Raises:
            InvalidDOIError: ``doi`` is not a DOI.
            PaperNotFoundError: neither source knows the DOI.
            UpstreamTimeoutError | UpstreamServiceError: both sources failed and at
                least one failure was not a plain "not found".
        """
        doi = normalize_doi(doi)
        outcomes = await asyncio.gather(
            self.fetch_crossref(doi),
            self.fetch_openalex(doi),
            return_exceptions=True,
        )

        records: dict[DataSource, JSONObject] = {}
        failures: dict[DataSource, IngestionError] = {}
        for source, outcome in zip((DataSource.CROSSREF, DataSource.OPENALEX), outcomes, strict=True):
            if isinstance(outcome, IngestionError):
                failures[source] = outcome
            elif isinstance(outcome, BaseException):
                raise outcome  # a bug or a cancellation, not an upstream problem
            else:
                records[source] = outcome

        if not records:
            errors = list(failures.values())
            if all(isinstance(error, PaperNotFoundError) for error in errors):
                raise PaperNotFoundError(doi, " and ".join(failures))
            # A timeout or outage means we cannot claim the DOI does not exist.
            raise next(error for error in errors if not isinstance(error, PaperNotFoundError))

        for error in failures.values():
            logger.warning("Continuing without %s: %s", error.source, error)

        return build_paper_metadata(
            doi,
            crossref=records.get(DataSource.CROSSREF),
            openalex=records.get(DataSource.OPENALEX),
        )

    # ----------------------------------------------------------------- transport

    async def _get_json(
        self,
        source: DataSource,
        doi: str,
        url: str,
        params: dict[str, str] | None = None,
    ) -> JSONObject:
        """GET ``url`` and return its JSON object, retrying transient failures."""
        attempts = self._settings.max_retries + 1
        for attempt in range(1, attempts + 1):
            is_last = attempt == attempts
            try:
                response = await self._client.get(url, params=params, headers=self._headers)
            except httpx.TimeoutException as exc:
                if is_last:
                    raise UpstreamTimeoutError(source, self._settings.http_timeout, attempts) from exc
                logger.debug("%s timed out (attempt %d/%d)", source, attempt, attempts)
                await self._sleep_before_retry(attempt)
                continue
            except httpx.TransportError as exc:
                if is_last:
                    raise UpstreamServiceError(source, f"network error: {exc!r}") from exc
                logger.debug("%s network error (attempt %d/%d): %r", source, attempt, attempts, exc)
                await self._sleep_before_retry(attempt)
                continue

            status = response.status_code
            if status == httpx.codes.NOT_FOUND:
                raise PaperNotFoundError(doi, source)
            if status in _RETRYABLE_STATUS and not is_last:
                logger.debug("%s returned HTTP %d (attempt %d/%d)", source, status, attempt, attempts)
                await self._sleep_before_retry(attempt, response)
                continue
            if response.is_error:
                raise UpstreamServiceError(source, f"HTTP {status}", status_code=status)

            try:
                payload = response.json()
            except ValueError as exc:
                raise UpstreamServiceError(source, "response is not valid JSON", status) from exc
            if not isinstance(payload, dict):
                raise UpstreamServiceError(source, "expected a JSON object", status)
            return payload

        raise AssertionError("unreachable: the last attempt always returns or raises")

    async def _sleep_before_retry(self, attempt: int, response: httpx.Response | None = None) -> None:
        """Exponential backoff with jitter; honours a numeric ``Retry-After`` header."""
        delay = self._settings.retry_backoff * 2 ** (attempt - 1)
        if response is not None:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            if retry_after is not None:
                delay = max(delay, retry_after)
        delay = min(delay, _MAX_RETRY_DELAY)
        if delay > 0:
            await asyncio.sleep(delay + random.uniform(0, delay / 10))


# ---------------------------------------------------------------------- merging


def build_paper_metadata(
    doi: str,
    *,
    crossref: JSONObject | None,
    openalex: JSONObject | None,
) -> PaperMetadata:
    """Merge a Crossref ``message`` and an OpenAlex work into one :class:`PaperMetadata`.

    Precedence: Crossref (the registration agency) for bibliographic facts, OpenAlex
    for authors, open-access status and citation counts. Either input may be ``None``.
    """
    doi = normalize_doi(doi)
    cr = crossref or {}
    oa = openalex or {}
    venue = _as_dict(_dig(oa, "primary_location", "source"))

    update_notices = _dedupe(
        notice
        for entry in _as_list(cr.get("updated-by"))
        if (notice := _update_notice(entry, this_doi=doi, this_is_notice=False))
    )
    amends = _dedupe(
        notice
        for entry in _as_list(cr.get("update-to"))
        if (notice := _update_notice(entry, this_doi=doi, this_is_notice=True))
    )
    # OpenAlex also sets `is_retracted` on the retraction *notice* itself (its `type`
    # is "retraction"), so for notices only Crossref's `updated-by` is trusted.
    is_notice = bool(amends) or oa.get("type") in _OPENALEX_NOTICE_TYPES
    is_retracted = (oa.get("is_retracted") is True and not is_notice) or any(
        notice.update_type.is_retraction for notice in update_notices
    )

    issns = [
        issn
        for raw in (*_as_list(cr.get("ISSN")), *_as_list(venue.get("issn")))
        if (issn := parse_issn(_text(raw)))
    ]

    sources = [
        source
        for source, record in ((DataSource.CROSSREF, crossref), (DataSource.OPENALEX, openalex))
        if record is not None
    ]

    return PaperMetadata(
        doi=doi,
        title=_first_text(cr.get("title")) or _clean_text(oa.get("title")),
        year=_first_not_none(_crossref_year(cr), _as_year(oa.get("publication_year"))),
        journal_name=_first_text(cr.get("container-title")) or _clean_text(venue.get("display_name")),
        journal_issn=issns,
        publisher=_clean_text(cr.get("publisher")) or _clean_text(venue.get("host_organization_name")),
        openalex_id=parse_openalex_id(_text(oa.get("id")), "W"),
        is_oa=_as_bool(_dig(oa, "open_access", "is_oa")),
        is_retracted=is_retracted,
        update_notices=update_notices,
        amends=amends,
        authors=_openalex_authors(oa) or _crossref_authors(cr),
        cited_by_count=_first_not_none(
            _as_count(oa.get("cited_by_count")),
            _as_count(cr.get("is-referenced-by-count")),
        ),
        sources=sources,
    )


def _update_notice(entry: object, *, this_doi: str, this_is_notice: bool) -> UpdateNotice | None:
    """Parse one ``updated-by`` / ``update-to`` entry, orienting it relative to ``this_doi``."""
    if not isinstance(entry, dict):
        return None
    other_doi = try_normalize_doi(_text(entry.get("DOI")))
    if other_doi is None:
        return None
    notice_doi, target_doi = (this_doi, other_doi) if this_is_notice else (other_doi, this_doi)
    return UpdateNotice(
        update_type=UpdateType(_text(entry.get("type")) or UpdateType.OTHER),
        label=_clean_text(entry.get("label")),
        notice_doi=notice_doi,
        target_doi=target_doi,
        date=_crossref_date(entry.get("updated")),
        source=_clean_text(entry.get("source")),
        record_id=_text(entry.get("record-id")),
    )


def _openalex_authors(work: JSONObject) -> list[AuthorInfo]:
    authors: list[AuthorInfo] = []
    for authorship in _as_list(work.get("authorships")):
        if not isinstance(authorship, dict):
            continue
        author = _as_dict(authorship.get("author"))
        name = _clean_text(author.get("display_name")) or _clean_text(authorship.get("raw_author_name"))
        if not name:
            continue
        institutions = [
            _clean_text(institution.get("display_name"))
            for institution in _as_list(authorship.get("institutions"))
            if isinstance(institution, dict)
        ]
        raw_affiliations = [_clean_text(raw) for raw in _as_list(authorship.get("raw_affiliation_strings"))]
        authors.append(
            AuthorInfo(
                name=name,
                orcid=parse_orcid(_text(author.get("orcid"))),
                openalex_id=parse_openalex_id(_text(author.get("id")), "A"),
                affiliations=_dedupe(i for i in institutions if i)
                or _dedupe(a for a in raw_affiliations if a),
            )
        )
    return authors


def _crossref_authors(message: JSONObject) -> list[AuthorInfo]:
    authors: list[AuthorInfo] = []
    for author in _as_list(message.get("author")):
        if not isinstance(author, dict):
            continue
        parts = (_clean_text(author.get("given")), _clean_text(author.get("family")))
        name = " ".join(part for part in parts if part) or _clean_text(author.get("name"))
        if not name:
            continue
        affiliations = (
            _clean_text(affiliation.get("name"))
            for affiliation in _as_list(author.get("affiliation"))
            if isinstance(affiliation, dict)
        )
        authors.append(
            AuthorInfo(
                name=name,
                orcid=parse_orcid(_text(author.get("ORCID"))),
                affiliations=_dedupe(a for a in affiliations if a),
            )
        )
    return authors


def _crossref_year(message: JSONObject) -> int | None:
    for field in _CROSSREF_DATE_FIELDS:
        date_parts = _as_list(_dig(message, field, "date-parts"))
        first = _as_list(date_parts[0]) if date_parts else []
        if first and (year := _as_year(first[0])) is not None:
            return year
    return None


def _crossref_date(value: object) -> dt.date | None:
    """Parse a Crossref date object; only full ``[y, m, d]`` dates are returned."""
    date_parts = _as_list(_dig(value, "date-parts"))
    parts = _as_list(date_parts[0]) if date_parts else []
    if len(parts) >= 3 and all(isinstance(p, int) and not isinstance(p, bool) for p in parts[:3]):
        try:
            return dt.date(parts[0], parts[1], parts[2])
        except ValueError:
            return None
    return None


# ---------------------------------------------------------- defensive accessors


def _dig(obj: object, *keys: str) -> object:
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _as_dict(value: object) -> JSONObject:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_year(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 1000 <= value <= 2100:
        return value
    return None


def _as_count(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _text(value: object) -> str | None:
    """Return a stripped string for str/int values, else ``None``."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _clean_text(value: object) -> str | None:
    """Strip JATS/HTML tags, unescape entities and collapse whitespace."""
    if not isinstance(value, str):
        return None
    cleaned = _WHITESPACE.sub(" ", html.unescape(_TAG.sub("", value))).strip()
    return cleaned or None


def _first_text(value: object) -> str | None:
    """Crossref wraps titles in lists; return the first non-empty one."""
    if isinstance(value, list):
        return next((text for item in value if (text := _clean_text(item))), None)
    return _clean_text(value)


def _first_not_none(*values: _T | None) -> _T | None:
    return next((value for value in values if value is not None), None)


def _dedupe(items: Iterable[_T]) -> list[_T]:
    seen: list[_T] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None  # HTTP-date form: fall back to plain backoff
    return seconds if seconds >= 0 else None
