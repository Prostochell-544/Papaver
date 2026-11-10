import json
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from papaver import cli
from papaver.core.config import Settings
from papaver.core.exceptions import PaperNotFoundError
from papaver.services.audit import run_audit
from tests.conftest import WAKEFIELD_DOI

CROSSREF_HOST = "api.crossref.org"
OPENALEX_HOST = "api.openalex.org"
S2_HOST = "api.semanticscholar.org"
WAKEFIELD_INPUT = "10.1016/S0140-6736(97)11096-0"


@pytest.fixture
def all_sources_ok(
    respx_mock: respx.MockRouter,
    crossref_wakefield: dict[str, Any],
    openalex_wakefield: dict[str, Any],
    s2_wakefield_page: dict[str, Any],
) -> respx.MockRouter:
    respx_mock.get(host=CROSSREF_HOST).respond(json=crossref_wakefield)
    respx_mock.get(host=OPENALEX_HOST).respond(json=openalex_wakefield)
    respx_mock.get(host=S2_HOST).respond(json=s2_wakefield_page)
    return respx_mock


# ------------------------------------------------------------------ pipeline


async def test_run_audit_combines_metadata_and_citations(
    settings: Settings, all_sources_ok: respx.MockRouter
) -> None:
    report = await run_audit(WAKEFIELD_INPUT, settings, use_cache=False)

    assert report.target == WAKEFIELD_INPUT
    assert report.paper.doi == WAKEFIELD_DOI
    assert report.paper.is_retracted is True
    assert report.citations is not None
    assert report.citations.scanned == 2
    assert [i.citing_paper.doi for i in report.citations.items] == ["10.3390/vaccines11020288"]
    assert report.warnings == []
    assert report.verdict is None


async def test_citation_failure_becomes_warning(
    settings: Settings,
    respx_mock: respx.MockRouter,
    crossref_wakefield: dict[str, Any],
    openalex_wakefield: dict[str, Any],
) -> None:
    respx_mock.get(host=CROSSREF_HOST).respond(json=crossref_wakefield)
    respx_mock.get(host=OPENALEX_HOST).respond(json=openalex_wakefield)
    respx_mock.get(host=S2_HOST).respond(429)

    report = await run_audit(WAKEFIELD_DOI, settings, use_cache=False)

    assert report.paper.is_retracted is True
    assert report.citations is None
    assert len(report.warnings) == 1 and "rate limited" in report.warnings[0]


async def test_unknown_to_semantic_scholar_is_a_soft_warning(
    settings: Settings,
    respx_mock: respx.MockRouter,
    crossref_wakefield: dict[str, Any],
    openalex_wakefield: dict[str, Any],
) -> None:
    respx_mock.get(host=CROSSREF_HOST).respond(json=crossref_wakefield)
    respx_mock.get(host=OPENALEX_HOST).respond(json=openalex_wakefield)
    respx_mock.get(host=S2_HOST).respond(404)

    report = await run_audit(WAKEFIELD_DOI, settings, use_cache=False)

    assert report.citations is None
    assert report.warnings == ["Semantic Scholar has no record of this DOI; citation contexts were skipped."]


async def test_missing_crossref_is_flagged(
    settings: Settings,
    respx_mock: respx.MockRouter,
    openalex_wakefield: dict[str, Any],
    s2_wakefield_page: dict[str, Any],
) -> None:
    respx_mock.get(host=CROSSREF_HOST).mock(side_effect=httpx.ConnectError("down"))
    respx_mock.get(host=OPENALEX_HOST).respond(json=openalex_wakefield)
    respx_mock.get(host=S2_HOST).respond(json=s2_wakefield_page)

    report = await run_audit(WAKEFIELD_DOI, settings, use_cache=False)

    assert any("No data from Crossref" in warning for warning in report.warnings)
    assert report.citations is not None


async def test_metadata_not_found_cancels_citations(settings: Settings, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(host=CROSSREF_HOST).respond(404)
    respx_mock.get(host=OPENALEX_HOST).respond(404)
    respx_mock.get(host=S2_HOST).mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(PaperNotFoundError):
        await run_audit("10.1234/nope", settings, use_cache=False)


async def test_zero_citations_skips_semantic_scholar(
    settings: Settings, all_sources_ok: respx.MockRouter
) -> None:
    report = await run_audit(WAKEFIELD_DOI, settings, max_citations=0, use_cache=False)

    assert report.citations is None
    assert report.warnings == []
    assert all(call.request.url.host != S2_HOST for call in all_sources_ok.calls)


# ----------------------------------------------------------------------- CLI


@pytest.fixture
def runner(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return CliRunner()


def test_cli_check_prints_metadata_and_contexts(runner: CliRunner, all_sources_ok: respx.MockRouter) -> None:
    result = runner.invoke(cli.app, ["check", WAKEFIELD_INPUT, "--no-cache"])

    assert result.exit_code == 0, result.output
    assert "is_retracted=True" in result.output
    assert "Citation contexts (Semantic Scholar)" in result.output
    assert "Scanned 2 citing papers: 1 with usable contexts, 1 influential." in result.output
    assert "now-infamous" in result.output  # rich wraps lines, so match a single word


def test_cli_check_json(runner: CliRunner, all_sources_ok: respx.MockRouter) -> None:
    result = runner.invoke(cli.app, ["check", WAKEFIELD_INPUT, "--json", "--no-cache"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["paper"]["is_retracted"] is True
    assert report["citations"]["scanned"] == 2


def test_cli_rejects_invalid_doi(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, ["check", "not-a-doi"])

    assert result.exit_code == 2
    assert "Not a valid DOI" in result.output


def test_cli_reports_not_found(runner: CliRunner, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(host=CROSSREF_HOST).respond(404)
    respx_mock.get(host=OPENALEX_HOST).respond(404)
    respx_mock.get(host=S2_HOST).respond(404)

    result = runner.invoke(cli.app, ["check", "10.1234/nope", "--no-cache"])

    assert result.exit_code == 1
    assert "Not found" in result.output
