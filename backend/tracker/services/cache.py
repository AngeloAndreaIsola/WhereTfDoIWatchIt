"""Cache layer: ingest Streaming Availability API results into local models.

Strategy:
- Check Availability.last_checked_at freshness; if stale or missing, refetch.
- Honor 100 req/day rate limit; defer refetch when budget exhausted.
- Write Availability, LanguageInfo, LanguageEntry in one transaction per movie.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from django.db import transaction
from django.utils import timezone

from tracker.languages import normalize_language_code
from ..models import Availability, LanguageEntry, LanguageInfo, Movie, Region
from .offers import Offer
from .rate_limit import RateLimitExceeded, RateLimiter
from .streaming_api import StreamingAvailabilityClient, StreamingResult

logger = logging.getLogger(__name__)

STALE_AFTER = dt.timedelta(hours=24)


@dataclass
class CacheStatus:
    refreshed: bool
    reason: str  # "fresh" | "stale" | "missing" | "rate_limited"
    offers_written: int = 0


class AvailabilityCache:
    def __init__(
        self,
        client: Optional[StreamingAvailabilityClient] = None,
        limiter: Optional[RateLimiter] = None,
        stale_after: dt.timedelta = STALE_AFTER,
    ):
        self.client = client or StreamingAvailabilityClient()
        self.limiter = limiter or RateLimiter("streaming_availability")
        self.stale_after = stale_after

    def is_stale(self, movie: Movie) -> bool:
        latest = (
            movie.availabilities.order_by("-last_checked_at")
            .values_list("last_checked_at", flat=True)
            .first()
        )
        if latest is None:
            return True
        return (timezone.now() - latest) > self.stale_after

    def get_cached_only(self, movie: Movie) -> CacheStatus:
        """Return cached Availability rows for movie without blocking on refresh.

        Used by watchlist_report to avoid timing out on multiple movies.
        Returns whatever is in cache (fresh, stale, or missing).
        """
        offers = self.read_offers(movie)
        if not self.is_stale(movie):
            return CacheStatus(refreshed=False, reason="fresh", offers_written=len(offers))

        if not offers:
            return CacheStatus(refreshed=False, reason="missing", offers_written=0)

        return CacheStatus(refreshed=False, reason="stale", offers_written=len(offers))

    def get_or_refresh(self, movie: Movie) -> CacheStatus:
        """Return cached/fresh Availability rows for movie, refreshing if stale."""
        if not self.is_stale(movie):
            offers = self.read_offers(movie)
            return CacheStatus(refreshed=False, reason="fresh", offers_written=len(offers))

        try:
            self.limiter.acquire()
        except RateLimitExceeded as exc:
            logger.warning("Rate limit hit, serving stale cache: %s", exc)
            offers = self.read_offers(movie)
            return CacheStatus(refreshed=False, reason="rate_limited", offers_written=len(offers))

        result = self.client.get_availability(movie.tmdb_id)
        written = self._write_result(movie, result)
        return CacheStatus(refreshed=True, reason="stale", offers_written=written)

    @transaction.atomic
    def _write_result(self, movie: Movie, result: StreamingResult) -> int:
        now = timezone.now()
        written = 0
        for offer in result.offers:
            # Store the stable service id (e.g. "prime"), not the display name
            # ("Prime Video"), so provider filtering is reliable across regions.
            region, _ = Region.objects.get_or_create(
                provider=offer.service_id,
                country=offer.country,
            )
            avail, _ = Availability.objects.update_or_create(
                movie=movie,
                region=region,
                defaults={
                    "last_checked_at": now,
                    "is_available": True,
                    "offer_type": offer.offer_type,
                    "quality": offer.quality or "",
                    "url": offer.link or "",
                },
            )
            info, _ = LanguageInfo.objects.get_or_create(movie=movie, region=region)
            info.entries.all().delete()
            for code in offer.audio_languages:
                norm = normalize_language_code(code)
                if norm:
                    LanguageEntry.objects.create(
                        language_info=info, kind="audio", language_code=norm
                    )
            for code in offer.subtitle_languages:
                norm = normalize_language_code(code)
                if norm:
                    LanguageEntry.objects.create(
                        language_info=info, kind="subtitle", language_code=norm
                    )
            written += 1
        return written

    def read_offers(self, movie: Movie) -> list[Offer]:
        rows: list[Offer] = []
        for avail in movie.availabilities.select_related("region").all():
            info = (
                LanguageInfo.objects.filter(movie=movie, region=avail.region)
                .prefetch_related("entries")
                .first()
            )
            audio = []
            subs = []
            if info:
                for entry in info.entries.all():
                    norm = normalize_language_code(entry.language_code) or entry.language_code.upper()
                    if entry.kind == "audio" and norm not in audio:
                        audio.append(norm)
                    elif entry.kind == "subtitle" and norm not in subs:
                        subs.append(norm)
            rows.append(
                Offer(
                    country=avail.region.country,
                    service=avail.region.provider,
                    offer_type=avail.offer_type,
                    quality=avail.quality,
                    url=avail.url,
                    audio_languages=audio,
                    subtitle_languages=subs,
                )
            )

        from tracker.models import ScrapedContentMapping

        scraped_mappings = ScrapedContentMapping.objects.filter(
            movie=movie,
            is_available=True,
        ).select_related("provider")

        for mapping in scraped_mappings:
            payload = mapping.raw_payload or {}
            audio = payload.get("audio_languages", []) if isinstance(payload, dict) else []
            subs = payload.get("subtitle_languages", []) if isinstance(payload, dict) else []
            rows.append(
                Offer(
                    country=payload.get("country", "") if isinstance(payload, dict) else (mapping.country_code or ""),
                    service=mapping.provider.slug,
                    offer_type="free",
                    quality=mapping.provider.config.get("quality", "") if mapping.provider.config else "",
                    url=mapping.scraped_url or "",
                    audio_languages=[str(a).upper() for a in audio],
                    subtitle_languages=[str(s).upper() for s in subs],
                    is_scraped_source=True,
                )
            )

        return rows
