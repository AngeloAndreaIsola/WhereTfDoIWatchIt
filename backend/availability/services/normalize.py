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


#: JustWatch reports monetization as an upper-snake enum that is *not* the
#: same vocabulary as ours: ``FLATRATE`` is a subscription, ``ADS`` is
#: free-with-ads. They are mapped explicitly rather than guessed, because
#: guessing "any FLATRATE-ish value is a subscription" is exactly how an
#: aggregator's rental store would end up ranked as a subscription.
JUSTWATCH_MONETIZATION_TYPES = {
    "flatrate": OfferType.SUBSCRIPTION,
    "subscription": OfferType.SUBSCRIPTION,
    "svod": OfferType.SUBSCRIPTION,
    "ads": OfferType.FREE_WITH_ADS,
    "avod": OfferType.FREE_WITH_ADS,
    "free": OfferType.FREE,
    "freemium": OfferType.FREE,
    "rent": OfferType.RENT,
    "rental": OfferType.RENT,
    "tvod": OfferType.BUY,
    "buy": OfferType.BUY,
    "purchase": OfferType.BUY,
    "addon": OfferType.ADDON,
    "add_on": OfferType.ADDON,
}

#: JustWatch quality tokens -> the shared ``tracker.services.score.QUALITY_RANK``
#: vocabulary the ranking/filtering code already reads (4k/hdr/hd/sd). The
#: source sends ``_4K`` (underscore-prefixed, measured), which is *not* a key
#: ``QUALITY_RANK`` knows, so it is translated here instead of being stored
#: raw.
JUSTWATCH_QUALITIES = {
    "_4k": "4k",
    "4k": "4k",
    "uhd": "4k",
    "hdr": "hdr",
    "qhd": "qhd",
    "hd": "hd",
    "sd": "sd",
}


def normalize_content_type(value: str) -> str:
    try:
        return CONTENT_TYPES[str(value).strip().lower()]
    except KeyError as exc:
        raise ValueError(f"Unsupported source content type: {value!r}") from exc


def normalize_offer_type(value: str) -> str:
    return OFFER_TYPES.get(str(value or "").strip().lower(), OfferType.UNKNOWN)


def normalize_justwatch_offer_type(value: str) -> str:
    """JustWatch ``monetizationType`` -> canonical ``OfferType``.

    Unrecognised values map to ``UNKNOWN`` rather than raising: an aggregator
    that invents a new monetization enum must not take down a whole catalogue
    page. ``UNKNOWN`` still persists as a real offer row, so the evidence
    survives and is visible instead of being dropped.
    """
    return JUSTWATCH_MONETIZATION_TYPES.get(
        str(value or "").strip().lower(), OfferType.UNKNOWN
    )


def normalize_justwatch_quality(value: str) -> str:
    """JustWatch ``presentationType`` -> the shared quality vocabulary.

    Returns ``""`` for anything unrecognised (rather than the raw token), so
    an unknown rendition reads as "quality not stated" — which the ranking
    code already handles — instead of polluting the filterable vocabulary
    with a value nothing else in the app can interpret.
    """
    return JUSTWATCH_QUALITIES.get(str(value or "").strip().lower(), "")


def normalize_country(value: str) -> str:
    code = str(value or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError(f"Unsupported country code: {value!r}")
    return code


def truncate_field(value: str, max_length: int) -> str:
    """Clamp a provider-supplied identifier to a column's ``max_length``.

    Provider-native ids are the provider's own strings — a Pluto TV sports
    replay slug, a BBC programme path — and nothing bounds them to our schema.
    ``ProviderContent.provider_content_id``, ``Offer`` and ``ContentMapping``
    all declare ``max_length=100``, so one long slug (a real Pluto item measured
    125 chars) aborted an entire catalogue refresh with a Postgres
    ``value too long for type character varying(100)``: a single row took down
    ~1,987 items.

    Truncation is applied **here, at the shared normalize boundary**, rather
    than in the individual source, because every collector funnels through this
    module and the constraint is a property of our columns — not of Pluto. A
    source that hits it tomorrow is then covered by construction instead of
    needing its own patch.

    Plain truncation (not a hash) keeps the value human-readable and stable, and
    the result stays unique in practice: these ids are unique per provider to
    begin with, so collisions would require two ids sharing a 100-char prefix.
    """

    text = str(value or "")
    return text if len(text) <= max_length else text[:max_length]


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
