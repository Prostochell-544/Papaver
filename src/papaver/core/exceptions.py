"""Exception hierarchy for Papaver.

Everything raised on purpose by Papaver derives from :class:`PapaverError`, so the CLI
can turn it into a clean error message instead of a traceback.
"""


class PapaverError(Exception):
    """Base class for all expected Papaver failures."""


class IngestionError(PapaverError):
    """A bibliographic data source could not deliver a record."""

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source


class PaperNotFoundError(IngestionError):
    """The DOI is unknown to the data source (HTTP 404)."""

    def __init__(self, doi: str, source: str) -> None:
        super().__init__(source, f"no record found for DOI {doi}")
        self.doi = doi


class UpstreamTimeoutError(IngestionError):
    """The data source did not answer in time, even after retries."""

    def __init__(self, source: str, timeout: float, attempts: int) -> None:
        super().__init__(source, f"timed out after {attempts} attempt(s) ({timeout:g}s each)")
        self.timeout = timeout
        self.attempts = attempts


class UpstreamServiceError(IngestionError):
    """The data source answered with an error or an unusable payload."""

    def __init__(self, source: str, message: str, status_code: int | None = None) -> None:
        super().__init__(source, message)
        self.status_code = status_code
