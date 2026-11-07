"""Shared fixtures for reconciliation tests (not discovered as tests itself).

Reconciliation is exercised through raw rows, so the fixture only knows how to
write observations: two providers, two sources, two countries, and explicit
control over the values reconciliation compares (windows, languages, freshness,
sync history).
"""
from __future__ import annotations

from django.utils import timezone

from availability.domain.enums import (
    ContentType,
    OfferType,
    SourceType,
    SyncMode,
    SyncStatus,
)
from availability.models import Country, Offer, Source, SyncRun
from availability.services.reconciliation import AvailabilityReconciliationService
from tracker.models import Provider


class ReconciliationFixtureMixin:
    """Raw-row factories and a reconciliation service over them."""

    def setUp(self):
        super().setUp()
        self.netflix, _ = Provider.objects.get_or_create(
            slug="recon-netflix", defaults={"name": "Netflix"},
        )
        self.prime, _ = Provider.objects.get_or_create(
            slug="recon-prime", defaults={"name": "Prime Video"},
        )
        self.source_a = Source.objects.create(
            slug="recon-source-a", name="Source A", type=SourceType.API,
        )
        self.source_b = Source.objects.create(
            slug="recon-source-b", name="Source B", type=SourceType.AGGREGATOR,
        )
        self.us, _ = Country.objects.get_or_create(code="US", defaults={"name": "United States"})
        self.nl, _ = Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands"})
        self.now = timezone.now()
        self.service = AvailabilityReconciliationService()

    def offer(self, *, tmdb_id=550, provider=None, source=None, country=None,
              content_type=ContentType.MOVIE, offer_type=OfferType.SUBSCRIPTION,
              provider_content_id="pcid", provider_url="", quality="",
              audio_languages=None, subtitle_languages=None,
              available_from=None, available_until=None,
              currently_available=True, season_number=None, episode_number=None,
              last_seen=None, sync_run=None) -> Offer:
        """Create one raw observation. ``provider_content_id`` separates rows
        that describe the same offer (identity is provider+country+content+
        offer_type+provider content id, so duplicates need distinct ids)."""
        row = Offer.objects.create(
            provider=provider or self.netflix,
            country=country or self.us,
            source=source or self.source_a,
            content_type=content_type,
            tmdb_id=tmdb_id,
            season_number=season_number,
            episode_number=episode_number,
            offer_type=offer_type,
            provider_content_id=provider_content_id,
            provider_url=provider_url,
            quality=quality,
            audio_languages=audio_languages,
            subtitle_languages=subtitle_languages,
            available_from=available_from,
            available_until=available_until,
            currently_available=currently_available,
            sync_run=sync_run,
        )
        if last_seen is not None:
            Offer.objects.filter(pk=row.pk).update(last_seen=last_seen)
        return row

    def sync_run(self, *, status=SyncStatus.SUCCEEDED, provider=None, source=None,
                 country=None, created_at=None) -> SyncRun:
        run = SyncRun.objects.create(
            provider=provider or self.netflix,
            source=source or self.source_a,
            country=country,
            mode=SyncMode.FULL,
            status=status,
            started_at=self.now,
        )
        if created_at is not None:
            SyncRun.objects.filter(pk=run.pk).update(created_at=created_at)
        return run

    def reconcile_movie(self, tmdb_id=550, **kwargs):
        kwargs.setdefault("now", self.now)
        return self.service.reconcile(
            content_types=(ContentType.MOVIE,), tmdb_id=tmdb_id, **kwargs
        )
