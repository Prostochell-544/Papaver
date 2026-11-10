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
import logging
from typing import Final

from papaver.core.exceptions import IngestionError, PaperNotFoundError, UpstreamServiceError
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
from papaver.services.http import ApiService
from papaver.utils.doi import normalize_doi, quote_doi, try_normalize_doi
from papaver.utils.payload import (
    JSONObject,
    as_bool,
    as_count,
    as_dict,
    as_list,
    as_year,
    clean_text,
    dedupe,
    dig,
    first_not_none,
    first_text,
    text,
)

__all__ = ["IngestionService", "build_paper_metadata"]

logger = logging.getLogger(__name__)

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


class IngestionService(ApiService):
    """Fetches and merges paper metadata from Crossref and OpenAlex.

    ::

        async with IngestionService() as ingest:
            paper = await ingest.get_paper_metadata("10.1016/S0140-6736(97)11096-0")
    """

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
        payload = await self._get_json(DataSource.CROSSREF, url, doi=doi)
        message = payload.get("message")
        if not isinstance(message, dict):
            raise UpstreamServiceError(DataSource.CROSSREF, "response has no 'message' object")
        return message

    async def fetch_openalex(self, doi: str) -> JSONObject:
        """Return the OpenAlex work record for a DOI (same errors as :meth:`fetch_crossref`)."""
        doi = normalize_doi(doi)
        url = f"{self._settings.openalex_base_url}/works/https://doi.org/{quote_doi(doi)}"
        return await self._get_json(
            DataSource.OPENALEX, url, doi=doi, params={"select": _OPENALEX_SELECT}
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
    venue = as_dict(dig(oa, "primary_location", "source"))

    update_notices = dedupe(
        notice
        for entry in as_list(cr.get("updated-by"))
        if (notice := _update_notice(entry, this_doi=doi, this_is_notice=False))
    )
    amends = dedupe(
        notice
        for entry in as_list(cr.get("update-to"))
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
        for raw in (*as_list(cr.get("ISSN")), *as_list(venue.get("issn")))
        if (issn := parse_issn(text(raw)))
    ]

    sources = [
        source
        for source, record in ((DataSource.CROSSREF, crossref), (DataSource.OPENALEX, openalex))
        if record is not None
    ]

    return PaperMetadata(
        doi=doi,
        title=first_text(cr.get("title")) or clean_text(oa.get("title")),
        year=first_not_none(_crossref_year(cr), as_year(oa.get("publication_year"))),
        journal_name=first_text(cr.get("container-title")) or clean_text(venue.get("display_name")),
        journal_issn=issns,
        publisher=clean_text(cr.get("publisher")) or clean_text(venue.get("host_organization_name")),
        openalex_id=parse_openalex_id(text(oa.get("id")), "W"),
        is_oa=as_bool(dig(oa, "open_access", "is_oa")),
        is_retracted=is_retracted,
        update_notices=update_notices,
        amends=amends,
        authors=_openalex_authors(oa) or _crossref_authors(cr),
        cited_by_count=first_not_none(
            as_count(oa.get("cited_by_count")),
            as_count(cr.get("is-referenced-by-count")),
        ),
        sources=sources,
    )


def _update_notice(entry: object, *, this_doi: str, this_is_notice: bool) -> UpdateNotice | None:
    """Parse one ``updated-by`` / ``update-to`` entry, orienting it relative to ``this_doi``."""
    if not isinstance(entry, dict):
        return None
    other_doi = try_normalize_doi(text(entry.get("DOI")))
    if other_doi is None:
        return None
    notice_doi, target_doi = (this_doi, other_doi) if this_is_notice else (other_doi, this_doi)
    return UpdateNotice(
        update_type=UpdateType(text(entry.get("type")) or UpdateType.OTHER),
        label=clean_text(entry.get("label")),
        notice_doi=notice_doi,
        target_doi=target_doi,
        date=_crossref_date(entry.get("updated")),
        source=clean_text(entry.get("source")),
        record_id=text(entry.get("record-id")),
    )


def _openalex_authors(work: JSONObject) -> list[AuthorInfo]:
    authors: list[AuthorInfo] = []
    for authorship in as_list(work.get("authorships")):
        if not isinstance(authorship, dict):
            continue
        author = as_dict(authorship.get("author"))
        name = clean_text(author.get("display_name")) or clean_text(authorship.get("raw_author_name"))
        if not name:
            continue
        institutions = [
            clean_text(institution.get("display_name"))
            for institution in as_list(authorship.get("institutions"))
            if isinstance(institution, dict)
        ]
        raw_affiliations = [clean_text(raw) for raw in as_list(authorship.get("raw_affiliation_strings"))]
        authors.append(
            AuthorInfo(
                name=name,
                orcid=parse_orcid(text(author.get("orcid"))),
                openalex_id=parse_openalex_id(text(author.get("id")), "A"),
                affiliations=dedupe(i for i in institutions if i)
                or dedupe(a for a in raw_affiliations if a),
            )
        )
    return authors


def _crossref_authors(message: JSONObject) -> list[AuthorInfo]:
    authors: list[AuthorInfo] = []
    for author in as_list(message.get("author")):
        if not isinstance(author, dict):
            continue
        parts = (clean_text(author.get("given")), clean_text(author.get("family")))
        name = " ".join(part for part in parts if part) or clean_text(author.get("name"))
        if not name:
            continue
        affiliations = (
            clean_text(affiliation.get("name"))
            for affiliation in as_list(author.get("affiliation"))
            if isinstance(affiliation, dict)
        )
        authors.append(
            AuthorInfo(
                name=name,
                orcid=parse_orcid(text(author.get("ORCID"))),
                affiliations=dedupe(a for a in affiliations if a),
            )
        )
    return authors


def _crossref_year(message: JSONObject) -> int | None:
    for field in _CROSSREF_DATE_FIELDS:
        date_parts = as_list(dig(message, field, "date-parts"))
        first = as_list(date_parts[0]) if date_parts else []
        if first and (year := as_year(first[0])) is not None:
            return year
    return None


def _crossref_date(value: object) -> dt.date | None:
    """Parse a Crossref date object; only full ``[y, m, d]`` dates are returned."""
    date_parts = as_list(dig(value, "date-parts"))
    parts = as_list(date_parts[0]) if date_parts else []
    if len(parts) >= 3 and all(isinstance(p, int) and not isinstance(p, bool) for p in parts[:3]):
        try:
            return dt.date(parts[0], parts[1], parts[2])
        except ValueError:
            return None
    return None
