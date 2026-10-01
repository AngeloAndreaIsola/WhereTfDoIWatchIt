"""LEGACY / TRANSITIONAL cache layer — no longer the canonical write path.

Since Phase 3, availability persistence flows through
``availability.services.refresh.AvailabilityRefreshService`` ->
``OfferRepository`` -> ``availability.Offer`` (see
``tracker.services.bulk_refresh``). This module remains only for:

- ``seed_prime_fixture`` (offline fixture seeding via ``_write_result``);
- legacy tests covering the old Region/LanguageInfo write semantics.

``get_or_refresh()`` has no production callers left; do not add new ones.
New code must read through ``tracker.services.availability_reads`` and write
through the availability refresh service.

Strategy (legacy):
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
from .streaming_api import (
    StreamingAvailabilityClient,
    StreamingOffer,
    StreamingResult,
    resolve_season_number,
)

logger = logging.getLogger(__name__)

STALE_AFTER = dt.timedelta(hours=24)


def _season_matches(raw_seasons, season: int) -> bool:
    """True when *season* (an int) is recorded in a stored ``seasons_available``.

    Stored values are ints, but JSON round-trips are tolerated defensively so a
    legacy/string value can never break an offers read.
    """
    for value in raw_seasons or []:
        try:
            if int(value) == int(season):
                return True
        except (TypeError, ValueError):
            continue
    return False


def langs_from_info(info: Optional[LanguageInfo]) -> tuple[list[str], list[str]]:
    """Extract (audio, subtitle) canonical codes from a LanguageInfo row."""
    audio: list[str] = []
    subs: list[str] = []
    if info:
        for entry in info.entries.all():
            norm = normalize_language_code(entry.language_code) or entry.language_code.upper()
            if entry.kind == "audio" and norm not in audio:
                audio.append(norm)
            elif entry.kind == "subtitle" and norm not in subs:
                subs.append(norm)
    return audio, subs


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

        result = self.client.get_availability(movie.tmdb_id, content_type=movie.content_type)
        written = self._write_result(movie, result)
        return CacheStatus(refreshed=True, reason="stale", offers_written=written)

    @transaction.atomic
    def _write_result(self, movie: Movie, result: StreamingResult) -> int:
        now = timezone.now()
        written = 0

        # Season availability comes from the upstream seasons[] payload only —
        # never from TMDb metadata — so a season TMDb knows about but the
        # streaming catalog has no option for is never marked available.
        season_regions = self._seasons_by_region(movie, result)
        show_keys = {(offer.service_id, offer.country) for offer in result.offers}

        for offer in result.offers:
            # Store the stable service id (e.g. "prime"), not the display name
            # ("Prime Video"), so provider filtering is reliable across regions.
            region, _ = Region.objects.get_or_create(
                provider=offer.service_id,
                country=offer.country,
            )
            region_key = (offer.service_id, offer.country)
            seasons_available = sorted(
                season_regions.get(region_key, {}).get("seasons", set())
            )
            Availability.objects.update_or_create(
                movie=movie,
                region=region,
                defaults={
                    "last_checked_at": now,
                    "is_available": True,
                    "offer_type": offer.offer_type,
                    "quality": offer.quality or "",
                    "url": offer.link or "",
                    "seasons_available": seasons_available,
                },
            )
            self._write_languages(movie, region, offer)
            written += 1

        # Regions the show-level catalog has no (allowed) entry for, but where
        # individual seasons are streamable. Row shape matches a normal offer so
        # they flow through the unchanged read/rank pipeline.
        for key, info in season_regions.items():
            if key in show_keys:
                continue
            offer = info["offer"]
            region, _ = Region.objects.get_or_create(provider=key[0], country=key[1])
            Availability.objects.update_or_create(
                movie=movie,
                region=region,
                defaults={
                    "last_checked_at": now,
                    "is_available": True,
                    "offer_type": offer.offer_type,
                    "quality": offer.quality or "",
                    "url": offer.link or "",
                    "seasons_available": sorted(info["seasons"]),
                },
            )
            self._write_languages(movie, region, offer)
            written += 1

        # Per-season language rows: each season's own offer carries its own
        # audio/subs, so they are stored as (movie, region, season) rows rather
        # than folded into the show-level row above. Season rows from a
        # previous refresh are dropped first so a season that disappeared
        # upstream can never serve stale languages.
        LanguageInfo.objects.filter(movie=movie, season__isnull=False).delete()
        for season in result.seasons or []:
            number = resolve_season_number(season, movie.seasons_data)
            if number is None:
                continue
            for offer in season.offers or []:
                region, _ = Region.objects.get_or_create(
                    provider=offer.service_id,
                    country=offer.country,
                )
                self._write_languages(movie, region, offer, season=number)

        return written

    @staticmethod
    def _seasons_by_region(movie: Movie, result: StreamingResult) -> dict:
        """Map (service_id, country) → {"seasons": set[int], "offer": StreamingOffer}.

        Season numbers are resolved against ``Movie.seasons_data`` (TMDb
        metadata). Unresolvable seasons are dropped rather than guessed, and
        seasons whose options are subscription-free filtered out contribute
        nothing, so they never reach ``Availability.seasons_available``.
        """
        mapping: dict[tuple[str, str], dict] = {}
        for season in result.seasons or []:
            number = resolve_season_number(season, movie.seasons_data)
            if number is None:
                logger.debug(
                    "Skipping unresolvable season title %r for tmdb %s",
                    season.title,
                    movie.tmdb_id,
                )
                continue
            for offer in season.offers or []:
                key = (offer.service_id, offer.country)
                entry = mapping.setdefault(key, {"seasons": set(), "offer": offer})
                entry["seasons"].add(number)
        return mapping

    @staticmethod
    def _write_languages(
        movie: Movie,
        region: Region,
        offer: StreamingOffer,
        season: Optional[int] = None,
    ) -> None:
        """Replace the stored audio/subtitle language rows for one movie+region.

        ``season=None`` writes the show-level row; a season number writes that
        season's row, keyed alongside it by the (movie, region, season)
        constraint on LanguageInfo.
        """
        info, _ = LanguageInfo.objects.get_or_create(
            movie=movie, region=region, season=season
        )
        info.entries.all().delete()
        for code in offer.audio_languages or []:
            norm = normalize_language_code(code)
            if norm:
                LanguageEntry.objects.create(
                    language_info=info, kind="audio", language_code=norm
                )
        for code in offer.subtitle_languages or []:
            norm = normalize_language_code(code)
            if norm:
                LanguageEntry.objects.create(
                    language_info=info, kind="subtitle", language_code=norm
                )

    def read_offers(self, movie: Movie, season: Optional[int] = None) -> list[Offer]:
        """Cached offers for *movie*.

        ``season`` (series only) narrows the result to Availability rows that
        recorded that season in ``seasons_available``. Scraped offers carry no
        season information, so they are only returned for the unfiltered view.
        """
        rows: list[Offer] = []
        # Show-level rows are always loaded; season rows only when a season
        # was requested. A season row (even an empty one) is authoritative for
        # that season; the show-level row is the fallback for seasons the
        # refresh never wrote a row for (legacy caches, seasons without data).
        show_infos = {
            i.region_id: i
            for i in LanguageInfo.objects.filter(
                movie=movie, season__isnull=True
            ).prefetch_related("entries")
        }
        season_infos: dict[int, LanguageInfo] = {}
        if season is not None:
            season_infos = {
                i.region_id: i
                for i in LanguageInfo.objects.filter(
                    movie=movie, season=season
                ).prefetch_related("entries")
            }
        for avail in movie.availabilities.select_related("region").all():
            if season is not None and not _season_matches(avail.seasons_available, season):
                continue
            info = season_infos.get(avail.region_id) if season is not None else None
            if info is None:
                info = show_infos.get(avail.region_id)
            audio, subs = langs_from_info(info)
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

        if season is not None:
            # Scraper rows are show-level only: they carry no season signal, so
            # they stay out of a season-filtered view instead of pretending to
            # be available for that season.
            return rows

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
