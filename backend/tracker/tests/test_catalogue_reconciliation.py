from unittest.mock import patch

from django.test import TestCase

from availability.models import ContentMapping, Offer, ProviderContent, Source, TmdbDetailsCache
from tracker.models import CanonicalBootstrapEvidence, DiscoveredCatalogueRecord, Movie, Provider, ScrapedContentMapping
from tracker.services.catalogue_reconciliation import _extract_year, reconcile_catalogue


class CatalogueReconciliationTests(TestCase):
    def setUp(self):
        self.hydra = Provider.objects.get_or_create(slug="hydrahd", defaults={"name": "HydraHD", "category": "scraper"})[0]
        self.anime = Provider.objects.get_or_create(slug="animeworld", defaults={"name": "AnimeWorld", "category": "scraper"})[0]

    def record(self, provider, title, metadata, *, url="https://provider.example/title/1", native_id=""):
        if url == "https://provider.example/title/1":
            url = f"https://provider.example/title/{title.lower().replace(' ', '-')}"
        return DiscoveredCatalogueRecord.objects.create(
            provider=provider, title=title, provider_native_id=native_id,
            source_url=url, identity_url=url, source_urls_seen=[url],
            original_metadata=metadata,
        )

    def test_classifies_only_supported_source_evidence_and_keeps_weak_hints_pending(self):
        movie = self.record(self.hydra, "Night Train", {"catalogue_partition": "movies", "year": 2024})
        series = self.record(self.hydra, "Long Road", {"catalogue_partition": "tv-shows", "year": 2020})
        ambiguous = self.record(self.anime, "Old Anime", {"catalogue_partition": "az-list", "listing_content_type_hint": "movie"})
        rss = self.record(self.anime, "A Series", {"source_kind": "official_rss", "anime_type": "anime"})

        report = reconcile_catalogue()
        proposals = {p["record_id"]: p for p in report["records"]}
        self.assertEqual(proposals[movie.pk]["content_type"], "movie")
        self.assertEqual(proposals[series.pk]["content_type"], "series")
        self.assertIsNone(proposals[ambiguous.pk]["content_type"])
        self.assertEqual(proposals[rss.pk]["content_type"], "series")
        self.assertEqual(report["mode"], "DRY_RUN")
        self.assertEqual(DiscoveredCatalogueRecord.objects.filter(classification_state="CLASSIFIED").count(), 0)

    def test_exact_title_year_and_type_is_high_confidence_but_mismatch_needs_review(self):
        Movie.objects.create(tmdb_id=101, title="Night Train", year=2024, content_type="movie")
        match = self.record(self.hydra, "Night Train", {"catalogue_partition": "movies", "year": 2024})
        year_conflict = self.record(self.hydra, "Night Train", {"catalogue_partition": "movies", "year": 2011}, url="https://provider.example/title/2")
        type_conflict = self.record(self.hydra, "Night Train", {"catalogue_partition": "tv-shows", "year": 2024}, url="https://provider.example/title/3")

        report = reconcile_catalogue()
        proposals = {p["record_id"]: p for p in report["records"]}
        self.assertEqual(proposals[match.pk]["match"]["status"], "MATCH_HIGH")
        self.assertEqual(proposals[match.pk]["match"]["confidence"], 0.99)
        self.assertEqual(proposals[year_conflict.pk]["match"]["status"], "POSSIBLE_REVIEW")
        self.assertEqual(proposals[type_conflict.pk]["match"]["status"], "POSSIBLE_REVIEW")

    def test_only_provenance_marked_external_identifier_is_verified(self):
        Movie.objects.create(tmdb_id=101, title="Night Train", year=2024, content_type="movie")
        trusted = self.record(self.hydra, "Other title", {
            "catalogue_partition": "movies", "year": 2024,
            "external_ids": {"tmdb": 101}, "external_ids_trusted": True,
        })
        untrusted = self.record(self.hydra, "Other title", {
            "catalogue_partition": "movies", "year": 2024,
            "external_ids": {"tmdb": 101},
        }, url="https://provider.example/title/untrusted")
        proposals = {row["record_id"]: row for row in reconcile_catalogue()["records"]}
        self.assertEqual(proposals[trusted.pk]["match"]["status"], "MATCH_VERIFIED")
        self.assertEqual(proposals[untrusted.pk]["match"]["status"], "POSSIBLE_REVIEW")

    def test_verified_tmdb_alternative_title_matches_only_with_year_and_type(self):
        movie = Movie.objects.create(tmdb_id=101, title="The Official Title", year=2024,
                                     content_type="movie")
        TmdbDetailsCache.objects.create(
            tmdb_id=101, content_type="movie",
            details={"tmdb_id": 101, "title": "The Official Title",
                     "original_title": "Titolo Alternativo", "year": 2024,
                     "content_type": "movie"},
        )
        record = self.record(self.hydra, "Titolo Alternativo",
                             {"catalogue_partition": "movies", "year": 2024})
        proposal = next(row for row in reconcile_catalogue()["records"]
                        if row["record_id"] == record.pk)
        self.assertEqual(proposal["match"]["status"], "MATCH_VERIFIED")
        self.assertEqual(proposal["match"]["candidates"][0]["movie_id"], movie.pk)

    def test_same_title_same_year_multiple_canonical_ids_require_review(self):
        Movie.objects.create(tmdb_id=201, title="The Nest", year=2025, content_type="movie")
        Movie.objects.create(tmdb_id=202, title="The Nest", year=2025, content_type="movie")
        record = self.record(self.hydra, "The Nest",
                             {"catalogue_partition": "movies", "year": 2025})
        proposal = next(row for row in reconcile_catalogue()["records"]
                        if row["record_id"] == record.pk)
        self.assertEqual(proposal["match"]["status"], "POSSIBLE_REVIEW")
        self.assertEqual(len(proposal["match"]["candidates"]), 2)

    def test_fuzzy_title_requires_year_type_and_matching_overview_evidence(self):
        movie = Movie.objects.create(tmdb_id=301, title="The Example Documentary", year=2024,
                                     content_type="movie")
        overview = "A careful documentary follows a small coastal community through a difficult winter season."
        TmdbDetailsCache.objects.create(
            tmdb_id=301, content_type="movie",
            details={"tmdb_id": 301, "title": movie.title, "year": 2024,
                     "content_type": "movie", "overview": overview},
        )
        record = self.record(self.hydra, "The Example Documentry", {
            "catalogue_partition": "movies", "year": 2024,
            "overview": overview,
        })
        proposal = next(row for row in reconcile_catalogue()["records"]
                        if row["record_id"] == record.pk)
        self.assertEqual(proposal["match"]["status"], "MATCH_HIGH")
        self.assertIn("overview token overlap", " ".join(proposal["match"]["evidence"]))
        self.assertEqual(proposal["match"]["candidates"][0]["movie_id"], movie.pk)

    def test_apply_preserves_provider_identity_and_url_without_creating_offers(self):
        Movie.objects.create(tmdb_id=101, title="Night Train", year=2024, content_type="movie")
        Source.objects.create(slug="hydrahd", name="HydraHD", type="web")
        record = self.record(
            self.hydra, "Night Train",
            {"catalogue_partition": "movies", "year": 2024},
            url="https://hydrahd.gd/movie/night-train", native_id="native-1",
        )
        CanonicalBootstrapEvidence.objects.create(
            discovered_record=record, movie=Movie.objects.get(tmdb_id=101),
            source_kind="tmdb_search_and_details", verification_method="exact_title_year_type",
            evidence={"verified": True},
        )

        report = reconcile_catalogue(apply=True)
        record.refresh_from_db()
        content = ProviderContent.objects.get(provider=self.hydra, provider_content_id="native-1")
        mapping = ContentMapping.objects.get(provider=self.hydra, provider_content_id="native-1")
        self.assertEqual(report["offers_created"], 0)
        self.assertEqual(record.classified_content_type, "movie")
        self.assertEqual(content.provider_url, record.source_url)
        self.assertEqual(content.currently_available, False)
        self.assertEqual(mapping.tmdb_id, 101)
        self.assertIn("TMDb search and details verified", mapping.match_evidence["evidence"][0])
        self.assertFalse(Offer.objects.exists())

    def test_exact_title_year_without_verified_evidence_is_not_applied_as_mapping(self):
        Movie.objects.create(tmdb_id=102, title="Night Train", year=2024, content_type="movie")
        Source.objects.create(slug="hydrahd", name="HydraHD", type="web")
        record = self.record(self.hydra, "Night Train",
                             {"catalogue_partition": "movies", "year": 2024},
                             url="https://hydrahd.gd/movie/night-train-proposed", native_id="proposed-1")

        report = reconcile_catalogue(apply=True)
        proposal = next(row for row in report["records"] if row["record_id"] == record.pk)
        self.assertEqual(proposal["match"]["status"], "MATCH_HIGH")
        self.assertFalse(ContentMapping.objects.filter(provider=self.hydra).exists())
        self.assertFalse(Offer.objects.exists())

    def test_one_apply_failure_does_not_block_other_classified_records(self):
        first = self.record(self.hydra, "First", {"catalogue_partition": "movies"})
        second = self.record(self.hydra, "Second", {"catalogue_partition": "movies"}, url="https://provider.example/title/second")
        with patch("tracker.services.catalogue_reconciliation._apply_classification", side_effect=[RuntimeError("row failure"), "provider_content_created"]) as apply:
            report = reconcile_catalogue(apply=True)
        proposals = {row["record_id"]: row for row in report["records"]}
        self.assertEqual(proposals[first.pk]["action"], "APPLY_FAILED")
        self.assertEqual(proposals[second.pk]["action"], "provider_content_created")
        self.assertEqual(apply.call_count, 2)

    def test_year_extraction_rejects_non_year_numeric_values(self):
        record = self.record(self.hydra, "Fractional", {"catalogue_partition": "movies", "year": 2024.5})
        self.assertIsNone(_extract_year(record)[0])

    def test_legacy_mapping_overlap_is_reported_as_source_evidence_not_canonical_match(self):
        url = "https://hydrahd.gd/movie/night-train"
        self.record(self.hydra, "Night Train", {"catalogue_partition": "movies", "year": 2024}, url=url)
        ScrapedContentMapping.objects.create(
            provider=self.hydra, scraped_title="Night Train", scraped_year=2024,
            scraped_content_type="movie", scraped_url=url,
        )
        report = reconcile_catalogue(provider_slugs=["hydrahd"])
        proposal = report["records"][0]
        self.assertEqual(proposal["match"]["status"], "UNMATCHED")
        self.assertEqual(proposal["legacy_mapping_candidates"][0]["identity_status"], "SAME_SOURCE_URL")
        self.assertEqual(report["legacy_mapping_overlaps"]["discovered_records_with_exact_source_url_match"], 1)
