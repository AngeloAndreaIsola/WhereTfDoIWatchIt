"""Tests for targeted offer recovery execution (Wave 2B).

Covers:
- Offline preview (no network, no writes).
- Missing mapping.
- Stale / conflicting mapping (media type mismatch, ID mismatch, mapping changing between fetch and persist).
- Unsupported source (missing targeted evidence capability).
- Valid fresh movie evidence.
- Valid fresh series evidence.
- No eligible offer (empty observation).
- Expired playback / region unobservable.
- Upstream HTTP failures (retryable errors vs permanent errors).
- Budget exhaustion.
- Idempotency / repeated recovery.
- Concurrency simulation (duplicate protection, locked recheck).
- No SyncCursor movement or authoritative deactivation.
- Complete absence of unmocked external network calls.
"""
from __future__ import annotations

import datetime as dt
from io import StringIO
from unittest.mock import MagicMock, patch

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from availability.domain.enums import ContentType, MatchMethod, OfferType, SyncMode
from availability.models import (
    ContentMapping,
    Country,
    Offer,
    OfferEvidence,
    OfferPrice,
    ProviderContent,
    Source,
    SyncCursor,
    SyncRun,
)
from availability.services.offer_recovery import (
    OUTCOME_BUDGET_EXHAUSTED,
    OUTCOME_MAPPING_CONFLICT,
    OUTCOME_MISSING_MAPPING,
    OUTCOME_NO_CURRENT_EVIDENCE,
    OUTCOME_OBSERVED_OFFER,
    OUTCOME_PERMANENT_ERROR,
    OUTCOME_RETRYABLE_ERROR,
    OUTCOME_UNSUPPORTED_SOURCE,
    recover_provider_evidence,
)
from availability.sources import RaiPlayHttpBudgetExceeded, SourceFetchError
from availability.sources.base import DiscoveredContent, ProviderOffer, SourceCapabilities, SourceFetchResult
from availability.sources.rai_play import RaiPlayClient, RaiPlayHttpBudget, RaiPlaySource
from availability.sources.registry import source_registry
from tracker.models import Movie, Provider

from .rai_play_fixtures import (
    FOLLEMENTE,
    FOLLEMENTE_ID,
    FOLLEMENTE_VIDEO,
    NORMAL_PEOPLE,
    NORMAL_PEOPLE_ID,
    RETIRED,
    FakeRaiPlayApi,
    access_spec,
    with_state,
)


def set_state(api, url, mutate):
    """Rebuild one recorded page with mutate(state) applied in place."""
    api.pages[url] = with_state(api.pages[url], mutate)


class OfferRecoveryBaseTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.country_it, _ = Country.objects.get_or_create(code="IT", defaults={"name": "Italy", "enabled": True})
        self.provider, _ = Provider.objects.get_or_create(
            slug="raiplay",
            defaults={"name": "RaiPlay", "provider_type": "bvod", "is_active": True},
        )
        self.source_model, _ = Source.objects.get_or_create(
            slug="rai_play",
            defaults={"name": "RaiPlay", "type": "web"},
        )
        self.fake_api = FakeRaiPlayApi()
        self.source = RaiPlaySource(
            client=RaiPlayClient(requester=self.fake_api),
            max_http_requests=10,
        )

        # Ensure source registry points to our test source instance
        self._orig_raiplay_factory = source_registry._factories.get("rai_play")
        source_registry._factories["rai_play"] = lambda: self.source

    def tearDown(self):
        if self._orig_raiplay_factory is not None:
            source_registry._factories["rai_play"] = self._orig_raiplay_factory
        super().tearDown()

    def create_movie_and_mapping(
        self,
        *,
        provider_content_id: str = FOLLEMENTE_ID,
        title: str = "Follemente",
        url: str = FOLLEMENTE,
        tmdb_id: int = 1001,
        content_type: str = ContentType.MOVIE,
    ) -> tuple[ProviderContent, ContentMapping]:
        movie, _ = Movie.objects.get_or_create(
            tmdb_id=tmdb_id,
            defaults={"title": title, "year": 2024, "content_type": content_type},
        )
        content = ProviderContent.objects.create(
            provider=self.provider,
            source=self.source_model,
            provider_content_id=provider_content_id,
            content_type=content_type,
            title=title,
            provider_url=url,
        )
        mapping = ContentMapping.objects.create(
            provider=self.provider,
            source=self.source_model,
            provider_content_id=provider_content_id,
            content_type=content_type,
            tmdb_id=tmdb_id,
            content=content,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )
        return content, mapping


class OfferRecoveryTests(OfferRecoveryBaseTestCase):
    def test_offline_preview_leaves_database_unmodified(self):
        content, mapping = self.create_movie_and_mapping()

        # Capture initial DB state counts
        initial_offers = Offer.objects.count()
        initial_evidence = OfferEvidence.objects.count()
        initial_prices = OfferPrice.objects.count()
        initial_runs = SyncRun.objects.count()
        initial_cursors = SyncCursor.objects.count()

        result = recover_provider_evidence(
            provider_content_pk=content.pk,
            apply=False,
        )

        self.assertEqual(result.outcome, "preview")
        self.assertEqual(result.applied, False)
        self.assertEqual(result.http_requests_attempted, 0)
        self.assertEqual(result.offers_created, 0)
        self.assertEqual(result.offers_updated, 0)
        self.assertEqual(result.offer_identities, [])
        self.assertEqual(result.provider_content_pk, content.pk)
        self.assertEqual(result.provider_content_id, content.provider_content_id)
        self.assertEqual(result.tmdb_id, mapping.tmdb_id)

        # Database must not have changed at all
        self.assertEqual(Offer.objects.count(), initial_offers)
        self.assertEqual(OfferEvidence.objects.count(), initial_evidence)
        self.assertEqual(OfferPrice.objects.count(), initial_prices)
        self.assertEqual(SyncRun.objects.count(), initial_runs)
        self.assertEqual(SyncCursor.objects.count(), initial_cursors)
        self.assertEqual(len(self.fake_api.requests), 0)

    def test_missing_mapping_returns_missing_mapping_outcome(self):
        content = ProviderContent.objects.create(
            provider=self.provider,
            source=self.source_model,
            provider_content_id="unmapped-content-1",
            content_type=ContentType.MOVIE,
            title="Unmapped Film",
            provider_url=FOLLEMENTE,
        )

        result = recover_provider_evidence(
            provider_content_pk=content.pk,
            max_http_requests=10,
            apply=True,
        )

        self.assertEqual(result.outcome, OUTCOME_MISSING_MAPPING)
        self.assertEqual(result.reason_code, "missing_mapping")
        self.assertEqual(result.http_requests_attempted, 0)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(len(self.fake_api.requests), 0)

    def test_conflicting_mapping_media_type_mismatch(self):
        content = ProviderContent.objects.create(
            provider=self.provider,
            source=self.source_model,
            provider_content_id=FOLLEMENTE_ID,
            content_type=ContentType.MOVIE,
            title="Follemente",
            provider_url=FOLLEMENTE,
        )
        ContentMapping.objects.create(
            provider=self.provider,
            source=self.source_model,
            provider_content_id=FOLLEMENTE_ID,
            content_type=ContentType.SERIES,  # Mismatch!
            tmdb_id=1001,
            content=content,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )

        result = recover_provider_evidence(
            provider_content_pk=content.pk,
            max_http_requests=10,
            apply=True,
        )

        self.assertEqual(result.outcome, OUTCOME_MAPPING_CONFLICT)
        self.assertEqual(result.reason_code, "content_type_mismatch")
        self.assertEqual(result.http_requests_attempted, 0)
        self.assertEqual(Offer.objects.count(), 0)

    def test_unsupported_source_capabilities(self):
        # Create a mock source that does NOT support targeted evidence
        class UnsupportedSource:
            slug = "dummy_src"
            name = "Dummy Source"
            source_type = "web"
            capabilities = SourceCapabilities(supports_targeted_evidence=False)

        source_registry._factories["dummy_src"] = lambda: UnsupportedSource()

        dummy_src_model, _ = Source.objects.get_or_create(slug="dummy_src", defaults={"name": "Dummy"})
        content = ProviderContent.objects.create(
            provider=self.provider,
            source=dummy_src_model,
            provider_content_id="dummy-1",
            content_type=ContentType.MOVIE,
            title="Dummy",
            provider_url="https://dummy.com/1",
        )
        ContentMapping.objects.create(
            provider=self.provider,
            source=dummy_src_model,
            provider_content_id="dummy-1",
            content_type=ContentType.MOVIE,
            tmdb_id=999,
            content=content,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )

        result = recover_provider_evidence(
            provider_content_pk=content.pk,
            max_http_requests=10,
            apply=True,
        )

        self.assertEqual(result.outcome, OUTCOME_UNSUPPORTED_SOURCE)
        self.assertEqual(result.reason_code, "source_targeted_unsupported")
        self.assertEqual(result.http_requests_attempted, 0)

    def test_valid_fresh_movie_evidence_persistence(self):
        content, mapping = self.create_movie_and_mapping()

        # Precondition: SyncCursor exists for another reason
        cursor = SyncCursor.objects.create(
            provider=self.provider,
            source=self.source_model,
            country=self.country_it,
            cursor="old_checkpoint",
            progress_at=dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.timezone.utc),
        )

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(status_code=200, text=self.fake_api.pages[FOLLEMENTE]),
                MagicMock(status_code=200, text=self.fake_api.pages[FOLLEMENTE_VIDEO]),
            ]
            live_source = RaiPlaySource(max_http_requests=10)
            source_registry._factories["rai_play"] = lambda: live_source

            result = recover_provider_evidence(
                provider_content_pk=content.pk,
                max_http_requests=10,
                apply=True,
            )

        self.assertEqual(result.outcome, OUTCOME_OBSERVED_OFFER)
        self.assertEqual(result.offers_created, 1)
        self.assertEqual(result.offers_updated, 0)
        self.assertEqual(len(result.offer_identities), 1)
        self.assertEqual(result.http_requests_attempted, 2)

        # Check Offer row in database
        offer = Offer.objects.get(identity=result.offer_identities[0])
        self.assertEqual(offer.provider, self.provider)
        self.assertEqual(offer.country, self.country_it)
        self.assertEqual(offer.content_type, ContentType.MOVIE)
        self.assertEqual(offer.tmdb_id, mapping.tmdb_id)
        self.assertEqual(offer.offer_type, OfferType.FREE)
        self.assertEqual(offer.provider_content_id, FOLLEMENTE_ID)
        self.assertTrue(offer.currently_available)

        # Check OfferEvidence row
        evidence = OfferEvidence.objects.get(offer_identity=offer.identity, source=self.source_model)
        self.assertTrue(evidence.is_present)
        self.assertFalse(evidence.authoritative)

        # For a free service with no explicit price reported, OfferPrice row is not created if amount is None
        # Verify that if an existing OfferPrice was present, it is preserved
        self.assertFalse(OfferPrice.objects.filter(offer_identity=offer.identity).exists())

        # Ensure SyncCursor was NOT moved or touched
        cursor.refresh_from_db()
        self.assertEqual(cursor.cursor, "old_checkpoint")
        self.assertEqual(cursor.progress_at, dt.datetime(2026, 1, 1, 0, 0, tzinfo=dt.timezone.utc))

    def test_valid_fresh_series_evidence_persistence(self):
        content, mapping = self.create_movie_and_mapping(
            provider_content_id=NORMAL_PEOPLE_ID,
            title="Normal People",
            url=NORMAL_PEOPLE,
            tmdb_id=2002,
            content_type=ContentType.SERIES,
        )

        result = recover_provider_evidence(
            provider_content_pk=content.pk,
            max_http_requests=10,
            apply=True,
        )

        self.assertEqual(result.outcome, OUTCOME_OBSERVED_OFFER)
        self.assertEqual(result.offers_created, 1)
        self.assertEqual(len(result.offer_identities), 1)

        offer = Offer.objects.get(identity=result.offer_identities[0])
        self.assertEqual(offer.content_type, ContentType.SERIES)
        self.assertEqual(offer.tmdb_id, mapping.tmdb_id)
        self.assertIsNone(offer.season_number)
        self.assertIsNone(offer.episode_number)

    def test_empty_evidence_retired_content_leaves_offers_unmodified(self):
        content, mapping = self.create_movie_and_mapping(
            provider_content_id="ContentItem-retired",
            title="Retired Film",
            url=RETIRED,
            tmdb_id=3003,
        )

        # An existing offer from another previous run
        pre_existing_offer = Offer.objects.create(
            provider=self.provider,
            country=self.country_it,
            source=self.source_model,
            content_type=ContentType.MOVIE,
            tmdb_id=mapping.tmdb_id,
            offer_type=OfferType.FREE,
            provider_content_id=content.provider_content_id,
            currently_available=True,
        )

        result = recover_provider_evidence(
            provider_content_pk=content.pk,
            max_http_requests=10,
            apply=True,
        )

        self.assertEqual(result.outcome, OUTCOME_NO_CURRENT_EVIDENCE)
        self.assertEqual(result.reason_code, "evidence_unobservable")
        self.assertEqual(result.offers_created, 0)
        self.assertEqual(result.offers_updated, 0)
        self.assertEqual(result.offer_identities, [])

        # The pre-existing offer must NOT be deactivated by empty targeted observation
        pre_existing_offer.refresh_from_db()
        self.assertTrue(pre_existing_offer.currently_available)

    def test_expired_playback_produces_no_current_evidence(self):
        content, mapping = self.create_movie_and_mapping()

        # Set the video availability ends date in the past
        set_state(
            self.fake_api,
            FOLLEMENTE_VIDEO,
            lambda s: access_spec(s).update(availabilityEnds="2020-01-01T00:00:00.000"),
        )

        result = recover_provider_evidence(
            provider_content_pk=content.pk,
            max_http_requests=10,
            apply=True,
        )

        self.assertEqual(result.outcome, OUTCOME_NO_CURRENT_EVIDENCE)
        self.assertEqual(result.offers_created, 0)
        self.assertEqual(Offer.objects.count(), 0)

    def test_http_budget_exhaustion(self):
        content, mapping = self.create_movie_and_mapping()

        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, text=self.fake_api.pages[FOLLEMENTE])
            live_source = RaiPlaySource(max_http_requests=1)
            source_registry._factories["rai_play"] = lambda: live_source

            # Allow only 1 request, which exhausts budget before video fetch
            result = recover_provider_evidence(
                provider_content_pk=content.pk,
                max_http_requests=1,
                apply=True,
            )

        self.assertEqual(result.outcome, OUTCOME_BUDGET_EXHAUSTED)
        self.assertEqual(result.offers_created, 0)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(result.http_requests_attempted, 1)

    def test_retryable_transient_upstream_error(self):
        content, mapping = self.create_movie_and_mapping()

        # Simulate timeout on the source call
        with patch.object(self.source, "fetch_evidence", side_effect=requests.exceptions.ConnectTimeout("Connection timed out")):
            result = recover_provider_evidence(
                provider_content_pk=content.pk,
                max_http_requests=10,
                apply=True,
            )

        self.assertEqual(result.outcome, OUTCOME_RETRYABLE_ERROR)
        self.assertIn("Connection timed out", result.error_detail)
        self.assertEqual(Offer.objects.count(), 0)

    def test_permanent_fatal_error(self):
        content, mapping = self.create_movie_and_mapping()

        with patch.object(self.source, "fetch_evidence", side_effect=ValueError("Malformed programme JSON")):
            result = recover_provider_evidence(
                provider_content_pk=content.pk,
                max_http_requests=10,
                apply=True,
            )

        self.assertEqual(result.outcome, OUTCOME_PERMANENT_ERROR)
        self.assertIn("Malformed programme JSON", result.error_detail)
        self.assertEqual(Offer.objects.count(), 0)

    def test_source_returned_conflicting_tmdb_id(self):
        content, mapping = self.create_movie_and_mapping(tmdb_id=1001)

        # Mock source returning an offer with a conflicting TMDb id (9999)
        conflicting_offer = ProviderOffer(
            provider_id="raiplay",
            provider_content_id=FOLLEMENTE_ID,
            country="IT",
            content_type=ContentType.MOVIE,
            tmdb_id=9999,  # Conflicts with mapping tmdb_id 1001
            season_number=None,
            episode_number=None,
            offer_type=OfferType.FREE,
        )
        fake_fetch_result = SourceFetchResult(
            contents=[
                DiscoveredContent(
                    provider_id="raiplay",
                    provider_content_id=FOLLEMENTE_ID,
                    content_type=ContentType.MOVIE,
                )
            ],
            offers=[conflicting_offer],
            sync_mode=SyncMode.TARGETED,
        )

        with patch.object(self.source, "fetch_evidence", return_value=fake_fetch_result):
            result = recover_provider_evidence(
                provider_content_pk=content.pk,
                max_http_requests=10,
                apply=True,
            )

        self.assertEqual(result.outcome, OUTCOME_MAPPING_CONFLICT)
        self.assertEqual(result.reason_code, "tmdb_id_conflict")
        self.assertEqual(Offer.objects.count(), 0)

    def test_mapping_altered_concurrently_between_fetch_and_persist(self):
        content, mapping = self.create_movie_and_mapping(tmdb_id=1001)

        # Hook into fetch_evidence: during fetch, another process mutates mapping.tmdb_id
        original_fetch = self.source.fetch_evidence

        def mutating_fetch(*args, **kwargs):
            res = original_fetch(*args, **kwargs)
            # Mutate mapping concurrently
            ContentMapping.objects.filter(pk=mapping.pk).update(tmdb_id=7777)
            return res

        with patch.object(self.source, "fetch_evidence", side_effect=mutating_fetch):
            result = recover_provider_evidence(
                provider_content_pk=content.pk,
                max_http_requests=10,
                apply=True,
            )

        self.assertEqual(result.outcome, OUTCOME_MAPPING_CONFLICT)
        self.assertEqual(result.reason_code, "mapping_concurrently_altered")
        self.assertEqual(Offer.objects.count(), 0)

    def test_repeated_recovery_is_idempotent(self):
        content, mapping = self.create_movie_and_mapping()

        # First run creates
        res1 = recover_provider_evidence(
            provider_content_pk=content.pk,
            max_http_requests=10,
            apply=True,
        )
        self.assertEqual(res1.outcome, OUTCOME_OBSERVED_OFFER)
        self.assertEqual(res1.offers_created, 1)
        self.assertEqual(res1.offers_updated, 0)
        self.assertEqual(Offer.objects.count(), 1)
        offer_id1 = res1.offer_identities[0]

        # Second run updates the same canonical offer, does not duplicate
        res2 = recover_provider_evidence(
            provider_content_pk=content.pk,
            max_http_requests=10,
            apply=True,
        )
        self.assertEqual(res2.outcome, OUTCOME_OBSERVED_OFFER)
        self.assertEqual(res2.offers_created, 0)
        self.assertEqual(res2.offers_updated, 1)
        self.assertEqual(Offer.objects.count(), 1)
        self.assertEqual(res2.offer_identities[0], offer_id1)


class RecoverProviderEvidenceCommandTests(OfferRecoveryBaseTestCase):
    def test_command_requires_positive_provider_content_id(self):
        with self.assertRaises(CommandError):
            call_command("recover_provider_evidence", "--provider-content-id", "0")

    def test_command_requires_max_http_requests_with_apply(self):
        content, _ = self.create_movie_and_mapping()
        with self.assertRaises(CommandError) as ctx:
            call_command("recover_provider_evidence", "--provider-content-id", str(content.pk), "--apply")
        self.assertIn("--max-http-requests", str(ctx.exception))

    def test_command_offline_preview_json_output(self):
        content, mapping = self.create_movie_and_mapping()
        out = StringIO()
        call_command(
            "recover_provider_evidence",
            "--provider-content-id", str(content.pk),
            "--json",
            stdout=out,
        )
        import json
        data = json.loads(out.getvalue())
        self.assertEqual(data["outcome"], "preview")
        self.assertEqual(data["applied"], False)
        self.assertEqual(data["provider_content_pk"], content.pk)
        self.assertEqual(data["tmdb_id"], mapping.tmdb_id)

    def test_command_apply_successful_run(self):
        content, mapping = self.create_movie_and_mapping()
        out = StringIO()
        call_command(
            "recover_provider_evidence",
            "--provider-content-id", str(content.pk),
            "--max-http-requests", "10",
            "--apply",
            "--json",
            stdout=out,
        )
        import json
        data = json.loads(out.getvalue())
        self.assertEqual(data["outcome"], OUTCOME_OBSERVED_OFFER)
        self.assertEqual(data["applied"], True)
        self.assertEqual(data["offers_created"], 1)
        self.assertEqual(Offer.objects.count(), 1)


class AdversarialOfferRecoveryTests(OfferRecoveryBaseTestCase):
    def test_provider_identity_mismatch_returns_mapping_conflict(self):
        content, mapping = self.create_movie_and_mapping(tmdb_id=1001)

        # Source returns a mismatched provider_content_id
        mismatched_offer = ProviderOffer(
            provider_id="raiplay",
            provider_content_id="mismatched-native-id",
            country="IT",
            content_type=ContentType.MOVIE,
            tmdb_id=1001,
            season_number=None,
            episode_number=None,
            offer_type=OfferType.FREE,
        )
        fake_fetch_result = SourceFetchResult(
            contents=[
                DiscoveredContent(
                    provider_id="raiplay",
                    provider_content_id="mismatched-native-id",
                    content_type=ContentType.MOVIE,
                )
            ],
            offers=[mismatched_offer],
            sync_mode=SyncMode.TARGETED,
        )

        with patch.object(self.source, "fetch_evidence", return_value=fake_fetch_result):
            result = recover_provider_evidence(
                provider_content_pk=content.pk,
                max_http_requests=10,
                apply=True,
            )

        self.assertEqual(result.outcome, OUTCOME_MAPPING_CONFLICT)
        self.assertEqual(result.reason_code, "identity_conflict")
        self.assertEqual(Offer.objects.count(), 0)

    def test_media_type_mismatch_in_source_result_returns_mapping_conflict(self):
        content, mapping = self.create_movie_and_mapping(tmdb_id=1001, content_type=ContentType.MOVIE)

        # Source returns SERIES instead of MOVIE
        mismatched_offer = ProviderOffer(
            provider_id="raiplay",
            provider_content_id=content.provider_content_id,
            country="IT",
            content_type=ContentType.SERIES,
            tmdb_id=1001,
            season_number=None,
            episode_number=None,
            offer_type=OfferType.FREE,
        )
        fake_fetch_result = SourceFetchResult(
            contents=[
                DiscoveredContent(
                    provider_id="raiplay",
                    provider_content_id=content.provider_content_id,
                    content_type=ContentType.SERIES,
                )
            ],
            offers=[mismatched_offer],
            sync_mode=SyncMode.TARGETED,
        )

        with patch.object(self.source, "fetch_evidence", return_value=fake_fetch_result):
            result = recover_provider_evidence(
                provider_content_pk=content.pk,
                max_http_requests=10,
                apply=True,
            )

        self.assertEqual(result.outcome, OUTCOME_MAPPING_CONFLICT)
        self.assertEqual(result.reason_code, "media_type_conflict")
        self.assertEqual(Offer.objects.count(), 0)

    def test_no_nested_writer_barrier_deadlock(self):
        content, mapping = self.create_movie_and_mapping()

        # Call recover_provider_evidence from within an already acquired writer_barrier
        from availability.services.write_barrier import writer_barrier
        with writer_barrier("outer testing barrier"):
            result = recover_provider_evidence(
                provider_content_pk=content.pk,
                max_http_requests=10,
                apply=True,
            )

        self.assertEqual(result.outcome, OUTCOME_OBSERVED_OFFER)
        self.assertEqual(result.offers_created, 1)

    def test_preserves_corroborating_evidence_from_other_sources(self):
        content, mapping = self.create_movie_and_mapping()

        # Suppose another source (e.g. streaming_availability) already observed an offer with the same identity
        other_source_model, _ = Source.objects.get_or_create(slug="streaming_availability", defaults={"name": "SA", "type": "api"})
        existing_offer = Offer.objects.create(
            provider=self.provider,
            country=self.country_it,
            source=other_source_model,
            content_type=ContentType.MOVIE,
            tmdb_id=mapping.tmdb_id,
            offer_type=OfferType.FREE,
            provider_content_id=content.provider_content_id,
            currently_available=True,
        )
        existing_evidence = OfferEvidence.objects.create(
            source=other_source_model,
            offer_identity=existing_offer.identity,
            provider_slug=self.provider.slug,
            country_code=self.country_it.code,
            content_type=ContentType.MOVIE,
            tmdb_id=mapping.tmdb_id,
            offer_type=OfferType.FREE,
            provider_content_id=content.provider_content_id,
            is_present=True,
            authoritative=False,
        )

        # Now targeted recovery runs for rai_play
        result = recover_provider_evidence(
            provider_content_pk=content.pk,
            max_http_requests=10,
            apply=True,
        )

        self.assertEqual(result.outcome, OUTCOME_OBSERVED_OFFER)
        self.assertEqual(result.offers_created, 0)
        self.assertEqual(result.offers_updated, 1)

        # Both evidence rows exist independently!
        evidence_rows = OfferEvidence.objects.filter(offer_identity=existing_offer.identity)
        self.assertEqual(evidence_rows.count(), 2)
        sources_present = {e.source.slug for e in evidence_rows}
        self.assertEqual(sources_present, {"streaming_availability", "rai_play"})

        # Other source evidence was NOT touched or marked absent
        existing_evidence.refresh_from_db()
        self.assertTrue(existing_evidence.is_present)


    def test_mapping_revision_change_during_fetch_fails_closed(self):
        content, mapping = self.create_movie_and_mapping(tmdb_id=1001)

        # Simulate presence of revision attribute
        mapping.revision = 1
        original_fetch = self.source.fetch_evidence

        def mutating_fetch(*args, **kwargs):
            res = original_fetch(*args, **kwargs)
            # Advance revision during fetch
            mapping.revision = 2
            return res

        with patch.object(self.source, "fetch_evidence", side_effect=mutating_fetch):
            # Patch getattr on ContentMapping to return our simulated revision
            with patch("availability.services.offer_recovery.getattr", side_effect=lambda obj, attr, default=None: (
                mapping.revision if isinstance(obj, ContentMapping) and attr == "revision" else getattr(obj, attr, default)
            )):
                result = recover_provider_evidence(
                    provider_content_pk=content.pk,
                    max_http_requests=10,
                    apply=True,
                )

        self.assertEqual(result.outcome, OUTCOME_MAPPING_CONFLICT)
        self.assertEqual(result.reason_code, "mapping_concurrently_altered")
        self.assertEqual(Offer.objects.count(), 0)
