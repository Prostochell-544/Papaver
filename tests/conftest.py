"""Shared fixtures: settings isolated from `.env`, and trimmed real API payloads."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from papaver.core.config import Settings
from papaver.services.ingest import IngestionService

WAKEFIELD_DOI = "10.1016/s0140-6736(97)11096-0"
RETRACTION_NOTICE_DOI = "10.1016/s0140-6736(10)60175-4"


@pytest.fixture
def settings() -> Settings:
    # `_env_file=None` keeps a developer's local `.env` out of the tests.
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        contact_email="tests@example.org",
        max_retries=1,
        retry_backoff=0,
    )


@pytest.fixture
async def service(settings: Settings) -> AsyncIterator[IngestionService]:
    async with IngestionService(settings) as ingest:
        yield ingest


@pytest.fixture
def crossref_wakefield() -> dict[str, Any]:
    """Subset of https://api.crossref.org/works/10.1016/S0140-6736(97)11096-0."""
    return {
        "status": "ok",
        "message-type": "work",
        "message": {
            "DOI": WAKEFIELD_DOI,
            "type": "journal-article",
            "title": [
                "RETRACTED: Ileal-lymphoid-nodular hyperplasia, non-specific colitis, "
                "and pervasive developmental disorder in children"
            ],
            "container-title": ["The Lancet"],
            "ISSN": ["0140-6736"],
            "publisher": "Elsevier BV",
            "published": {"date-parts": [[1998, 2]]},
            "is-referenced-by-count": 2048,
            "author": [
                {"given": "AJ", "family": "Wakefield", "sequence": "first", "affiliation": []},
                {"given": "SH", "family": "Murch", "sequence": "additional", "affiliation": []},
            ],
            "updated-by": [
                {
                    "DOI": "10.1016/s0140-6736(04)15715-2",
                    "type": "correction",
                    "label": "Correction",
                    "source": "retraction-watch",
                    "updated": {"date-parts": [[2004, 3, 6]]},
                    "record-id": "17269",
                },
                {
                    "DOI": RETRACTION_NOTICE_DOI,
                    "type": "retraction",
                    "label": "Retraction",
                    "source": "retraction-watch",
                    "updated": {"date-parts": [[2010, 2, 6]]},
                    "record-id": "4036",
                },
            ],
            "relation": {},
        },
    }


@pytest.fixture
def openalex_wakefield() -> dict[str, Any]:
    """Subset of https://api.openalex.org/works/https://doi.org/10.1016/S0140-6736(97)11096-0."""
    return {
        "id": "https://openalex.org/W2117847125",
        "doi": f"https://doi.org/{WAKEFIELD_DOI}",
        "title": "RETRACTED: Ileal-lymphoid-nodular hyperplasia, non-specific colitis, "
        "and pervasive developmental disorder in children",
        "type": "article",
        "publication_year": 1998,
        "is_retracted": True,
        "open_access": {"is_oa": False, "oa_status": "closed"},
        "primary_location": {
            "source": {
                "id": "https://openalex.org/S49861241",
                "display_name": "The Lancet",
                "issn_l": "0099-5355",
                "issn": ["0099-5355", "0140-6736", "1474-547X"],
                "host_organization_name": "Elsevier BV",
            }
        },
        "authorships": [
            {
                "author": {
                    "id": "https://openalex.org/A5110236514",
                    "display_name": "Andrew J. Wakefield",
                    "orcid": None,
                },
                "institutions": [{"display_name": "The Royal Free Hospital"}],
                "raw_author_name": "AJ Wakefield",
                "raw_affiliation_strings": ["Royal Free Hospital and School of Medicine, London, UK"],
            },
            {
                "author": {
                    "id": "https://openalex.org/A5082281633",
                    "display_name": "SIMON H MURCH",
                    "orcid": "https://orcid.org/0000-0002-3870-8229",
                },
                "institutions": [],
                "raw_author_name": "SH Murch",
                "raw_affiliation_strings": ["Royal Free Hospital and School of Medicine, London, UK"],
            },
        ],
        "cited_by_count": 3106,
    }
