import pytest

from papaver.utils.doi import (
    InvalidDOIError,
    doi_to_url,
    is_valid_doi,
    normalize_doi,
    quote_doi,
    try_normalize_doi,
)

WAKEFIELD = "10.1016/s0140-6736(97)11096-0"


@pytest.mark.parametrize(
    "raw",
    [
        "10.1016/S0140-6736(97)11096-0",
        "  10.1016/S0140-6736(97)11096-0\n",
        "doi:10.1016/S0140-6736(97)11096-0",
        "DOI: 10.1016/S0140-6736(97)11096-0",
        "info:doi/10.1016/S0140-6736(97)11096-0",
        "https://doi.org/10.1016/S0140-6736(97)11096-0",
        "http://dx.doi.org/10.1016/S0140-6736(97)11096-0",
        "https://doi.org/10.1016%2FS0140-6736%2897%2911096-0",
        "https://doi.org/10.1016/S0140-6736(97)11096-0?utm_source=x#section",
        "(doi:10.1016/S0140-6736(97)11096-0).",
        "10.1016/S0140-\n6736(97)11096-0",
        "10.1016/S0140-6736(97)11096-0​",
    ],
)
def test_normalize_doi_variants(raw: str) -> None:
    assert normalize_doi(raw) == WAKEFIELD


def test_normalize_publisher_url() -> None:
    url = "https://onlinelibrary.wiley.com/doi/full/10.1002/anie.201915678?af=R"
    assert normalize_doi(url) == "10.1002/anie.201915678"


@pytest.mark.parametrize("raw", ["", "   ", "not-a-doi", "10.1/abc", "10.1016/", "https://example.org/paper"])
def test_normalize_doi_rejects_garbage(raw: str) -> None:
    with pytest.raises(InvalidDOIError):
        normalize_doi(raw)
    assert try_normalize_doi(raw) is None
    assert not is_valid_doi(raw)


def test_invalid_doi_error_is_value_error() -> None:
    with pytest.raises(ValueError, match="Not a valid DOI"):
        normalize_doi("nope")


def test_quote_doi_keeps_parentheses_and_escapes_url_breakers() -> None:
    assert quote_doi(WAKEFIELD) == WAKEFIELD
    assert quote_doi("10.1000/a#b?c d") == "10.1000/a%23b%3Fc%20d"


def test_doi_to_url() -> None:
    assert doi_to_url("doi:10.1016/S0140-6736(97)11096-0") == f"https://doi.org/{WAKEFIELD}"
