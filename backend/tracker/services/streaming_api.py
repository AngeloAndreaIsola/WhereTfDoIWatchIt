"""Streaming Availability API client (RapidAPI movieofthenight, v4).

Fetches availability for a single TMDb ID across all countries in one request.
Filters strictly to subscription + free offer types (rent/buy/addon excluded by design).

The v4 ``/shows/movie/{tmdbId}`` endpoint returns a Show object whose
``streamingOptions`` map is keyed by lowercase country code; each option nests
the provider under ``service.id`` / ``service.name`` and lists ``audios`` /
``subtitles`` as objects. Older schemas exposed a flat ``streamingInfo`` map with
``serviceId`` / ``audioLanguages`` string lists — we still parse that shape as a
fallback so saved legacy fixtures keep working.

Series responses additionally carry ``seasons[]``: every season of the series
with its own ``streamingOptions`` map (same option shape, same country keys) in
one request — no per-season call. Season objects expose only a ``title``, never
a number, so callers map them to TMDb season numbers through
:func:`resolve_season_number`.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import requests
from django.conf import settings

from tracker.languages import normalize_language_code

logger = logging.getLogger(__name__)


class UpstreamTitleNotFound(Exception):
    """The upstream catalogue has no record of this title at all (HTTP 404).

    Distinct from a transport failure on purpose. A 404 means *this provider
    does not carry this title* — a statement about the world that will still be
    true on the next attempt, an hour later or a month later. Retrying it can
    never succeed, and every retry spends metered API budget to relearn the
    same fact, so callers treat it as a **permanent** outcome: fail the request
    immediately instead of burning ``MAX_ATTEMPTS`` calls on a guaranteed no.

    Anything else (429, 5xx, timeouts, connection resets) stays a plain
    exception, because those *are* transient and worth retrying.
    """

    def __init__(self, message: str, *, status_code: int = 404):
        super().__init__(message)
        self.status_code = status_code

RAPIDAPI_HOST = "streaming-availability.p.rapidapi.com"
DEFAULT_TIMEOUT = 15
ALLOWED_OFFER_TYPES = {"subscription", "free"}
QUALITY_ORDER = {"4k": 4, "uhd": 4, "qhd": 3, "hdr": 3, "hd": 2, "sd": 1}

# v4 season objects carry no season number — only a ``title`` ("Season 1",
# "Series 3", "Specials", ...). These are the titles that mean season 0.
SPECIALS_TITLES = {"specials", "special", "special episode", "bonus", "extras"}
_TRAILING_NUMBER_RE = re.compile(r"(\d+)\s*$")

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


def _extract_langs(items, code_getter) -> Optional[list[str]]:
    """Normalize + de-dupe a list of language entries, preserving order.

    Returns None when the payload carries no language metadata at all, so
    "unknown languages" stays distinguishable from a known-empty list ([]).
    """
    if items is None:
        return None
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
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
    # None = the source exposed no language metadata; [] = known to have none.
    audio_languages: Optional[list[str]] = None
    subtitle_languages: Optional[list[str]] = None


@dataclass
class StreamingSeason:
    """One entry of the v4 ``seasons[]`` array of a series response.

    The upstream season object exposes only ``title`` (no number), plus its own
    ``streamingOptions`` map shaped exactly like the show-level one. ``number``
    is filled in only when the payload ever provides one; otherwise callers
    resolve it through :func:`resolve_season_number`.
    """

    title: str = ""
    number: Optional[int] = None
    offers: list[StreamingOffer] = field(default_factory=list)


@dataclass
class StreamingResult:
    tmdb_id: int
    offers: list[StreamingOffer] = field(default_factory=list)
    seasons: list[StreamingSeason] = field(default_factory=list)


def resolve_season_number(
    season: StreamingSeason, seasons_data: Optional[list[dict]]
) -> Optional[int]:
    """Map an upstream season onto a TMDb ``season_number``.

    Resolution order:
    1. an explicit number on the payload (defensive; not present today);
    2. exact (case-insensitive) match of the season title against the TMDb
       season names in ``Movie.seasons_data`` ("Flux" → 13);
    3. specials-style titles ("Specials") → season 0;
    4. the trailing number of the title ("Season 2" → 2, "Series 10" → 10).

    Returns None when the title carries no usable signal, or when it resolves
    to a number the TMDb season list does not contain (so a title like "24"
    can never invent a phantom season). When ``seasons_data`` is empty — TMDb
    enrichment never ran — only steps 1/3/4 apply.
    """
    if season.number is not None:
        return season.number

    title = (season.title or "").strip()
    if not title:
        return None

    known = set()
    for entry in seasons_data or []:
        number = entry.get("season_number") if isinstance(entry, dict) else None
        if isinstance(number, int) and not isinstance(number, bool):
            known.add(number)

    key = title.casefold()
    number = None
    for entry in seasons_data or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if name and name.casefold() == key:
            value = entry.get("season_number")
            if isinstance(value, int) and not isinstance(value, bool):
                number = value
            break
    if number is None and key in SPECIALS_TITLES:
        number = 0
    if number is None:
        match = _TRAILING_NUMBER_RE.search(title)
        if match:
            number = int(match.group(1))

    if number is None:
        return None
    if known and number not in known:
        return None
    return number


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
        # v4 addresses TMDB ids as movie/<id> and tv/<id>; /shows/series/<id> 404s.
        show_type = "tv" if content_type in ("series", "tv") else "movie"
        url = f"https://{RAPIDAPI_HOST}/shows/{show_type}/{tmdb_id}"
        headers = {
            "x-rapidapi-key": self.api_key,
            "x-rapidapi-host": RAPIDAPI_HOST,
        }
        resp = requests.get(url, headers=headers, timeout=self.timeout)
        # A 404 is the one upstream answer that is a permanent fact about the
        # title rather than a transient fault, so it is translated into the
        # dedicated signal here — at the boundary where the status code is
        # still visible — instead of surfacing as a generic HTTPError that
        # every caller would have to re-classify.
        if resp.status_code == 404:
            raise UpstreamTitleNotFound(
                f"Streaming Availability has no record of {show_type} {tmdb_id} "
                f"(404 from {url})"
            )
        resp.raise_for_status()
        data = resp.json()
        offers = self._parse_offers(data)
        seasons = self._parse_seasons(data)
        # v4 tmdbId is a string like "movie/588228"; trust the requested id.
        return StreamingResult(tmdb_id=tmdb_id, offers=offers, seasons=seasons)

    @staticmethod
    def _parse_offers(payload: dict) -> list[StreamingOffer]:
        offers: list[StreamingOffer] = []
        # v4 nests availability under "streamingOptions"; legacy used "streamingInfo".
        catalog = payload.get("streamingOptions")
        if catalog is None:
            catalog = payload.get("streamingInfo") or {}
        for country, country_options in (catalog if isinstance(catalog, dict) else {}).items():
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

    @staticmethod
    def _parse_seasons(payload: dict) -> list[StreamingSeason]:
        """Parse the v4 ``seasons[]`` array of a series payload.

        Each season carries its own country-keyed ``streamingOptions`` map with
        the very same option shape as the show-level one, so the season options
        go through :meth:`_parse_offers` unchanged (subscription/free filter
        included). Movies have no ``seasons`` key; malformed entries are skipped
        rather than raising, so a bad season can never break an availability
        refresh.
        """
        raw_seasons = payload.get("seasons")
        if not isinstance(raw_seasons, list):
            return []

        seasons: list[StreamingSeason] = []
        for entry in raw_seasons:
            if not isinstance(entry, dict):
                continue

            number = entry.get("number")
            if number is None:
                number = entry.get("seasonNumber")
            if isinstance(number, bool) or not isinstance(number, int):
                number = None

            options = entry.get("streamingOptions")
            offers = (
                StreamingAvailabilityClient._parse_offers({"streamingOptions": options})
                if isinstance(options, dict)
                else []
            )
            seasons.append(
                StreamingSeason(
                    title=str(entry.get("title") or "").strip(),
                    number=number,
                    offers=offers,
                )
            )
        return seasons
