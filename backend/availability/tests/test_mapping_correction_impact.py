"""Tests for the read-only Mapping Correction Impact Planner.

Verifies:
- Report generation for matched, unmatched, and conflicted mappings.
- Shared evidence safety & corroborating observations across sources.
- Invalid availability and offer identity obsolescence detection.
- Deterministic precondition fingerprinting and canonical digest stability.
- Strict no-write regression assertions (counts & checksums of database rows
  before and after, spy assertion that no model mutation methods are called).
- Network isolation (no socket/HTTP calls).
- Edge-case handling (missing ProviderContent, missing mapping, unchanged target,
  conflicting content types, manual mapping conflicts).
- CLI management command execution (human readable, --json, and --write-manifest).
"""
from __future__ import annotations

import io
import json
import socket
import tempfile
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import TestCase
from django.utils import timezone

from tracker.models import Movie, Provider
from availability.domain.enums import ContentType, MatchMethod, OfferType, SourceType
from availability.models import (
    ContentMapping,
    Country,
    Offer,
    OfferEvidence,
    OfferPrice,
    ProviderContent,
    Source,
    build_offer_identity,
    build_provider_content_identity,
)
from availability.services.mapping_correction_impact import (
    MANIFEST_SCHEMA,
    MappingCorrectionImpactPlanner,
    MappingCorrectionPlannerError,
    _sorted_fingerprint_rows,
    canonical_json_bytes,
    compute_digest,
)


class BasePlannerTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.country_gb, _ = Country.objects.get_or_create(
            code="GB", defaults={"name": "United Kingdom"}
        )
        self.country_us, _ = Country.objects.get_or_create(
            code="US", defaults={"name": "United States"}
        )

        self.provider_bbc, _ = Provider.objects.get_or_create(
            slug="bbc_iplayer", defaults={"name": "BBC iPlayer"}
        )
        self.provider_tubi, _ = Provider.objects.get_or_create(
            slug="tubi", defaults={"name": "Tubi"}
        )

        self.source_bbc, _ = Source.objects.get_or_create(
            slug="bbc_collector", defaults={"name": "BBC Collector", "type": SourceType.WEB}
        )
        self.source_tubi, _ = Source.objects.get_or_create(
            slug="tubi_collector", defaults={"name": "Tubi Collector", "type": SourceType.WEB}
        )
        self.source_sa, _ = Source.objects.get_or_create(
            slug="streaming_availability", defaults={"name": "Streaming Availability", "type": SourceType.API}
        )

        self.movie_old, _ = Movie.objects.get_or_create(
            tmdb_id=1001,
            defaults={"title": "The Wrong Title", "year": 2010},
        )
        self.movie_proposed, _ = Movie.objects.get_or_create(
            tmdb_id=2002,
            defaults={"title": "The Right Title", "year": 2012},
        )


class MappingCorrectionImpactPlannerTests(BasePlannerTestCase):

    def test_impact_report_with_existing_mapping_and_offers(self):
        pcid = "bbc-prog-123"
        pc = ProviderContent.objects.create(
            provider=self.provider_bbc,
            source=self.source_bbc,
            provider_content_id=pcid,
            content_type=ContentType.MOVIE,
            title="The Title on BBC",
            identity=build_provider_content_identity(
                self.provider_bbc.slug, pcid, ContentType.MOVIE
            ),
        )
        mapping = ContentMapping.objects.create(
            provider=self.provider_bbc,
            source=self.source_bbc,
            provider_content_id=pcid,
            content_type=ContentType.MOVIE,
            tmdb_id=1001,
            content=pc,
            confidence=0.7,
            match_method=MatchMethod.TITLE_YEAR,
        )
        offer_identity = build_offer_identity(
            self.provider_bbc.slug,
            "GB",
            ContentType.MOVIE,
            1001,
            None,
            None,
            OfferType.FREE,
            pcid,
        )
        offer = Offer.objects.create(
            provider=self.provider_bbc,
            country=self.country_gb,
            source=self.source_bbc,
            content_type=ContentType.MOVIE,
            tmdb_id=1001,
            offer_type=OfferType.FREE,
            provider_content_id=pcid,
            identity=offer_identity,
            currently_available=True,
        )
        evidence = OfferEvidence.objects.create(
            source=self.source_bbc,
            offer_identity=offer_identity,
            provider_slug=self.provider_bbc.slug,
            country_code="GB",
            content_type=ContentType.MOVIE,
            tmdb_id=1001,
            offer_type=OfferType.FREE,
            provider_content_id=pcid,
            is_present=True,
            authoritative=False,
        )
        price = OfferPrice.objects.create(
            source=self.source_bbc,
            offer_identity=offer_identity,
            provider_slug=self.provider_bbc.slug,
            country_code="GB",
            content_type=ContentType.MOVIE,
            tmdb_id=1001,
            offer_type=OfferType.FREE,
            provider_content_id=pcid,
            amount=Decimal("0.00"),
            currency="GBP",
            is_current=True,
        )

        planner = MappingCorrectionImpactPlanner(
            provider="bbc_iplayer",
            provider_content_id=pcid,
            proposed_tmdb_id=2002,
            proposed_content_type="movie",
        )
        manifest = planner.plan()

        self.assertEqual(manifest["schema"], MANIFEST_SCHEMA)
        self.assertTrue(manifest["digest"].startswith("sha256:"))
        self.assertTrue(manifest["precondition_fingerprint"].startswith("sha256:"))

        report = manifest["report"]
        self.assertEqual(report["existing_mapping"]["id"], mapping.id)
        self.assertEqual(report["existing_mapping"]["target_tmdb_id"], 1001)
        self.assertFalse(report["existing_mapping"]["is_manual"])

        self.assertEqual(report["provider_native_identity"]["content_rows_count"], 1)
        self.assertEqual(report["former_tmdb_target"]["tmdb_id"], 1001)
        self.assertEqual(report["former_tmdb_target"]["title"], "The Wrong Title")
        self.assertEqual(report["proposed_tmdb_target"]["tmdb_id"], 2002)
        self.assertEqual(report["proposed_tmdb_target"]["title"], "The Right Title")

        self.assertEqual(len(report["associated_offers"]), 1)
        self.assertEqual(report["associated_offers"][0]["id"], offer.id)
        self.assertEqual(len(report["associated_evidence"]), 1)
        self.assertEqual(report["associated_evidence"][0]["id"], evidence.id)
        self.assertEqual(len(report["associated_prices"]), 1)
        self.assertEqual(report["associated_prices"][0]["id"], price.id)

        # Invalid availability
        inv = report["potentially_invalid_availability"]
        self.assertEqual(inv["former_target_active_offers_lost"], 1)
        self.assertEqual(inv["stale_offer_identities_count"], 1)
        self.assertTrue(inv["stale_offers"][0]["identity_diverges"])
        self.assertTrue(inv["stale_offers"][0]["reconciliation_mapping_doubt_risk"])

    def test_corroborating_observations_from_other_sources(self):
        pcid = "bbc-prog-456"
        # Another provider has an offer for proposed TMDb 2002
        Offer.objects.create(
            provider=self.provider_tubi,
            country=self.country_us,
            source=self.source_tubi,
            content_type=ContentType.MOVIE,
            tmdb_id=2002,
            offer_type=OfferType.FREE_WITH_ADS,
            provider_content_id="tubi-prog-456",
            identity="tubi|US|movie|2002|||free_with_ads|tubi-prog-456",
            currently_available=True,
        )

        planner = MappingCorrectionImpactPlanner(
            provider=self.provider_bbc,
            provider_content_id=pcid,
            proposed_tmdb_id=2002,
            proposed_content_type="movie",
        )
        manifest = planner.plan()
        corrob = manifest["report"]["corroborating_observations"]
        self.assertTrue(corrob["is_corroborated_globally"])
        self.assertFalse(corrob["is_corroborated_at_provider"])
        self.assertEqual(corrob["other_providers_offers_count"], 1)
        self.assertEqual(corrob["other_providers_sample"][0]["provider"], "tubi")

    def test_manual_mapping_conflict_detection(self):
        pcid = "bbc-prog-manual"
        # 1. Existing mapping is manual
        ContentMapping.objects.create(
            provider=self.provider_bbc,
            source=self.source_bbc,
            provider_content_id=pcid,
            content_type=ContentType.MOVIE,
            tmdb_id=1001,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )
        # 2. Another manual mapping for same provider claiming proposed tmdb_id
        ContentMapping.objects.create(
            provider=self.provider_bbc,
            source=self.source_bbc,
            provider_content_id="bbc-prog-other",
            content_type=ContentType.MOVIE,
            tmdb_id=2002,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )

        planner = MappingCorrectionImpactPlanner(
            provider=self.provider_bbc,
            provider_content_id=pcid,
            proposed_tmdb_id=2002,
            proposed_content_type="movie",
        )
        manifest = planner.plan()
        conflicts = manifest["report"]["manual_mapping_conflicts"]
        self.assertTrue(conflicts["has_manual_conflicts"])
        self.assertEqual(len(conflicts["manual_conflicts"]), 2)
        conflict_types = {c["type"] for c in conflicts["manual_conflicts"]}
        self.assertIn("target_mapping_already_manual", conflict_types)
        self.assertIn("competing_manual_mapping_for_proposed_tmdb", conflict_types)

    def test_unmapped_and_missing_provider_content_edge_cases(self):
        # Scope where neither ContentMapping nor ProviderContent exists
        planner = MappingCorrectionImpactPlanner(
            provider=self.provider_bbc,
            provider_content_id="unseen-content-id",
            proposed_tmdb_id=2002,
            proposed_content_type="movie",
        )
        manifest = planner.plan()
        report = manifest["report"]
        self.assertIsNone(report["existing_mapping"])
        self.assertEqual(report["provider_native_identity"]["content_rows_count"], 0)
        self.assertIsNone(report["former_tmdb_target"])
        self.assertEqual(len(report["associated_offers"]), 0)
        self.assertEqual(report["potentially_invalid_availability"]["stale_offer_identities_count"], 0)

    def test_unchanged_proposed_target_edge_case(self):
        pcid = "bbc-prog-unchanged"
        ContentMapping.objects.create(
            provider=self.provider_bbc,
            source=self.source_bbc,
            provider_content_id=pcid,
            content_type=ContentType.MOVIE,
            tmdb_id=2002,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )
        offer_id = build_offer_identity(
            self.provider_bbc.slug, "GB", ContentType.MOVIE, 2002, None, None, OfferType.FREE, pcid
        )
        Offer.objects.create(
            provider=self.provider_bbc,
            country=self.country_gb,
            source=self.source_bbc,
            content_type=ContentType.MOVIE,
            tmdb_id=2002,
            offer_type=OfferType.FREE,
            provider_content_id=pcid,
            identity=offer_id,
            currently_available=True,
        )
        planner = MappingCorrectionImpactPlanner(
            provider=self.provider_bbc,
            provider_content_id=pcid,
            proposed_tmdb_id=2002,
            proposed_content_type="movie",
        )
        manifest = planner.plan()
        inv = manifest["report"]["potentially_invalid_availability"]
        self.assertEqual(inv["stale_offer_identities_count"], 0)
        self.assertFalse(inv["stale_offers"][0]["identity_diverges"])
        self.assertFalse(inv["stale_offers"][0]["reconciliation_mapping_doubt_risk"])

    def test_conflicting_content_types_and_shared_offers(self):
        pcid = "bbc-prog-series"
        # Proposed is series, existing was movie
        ContentMapping.objects.create(
            provider=self.provider_bbc,
            source=self.source_bbc,
            provider_content_id=pcid,
            content_type=ContentType.MOVIE,
            tmdb_id=1001,
            confidence=0.7,
            match_method=MatchMethod.TITLE_YEAR,
        )
        offer_id = build_offer_identity(
            self.provider_bbc.slug, "GB", ContentType.MOVIE, 1001, None, None, OfferType.FREE, pcid
        )
        Offer.objects.create(
            provider=self.provider_bbc,
            country=self.country_gb,
            source=self.source_bbc,
            content_type=ContentType.MOVIE,
            tmdb_id=1001,
            offer_type=OfferType.FREE,
            provider_content_id=pcid,
            identity=offer_id,
            currently_available=True,
        )
        planner = MappingCorrectionImpactPlanner(
            provider=self.provider_bbc,
            provider_content_id=pcid,
            proposed_tmdb_id=2002,
            proposed_content_type="series",
        )
        manifest = planner.plan()
        inv = manifest["report"]["potentially_invalid_availability"]
        self.assertEqual(inv["stale_offer_identities_count"], 1)
        self.assertEqual(inv["stale_offers"][0]["current_content_type"], "movie")
        self.assertTrue(inv["stale_offers"][0]["identity_diverges"])

    def test_invalid_planner_inputs(self):
        with self.assertRaises(MappingCorrectionPlannerError):
            MappingCorrectionImpactPlanner(
                provider="non_existent_provider",
                provider_content_id="123",
                proposed_tmdb_id=2002,
                proposed_content_type="movie",
            ).plan()

        with self.assertRaises(MappingCorrectionPlannerError):
            MappingCorrectionImpactPlanner(
                provider="bbc_iplayer",
                provider_content_id="123",
                proposed_tmdb_id=2002,
                proposed_content_type="invalid_type",
            )

        with self.assertRaises(MappingCorrectionPlannerError):
            MappingCorrectionImpactPlanner(
                provider="bbc_iplayer",
                provider_content_id="   ",
                proposed_tmdb_id=2002,
                proposed_content_type="movie",
            )

        with self.assertRaises(MappingCorrectionPlannerError):
            MappingCorrectionImpactPlanner(
                provider="bbc_iplayer",
                provider_content_id="123",
                proposed_tmdb_id="not_an_int",  # type: ignore
                proposed_content_type="movie",
            )


class PreconditionFingerprintTests(BasePlannerTestCase):

    def test_fingerprint_changes_on_material_data_modification(self):
        pcid = "bbc-prog-fp"
        m = ContentMapping.objects.create(
            provider=self.provider_bbc,
            source=self.source_bbc,
            provider_content_id=pcid,
            content_type=ContentType.MOVIE,
            tmdb_id=1001,
            confidence=0.7,
            match_method=MatchMethod.TITLE_YEAR,
        )
        planner = MappingCorrectionImpactPlanner(
            provider="bbc_iplayer",
            provider_content_id=pcid,
            proposed_tmdb_id=2002,
            proposed_content_type="movie",
        )
        fp1 = planner.plan()["precondition_fingerprint"]

        # 1. Same database state yields identical fingerprint
        fp2 = planner.plan()["precondition_fingerprint"]
        self.assertEqual(fp1, fp2)

        # 2. Material change to mapping changes fingerprint
        m.confidence = 0.95
        m.save()
        fp3 = planner.plan()["precondition_fingerprint"]
        self.assertNotEqual(fp1, fp3)

        # 3. Adding an offer changes fingerprint
        Offer.objects.create(
            provider=self.provider_bbc,
            country=self.country_gb,
            source=self.source_bbc,
            content_type=ContentType.MOVIE,
            tmdb_id=1001,
            offer_type=OfferType.FREE,
            provider_content_id=pcid,
            identity=build_offer_identity(
                self.provider_bbc.slug, "GB", ContentType.MOVIE, 1001, None, None, OfferType.FREE, pcid
            ),
        )
        fp4 = planner.plan()["precondition_fingerprint"]
        self.assertNotEqual(fp3, fp4)

    def test_fingerprint_includes_all_corroboration_sources_and_states(self):
        """Every corroborating query result must invalidate the plan when changed."""
        planner = MappingCorrectionImpactPlanner(
            provider=self.provider_bbc,
            provider_content_id="bbc-target",
            proposed_tmdb_id=2002,
            proposed_content_type="movie",
        )
        fingerprints = []

        def fingerprint():
            value = planner.plan()["precondition_fingerprint"]
            fingerprints.append(value)
            return value

        baseline = fingerprint()
        other_provider_offer = Offer.objects.create(
            provider=self.provider_tubi,
            country=self.country_us,
            source=self.source_tubi,
            content_type=ContentType.MOVIE,
            tmdb_id=2002,
            offer_type=OfferType.FREE_WITH_ADS,
            provider_content_id="tubi-target",
            identity=build_offer_identity(
                "tubi", "US", ContentType.MOVIE, 2002, None, None,
                OfferType.FREE_WITH_ADS, "tubi-target"
            ),
        )
        after_other_provider = fingerprint()
        self.assertNotEqual(baseline, after_other_provider)

        same_provider_offer = Offer.objects.create(
            provider=self.provider_bbc,
            country=self.country_gb,
            source=self.source_bbc,
            content_type=ContentType.MOVIE,
            tmdb_id=2002,
            offer_type=OfferType.FREE,
            provider_content_id="bbc-alternate",
            identity=build_offer_identity(
                "bbc_iplayer", "GB", ContentType.MOVIE, 2002, None, None,
                OfferType.FREE, "bbc-alternate"
            ),
        )
        after_same_provider = fingerprint()
        self.assertNotEqual(after_other_provider, after_same_provider)

        evidence = OfferEvidence.objects.create(
            source=self.source_sa,
            offer_identity=other_provider_offer.identity,
            provider_slug="tubi",
            country_code="US",
            content_type=ContentType.MOVIE,
            tmdb_id=2002,
            offer_type=OfferType.FREE_WITH_ADS,
            provider_content_id="tubi-target",
            is_present=True,
            authoritative=False,
        )
        after_present_evidence = fingerprint()
        self.assertNotEqual(after_same_provider, after_present_evidence)

        evidence.is_present = False
        evidence.authoritative = True
        evidence.save(update_fields=["is_present", "authoritative", "updated_at"])
        after_authoritative_removal = fingerprint()
        self.assertNotEqual(after_present_evidence, after_authoritative_removal)

        proposed_mapping = ContentMapping.objects.create(
            provider=self.provider_tubi,
            source=self.source_tubi,
            provider_content_id="tubi-target",
            content_type=ContentType.MOVIE,
            tmdb_id=2002,
            confidence=0.9,
            match_method=MatchMethod.TITLE_YEAR,
        )
        after_mapping = fingerprint()
        self.assertNotEqual(after_authoritative_removal, after_mapping)

        proposed_mapping.delete()
        after_mapping_removal = fingerprint()
        self.assertNotEqual(after_mapping, after_mapping_removal)

        evidence.delete()
        after_evidence_removal = fingerprint()
        self.assertNotEqual(after_mapping_removal, after_evidence_removal)

        self.assertEqual(len(set(fingerprints)), 6)
        self.assertIsNotNone(same_provider_offer)
        self.assertIsNotNone(proposed_mapping)

    def test_fingerprint_is_invariant_to_corroboration_query_order(self):
        planner = MappingCorrectionImpactPlanner(
            provider=self.provider_bbc,
            provider_content_id="bbc-order",
            proposed_tmdb_id=2002,
            proposed_content_type="movie",
        )
        for provider_content_id, source, country, provider in (
            ("tubi-two", self.source_tubi, self.country_us, self.provider_tubi),
            ("tubi-one", self.source_tubi, self.country_us, self.provider_tubi),
        ):
            Offer.objects.create(
                provider=provider,
                country=country,
                source=source,
                content_type=ContentType.MOVIE,
                tmdb_id=2002,
                offer_type=OfferType.FREE,
                provider_content_id=provider_content_id,
                identity=build_offer_identity(
                    provider.slug, country.code, ContentType.MOVIE, 2002, None, None,
                    OfferType.FREE, provider_content_id
                ),
            )
        first = planner.plan()["precondition_fingerprint"]
        Offer.objects.filter(provider_content_id="tubi-two").update(provider_content_id="tubi-zero")
        Offer.objects.filter(provider_content_id="tubi-zero").update(provider_content_id="tubi-two")
        second = planner.plan()["precondition_fingerprint"]
        self.assertEqual(first, second)

    def test_canonical_fingerprint_row_sorting_is_order_invariant(self):
        first = {"source": "one", "is_present": True, "authoritative": False}
        second = {"source": "two", "is_present": False, "authoritative": True}
        self.assertEqual(
            _sorted_fingerprint_rows([first, second]),
            _sorted_fingerprint_rows([second, first]),
        )
        self.assertEqual(
            canonical_json_bytes({"rows": _sorted_fingerprint_rows([first, second])}),
            canonical_json_bytes({"rows": _sorted_fingerprint_rows([second, first])}),
        )

    def test_digest_excludes_volatile_metadata(self):
        pcid = "bbc-prog-digest"
        planner = MappingCorrectionImpactPlanner(
            provider="bbc_iplayer",
            provider_content_id=pcid,
            proposed_tmdb_id=2002,
            proposed_content_type="movie",
        )
        manifest1 = planner.plan(now=datetime(2026, 1, 1, 12, 0, tzinfo=dt_timezone.utc))
        manifest2 = planner.plan(now=datetime(2026, 1, 2, 15, 30, tzinfo=dt_timezone.utc))

        # Timestamps differ
        self.assertNotEqual(manifest1["generated_at"], manifest2["generated_at"])
        # But canonical digest is reproducible and equal
        self.assertEqual(manifest1["digest"], manifest2["digest"])
        # Self-digest omission check
        self.assertNotIn("digest", manifest1["digest"])


class StrictNoWriteRegressionTests(BasePlannerTestCase):

    def _snapshot_database_state(self) -> dict:
        """Capture row counts and IDs across all relevant availability and tracker tables."""
        return {
            "ContentMapping": list(ContentMapping.objects.order_by("id").values()),
            "ProviderContent": list(ProviderContent.objects.order_by("id").values()),
            "Offer": list(Offer.objects.order_by("id").values()),
            "OfferEvidence": list(OfferEvidence.objects.order_by("id").values()),
            "OfferPrice": list(OfferPrice.objects.order_by("id").values()),
            "Movie": list(Movie.objects.order_by("id").values()),
            "Provider": list(Provider.objects.order_by("id").values()),
            "Country": list(Country.objects.order_by("id").values()),
            "Source": list(Source.objects.order_by("id").values()),
        }

    def test_planner_and_command_perform_zero_writes(self):
        pcid = "bbc-prog-nowrite"
        pc = ProviderContent.objects.create(
            provider=self.provider_bbc,
            source=self.source_bbc,
            provider_content_id=pcid,
            content_type=ContentType.MOVIE,
            title="Safe Title",
            identity=build_provider_content_identity(
                self.provider_bbc.slug, pcid, ContentType.MOVIE
            ),
        )
        ContentMapping.objects.create(
            provider=self.provider_bbc,
            source=self.source_bbc,
            provider_content_id=pcid,
            content_type=ContentType.MOVIE,
            tmdb_id=1001,
            content=pc,
            confidence=0.7,
            match_method=MatchMethod.TITLE_YEAR,
        )
        offer_id = build_offer_identity(
            self.provider_bbc.slug, "GB", ContentType.MOVIE, 1001, None, None, OfferType.FREE, pcid
        )
        Offer.objects.create(
            provider=self.provider_bbc,
            country=self.country_gb,
            source=self.source_bbc,
            content_type=ContentType.MOVIE,
            tmdb_id=1001,
            offer_type=OfferType.FREE,
            provider_content_id=pcid,
            identity=offer_id,
        )

        before_state = self._snapshot_database_state()

        # Spy on all mutation methods
        with patch.object(ContentMapping, "save") as mock_cm_save, \
             patch.object(ContentMapping, "delete") as mock_cm_del, \
             patch.object(Offer, "save") as mock_o_save, \
             patch.object(Offer, "delete") as mock_o_del, \
             patch.object(OfferEvidence, "save") as mock_ev_save, \
             patch.object(OfferPrice, "save") as mock_p_save:

            planner = MappingCorrectionImpactPlanner(
                provider="bbc_iplayer",
                provider_content_id=pcid,
                proposed_tmdb_id=2002,
                proposed_content_type="movie",
            )
            manifest = planner.plan()
            self.assertIsNotNone(manifest)

            # Also run management command with --write-manifest to ensure file writes
            # do not write to the database
            with tempfile.TemporaryDirectory() as tmpdir:
                manifest_path = Path(tmpdir) / "test-manifest.json"
                out = io.StringIO()
                call_command(
                    "plan_mapping_correction",
                    provider="bbc_iplayer",
                    provider_content_id=pcid,
                    tmdb_id=2002,
                    content_type="movie",
                    write_manifest=str(manifest_path),
                    stdout=out,
                )
                self.assertTrue(manifest_path.exists())

            mock_cm_save.assert_not_called()
            mock_cm_del.assert_not_called()
            mock_o_save.assert_not_called()
            mock_o_del.assert_not_called()
            mock_ev_save.assert_not_called()
            mock_p_save.assert_not_called()

        after_state = self._snapshot_database_state()
        self.assertEqual(before_state, after_state)

    def test_zero_network_calls_during_planning(self):
        pcid = "bbc-prog-nonetwork"
        with patch.object(socket, "socket", side_effect=AssertionError("Network socket opened")):
            planner = MappingCorrectionImpactPlanner(
                provider="bbc_iplayer",
                provider_content_id=pcid,
                proposed_tmdb_id=2002,
                proposed_content_type="movie",
            )
            manifest = planner.plan()
            self.assertIsNotNone(manifest)


class ManagementCommandTests(BasePlannerTestCase):

    def test_command_human_readable_output(self):
        out = io.StringIO()
        call_command(
            "plan_mapping_correction",
            provider="bbc_iplayer",
            provider_content_id="prog-1",
            tmdb_id=2002,
            content_type="movie",
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn("MAPPING CORRECTION IMPACT PLANNER REPORT", output)
        self.assertIn("Digest", output)
        self.assertIn("Precondition Fingerprint", output)
        self.assertIn("No database rows were changed", output)

    def test_command_json_output(self):
        out = io.StringIO()
        call_command(
            "plan_mapping_correction",
            provider="bbc_iplayer",
            provider_content_id="prog-1",
            tmdb_id=2002,
            content_type="movie",
            json=True,
            stdout=out,
        )
        output = out.getvalue()
        data = json.loads(output)
        self.assertEqual(data["schema"], MANIFEST_SCHEMA)
        self.assertEqual(data["inputs"]["provider"], "bbc_iplayer")
        self.assertEqual(data["inputs"]["proposed_tmdb_id"], 2002)

    def test_command_write_manifest_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "manifest.json"
            out = io.StringIO()
            call_command(
                "plan_mapping_correction",
                provider="bbc_iplayer",
                provider_content_id="prog-1",
                tmdb_id=2002,
                content_type="movie",
                write_manifest=str(file_path),
                stdout=out,
            )
            self.assertTrue(file_path.exists())
            loaded = json.loads(file_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["schema"], MANIFEST_SCHEMA)

    def test_command_validation_failures(self):
        with self.assertRaises(CommandError):
            call_command(
                "plan_mapping_correction",
                provider="non_existent",
                provider_content_id="prog-1",
                tmdb_id=2002,
                content_type="movie",
            )
