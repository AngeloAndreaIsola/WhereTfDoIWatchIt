"""Core reconciliation behaviour: grouping, aggregation, provenance, hygiene.

Covers the rules §49-§61 for the non-conflicting cases: what one observation
becomes, how several become one, what stays behind as provenance, and what
reconciliation is forbidden to do (write, fetch, rank).
"""
from datetime import timedelta

from django.test import TestCase

from availability.domain.enums import (
    ConflictKind,
    ContentType,
    FreshnessState,
    MatchMethod,
    OfferType,
    SyncStatus,
)
from availability.domain.reconciliation import ReconciliationResult
from availability.models import ContentMapping, Offer, ProviderContent, Source
from availability.services.query import AvailabilityQueryService
from tracker.models import Provider

from .reconciliation_fixtures import ReconciliationFixtureMixin


class ReconciliationCoreTests(ReconciliationFixtureMixin, TestCase):
    def test_single_observation_becomes_one_effective_offer(self):
        row = self.offer(
            provider_url="https://example.test/watch",
            quality="4K",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )

        result = self.reconcile_movie()

        self.assertIsInstance(result, ReconciliationResult)
        self.assertEqual(result.content_types, (ContentType.MOVIE,))
        self.assertEqual(len(result.offers), 1)
        offer = result.offers[0]
        self.assertEqual(offer.provider, "recon-netflix")
        self.assertEqual(offer.country, "US")
        self.assertEqual(offer.offer_type, OfferType.SUBSCRIPTION)
        self.assertEqual(offer.tmdb_id, 550)
        self.assertTrue(offer.currently_available)
        self.assertEqual(offer.quality, "4K")
        self.assertEqual(offer.audio_languages, ("EN",))
        self.assertEqual(offer.provider_url, "https://example.test/watch")
        self.assertEqual(offer.sources, ("recon-source-a",))
        self.assertEqual(offer.offer_ids, (row.pk,))
        self.assertEqual(len(offer.observations), 1)
        self.assertEqual(offer.conflicts, ())
        self.assertEqual(result.conflicts, ())
        self.assertEqual(result.diagnostics.observations, 1)
        self.assertEqual(result.diagnostics.effective_offers, 1)
        self.assertEqual(result.diagnostics.providers, 1)
        self.assertEqual(result.diagnostics.conflicts, 0)
        self.assertEqual(result.now, self.now)

    def test_observations_from_two_sources_group_into_one_offer(self):
        first = self.offer(
            provider_content_id="sa-550",
            source=self.source_a,
            provider_url="https://a.test/550",
        )
        second = self.offer(
            provider_content_id="sb-550",
            source=self.source_b,
            provider_url="https://b.test/550",
            last_seen=self.now - timedelta(minutes=10),
        )

        result = self.reconcile_movie()

        self.assertEqual(len(result.offers), 1)
        offer = result.offers[0]
        self.assertEqual(offer.sources, ("recon-source-a", "recon-source-b"))
        self.assertEqual(offer.offer_ids, tuple(sorted([first.pk, second.pk])))
        self.assertEqual(len(offer.observations), 2)
        self.assertEqual(result.conflicts, ())
        # The freshest source owns the link; every source's link stays reachable.
        self.assertEqual(offer.provider_url, "https://a.test/550")
        self.assertEqual(offer.source_urls, (
            ("recon-source-a", "https://a.test/550"),
            ("recon-source-b", "https://b.test/550"),
        ))

    def test_reconciliation_never_writes_to_the_raw_rows(self):
        self.offer(provider_content_id="live")
        self.offer(provider_content_id="deactivated", currently_available=False)
        before = {
            row.pk: (row.currently_available, row.last_seen, row.quality, row.source_id)
            for row in Offer.objects.all()
        }

        self.reconcile_movie()
        self.service.audit(now=self.now)

        after = {
            row.pk: (row.currently_available, row.last_seen, row.quality, row.source_id)
            for row in Offer.objects.all()
        }
        self.assertEqual(before, after)
        self.assertEqual(Offer.objects.count(), 2)

    def test_deactivated_observation_is_reported_but_not_available(self):
        live = self.offer(provider_content_id="live")
        deactivated = self.offer(provider_content_id="gone", currently_available=False)

        result = self.reconcile_movie()

        offer = result.offers[0]
        self.assertTrue(offer.currently_available)
        self.assertEqual(
            [(obs.offer_id, obs.currently_available, obs.available_now)
             for obs in offer.observations],
            [(live.pk, True, True), (deactivated.pk, False, False)],
        )
        self.assertEqual(
            [conflict.kind for conflict in result.conflicts],
            [ConflictKind.AVAILABILITY],
        )

    def test_availability_window_is_evaluated_against_the_supplied_clock(self):
        self.offer(
            provider_content_id="soon",
            available_from=self.now + timedelta(days=1),
            available_until=self.now + timedelta(days=30),
        )

        before_open = self.reconcile_movie()
        self.assertFalse(before_open.offers[0].currently_available)
        self.assertFalse(before_open.offers[0].observations[0].available_now)

        during = self.service.reconcile(
            content_types=(ContentType.MOVIE,),
            tmdb_id=550,
            now=self.now + timedelta(days=2),
        )
        self.assertTrue(during.offers[0].currently_available)

        after_close = self.service.reconcile(
            content_types=(ContentType.MOVIE,),
            tmdb_id=550,
            now=self.now + timedelta(days=31),
        )
        self.assertFalse(after_close.offers[0].currently_available)

    def test_expired_observation_is_not_available(self):
        self.offer(available_until=self.now - timedelta(minutes=1))

        result = self.reconcile_movie()

        self.assertFalse(result.offers[0].currently_available)
        self.assertIsNotNone(result.offers[0].available_until)

    def test_country_scoping_and_unknown_country(self):
        self.offer(provider_content_id="us")
        self.offer(provider_content_id="nl", country=self.nl)

        us = self.reconcile_movie(country="us")
        self.assertEqual([offer.country for offer in us.offers], ["US"])
        self.assertEqual(us.country, "US")

        nowhere = self.reconcile_movie(country="zz")
        self.assertEqual(nowhere.offers, ())
        self.assertEqual(nowhere.country, "ZZ")
        self.assertEqual(nowhere.diagnostics.observations, 0)

    def test_series_and_season_rows_reconcile_together_and_stay_scoped(self):
        series_row = self.offer(
            content_type=ContentType.SERIES, season_number=1, episode_number=1,
            provider_content_id="s1e1",
        )
        season_row = self.offer(
            content_type=ContentType.SEASON, season_number=1,
            offer_type=OfferType.FREE, provider_content_id="s1",
        )
        self.offer(
            content_type=ContentType.SERIES, season_number=2, episode_number=1,
            provider_content_id="s2e1",
        )

        result = self.service.reconcile(
            content_types=(ContentType.SERIES, ContentType.SEASON),
            tmdb_id=550,
            season_number=1,
            now=self.now,
        )

        self.assertEqual(
            {
                (offer.content_type, offer.season_number, offer.episode_number)
                for offer in result.offers
            },
            {
                (ContentType.SERIES, 1, 1),
                (ContentType.SEASON, 1, None),
            },
        )
        self.assertEqual(
            sorted(offer.offer_ids[0] for offer in result.offers),
            sorted([series_row.pk, season_row.pk]),
        )
        self.assertEqual(result.diagnostics.observations, 2)

    def test_quality_and_language_values_merge_without_inventing_disagreement(self):
        self.offer(
            provider_content_id="a", quality="4K",
            audio_languages=["EN", "IT"], subtitle_languages=[],
        )
        self.offer(
            provider_content_id="b", source=self.source_b,
            audio_languages=["IT", "EN"], subtitle_languages=None,
        )

        result = self.reconcile_movie()

        offer = result.offers[0]
        self.assertEqual(result.conflicts, ())
        self.assertEqual(offer.quality, "4K")              # known beats unknown
        self.assertEqual(offer.audio_languages, ("EN", "IT"))  # order never conflicts
        self.assertEqual(offer.subtitle_languages, ())      # known empty beats unknown

    def test_freshness_follows_the_source_declared_interval(self):
        self.source_b.configuration = {"expected_refresh_interval_seconds": 3600}
        self.source_b.save(update_fields=["configuration"])
        timed = self.source_b
        self.offer(
            source=timed, provider_content_id="fresh",
            last_seen=self.now - timedelta(minutes=5),
        )
        self.offer(
            source=timed, provider_content_id="stale",
            last_seen=self.now - timedelta(hours=2),
        )
        self.offer(
            source=self.source_a, provider_content_id="never-configured",
            last_seen=self.now - timedelta(days=30),
        )

        result = self.reconcile_movie()

        offer = result.offers[0]
        freshness = {
            obs.provider_content_id: obs.freshness for obs in offer.observations
        }
        self.assertEqual(freshness, {
            "fresh": FreshnessState.FRESH,
            "stale": FreshnessState.STALE,
            "never-configured": FreshnessState.UNKNOWN,
        })
        self.assertEqual(result.diagnostics.stale_observations, 1)
        self.assertEqual(result.diagnostics.sources, ("recon-source-a", "recon-source-b"))
        # Mixed freshness is a reportable disagreement, not a hidden state.
        self.assertEqual(
            [conflict.kind for conflict in result.conflicts],
            [ConflictKind.FRESHNESS],
        )

    def test_sync_status_comes_from_the_latest_run_for_the_observed_scope(self):
        self.offer(provider_content_id="pc")
        self.sync_run(status=SyncStatus.SUCCEEDED, country=self.us,
                      created_at=self.now - timedelta(hours=3))
        self.sync_run(status=SyncStatus.FAILED, country=self.us,
                      created_at=self.now - timedelta(hours=1))

        result = self.reconcile_movie()

        # A failed refresh is a diagnostic: the observation itself still counts.
        self.assertTrue(result.offers[0].currently_available)
        self.assertEqual(
            result.offers[0].observations[0].sync_status, SyncStatus.FAILED,
        )
        self.assertEqual(result.diagnostics.failed_syncs, 1)

    def test_sync_status_is_unknown_when_the_scope_never_synced(self):
        self.offer(provider_content_id="pc")

        result = self.reconcile_movie()

        self.assertIsNone(result.offers[0].observations[0].sync_status)
        self.assertEqual(result.diagnostics.failed_syncs, 0)

    def test_provenance_retains_first_seen_last_seen_and_sync_runs(self):
        run = self.sync_run(country=self.us, status=SyncStatus.SUCCEEDED)
        row = self.offer(
            provider_content_id="pc", sync_run=run,
            last_seen=self.now - timedelta(hours=6),
        )
        stored = Offer.objects.get(pk=row.pk)

        result = self.reconcile_movie()

        offer = result.offers[0]
        self.assertEqual(offer.sync_run_ids, (run.pk,))
        self.assertEqual(offer.first_seen, stored.first_seen)
        self.assertEqual(offer.last_seen, stored.last_seen)

    def test_audit_counts_observations_groups_conflicts_and_unresolved_contents(self):
        resolved = ProviderContent.objects.create(
            provider=self.netflix, source=self.source_a, provider_content_id="mapped",
            content_type=ContentType.MOVIE, title="Mapped",
        )
        ProviderContent.objects.create(
            provider=self.netflix, source=self.source_a, provider_content_id="bare",
            content_type=ContentType.MOVIE, title="Bare",
        )
        ContentMapping.objects.create(
            provider=self.netflix, source=self.source_a, provider_content_id="mapped",
            content_type=ContentType.MOVIE, tmdb_id=550,
            match_method=MatchMethod.EXTERNAL_ID, confidence=1.0, content=resolved,
        )
        self.offer(provider_content_id="pc")

        audit = self.service.audit(now=self.now)

        self.assertEqual(audit.observations, 1)
        self.assertEqual(audit.groups, 1)
        self.assertEqual(audit.effective_offers, 1)
        self.assertEqual(audit.conflicts, 0)
        self.assertEqual(audit.unresolved_contents, 1)
        self.assertEqual(audit.sources, ("recon-source-a",))
        self.assertEqual(audit.now, self.now)

    def test_youtube_offer_without_identity_mapping_is_not_watchable(self):
        youtube, _ = Provider.objects.get_or_create(
            slug="youtube", defaults={"name": "YouTube"}
        )
        source, _ = Source.objects.get_or_create(
            slug="youtube", defaults={"name": "YouTube"}
        )
        self.offer(provider=youtube, source=source, provider_content_id="native-video")

        result = self.reconcile_movie()

        observation = result.offers[0].observations[0]
        self.assertFalse(observation.available_now)
        self.assertEqual(observation.mapping_doubt, "provider-content mapping is unresolved")

    def test_youtube_external_id_from_youtube_is_quarantined_by_reconciliation(self):
        youtube, _ = Provider.objects.get_or_create(
            slug="youtube", defaults={"name": "YouTube"}
        )
        source, _ = Source.objects.get_or_create(
            slug="youtube", defaults={"name": "YouTube"}
        )
        content = ProviderContent.objects.create(
            provider=youtube, source=source, provider_content_id="old-video",
            content_type=ContentType.MOVIE, title="Matilda",
        )
        ContentMapping.objects.create(
            provider=youtube, source=source, provider_content_id="old-video",
            content_type=ContentType.MOVIE, tmdb_id=550,
            match_method=MatchMethod.EXTERNAL_ID, confidence=0.95, content=content,
        )
        self.offer(provider=youtube, source=source, provider_content_id="old-video")

        result = self.reconcile_movie()

        observation = result.offers[0].observations[0]
        self.assertFalse(observation.available_now)
        self.assertEqual(
            observation.mapping_doubt,
            "provider-content mapping provenance is unverified",
        )

    def test_youtube_provider_content_with_synthetic_mapping_counts_unresolved(self):
        youtube, _ = Provider.objects.get_or_create(
            slug="youtube", defaults={"name": "YouTube"}
        )
        source, _ = Source.objects.get_or_create(
            slug="youtube", defaults={"name": "YouTube"}
        )
        content = ProviderContent.objects.create(
            provider=youtube, source=source, provider_content_id="old-video",
            content_type=ContentType.MOVIE, title="Matilda",
        )
        ContentMapping.objects.create(
            provider=youtube, source=source, provider_content_id="old-video",
            content_type=ContentType.MOVIE, tmdb_id=550,
            match_method=MatchMethod.EXTERNAL_ID, confidence=0.95, content=content,
        )

        audit = self.service.audit(now=self.now)

        self.assertEqual(audit.unresolved_contents, 1)

    def test_empty_result_when_nothing_was_observed(self):
        result = self.service.reconcile(
            content_types=(ContentType.MOVIE,), tmdb_id=4242, now=self.now,
        )

        self.assertEqual(result.offers, ())
        self.assertEqual(result.conflicts, ())
        self.assertEqual(result.diagnostics.observations, 0)
        self.assertEqual(result.diagnostics.providers, 0)

    def test_validation_rejects_unknown_and_empty_content_types(self):
        with self.assertRaisesMessage(ValueError, "Invalid content_type: book"):
            self.service.reconcile(content_types=("book",), tmdb_id=550, now=self.now)
        with self.assertRaisesMessage(ValueError, "content_types must not be empty"):
            self.service.reconcile(content_types=(), tmdb_id=550, now=self.now)
        with self.assertRaisesMessage(ValueError, "Invalid content_type: book"):
            AvailabilityQueryService().get_reconciled_for_content(550, "book")

    def test_a_bare_content_type_string_is_accepted(self):
        self.offer()

        result = self.service.reconcile(
            content_types="movie", tmdb_id=550, now=self.now,
        )

        self.assertEqual(result.content_types, ("movie",))

    def test_reconciliation_of_the_same_rows_is_deterministic(self):
        self.offer(provider_content_id="a", quality="4K", audio_languages=["EN"])
        self.offer(
            provider_content_id="b", source=self.source_b,
            audio_languages=["EN"], quality="4K",
        )

        first = self.reconcile_movie()
        second = self.reconcile_movie()

        self.assertEqual(first, second)
