"""Association decisions must agree before and inside the atomic apply boundary."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from availability.models import ContentMapping, Offer, ProviderContent, Source, TmdbDetailsCache, TmdbSearchCache
from availability.tests.test_candidate_gateway import FakeResponse
from tracker.models import CanonicalBootstrapEvidence, DiscoveredCatalogueRecord, Movie
from tracker.models import Provider
from tracker.services.incremental_catalogue_reconciliation import _commit_record, run_incremental_reconciliation

MODULE = "tracker.services.incremental_catalogue_reconciliation"


class ParityFixtures:
    def setUp(self):
        self.provider = Provider.objects.get_or_create(slug="hydrahd", defaults={"name": "HydraHD"})[0]
        self.record = DiscoveredCatalogueRecord.objects.create(
            provider=self.provider, provider_native_id="parity-film", title="Parity Film",
            source_url="https://fixtures.invalid/parity-film",
            original_metadata={"catalogue_partition": "movies", "year": 2020})
        self.source = Source.objects.create(slug="parity-fixture", name="Fixture", enabled=False)

    def cache(self, tmdb_id=991, *, candidates=None, **details):
        TmdbSearchCache.objects.update_or_create(query="parity film", year_fingerprint="year:2020",
            content_type="movie", defaults={"year": 2020, "is_negative": candidates == [],
                "results": candidates if candidates is not None else [
                    {"tmdb_id": tmdb_id, "title": self.record.title, "year": 2020, "content_type": "movie"}],
                "expires_at": timezone.now() + timedelta(days=1)})
        TmdbDetailsCache.objects.update_or_create(tmdb_id=tmdb_id, content_type="movie", defaults={
            "details": {"tmdb_id": tmdb_id, "title": self.record.title, "year": 2020,
                        "content_type": "movie", **details},
            "expires_at": timezone.now() + timedelta(days=1)})

    def mapping(self, tmdb_id=991, media="movie"):
        movie = Movie.objects.get_or_create(tmdb_id=tmdb_id, content_type=media,
            defaults={"title": self.record.title, "year": 2020, "overview": "Curated canonical metadata"})[0]
        content = ProviderContent.objects.create(provider=self.provider, provider_content_id="parity-film",
            content_type=media, title=self.record.title, currently_available=False)
        return ContentMapping.objects.create(provider=self.provider, source=self.source,
            provider_content_id="parity-film", content_type=media, tmdb_id=movie.tmdb_id, content=content)

    def job(self, *, apply=False, **kwargs):
        return run_incremental_reconciliation("hydrahd", apply=apply, interval=0, retries=0,
            record_ids=[self.record.pk], limit=1, **kwargs)

    def snapshot(self):
        return {m._meta.label: list(m.objects.order_by("pk").values()) for m in apps.get_models()
                if m._meta.app_label in {"tracker", "availability"}}

    def canonical_rows(self):
        return {m.__name__: list(m.objects.order_by("pk").values())
                for m in (Movie, ContentMapping, CanonicalBootstrapEvidence)}

    def assert_pair(self, status, **kwargs):
        before = self.snapshot()
        preview = self.job(**kwargs)
        self.assertEqual(preview["records"][0]["status"], status)
        self.assertEqual(self.snapshot(), before, "Preview must not write any application table")
        applied = self.job(apply=True, **kwargs)
        self.assertEqual(applied["records"][0]["status"], "ASSOCIATED" if status == "VERIFIED" else status)
        self.assertEqual(preview["tmdb_budget"], applied["tmdb_budget"])
        self.assertFalse(Offer.objects.exists())
        return preview["records"][0], applied["records"][0]


@override_settings(TMDB_API_KEY="fixture-only")
class ReconciliationParityTests(ParityFixtures, TestCase):
    def test_same_association_is_reusable_without_rewriting_proof_or_metadata(self):
        self.cache()
        self.job(apply=True)
        before = self.canonical_rows()
        self.assert_pair("VERIFIED", review=True)
        self.assertEqual(self.canonical_rows(), before)

    def test_conflicting_association_is_rejected_in_preview_and_apply(self):
        self.mapping(992)
        self.cache(991)
        before = self.snapshot()
        preview, applied = self.assert_pair("CONFLICTING")
        self.assertEqual(preview["action"], "existing_canonical_mapping_conflict")
        self.assertEqual(preview["evidence"], applied["evidence"])
        after = self.snapshot()
        for model in ("tracker.Movie", "tracker.CanonicalBootstrapEvidence", "availability.ContentMapping",
                      "availability.ProviderContent"):
            self.assertEqual(after[model], before[model])

    def test_numeric_movie_tv_collision_does_not_merge_typed_identities(self):
        Movie.objects.create(tmdb_id=991, content_type="series", title="A Different Series", year=2020)
        self.cache()
        self.assert_pair("VERIFIED")
        self.assertEqual(set(Movie.objects.filter(tmdb_id=991).values_list("content_type", flat=True)),
                         {"movie", "series"})
        self.assertEqual(ContentMapping.objects.get().content_type, "movie")

    def test_existing_series_mapping_cannot_be_reused_as_movie(self):
        self.mapping(991, "series")
        self.cache(991)
        before = self.canonical_rows()
        self.assert_pair("CONFLICTING")
        self.assertEqual(self.canonical_rows(), before)

    def test_contradictory_provider_content_link_is_preserved_for_review(self):
        mapping = self.mapping()
        unrelated = ProviderContent.objects.create(provider=self.provider, provider_content_id="different-native",
            content_type="series", title="Different native identity", currently_available=False)
        ContentMapping.objects.filter(pk=mapping.pk).update(content=unrelated)
        self.cache()
        before = self.snapshot()
        preview, _ = self.assert_pair("CONFLICTING")
        self.assertEqual(preview["action"], "existing_provider_content_conflict")
        after = self.snapshot()
        self.assertEqual(after["availability.ProviderContent"], before["availability.ProviderContent"])
        self.assertEqual(after["availability.ContentMapping"], before["availability.ContentMapping"])

    def test_existing_proof_cannot_be_rebound_to_another_identity(self):
        self.cache(); self.job(apply=True)
        ContentMapping.objects.all().delete()
        self.cache(992)
        self.record.refresh_from_db()
        self.record.original_metadata["overview"] = "Changed source evidence"
        self.record.save()
        before = self.canonical_rows()
        preview, _ = self.assert_pair("CONFLICTING")
        self.assertEqual(preview["action"], "existing_verification_identity_conflict")
        self.assertEqual(self.canonical_rows(), before)

    def test_internally_contradictory_proof_is_not_silently_refreshed(self):
        self.cache(); self.job(apply=True)
        proof = CanonicalBootstrapEvidence.objects.get()
        proof.evidence["tmdb_id"] = 992
        proof.save()
        before = self.canonical_rows()
        preview, _ = self.assert_pair("CONFLICTING", review=True)
        self.assertEqual(preview["action"], "contradictory_verification_evidence")
        self.assertEqual(self.canonical_rows(), before)

    def test_trusted_id_change_cannot_overwrite_established_mapping_and_proof(self):
        self.cache(); self.job(apply=True)
        self.record.refresh_from_db()
        self.record.original_metadata.update(external_ids={"tmdb": "992"}, external_ids_trusted=True)
        self.record.save()
        self.cache(992)
        before = self.canonical_rows()
        self.assert_pair("CONFLICTING")
        self.assertEqual(self.canonical_rows(), before)
        self.record.refresh_from_db()
        self.assertEqual(self.record.original_metadata["external_ids"], {"tmdb": "992"})

    def test_ambiguous_candidates_remain_ambiguous_in_both_modes(self):
        self.cache(candidates=[{"tmdb_id": pk, "title": "Parity Film", "year": 2020, "content_type": "movie"}
                               for pk in (991, 992)])
        before = self.canonical_rows()
        self.assert_pair("AMBIGUOUS")
        self.assertEqual(self.canonical_rows(), before)

    def test_unmatched_records_remain_unmatched_in_both_modes(self):
        self.cache(candidates=[])
        self.assert_pair("UNMATCHED")
        self.assertFalse(ContentMapping.objects.exists())
        self.assertFalse(CanonicalBootstrapEvidence.objects.exists())

    def test_replay_does_not_change_completed_records_or_associations(self):
        self.cache(); self.job(apply=True)
        before = self.snapshot()
        self.assertEqual(self.job()["selected"], 0)
        self.assertEqual(self.job(apply=True)["selected"], 0)
        after = self.snapshot()
        for model in before:
            if model != "tracker.CatalogueReconciliationRun":
                self.assertEqual(before[model], after[model], model)

    def test_rejected_network_proposals_keep_all_attempts_and_no_preview_cache_writes(self):
        self.mapping(992)
        def responses():
            return [FakeResponse(200, {"results": [{"id": 991, "title": "Parity Film", "release_date": "2020-01-01"}]}),
                    FakeResponse(200, {"id": 991, "title": "Parity Film", "release_date": "2020-01-01"})]
        before = self.snapshot()
        with patch("requests.Session.get", side_effect=responses()) as http:
            preview = self.job(allow_network=True, request_budget=2)
        self.assertEqual(http.call_count, 2)
        self.assertEqual(self.snapshot(), before)
        with patch("requests.Session.get", side_effect=responses()) as http:
            applied = self.job(apply=True, allow_network=True, request_budget=2)
        self.assertEqual(http.call_count, 2)
        for result in (preview, applied):
            self.assertEqual(result["records"][0]["status"], "CONFLICTING")
            self.assertEqual(result["records"][0]["tmdb_requests"], 2)
            self.assertEqual(result["tmdb_budget"], {"limit": 2, "attempted": 2, "remaining": 0,
                             "succeeded": 2, "failed": 0, "retried": 0, "cache_hits": 0})
        self.assertFalse(CanonicalBootstrapEvidence.objects.exists())

    def test_apply_detects_proof_change_after_initial_validation(self):
        self.cache(); self.job(apply=True)
        changed = Movie.objects.create(tmdb_id=992, content_type="movie", title="Operator target", year=2020)
        def change_proof(*args, **kwargs):
            CanonicalBootstrapEvidence.objects.update(movie=changed)
            return _commit_record(*args, **kwargs)
        with patch(f"{MODULE}._commit_record", side_effect=change_proof):
            result = self.job(apply=True, review=True)
        self.assertEqual(result["records"][0]["status"], "CONFLICTING")
        self.assertEqual(CanonicalBootstrapEvidence.objects.get().movie_id, changed.pk)
        self.assertEqual(ContentMapping.objects.get().tmdb_id, 991)

    def test_apply_rechecks_canonical_year_after_candidate_verification(self):
        self.mapping(); self.cache()
        def change_year(*args, **kwargs):
            Movie.objects.filter(tmdb_id=991).update(year=2021)
            return _commit_record(*args, **kwargs)
        with patch(f"{MODULE}._commit_record", side_effect=change_year):
            result = self.job(apply=True)
        self.assertEqual(result["records"][0]["status"], "CONFLICTING")
        self.assertFalse(CanonicalBootstrapEvidence.objects.exists())
        self.assertEqual(Movie.objects.get().year, 2021)


@override_settings(TMDB_API_KEY="fixture-only")
class ReconciliationParityConcurrencyTests(ParityFixtures, TransactionTestCase):
    def test_independent_mapping_change_between_validation_and_commit_is_preserved(self):
        mapping = self.mapping(); self.cache()
        preview = self.job()
        self.assertEqual(preview["records"][0]["status"], "VERIFIED")
        Movie.objects.create(tmdb_id=992, content_type="movie", title="Operator target", year=2020)
        def change_mapping(*args, **kwargs):
            def operator():
                try:
                    return ContentMapping.objects.filter(pk=mapping.pk).update(tmdb_id=992)
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(operator).result(timeout=10), 1)
            return _commit_record(*args, **kwargs)
        with patch(f"{MODULE}._commit_record", side_effect=change_mapping):
            applied = self.job(apply=True)
        self.assertEqual(applied["records"][0]["status"], "CONFLICTING")
        self.assertEqual(applied["records"][0]["action"], "existing_canonical_mapping_conflict")
        self.assertEqual(ContentMapping.objects.get().tmdb_id, 992)
        self.assertFalse(CanonicalBootstrapEvidence.objects.exists())
        self.assertFalse(Offer.objects.exists())
