"""Conflict reporting: every disagreement kind, surfaced and never auto-resolved.

Reconciliation has no source ranking, so a disagreement can never pick a
winner. These tests pin the shape of each ``ConflictKind`` and prove that a
conflict is both reported at the result level and attached to the offers it
affects — while the raw rows stay exactly as they were.
"""
from datetime import timedelta

from django.test import TestCase

from availability.domain.enums import (
    ConflictKind,
    ContentType,
    FreshnessState,
    MatchMethod,
    OfferType,
)
from availability.domain.reconciliation import SourceFact
from availability.models import ContentMapping, Offer

from .reconciliation_fixtures import ReconciliationFixtureMixin


class ReconciliationConflictTests(ReconciliationFixtureMixin, TestCase):
    def kinds(self, result):
        return [conflict.kind for conflict in result.conflicts]

    def test_sources_disagreeing_on_availability_surface_one_conflict(self):
        live = self.offer(provider_content_id="live")
        deactivated = self.offer(provider_content_id="gone", currently_available=False)

        result = self.reconcile_movie()

        self.assertEqual(self.kinds(result), [ConflictKind.AVAILABILITY])
        conflict = result.conflicts[0]
        self.assertEqual(conflict.provider, "recon-netflix")
        self.assertEqual(conflict.country, "US")
        self.assertEqual(conflict.offer_ids, tuple(sorted([live.pk, deactivated.pk])))
        self.assertEqual(conflict.sources, ("recon-source-a",))
        self.assertEqual(conflict.facts, (
            SourceFact("recon-source-a", "available"),
            SourceFact("recon-source-a", "unavailable"),
        ))
        self.assertEqual(
            [c.kind for c in result.offers[0].conflicts],
            [ConflictKind.AVAILABILITY],
        )
        # The disagreement does not stop the offer: one source still lists it.
        self.assertTrue(result.offers[0].currently_available)

    def test_sources_disagreeing_on_offer_type_conflict_for_the_whole_group(self):
        self.offer(provider_content_id="sub", offer_type=OfferType.SUBSCRIPTION,
                   source=self.source_a)
        self.offer(provider_content_id="rent", offer_type=OfferType.RENT,
                   source=self.source_b)

        result = self.reconcile_movie()

        self.assertEqual(self.kinds(result), [ConflictKind.OFFER_TYPE])
        conflict = result.conflicts[0]
        self.assertEqual(conflict.sources, ("recon-source-a", "recon-source-b"))
        self.assertEqual(conflict.facts, (
            SourceFact("recon-source-a", "subscription"),
            SourceFact("recon-source-b", "rent"),
        ))
        self.assertEqual(len(result.offers), 2)
        for offer in result.offers:
            self.assertEqual(
                [c.kind for c in offer.conflicts], [ConflictKind.OFFER_TYPE],
            )

    def test_one_source_offering_two_offer_types_is_not_a_conflict(self):
        self.offer(provider_content_id="sub", offer_type=OfferType.SUBSCRIPTION)
        self.offer(provider_content_id="rent", offer_type=OfferType.RENT)

        result = self.reconcile_movie()

        self.assertEqual(result.conflicts, ())
        self.assertEqual(len(result.offers), 2)

    def test_stored_mapping_pointing_elsewhere_blocks_and_surfaces(self):
        row = self.offer(provider_content_id="mapped")
        ContentMapping.objects.create(
            provider=self.netflix, source=self.source_a,
            provider_content_id="mapped", content_type=ContentType.MOVIE,
            tmdb_id=999, match_method=MatchMethod.EXTERNAL_ID, confidence=1.0,
        )

        result = self.reconcile_movie()

        offer = result.offers[0]
        self.assertEqual(self.kinds(result), [ConflictKind.MAPPING])
        conflict = result.conflicts[0]
        self.assertEqual(conflict.offer_ids, (row.pk,))
        self.assertIn("mapping points to tmdb 999", conflict.facts[0].value)

        observation = offer.observations[0]
        self.assertTrue(observation.currently_available)   # raw row untouched
        self.assertFalse(observation.available_now)        # doubted, so not counted
        self.assertFalse(offer.currently_available)

    def test_provider_content_claimed_by_two_tmdb_ids_blocks_and_surfaces(self):
        self.offer(provider_content_id="shared", tmdb_id=600)
        row = self.offer(provider_content_id="shared", tmdb_id=550)

        result = self.reconcile_movie()

        self.assertEqual(self.kinds(result), [ConflictKind.MAPPING])
        conflict = result.conflicts[0]
        self.assertEqual(conflict.offer_ids, (row.pk,))
        self.assertIn("also active for tmdb 600", conflict.facts[0].value)
        self.assertFalse(result.offers[0].currently_available)

    def test_both_mapping_paths_report_one_conflict(self):
        row = self.offer(provider_content_id="double")
        ContentMapping.objects.create(
            provider=self.netflix, source=self.source_a,
            provider_content_id="double", content_type=ContentType.MOVIE,
            tmdb_id=999, match_method=MatchMethod.EXTERNAL_ID, confidence=1.0,
        )
        self.offer(provider_content_id="double", tmdb_id=600)

        result = self.reconcile_movie()

        self.assertEqual(self.kinds(result), [ConflictKind.MAPPING])
        value = result.conflicts[0].facts[0].value
        self.assertIn("mapping points to tmdb 999", value)
        self.assertIn("also active for tmdb 600", value)
        self.assertEqual(result.conflicts[0].offer_ids, (row.pk,))

    def test_rows_without_a_provider_content_id_never_raise_a_mapping_conflict(self):
        # No provider content id means there is nothing to look a mapping up
        # with, and nothing another TMDB id can claim.
        self.offer(provider_content_id="", provider=self.netflix)
        self.offer(provider_content_id="", provider=self.prime)

        result = self.reconcile_movie()

        self.assertEqual(result.conflicts, ())
        self.assertEqual(len(result.offers), 2)
        self.assertTrue(all(offer.currently_available for offer in result.offers))

    def test_disagreeing_availability_windows_conflict_and_widen_to_unknown(self):
        self.offer(
            provider_content_id="a",
            available_from=self.now - timedelta(days=1),
            available_until=self.now + timedelta(days=10),
        )
        self.offer(
            provider_content_id="b", source=self.source_b,
            available_from=self.now - timedelta(days=7),
            available_until=self.now + timedelta(days=30),
        )

        result = self.reconcile_movie()

        offer = result.offers[0]
        self.assertEqual(self.kinds(result), [ConflictKind.WINDOW])
        self.assertIsNone(offer.available_from)
        self.assertIsNone(offer.available_until)
        conflict = result.conflicts[0]
        self.assertEqual(len(conflict.facts), 4)
        self.assertEqual(
            {fact.value.split("=")[0] for fact in conflict.facts},
            {"available_from", "available_until"},
        )
        self.assertTrue(all(
            fact.source in ("recon-source-a", "recon-source-b")
            for fact in conflict.facts
        ))

    def test_disagreeing_qualities_are_reported_instead_of_ranked(self):
        self.offer(provider_content_id="a", quality="4K")
        self.offer(provider_content_id="b", source=self.source_b, quality="hd")

        result = self.reconcile_movie()

        self.assertEqual(result.offers[0].quality, "")
        self.assertEqual(self.kinds(result), [ConflictKind.QUALITY])
        self.assertEqual(result.conflicts[0].facts, (
            SourceFact("recon-source-a", "4K"),
            SourceFact("recon-source-b", "hd"),
        ))

    def test_disagreeing_language_lists_conflict_while_the_other_field_survives(self):
        self.offer(
            provider_content_id="a",
            audio_languages=["EN"], subtitle_languages=["EN"],
        )
        self.offer(
            provider_content_id="b", source=self.source_b,
            audio_languages=["IT"], subtitle_languages=None,
        )

        result = self.reconcile_movie()

        offer = result.offers[0]
        self.assertIsNone(offer.audio_languages)
        self.assertEqual(offer.subtitle_languages, ("EN",))  # known beats unknown
        self.assertEqual(self.kinds(result), [ConflictKind.LANGUAGE])
        self.assertEqual(result.conflicts[0].facts, (
            SourceFact("recon-source-a", "audio=EN"),
            SourceFact("recon-source-b", "audio=IT"),
        ))

    def test_mixed_freshness_is_reported_with_its_states(self):
        for source in (self.source_a, self.source_b):
            source.configuration = {"expected_refresh_interval_seconds": 3600}
            source.save(update_fields=["configuration"])
        self.offer(provider_content_id="a", last_seen=self.now - timedelta(minutes=1))
        self.offer(
            provider_content_id="b", source=self.source_b,
            last_seen=self.now - timedelta(hours=6),
        )

        result = self.reconcile_movie()

        self.assertEqual(self.kinds(result), [ConflictKind.FRESHNESS])
        self.assertEqual(result.conflicts[0].facts, (
            SourceFact("recon-source-a", FreshnessState.FRESH.value),
            SourceFact("recon-source-b", FreshnessState.STALE.value),
        ))
        self.assertEqual(result.diagnostics.stale_observations, 1)

    def test_result_conflicts_are_unique_and_attached_only_where_they_apply(self):
        self.offer(provider_content_id="sub-a", offer_type=OfferType.SUBSCRIPTION,
                   quality="4K", source=self.source_a)
        self.offer(provider_content_id="sub-b", offer_type=OfferType.SUBSCRIPTION,
                   quality="hd", source=self.source_b)
        self.offer(provider_content_id="rent-b", offer_type=OfferType.RENT,
                   source=self.source_b)

        result = self.reconcile_movie()

        kinds = self.kinds(result)
        self.assertEqual(sorted(kind.value for kind in kinds),
                         ["offer_type", "quality"])
        self.assertEqual(len(kinds), len(set(kinds)))

        by_type = {offer.offer_type: offer for offer in result.offers}
        self.assertEqual(
            sorted(c.kind.value for c in by_type[OfferType.SUBSCRIPTION].conflicts),
            ["offer_type", "quality"],
        )
        self.assertEqual(
            [c.kind for c in by_type[OfferType.RENT].conflicts],
            [ConflictKind.OFFER_TYPE],
        )

    def test_conflict_reports_leave_the_raw_rows_intact(self):
        self.offer(provider_content_id="a", quality="4K")
        self.offer(provider_content_id="b", source=self.source_b, quality="hd")
        before = {
            row.pk: (row.quality, row.currently_available, row.last_seen)
            for row in Offer.objects.all()
        }

        self.reconcile_movie()
        self.service.audit(now=self.now)

        after = {
            row.pk: (row.quality, row.currently_available, row.last_seen)
            for row in Offer.objects.all()
        }
        self.assertEqual(before, after)
