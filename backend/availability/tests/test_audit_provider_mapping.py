"""Regression coverage for the read-only provider mapping audit."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from availability.models import (
    ContentMapping,
    Country,
    Offer,
    OfferEvidence,
    OfferPrice,
    ProviderContent,
    Source,
    SyncCursor,
)
from tracker.models import Movie, Provider, ScrapedContentMapping


class AuditProviderMappingTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(slug="audit-provider", name="Audit Provider")
        self.source = Source.objects.create(slug="audit-source", name="Audit Source")
        self.country, _ = Country.objects.get_or_create(code="US", defaults={"name": "United States"})

    def content(self, pcid, content_type, title, year=None):
        return ProviderContent.objects.create(
            provider=self.provider,
            source=self.source,
            provider_content_id=pcid,
            content_type=content_type,
            title=title,
            release_date=date(year, 1, 1) if year else None,
        )

    def movie(self, tmdb_id, content_type, title, year):
        return Movie.objects.create(
            tmdb_id=tmdb_id,
            content_type=content_type,
            title=title,
            year=year,
        )

    def audit(self, *, snapshot=False):
        stdout = StringIO()
        args = ["audit_provider_mapping", "--provider", self.provider.slug, "--format", "json"]
        if snapshot:
            args.append("--snapshot")
        call_command(*args, stdout=stdout)
        return json.loads(stdout.getvalue())

    def test_reports_mutually_exclusive_categories_using_local_matcher_semantics(self):
        self.movie(1, "movie", "Exact", 2020)
        self.movie(2, "movie", "Title only", 2019)
        self.movie(3, "series", "Series with catalog year", 2018)
        self.movie(4, "movie", "Ambiguous", 2001)
        self.movie(5, "movie", "Ambiguous", 2002)
        self.movie(6, "movie", "Conflicting year", 1999)
        self.movie(7, "series", "Wrong type", 2020)

        self.content("exact", "movie", "Exact", 2020)
        self.content("title-only", "movie", "Title only")
        self.content("series-title-only", "series", "Series with catalog year", 2024)
        self.content("ambiguous", "movie", "Ambiguous")
        self.content("year-conflict", "movie", "Conflicting year", 2020)
        self.content("type-conflict", "movie", "Wrong type", 2020)
        self.content("none", "movie", "Absent", 2020)
        self.content("ineligible", "episode", "Episode", 2020)

        report = self.audit()
        analysis = report["local_catalog_match_analysis"]

        self.assertEqual(report["records_without_content_mapping"], 8)
        self.assertEqual(report["locally_matchable_without_stored_mapping"], 3)
        self.assertEqual(analysis["unique_exact_title_year_matches"], 1)
        self.assertEqual(analysis["unique_title_only_matches"], 2)
        self.assertEqual(analysis["ambiguous_titles"], 1)
        self.assertEqual(analysis["conflicting_years"], 1)
        self.assertEqual(analysis["media_type_mismatches"], 1)
        self.assertEqual(analysis["no_candidate_in_local_tracker_movie"], 1)
        self.assertEqual(analysis["ineligible_for_local_mapping"], 1)
        self.assertEqual(sum(analysis.values()), 8)

    def test_groups_actual_mapping_method_confidence_combinations_without_inferring_origin(self):
        mapped = self.content("mapped", "movie", "Mapped", 2020)
        ContentMapping.objects.create(
            provider=self.provider, source=self.source, provider_content_id="mapped",
            content_type="movie", tmdb_id=101, content=mapped,
            match_method="title_year", confidence=1.0,
        )
        second = self.content("second", "movie", "Second", 2020)
        ContentMapping.objects.create(
            provider=self.provider, source=self.source, provider_content_id="second",
            content_type="movie", tmdb_id=102, content=second,
            match_method="manual", confidence=1.0,
        )

        report = self.audit()

        self.assertEqual(
            report["content_mappings_by_method_and_confidence"],
            {"manual": {"1.0": 1}, "title_year": {"1.0": 1}},
        )
        self.assertNotIn("valid_mappings_by_match_method", report)

    def test_counts_mapped_records_without_offers_once(self):
        mapped_without_offer = self.content("no-offer", "movie", "No Offer", 2020)
        mapped_with_offer = self.content("with-offer", "movie", "With Offer", 2020)
        for pc, tmdb_id in ((mapped_without_offer, 100), (mapped_with_offer, 101)):
            ContentMapping.objects.create(
                provider=self.provider, source=self.source,
                provider_content_id=pc.provider_content_id, content_type="movie",
                tmdb_id=tmdb_id, content=pc, match_method="manual", confidence=1.0,
            )
        Offer.objects.create(
            provider=self.provider, country=self.country, source=self.source,
            content_type="movie", tmdb_id=101, offer_type="free",
            provider_content_id="with-offer",
        )

        self.assertEqual(self.audit()["records_with_mapping_but_no_offer"], 1)

    def test_reports_only_actual_mismatch_candidates(self):
        from tracker.models import Movie as LocalMovie
        # local catalog row backing both mappings
        LocalMovie.objects.create(tmdb_id=1, title="Matched Title", year=2020)
        LocalMovie.objects.create(tmdb_id=2, title="Different Title", year=2020)

        agreeing = self.content("agreeing", "movie", "Matched Title", 2020)
        ContentMapping.objects.create(
            provider=self.provider, source=self.source, provider_content_id="agreeing",
            content_type="movie", tmdb_id=1, content=agreeing,
            match_method="manual", confidence=1.0,
        )
        type_conflict = self.content("type-conflict", "movie", "Matched Title", 2020)
        ContentMapping.objects.create(
            provider=self.provider, source=self.source, provider_content_id="type-conflict",
            content_type="series", tmdb_id=1, content=type_conflict,
            match_method="manual", confidence=1.0,
        )
        title_conflict = self.content("title-conflict", "movie", "Totally Different", 2020)
        ContentMapping.objects.create(
            provider=self.provider, source=self.source, provider_content_id="title-conflict",
            content_type="movie", tmdb_id=2, content=title_conflict,
            match_method="manual", confidence=1.0,
        )

        mismatches = self.audit()["mismatch_candidates"]

        by_pcid = {m["provider_content_id"]: m for m in mismatches}
        self.assertEqual(set(by_pcid), {"type-conflict", "title-conflict"})
        self.assertEqual(by_pcid["type-conflict"]["mismatch_reasons"], ["content_type"])
        self.assertEqual(by_pcid["title-conflict"]["mismatch_reasons"], ["title"])

    def test_snapshot_hashes_every_provider_scoped_audit_model_without_writing(self):
        pc = self.content("snapshot", "movie", "Snapshot", 2020)
        ContentMapping.objects.create(
            provider=self.provider, source=self.source, provider_content_id="snapshot",
            content_type="movie", tmdb_id=1, content=pc, match_method="manual", confidence=1.0,
        )
        offer = Offer.objects.create(
            provider=self.provider, country=self.country, source=self.source,
            content_type="movie", tmdb_id=1, offer_type="free", provider_content_id="snapshot",
        )
        OfferEvidence.objects.create(
            source=self.source, offer_identity=offer.identity, provider_slug=self.provider.slug,
            country_code="US", content_type="movie", tmdb_id=1, offer_type="free",
            provider_content_id="snapshot",
        )
        OfferPrice.objects.create(
            source=self.source, offer_identity=offer.identity, provider_slug=self.provider.slug,
            country_code="US", content_type="movie", tmdb_id=1, offer_type="free",
            provider_content_id="snapshot", amount=Decimal("1.0000"), currency="USD",
        )
        SyncCursor.objects.create(provider=self.provider, source=self.source, country=self.country)
        ScrapedContentMapping.objects.create(provider=self.provider, scraped_title="Legacy")

        def provider_scoped_rows():
            return {
                "provider_content": list(
                    ProviderContent.objects.filter(provider=self.provider).order_by("pk").values()
                ),
                "content_mappings": list(
                    ContentMapping.objects.filter(provider=self.provider).order_by("pk").values()
                ),
                "offers": list(
                    Offer.objects.filter(provider=self.provider).order_by("pk").values()
                ),
                "offer_evidence": list(
                    OfferEvidence.objects.filter(provider_slug=self.provider.slug).order_by("pk").values()
                ),
                "offer_prices": list(
                    OfferPrice.objects.filter(provider_slug=self.provider.slug).order_by("pk").values()
                ),
                "legacy_scraped_content_mappings": list(
                    ScrapedContentMapping.objects.filter(provider=self.provider).order_by("pk").values()
                ),
                "sync_cursors": list(
                    SyncCursor.objects.filter(provider=self.provider).order_by("pk").values()
                ),
            }

        before = provider_scoped_rows()
        self.audit()
        self.assertEqual(before, provider_scoped_rows())

        snapshot = self.audit(snapshot=True)["snapshot"]

        self.assertEqual(
            set(snapshot),
            {
                "provider_content", "content_mappings", "offers", "offer_evidence",
                "offer_prices", "legacy_scraped_content_mappings", "sync_cursors",
            },
        )
        self.assertTrue(all(value["count"] == 1 and len(value["hash"]) == 64 for value in snapshot.values()))
        self.assertEqual(before, provider_scoped_rows())
