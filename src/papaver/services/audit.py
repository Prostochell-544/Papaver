"""The ``check`` pipeline: metadata + citation contexts -> :class:`AuditReport`."""

import asyncio
import logging

from papaver.core.config import Settings
from papaver.core.exceptions import IngestionError, PaperNotFoundError
from papaver.core.schemas import AuditReport, CitationScan, DataSource, PaperMetadata
from papaver.db.cache import open_response_cache
from papaver.services.citations import CitationService
from papaver.services.http import create_http_client
from papaver.services.ingest import IngestionService
from papaver.utils.doi import normalize_doi

__all__ = ["run_audit"]

logger = logging.getLogger(__name__)

_METADATA_SOURCES: tuple[DataSource, ...] = (DataSource.CROSSREF, DataSource.OPENALEX)


async def run_audit(
    target: str,
    settings: Settings,
    *,
    max_citations: int | None = None,
    use_cache: bool = True,
) -> AuditReport:
    """Collect everything known about ``target`` (a DOI) into an :class:`AuditReport`.

    Crossref/OpenAlex metadata and Semantic Scholar citations are fetched concurrently.
    Metadata is mandatory: if it fails, the citation request is cancelled and the
    error propagates. Citations are optional: a failure there becomes a report warning.

    Raises:
        InvalidDOIError: ``target`` is not a DOI.
        PaperNotFoundError: neither Crossref nor OpenAlex knows the DOI.
        UpstreamTimeoutError | UpstreamServiceError: metadata could not be fetched.
    """
    doi = normalize_doi(target)
    budget = settings.s2_max_citations if max_citations is None else max_citations

    async with (
        open_response_cache(settings, enabled=use_cache) as cache,
        create_http_client(settings) as client,
        IngestionService(settings, client=client, cache=cache) as ingest,
        CitationService(settings, client=client, cache=cache) as citations,
    ):
        citations_task = (
            asyncio.create_task(citations.scan_citations(doi, max_citations=budget))
            if budget > 0
            else None
        )
        try:
            paper = await ingest.get_paper_metadata(doi)
        except BaseException:
            if citations_task is not None:
                citations_task.cancel()
                await asyncio.gather(citations_task, return_exceptions=True)
            raise
        scan, citation_warnings = await _collect_citations(citations_task)

    return AuditReport(
        target=target,
        paper=paper,
        citations=scan,
        warnings=[*_missing_source_warnings(paper), *citation_warnings],
    )


async def _collect_citations(
    task: asyncio.Task[CitationScan] | None,
) -> tuple[CitationScan | None, list[str]]:
    if task is None:
        return None, []
    try:
        return await task, []
    except PaperNotFoundError:
        return None, ["Semantic Scholar has no record of this DOI; citation contexts were skipped."]
    except IngestionError as exc:
        logger.debug("Citation scan failed", exc_info=exc)
        return None, [f"Citation contexts are unavailable ({exc})."]


def _missing_source_warnings(paper: PaperMetadata) -> list[str]:
    warnings: list[str] = []
    if DataSource.CROSSREF not in paper.sources:
        warnings.append(
            "No data from Crossref: retraction/correction notices (`updated-by`) were not checked."
        )
    if DataSource.OPENALEX not in paper.sources:
        warnings.append("No data from OpenAlex: authors, open-access status and citation count are partial.")
    return warnings
