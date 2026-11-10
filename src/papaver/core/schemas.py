"""Shared Pydantic v2 domain models.

The models validate strictly (unknown fields are rejected, identifiers are normalized
and pattern-checked). Sanitizing messy upstream payloads is the job of the service
layer, so one malformed field from an API never sinks the whole record.
"""

import re
import datetime as dt
from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from papaver.utils.doi import normalize_doi

__all__ = [
    "DOI",
    "AuditReport",
    "AuthorInfo",
    "CitationContextItem",
    "CitationIntent",
    "CitingPaper",
    "DataSource",
    "PaperMetadata",
    "UpdateNotice",
    "UpdateType",
    "VerdictLevel",
    "parse_issn",
    "parse_openalex_id",
    "parse_orcid",
]

#: A string validated and canonicalized by :func:`papaver.utils.doi.normalize_doi`.
DOI = Annotated[str, AfterValidator(normalize_doi)]

_ORCID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
_ISSN_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{3}[\dX]$")
_ORCID_URL_PREFIX: Final[re.Pattern[str]] = re.compile(
    r"^(?:https?://)?(?:www\.)?orcid\.org/", re.IGNORECASE
)
_OPENALEX_URL_PREFIX: Final[re.Pattern[str]] = re.compile(
    r"^(?:https?://)?(?:api\.)?openalex\.org/(?:\w+/)?", re.IGNORECASE
)


def parse_orcid(raw: str | None) -> str | None:
    """Return the bare ORCID iD (``0000-0002-1825-0097``) or ``None`` if ``raw`` is not one."""
    if raw is None:
        return None
    candidate = _ORCID_URL_PREFIX.sub("", raw.strip()).upper()
    return candidate if _ORCID_PATTERN.fullmatch(candidate) else None


def parse_issn(raw: str | None) -> str | None:
    """Return the ISSN as ``NNNN-NNNC`` (also accepts ``NNNNNNNC``) or ``None``."""
    if raw is None:
        return None
    candidate = raw.strip().upper()
    if len(candidate) == 8 and "-" not in candidate:
        candidate = f"{candidate[:4]}-{candidate[4:]}"
    return candidate if _ISSN_PATTERN.fullmatch(candidate) else None


def parse_openalex_id(raw: str | None, entity: str) -> str | None:
    """Return the short OpenAlex id (``W2117847125``) for the given entity letter, or ``None``.

    ``entity`` is the OpenAlex type letter: ``W`` works, ``A`` authors, ``S`` sources...
    """
    if raw is None:
        return None
    candidate = _OPENALEX_URL_PREFIX.sub("", raw.strip()).upper()
    return candidate if re.fullmatch(rf"{entity}\d+", candidate) else None


def _require(parsed: str | None, raw: str, what: str) -> str:
    if parsed is None:
        raise ValueError(f"not a valid {what}: {raw!r}")
    return parsed


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
    )


# --------------------------------------------------------------------------- enums


class DataSource(StrEnum):
    """External services Papaver pulls data from."""

    CROSSREF = "crossref"
    OPENALEX = "openalex"
    SEMANTIC_SCHOLAR = "semantic_scholar"


class UpdateType(StrEnum):
    """Kind of editorial update (Crossmark / Retraction Watch vocabulary).

    Crossref is inconsistent with separators (``expression_of_concern`` vs
    ``expression-of-concern``); lookups are normalized, and unknown values map to
    :attr:`OTHER` so a new upstream term never breaks parsing.
    """

    RETRACTION = "retraction"
    PARTIAL_RETRACTION = "partial-retraction"
    WITHDRAWAL = "withdrawal"
    REMOVAL = "removal"
    EXPRESSION_OF_CONCERN = "expression-of-concern"
    CORRECTION = "correction"
    CORRIGENDUM = "corrigendum"
    ERRATUM = "erratum"
    ADDENDUM = "addendum"
    CLARIFICATION = "clarification"
    NEW_VERSION = "new-version"
    NEW_EDITION = "new-edition"
    REINSTATEMENT = "reinstatement"
    OTHER = "other"

    @classmethod
    def _missing_(cls, value: object) -> "UpdateType | None":
        if not isinstance(value, str):
            return None
        key = re.sub(r"[\s_]+", "-", value.strip().lower())
        for member in cls:
            if member.value == key:
                return member
        return cls.OTHER

    @property
    def is_retraction(self) -> bool:
        """True for updates that take the whole work out of the scholarly record."""
        return self in {UpdateType.RETRACTION, UpdateType.WITHDRAWAL, UpdateType.REMOVAL}


class CitationIntent(StrEnum):
    """Citation intent labels returned by Semantic Scholar."""

    BACKGROUND = "background"
    METHODOLOGY = "methodology"
    RESULT = "result"
    OTHER = "other"

    @classmethod
    def _missing_(cls, value: object) -> "CitationIntent | None":
        if not isinstance(value, str):
            return None
        key = value.strip().lower()
        for member in cls:
            if member.value == key:
                return member
        return cls.OTHER


class VerdictLevel(StrEnum):
    """Traffic-light verdict, from worst to best."""

    BLACK_CODE = "BLACK_CODE"
    RED = "RED"
    YELLOW = "YELLOW"
    GREEN = "GREEN"


# -------------------------------------------------------------------------- models


class AuthorInfo(_StrictModel):
    """One author of a paper."""

    name: str = Field(min_length=1)
    orcid: str | None = Field(default=None, description="Bare ORCID iD, e.g. 0000-0002-1825-0097.")
    openalex_id: str | None = Field(default=None, description="OpenAlex author id, e.g. A5110236514.")
    affiliations: list[str] = Field(default_factory=list)

    @field_validator("orcid", mode="before")
    @classmethod
    def _validate_orcid(cls, value: object) -> object:
        if isinstance(value, str):
            return _require(parse_orcid(value), value, "ORCID iD")
        return value

    @field_validator("openalex_id", mode="before")
    @classmethod
    def _validate_openalex_id(cls, value: object) -> object:
        if isinstance(value, str):
            return _require(parse_openalex_id(value, "A"), value, "OpenAlex author id")
        return value


class UpdateNotice(_StrictModel):
    """An editorial notice (retraction, correction, expression of concern...) linking two works.

    ``notice_doi`` is the DOI of the notice itself and ``target_doi`` the DOI of the work
    it amends. Crossref exposes the link from both ends: ``updated-by`` on the amended
    work and ``update-to`` on the notice.
    """

    update_type: UpdateType
    label: str | None = Field(default=None, description="Human-readable label from Crossref.")
    notice_doi: DOI
    target_doi: DOI
    date: dt.date | None = None
    source: str | None = Field(
        default=None,
        description="Who asserted the update, e.g. 'publisher' or 'retraction-watch'.",
    )
    record_id: str | None = Field(default=None, description="Retraction Watch record id.")


class PaperMetadata(_StrictModel):
    """Bibliographic record aggregated from Crossref and OpenAlex."""

    doi: DOI
    title: str | None = None
    year: int | None = Field(default=None, ge=1000, le=2100)
    journal_name: str | None = None
    journal_issn: list[str] = Field(default_factory=list)
    publisher: str | None = None
    openalex_id: str | None = Field(default=None, description="OpenAlex work id, e.g. W2117847125.")

    is_oa: bool | None = Field(default=None, description="Open access status; None if unknown.")
    is_retracted: bool = False
    update_notices: list[UpdateNotice] = Field(
        default_factory=list,
        description="Notices that amend THIS paper (Crossref `updated-by`).",
    )
    amends: list[UpdateNotice] = Field(
        default_factory=list,
        description="Works that this record amends, when it is itself a notice (Crossref `update-to`).",
    )

    authors: list[AuthorInfo] = Field(default_factory=list)
    cited_by_count: int | None = Field(default=None, ge=0)
    sources: list[DataSource] = Field(
        default_factory=list,
        description="Data sources that contributed to this record.",
    )

    @field_validator("journal_issn", mode="before")
    @classmethod
    def _normalize_issns(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        result: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"ISSN must be a string, got {item!r}")
            issn = _require(parse_issn(item), item, "ISSN")
            if issn not in result:
                result.append(issn)
        return result

    @field_validator("openalex_id", mode="before")
    @classmethod
    def _validate_openalex_id(cls, value: object) -> object:
        if isinstance(value, str):
            return _require(parse_openalex_id(value, "W"), value, "OpenAlex work id")
        return value

    @property
    def retraction_notices(self) -> list[UpdateNotice]:
        """Notices that retract, withdraw or remove this paper."""
        return [n for n in self.update_notices if n.update_type.is_retraction]

    @property
    def is_update_notice(self) -> bool:
        """True if this record is itself a retraction/correction/... notice."""
        return bool(self.amends)


class CitingPaper(_StrictModel):
    """Minimal description of a paper that cites the audited one."""

    s2_paper_id: str | None = None
    title: str | None = None
    year: int | None = Field(default=None, ge=1000, le=2100)
    doi: DOI | None = None
    authors: list[str] = Field(default_factory=list)


class CitationContextItem(_StrictModel):
    """One citing paper with the sentences in which it cites the audited paper."""

    citing_paper: CitingPaper
    contexts: list[str] = Field(default_factory=list)
    intents: list[CitationIntent] = Field(default_factory=list)
    is_influential: bool = False


class AuditReport(_StrictModel):
    """Final container handed to the verdict engine and the terminal renderer."""

    target: str = Field(min_length=1, description="What the user asked to check (raw input).")
    paper: PaperMetadata
    verdict: VerdictLevel | None = Field(
        default=None,
        description="Filled in by the verdict engine; None until scored.",
    )
    reasons: list[str] = Field(default_factory=list)
    citations: list[CitationContextItem] = Field(default_factory=list)
    generated_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))

    def with_verdict(self, verdict: VerdictLevel, reasons: list[str]) -> Self:
        """Return a copy carrying the given verdict and its justification."""
        return self.model_copy(update={"verdict": verdict, "reasons": list(reasons)})
