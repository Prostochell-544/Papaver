"""Defensive accessors for untrusted JSON payloads.

External APIs omit fields, send ``null`` or change types without notice. These
helpers turn "whatever came back" into a typed value or ``None``, so parsers never
crash on a single odd field.
"""

import html
import re
from collections.abc import Iterable
from typing import Any, Final, TypeAlias, TypeVar

__all__ = [
    "JSONObject",
    "as_bool",
    "as_count",
    "as_dict",
    "as_list",
    "as_year",
    "clean_text",
    "dedupe",
    "dig",
    "first_not_none",
    "first_text",
    "text",
]

JSONObject: TypeAlias = dict[str, Any]
_T = TypeVar("_T")

# Only things that look like real markup (`<i>`, `</scp>`, `<mml:mi>`, `<br/>`) are
# stripped; "p < 0.05 and q > 3" has no letter right after `<` and is left alone.
_TAG: Final[re.Pattern[str]] = re.compile(r"</?[A-Za-z][\w:.-]*(?:\s[^<>]*)?/?>")
_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")


def dig(obj: object, *keys: str) -> object:
    """Follow nested dict keys; ``None`` as soon as something is not a dict."""
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def as_dict(value: object) -> JSONObject:
    return value if isinstance(value, dict) else {}


def as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def as_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def as_year(value: object) -> int | None:
    """A plausible publication year, else ``None``."""
    if isinstance(value, int) and not isinstance(value, bool) and 1000 <= value <= 2100:
        return value
    return None


def as_count(value: object) -> int | None:
    """A non-negative integer count, else ``None``."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def text(value: object) -> str | None:
    """A stripped non-empty string for str/int values (ids are sometimes numeric)."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def clean_text(value: object, *, strip_markup: bool = True) -> str | None:
    """Collapse whitespace; optionally strip JATS/HTML tags and unescape entities."""
    if not isinstance(value, str):
        return None
    if strip_markup:
        value = html.unescape(_TAG.sub("", value))
    cleaned = _WHITESPACE.sub(" ", value).strip()
    return cleaned or None


def first_text(value: object) -> str | None:
    """Crossref wraps titles in lists; return the first non-empty one."""
    if isinstance(value, list):
        return next((cleaned for item in value if (cleaned := clean_text(item))), None)
    return clean_text(value)


def first_not_none(*values: _T | None) -> _T | None:
    return next((value for value in values if value is not None), None)


def dedupe(items: Iterable[_T]) -> list[_T]:
    """Drop repeated items, keeping the first occurrence and the original order."""
    seen: list[_T] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen
