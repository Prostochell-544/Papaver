import copy
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError

from papaver.core.config import Settings
from papaver.core.exceptions import (
    PaperNotFoundError,
    UpstreamServiceError,
    UpstreamTimeoutError,
)
from papaver.core.schemas import AuthorInfo, DataSource, PaperMetadata, UpdateType
from papaver.services.ingest import IngestionService, build_paper_metadata
from tests.conftest import RETRACTION_NOTICE_DOI, WAKEFIELD_DOI

CROSSREF_HOST = "api.crossref.org"
OPENALEX_HOST = "api.openalex.org"


# ----------------------------------------------------------- orchestration (HTTP)


async def test_merges_crossref_and_openalex(
    service: IngestionService,
    respx_mock: respx.MockRouter,
    crossref_wakefield: dict[str, Any],
    openalex_wakefield: dict[str, Any],
) -> None:
    crossref = respx_mock.get(host=CROSSREF_HOST).respond(json=crossref_wakefield)
    openalex = respx_mock.get(host=OPENALEX_HOST).respond(json=openalex_wakefield)

    paper = await service.get_paper_metadata("https://doi.org/10.1016/S0140-6736(97)11096-0")

    assert paper.doi == WAKEFIELD_DOI
    assert paper.title is not None and paper.title.startswith("RETRACTED:")
    assert paper.year == 1998
    assert paper.journal_name == "The Lancet"
    assert paper.journal_issn == ["0140-6736", "0099-5355", "1474-547X"]
    assert paper.openalex_id == "W2117847125"
    assert paper.is_oa is False
    assert paper.is_retracted is True
    assert [n.update_type for n in paper.update_notices] == [UpdateType.CORRECTION, UpdateType.RETRACTION]
    assert [n.notice_doi for n in paper.retraction_notices] == [RETRACTION_NOTICE_DOI]
    assert all(n.target_doi == WAKEFIELD_DOI for n in paper.update_notices)
    assert paper.amends == []
    assert paper.cited_by_count == 3106  # OpenAlex preferred over Crossref's 2048
    assert paper.authors[0] == AuthorInfo(
        name="Andrew J. Wakefield",
        openalex_id="A5110236514",
        affiliations=["The Royal Free Hospital"],
    )
    assert paper.authors[1].orcid == "0000-0002-3870-8229"
    # No institutions -> falls back to the raw affiliation string.
    assert paper.authors[1].affiliations == ["Royal Free Hospital and School of Medicine, London, UK"]
    assert paper.sources == [DataSource.CROSSREF, DataSource.OPENALEX]

    crossref_request = crossref.calls.last.request
    assert crossref_request.url.path == f"/works/{WAKEFIELD_DOI}"
    assert crossref_request.headers["User-Agent"] == "Papaver/0.1.0 (mailto:tests@example.org)"
    openalex_request = openalex.calls.last.request
    assert WAKEFIELD_DOI in openalex_request.url.path
    assert "is_retracted" in openalex_request.url.params["select"]


async def test_falls_back_to_openalex_when_crossref_is_down(
    service: IngestionService,
    respx_mock: respx.MockRouter,
    openalex_wakefield: dict[str, Any],
) -> None:
    crossref = respx_mock.get(host=CROSSREF_HOST).respond(503)
    respx_mock.get(host=OPENALEX_HOST).respond(json=openalex_wakefield)

    paper = await service.get_paper_metadata(WAKEFIELD_DOI)

    assert crossref.call_count == 2  # first try + max_retries=1
    assert paper.sources == [DataSource.OPENALEX]
    assert paper.is_retracted is True  # from OpenAlex alone
    assert paper.update_notices == []
    assert paper.year == 1998
    assert paper.journal_issn == ["0099-5355", "0140-6736", "1474-547X"]


async def test_retries_transient_error_then_succeeds(
    service: IngestionService,
    respx_mock: respx.MockRouter,
    crossref_wakefield: dict[str, Any],
) -> None:
    route = respx_mock.get(host=CROSSREF_HOST)
    route.side_effect = [httpx.Response(429), httpx.Response(200, json=crossref_wakefield)]

    message = await service.fetch_crossref(WAKEFIELD_DOI)

    assert route.call_count == 2
    assert message["publisher"] == "Elsevier BV"


async def test_not_found_in_both_sources(service: IngestionService, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(host=CROSSREF_HOST).respond(404, text="Resource not found.")
    respx_mock.get(host=OPENALEX_HOST).respond(404, json={"error": "Not found"})

    with pytest.raises(PaperNotFoundError) as excinfo:
        await service.get_paper_metadata("10.1234/does-not-exist")

    assert excinfo.value.doi == "10.1234/does-not-exist"


async def test_timeouts_raise_upstream_timeout(service: IngestionService, respx_mock: respx.MockRouter) -> None:
    crossref = respx_mock.get(host=CROSSREF_HOST).mock(side_effect=httpx.ReadTimeout("slow"))
    respx_mock.get(host=OPENALEX_HOST).mock(side_effect=httpx.ConnectTimeout("slow"))

    with pytest.raises(UpstreamTimeoutError) as excinfo:
        await service.get_paper_metadata(WAKEFIELD_DOI)

    assert excinfo.value.attempts == 2
    assert crossref.call_count == 2


async def test_timeout_is_not_reported_as_not_found(
    service: IngestionService, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(host=CROSSREF_HOST).respond(404)
    respx_mock.get(host=OPENALEX_HOST).mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(UpstreamTimeoutError) as excinfo:
        await service.get_paper_metadata(WAKEFIELD_DOI)

    assert excinfo.value.source == DataSource.OPENALEX


async def test_non_json_payload_is_service_error(
    service: IngestionService, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(host=CROSSREF_HOST).respond(200, text="<html>maintenance</html>")

    with pytest.raises(UpstreamServiceError):
        await service.fetch_crossref(WAKEFIELD_DOI)


# ------------------------------------------------------------- merging (pure)


def test_retraction_notice_itself_is_not_marked_retracted() -> None:
    crossref_notice = {
        "title": ["Retraction—Ileal-lymphoid-nodular hyperplasia ..."],
        "update-to": [
            {
                "DOI": WAKEFIELD_DOI,
                "type": "retraction",
                "label": "Retraction",
                "source": "retraction-watch",
                "updated": {"date-parts": [[2010, 2, 6]]},
                "record-id": "4036",
            }
        ],
    }
    # OpenAlex really does flag the notice as retracted.
    openalex_notice = {"type": "retraction", "is_retracted": True}

    paper = build_paper_metadata(RETRACTION_NOTICE_DOI, crossref=crossref_notice, openalex=openalex_notice)

    assert paper.is_retracted is False
    assert paper.is_update_notice is True
    assert paper.update_notices == []
    (link,) = paper.amends
    assert (link.notice_doi, link.target_doi) == (RETRACTION_NOTICE_DOI, WAKEFIELD_DOI)
    assert link.update_type is UpdateType.RETRACTION


def test_crossref_only_detects_retraction_and_normalizes_types(crossref_wakefield: dict[str, Any]) -> None:
    message = copy.deepcopy(crossref_wakefield["message"])
    message["updated-by"][0]["type"] = "expression_of_concern"
    message["updated-by"].append(copy.deepcopy(message["updated-by"][1]))  # exact duplicate
    message["title"] = ["The <i>in vivo</i> &amp; <scp>effect</scp>"]

    paper = build_paper_metadata(WAKEFIELD_DOI, crossref=message, openalex=None)

    assert paper.is_retracted is True
    assert [n.update_type for n in paper.update_notices] == [
        UpdateType.EXPRESSION_OF_CONCERN,
        UpdateType.RETRACTION,
    ]
    assert paper.title == "The in vivo & effect"
    assert paper.authors[0].name == "AJ Wakefield"
    assert paper.cited_by_count == 2048
    assert paper.is_oa is None
    assert paper.sources == [DataSource.CROSSREF]


def test_malformed_upstream_fields_are_dropped_not_fatal() -> None:
    openalex = {
        "id": "garbage",
        "publication_year": 99999,
        "cited_by_count": -5,
        "primary_location": None,
        "authorships": [{"author": {"display_name": "A. Author", "orcid": "not-an-orcid"}}],
    }
    paper = build_paper_metadata(WAKEFIELD_DOI, crossref={"ISSN": ["bad", "0140 6736"]}, openalex=openalex)

    assert paper.openalex_id is None
    assert paper.year is None
    assert paper.cited_by_count is None
    assert paper.journal_issn == []
    assert paper.authors == [AuthorInfo(name="A. Author")]


# -------------------------------------------------------- schemas and settings


def test_update_type_accepts_crossref_spellings() -> None:
    assert UpdateType("expression_of_concern") is UpdateType.EXPRESSION_OF_CONCERN
    assert UpdateType("Partial Retraction") is UpdateType.PARTIAL_RETRACTION
    assert UpdateType("something-new") is UpdateType.OTHER
    assert UpdateType.WITHDRAWAL.is_retraction and not UpdateType.CORRECTION.is_retraction


def test_paper_metadata_is_strict() -> None:
    with pytest.raises(ValidationError):
        PaperMetadata(doi="not-a-doi")
    with pytest.raises(ValidationError):
        PaperMetadata.model_validate({"doi": WAKEFIELD_DOI, "unexpected": 1})
    with pytest.raises(ValidationError):
        AuthorInfo(name="X", orcid="1234")
    assert AuthorInfo(name="X", orcid="https://orcid.org/0000-0002-1825-009x").orcid == "0000-0002-1825-009X"


def test_user_agent() -> None:
    assert Settings(_env_file=None).user_agent() == "Papaver/0.1.0"  # type: ignore[call-arg]
    with_email = Settings(_env_file=None, contact_email=" me@example.org ")  # type: ignore[call-arg]
    assert with_email.user_agent() == "Papaver/0.1.0 (mailto:me@example.org)"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, contact_email="not-an-email")  # type: ignore[call-arg]
