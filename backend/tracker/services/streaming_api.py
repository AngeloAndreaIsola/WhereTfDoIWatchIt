"""Streaming Availability API client (RapidAPI movieofthenight).

Fetches availability for a single TMDb ID across all countries in one request.
Filters strictly to subscription + free offer types (rent/buy excluded by design).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

RAPIDAPI_HOST = "streaming-availability.p.rapidapi.com"
DEFAULT_TIMEOUT = 15
ALLOWED_OFFER_TYPES = {"subscription", "free"}
QUALITY_ORDER = {"4k": 4, "uhd": 4, "hdr": 3, "hd": 2, "sd": 1}


@dataclass
class StreamingOffer:
    country: str  # ISO-3166-1 alpha-2
    service_id: str
    service_name: str
    offer_type: str  # "subscription" | "free"
    quality: Optional[str] = None
    audio_languages: list[str] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)


@dataclass
class StreamingResult:
    tmdb_id: int
    offers: list[StreamingOffer] = field(default_factory=list)


class StreamingAvailabilityClient:
    def __init__(self, api_key: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT):
        self.api_key = api_key or getattr(settings, "RAPIDAPI_KEY", "")
        self.timeout = timeout

    def get_availability(self, tmdb_id: int) -> StreamingResult:
        url = f"https://{RAPIDAPI_HOST}/shows/movie/{tmdb_id}"
        headers = {
            "x-rapidapi-key": self.api_key,
            "x-rapidapi-host": RAPIDAPI_HOST,
        }
        params = {"country": "all"}
        resp = requests.get(url, headers=headers, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        offers = self._parse_offers(data)
        return StreamingResult(tmdb_id=int(data.get("tmdbId") or tmdb_id), offers=offers)

    @staticmethod
    def _parse_offers(payload: dict) -> list[StreamingOffer]:
        offers: list[StreamingOffer] = []
        for country, country_data in (payload.get("streamingInfo") or {}).items():
            for entry in country_data:
                offer_type = (entry.get("type") or "").lower()
                if offer_type not in ALLOWED_OFFER_TYPES:
                    continue
                quality = (entry.get("quality") or "").lower() or None
                offers.append(
                    StreamingOffer(
                        country=country.upper(),
                        service_id=str(entry.get("serviceId") or entry.get("id") or ""),
                        service_name=entry.get("serviceName") or entry.get("serviceId") or "",
                        offer_type=offer_type,
                        quality=quality,
                        audio_languages=[
                            (lang or "")[:2].lower()
                            for lang in (entry.get("audioLanguages") or [])
                            if lang
                        ],
                        subtitle_languages=[
                            (lang or "")[:2].lower()
                            for lang in (entry.get("subtitleLanguages") or [])
                            if lang
                        ],
                    )
                )
        return offers