"""Normalization helpers shared by source adapters.

They deliberately return values only; provider/country lookup and all database
writes belong to the refresh service and repositories respectively.
"""
from __future__ import annotations

import unicodedata
from typing import Iterable, Optional

from tracker.languages import normalize_language_code

from ..domain.enums import ContentType, OfferType


CONTENT_TYPES = {
    "movie": ContentType.MOVIE, "film": ContentType.MOVIE,
    "series": ContentType.SERIES, "show": ContentType.SERIES, "tv": ContentType.SERIES,
    "season": ContentType.SEASON, "episode": ContentType.EPISODE,
}
OFFER_TYPES = {
    "subscription": OfferType.SUBSCRIPTION, "svod": OfferType.SUBSCRIPTION,
    "free": OfferType.FREE, "avod": OfferType.FREE_WITH_ADS,
    "free_with_ads": OfferType.FREE_WITH_ADS, "free-with-ads": OfferType.FREE_WITH_ADS,
    "rent": OfferType.RENT, "rental": OfferType.RENT,
    "buy": OfferType.BUY, "purchase": OfferType.BUY, "tvod": OfferType.BUY,
    "addon": OfferType.ADDON, "add_on": OfferType.ADDON,
}


def normalize_content_type(value: str) -> str:
    try:
        return CONTENT_TYPES[str(value).strip().lower()]
    except KeyError as exc:
        raise ValueError(f"Unsupported source content type: {value!r}") from exc


def normalize_offer_type(value: str) -> str:
    return OFFER_TYPES.get(str(value or "").strip().lower(), OfferType.UNKNOWN)


def normalize_country(value: str) -> str:
    code = str(value or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError(f"Unsupported country code: {value!r}")
    return code


def normalize_title(value) -> str:
    """Casefolded, accent-stripped, punctuation-insensitive title key.

    The shared key behind title-verification matching: sources compare a
    candidate's title with the requested one through this function so two
    collectors never disagree on what "the same title" means (e.g. YouTube's
    private copy — kept untouched mid-Phase-E — and Mediaset's lookup).
    """
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.casefold()
    keep = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in lowered)
    return " ".join(keep.split())


def normalize_languages(values: Optional[Iterable[object]]) -> Optional[list[str]]:
    """Keep unknown (None) distinct from a known empty source list."""
    if values is None:
        return None
    normalized: list[str] = []
    for value in values:
        code = normalize_language_code(str(value)) or str(value).strip().upper()
        if code and code not in normalized:
            normalized.append(code)
    return normalized
