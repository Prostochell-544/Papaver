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
from papaver.core.config import Settings, get_settings
from papaver.core.exceptions import PaperNotFoundError, PapaverError
from papaver.core.schemas import PaperMetadata
from papaver.services.ingest import IngestionService
from papaver.utils.doi import InvalidDOIError, normalize_doi

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
        typer.Option("--verbose", "-v", help="Log retries and HTTP requests."),
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
) -> None:
    """Fetch a paper's metadata from Crossref and OpenAlex and print the merged record."""
    try:
        doi = normalize_doi(target)
    except InvalidDOIError as exc:
        _fail(f"{exc}. Only DOIs are supported for now (PMID/URL lookup comes later).", code=2)

    try:
        settings = get_settings()
    except ValidationError as exc:
        _fail(f"Invalid configuration (check PAPAVER_* variables / .env):\n{exc}", code=2)

    try:
        with err_console.status(f"Querying Crossref and OpenAlex for [bold]{escape(doi)}[/]..."):
            metadata = asyncio.run(_fetch_metadata(doi, settings))
    except PaperNotFoundError as exc:
        _fail(f"Not found: {exc}")
    except PapaverError as exc:
        _fail(str(exc))

    rich.print(metadata)


async def _fetch_metadata(doi: str, settings: Settings) -> PaperMetadata:
    async with IngestionService(settings) as ingest:
        return await ingest.get_paper_metadata(doi)


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
