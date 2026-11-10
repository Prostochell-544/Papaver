"""DOI normalization.

Kept free of any `papaver` imports so it can be shared with (or imported from) Alcedo.

The canonical form produced here is the bare, lower-cased DOI name, e.g.
``10.1016/s0140-6736(97)11096-0``. DOI names are case-insensitive (ISO 26324),
so lower-casing makes them safe to use as cache keys and for deduplication.
"""

import re
from typing import Final
from urllib.parse import quote, unquote, urlsplit

__all__ = [
    "DOI_PATTERN",
    "InvalidDOIError",
    "doi_to_url",
    "is_valid_doi",
    "normalize_doi",
    "quote_doi",
    "try_normalize_doi",
]

#: A complete, normalized DOI: directory indicator ``10.``, a 4-9 digit registrant
#: code, a slash and a non-empty suffix without whitespace.
DOI_PATTERN: Final[re.Pattern[str]] = re.compile(r"^10\.\d{4,9}/\S+$")

_DOI_START: Final[re.Pattern[str]] = re.compile(r"10\.\d{4,9}/")
_URL_SCHEME: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
# Whitespace plus invisible characters that survive copy-paste from PDFs and web pages.
_INVISIBLE: Final[re.Pattern[str]] = re.compile(r"[\s­​‌‍⁠﻿]+")
_TRAILING_PUNCTUATION: Final[str] = ".,;:!?'\"“”‘’"
_CLOSING_TO_OPENING: Final[dict[str, str]] = {")": "(", "]": "[", "}": "{", ">": "<"}


class InvalidDOIError(ValueError):
    """Raised when a string does not contain a recognizable DOI."""

    def __init__(self, raw: str) -> None:
        super().__init__(f"Not a valid DOI: {raw!r}")
        self.raw = raw


def _strip_trailing(candidate: str) -> str:
    """Drop sentence punctuation and *unbalanced* closing brackets from the end.

    Balanced brackets are part of many real DOIs, e.g. ``10.1016/S0140-6736(97)11096-0``,
    while ``(doi:10.1000/xyz).`` must lose both the ``.`` and the ``)``.
    """
    while candidate:
        last = candidate[-1]
        if last in _TRAILING_PUNCTUATION:
            candidate = candidate[:-1]
        elif last in _CLOSING_TO_OPENING and candidate.count(last) > candidate.count(
            _CLOSING_TO_OPENING[last]
        ):
            candidate = candidate[:-1]
        else:
            break
    return candidate


def normalize_doi(raw: str) -> str:
    """Extract and canonicalize a DOI from user input.

    Accepts bare DOIs, ``doi:`` / ``DOI:`` / ``info:doi/`` prefixes, ``https://doi.org/...``
    and ``dx.doi.org`` links, publisher URLs containing ``/doi/10....``, percent-encoded
    URLs, and stray whitespace or invisible characters.

    Raises:
        InvalidDOIError: if no valid DOI can be extracted.
    """
    text = _INVISIBLE.sub("", raw)
    if _URL_SCHEME.match(text):
        # The DOI lives in the path; the query string and fragment are tracking noise.
        text = unquote(urlsplit(text).path)

    match = _DOI_START.search(text)
    if match is None:
        raise InvalidDOIError(raw)

    candidate = _strip_trailing(text[match.start() :]).lower()
    if not DOI_PATTERN.fullmatch(candidate):
        raise InvalidDOIError(raw)
    return candidate


def try_normalize_doi(raw: str | None) -> str | None:
    """Like :func:`normalize_doi`, but returns ``None`` instead of raising."""
    if raw is None:
        return None
    try:
        return normalize_doi(raw)
    except InvalidDOIError:
        return None


def is_valid_doi(raw: str) -> bool:
    """Return ``True`` if ``raw`` contains a DOI that :func:`normalize_doi` accepts."""
    return try_normalize_doi(raw) is not None


def quote_doi(doi: str) -> str:
    """Percent-encode a normalized DOI for use inside a URL path.

    Characters that are common in DOIs and legal in paths (``/ : ; ( ) [ ]``) are kept
    as-is; anything that would break the URL (``# ? % < >`` spaces, etc.) is escaped.
    """
    return quote(doi, safe="/:;()[]")


def doi_to_url(doi: str) -> str:
    """Return the canonical resolver URL for a DOI."""
    return f"https://doi.org/{quote_doi(normalize_doi(doi))}"
