"""Command-line interface: ``papaver <command>``."""

import asyncio
import io
import logging
import sys
from typing import Annotated, NoReturn

import rich
import typer
from pydantic import ValidationError
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape

from papaver import __version__
from papaver.core.config import get_settings
from papaver.core.exceptions import PaperNotFoundError, PapaverError
from papaver.services.audit import run_audit
from papaver.utils.doi import InvalidDOIError, normalize_doi
from papaver.utils.formatters import render_audit_report

app = typer.Typer(
    name="papaver",
    help="Papaver: asynchronous integrity auditor for scientific publications.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)

#: Diagnostics (spinner, warnings, errors) go to stderr so stdout stays clean for data.
err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        rich.print(f"papaver {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Log retries, cache hits and HTTP requests."),
    ] = False,
) -> None:
    """Papaver: asynchronous integrity auditor for scientific publications."""
    _ensure_utf8_streams()
    logging.basicConfig(
        level=logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=err_console, show_time=False, show_path=False)],
        force=True,
    )
    if verbose:
        logging.getLogger("papaver").setLevel(logging.DEBUG)
        logging.getLogger("httpx").setLevel(logging.INFO)


@app.command()
def check(
    target: Annotated[
        str,
        typer.Argument(
            help="DOI of the paper: bare (10.xxxx/...), 'doi:10.xxxx/...' or a https://doi.org/ link.",
            show_default=False,
        ),
    ],
    max_citations: Annotated[
        int | None,
        typer.Option(
            "--max-citations",
            min=0,
            max=9999,
            help="Citing papers to scan on Semantic Scholar; 0 skips citations. "
            "Default: PAPAVER_S2_MAX_CITATIONS (1000).",
            show_default=False,
        ),
    ] = None,
    contexts: Annotated[
        int,
        typer.Option("--contexts", min=0, help="How many citation contexts to print."),
    ] = 10,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print the full report as JSON instead of the summary."),
    ] = False,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Ignore and do not update the local response cache."),
    ] = False,
) -> None:
    """Audit a paper: Crossref/OpenAlex metadata, retraction notices and citation contexts."""
    try:
        normalize_doi(target)
    except InvalidDOIError as exc:
        _fail(f"{exc}. Only DOIs are supported for now (PMID/URL lookup comes later).", code=2)

    try:
        settings = get_settings()
    except ValidationError as exc:
        _fail(f"Invalid configuration (check PAPAVER_* variables / .env):\n{exc}", code=2)

    try:
        with err_console.status("Querying Crossref, OpenAlex and Semantic Scholar..."):
            report = asyncio.run(
                run_audit(target, settings, max_citations=max_citations, use_cache=not no_cache)
            )
    except PaperNotFoundError as exc:
        _fail(f"Not found: {exc}")
    except PapaverError as exc:
        _fail(str(exc))

    if as_json:
        sys.stdout.write(report.model_dump_json(indent=2) + "\n")
    else:
        render_audit_report(report, rich.get_console(), max_contexts=contexts)


def _ensure_utf8_streams() -> None:
    """Switch redirected stdout/stderr to UTF-8.

    On Windows, a redirected stream (pipe, file, PyCharm's Run console) defaults to
    the ANSI code page (e.g. cp1251), which cannot encode many characters found in
    titles and author names (U+2010 hyphen, accented letters, Greek...). Interactive
    consoles already use UTF-8 and are left as they are.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if isinstance(stream, io.TextIOWrapper) and encoding != "utf8":
            stream.reconfigure(encoding="utf-8")


def _fail(message: str, *, code: int = 1) -> NoReturn:
    err_console.print(f"[bold red]Error:[/] {escape(message)}")
    raise typer.Exit(code=code)
