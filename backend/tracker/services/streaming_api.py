"""Streaming Availability API client (RapidAPI movieofthenight, v4).

Fetches availability for a single TMDb ID across all countries in one request.
Filters strictly to subscription + free offer types (rent/buy/addon excluded by design).

The v4 ``/shows/movie/{tmdbId}`` endpoint returns a Show object whose
``streamingOptions`` map is keyed by lowercase country code; each option nests
the provider under ``service.id`` / ``service.name`` and lists ``audios`` /
``subtitles`` as objects. Older schemas exposed a flat ``streamingInfo`` map with
``serviceId`` / ``audioLanguages`` string lists — we still parse that shape as a
fallback so saved legacy fixtures keep working.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import requests
from django.conf import settings

from tracker.languages import normalize_language_code

logger = logging.getLogger(__name__)

RAPIDAPI_HOST = "streaming-availability.p.rapidapi.com"
DEFAULT_TIMEOUT = 15
ALLOWED_OFFER_TYPES = {"subscription", "free"}
QUALITY_ORDER = {"4k": 4, "uhd": 4, "qhd": 3, "hdr": 3, "hd": 2, "sd": 1}

# Stable service id -> human display name for the UI. Falls back to a title-cased
# id for services not listed here.
SERVICE_DISPLAY_NAMES = {
    "prime": "Prime Video",
    "netflix": "Netflix",
    "apple": "Apple TV+",
    "appletv": "Apple TV+",
    "disney": "Disney+",
    "hbo": "Max",
    "max": "Max",
    "hulu": "Hulu",
    "paramount": "Paramount+",
    "peacock": "Peacock",
    "starz": "Starz",
    "mubi": "MUBI",
    "now": "NOW",
    "curiosity": "Curiosity Stream",
    "zee5": "Zee5",
}


def service_display_name(service_id: str) -> str:
    """Map a stable service id ("prime") to a display name ("Prime Video")."""
    sid = (service_id or "").lower()
    return SERVICE_DISPLAY_NAMES.get(sid, (service_id or "").title())


def _norm_lang(code: Optional[str]) -> Optional[str]:
    """Normalize an external language code/string to an uppercase canonical application code.

    Adapts external API representations (e.g. 'eng', 'en', 'deu', 'nor', 'Japanese')
    at the boundary to canonical codes ('EN', 'DE', 'NB', 'JA').
    Ignores unsupported external languages (returns None).
    """
    if not code:
        return None
    return normalize_language_code(str(code))


def _extract_langs(items, code_getter) -> list[str]:
    """Normalize + de-dupe a list of language entries, preserving order."""
    out: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        norm = _norm_lang(code_getter(item))
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def _audio_code(item) -> str:
    # v4: {"language": "eng", "region": "USA"}; legacy: "en"
    if isinstance(item, dict):
        return item.get("language") or (item.get("locale") or {}).get("language") or ""
    return item or ""


def _subtitle_code(item) -> str:
    # v4: {"closedCaptions": bool, "locale": {"language": "eng", ...}}; legacy: "en"
    if isinstance(item, dict):
        locale = item.get("locale")
        if isinstance(locale, dict):
            return locale.get("language") or ""
        return item.get("language") or ""
    return item or ""


@dataclass
class StreamingOffer:
    country: str  # ISO-3166-1 alpha-2
    service_id: str
    service_name: str
    offer_type: str  # "subscription" | "free"
    quality: Optional[str] = None
    link: Optional[str] = None
    audio_languages: list[str] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)


@dataclass
class StreamingResult:
    tmdb_id: int
    offers: list[StreamingOffer] = field(default_factory=list)


class StreamingAvailabilityClient:
    def __init__(self, api_key: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT):
        # Read the key from the environment directly. settings.RAPIDAPI_KEY is
        # itself defined as os.environ.get("RAPIDAPI_KEY", ""); reading env here
        # is equivalent and avoids recursing into the lazy settings proxy in
        # tests that substitute django.conf.settings.
        self.api_key = api_key or os.environ.get("RAPIDAPI_KEY", "")
        self.timeout = timeout

    def get_availability(self, tmdb_id: int, content_type: str = "movie") -> StreamingResult:
        # v4 /shows/{show_type}/{tmdbId} returns every country's options in one call;
        # no country param is needed (the legacy "country=all" is not valid v4).
        show_type = "series" if content_type in ("series", "tv") else "movie"
        url = f"https://{RAPIDAPI_HOST}/shows/{show_type}/{tmdb_id}"
        headers = {
            "x-rapidapi-key": self.api_key,
            "x-rapidapi-host": RAPIDAPI_HOST,
        }
        resp = requests.get(url, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        offers = self._parse_offers(data)
        # v4 tmdbId is a string like "movie/588228"; trust the requested id.
        return StreamingResult(tmdb_id=tmdb_id, offers=offers)

    @staticmethod
    def _parse_offers(payload: dict) -> list[StreamingOffer]:
        offers: list[StreamingOffer] = []
        # v4 nests availability under "streamingOptions"; legacy used "streamingInfo".
        catalog = payload.get("streamingOptions")
        if catalog is None:
            catalog = payload.get("streamingInfo") or {}
        for country, country_options in (catalog or {}).items():
            if not isinstance(country_options, list):
                continue
            for entry in country_options:
                if not isinstance(entry, dict):
                    continue
                offer_type = (entry.get("type") or "").lower()
                if offer_type not in ALLOWED_OFFER_TYPES:
                    continue

                service = entry.get("service")
                if isinstance(service, dict):
                    service_id = str(service.get("id") or "")
                    service_name = service.get("name") or service_id
                else:  # legacy flat shape
                    service_id = str(entry.get("serviceId") or service or "")
                    service_name = entry.get("serviceName") or service_id

                quality = (entry.get("quality") or "").lower() or None
                link = entry.get("link") or entry.get("videoLink") or entry.get("url") or None
                audios = entry.get("audios")
                if audios is None:
                    audios = entry.get("audioLanguages")
                subtitles = entry.get("subtitles")
                if subtitles is None:
                    subtitles = entry.get("subtitleLanguages")

                offers.append(
                    StreamingOffer(
                        country=country.upper(),
                        service_id=service_id,
                        service_name=service_name,
                        offer_type=offer_type,
                        quality=quality,
                        link=link,
                        audio_languages=_extract_langs(audios, _audio_code),
                        subtitle_languages=_extract_langs(subtitles, _subtitle_code),
                    )
                )
        return offers
