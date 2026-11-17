"""LEGACY / TRANSITIONAL write helper — no longer a read path.

Since Phase 3, availability persistence flows through
``availability.services.refresh.AvailabilityRefreshService`` ->
``OfferRepository`` -> ``availability.Offer`` (see
``tracker.services.bulk_refresh``). This module remains only for:

- ``seed_prime_fixture`` (offline fixture seeding via ``_write_result``);
- legacy tests covering the old Region/LanguageInfo write semantics.

Reads were retired with the Phase C read-path migration: the legacy
``read_offers``/``get_cached_only``/``get_or_refresh`` methods (and their
staleness/rate-limit helpers) had no production callers left, so they are
gone. New code must read through ``tracker.services.availability_reads``
(or the query services it delegates to) and write through the availability
refresh service.

Legacy write semantics kept here (``_write_result``):
- Write Availability, LanguageInfo, LanguageEntry in one transaction per movie.
- Season availability comes from the upstream seasons[] payload only.
"""
from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction
from django.utils import timezone

from tracker.languages import normalize_language_code
from ..models import Availability, LanguageEntry, LanguageInfo, Movie, Region
from .streaming_api import (
    StreamingOffer,
    StreamingResult,
    resolve_season_number,
)

logger = logging.getLogger(__name__)


class AvailabilityCache:
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
