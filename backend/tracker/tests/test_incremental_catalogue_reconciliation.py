import importlib
import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import requests
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from availability.models import ContentMapping, Offer, ProviderContent, TmdbDetailsCache, TmdbSearchCache
from availability.tests.test_candidate_gateway import FakeResponse
from tracker.models import (
    CanonicalBootstrapEvidence, CatalogueReconciliationRecord, CatalogueReconciliationRun,
    DiscoveredCatalogueRecord, Movie, Provider,
)
from tracker.scrapers.adapters import AnimeWorldScraper
from tracker.scrapers.base import ScrapedItem
from tracker.services.catalogue_discovery import _persist_unclassified_observation, run_discovery
from tracker.services.catalogue_reconciliation_inputs import fingerprint_v1
from tracker.services.incremental_catalogue_reconciliation import eligible_records, run_incremental_reconciliation

MODULE = "tracker.services.incremental_catalogue_reconciliation"


@override_settings(TMDB_API_KEY="fixture")
class IncrementalReconciliationTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.get_or_create(slug="hydrahd", defaults={"name": "HydraHD"})[0]
        self.anime = Provider.objects.get_or_create(slug="animeworld", defaults={"name": "AnimeWorld"})[0]

    def discovery(self, title="New Film", native_id="native-new", year=2020):
        item = ScrapedItem(title=title, provider_slug="hydrahd", year=year, content_type="movie",
                           source_url=f"https://hydrahd.gd/movie/{native_id}",
                           raw_payload={"id": native_id, "year": year})
        _persist_unclassified_observation(provider=self.provider, item=item, partition="movies")
        return DiscoveredCatalogueRecord.objects.get(provider=self.provider, title=title)

    def run_job(self, **kwargs):
        return run_incremental_reconciliation("hydrahd", apply=True, interval=0, **kwargs)

    def cache(self, record, tmdb_id=991, **details):
        media = "series" if record.provider == self.anime else "movie"
        year = record.original_metadata.get("year")
        TmdbSearchCache.objects.update_or_create(query=record.title.casefold(), year=year, content_type=media,
            defaults={"results": [{"tmdb_id": tmdb_id, "title": record.title, "year": year, "content_type": media}]})
        return TmdbDetailsCache.objects.update_or_create(tmdb_id=tmdb_id, content_type=media, defaults={
            "details": {"tmdb_id": tmdb_id, "title": record.title, "year": year, "content_type": media, **details},
            "expires_at": timezone.now() + timedelta(days=30),
        })[0]

    def test_new_hydra_discovery_flows_through_collector_and_replay(self):
        def page(scraper, slug, part, page, query=None):
            scraper.session.get("https://fixture.invalid/listing")
            return [ScrapedItem(title="New Film", provider_slug=slug, year=2020, content_type="movie",
                source_url="https://hydrahd.gd/movie/new", raw_payload={"id": "native-new", "year": 2020})], True, 1, "fixture"
        with patch("tracker.services.catalogue_discovery._parts", return_value=["movies"]), \
                patch("tracker.services.catalogue_discovery._page", side_effect=page), \
                patch("requests.Session.get", return_value=FakeResponse()):
            run_discovery("hydrahd", mode="incremental", request_budget=1, delay=0)
        record = DiscoveredCatalogueRecord.objects.get(provider=self.provider)
        self.assertEqual(record.classification_state, "PENDING")
        self.assertEqual([(r.pk, reason) for r, reason in eligible_records("hydrahd")], [(record.pk, "new_discovery")])
        search = FakeResponse(200, {"results": [{"id": 991, "title": "New Film", "release_date": "2020-01-01"}]})
        details = FakeResponse(200, {"id": 991, "title": "New Film", "release_date": "2020-01-01"})
        with patch("requests.Session.get", side_effect=[search, details]) as request:
            first = self.run_job(allow_network=True, request_budget=2)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(first["records"][0]["status"], "ASSOCIATED")
        mapping = ContentMapping.objects.get(provider=self.provider)
        self.assertEqual(mapping.provider_content_id, record.provider_native_id)
        self.assertEqual(mapping.tmdb_id, 991)
        with patch("requests.Session.get", side_effect=AssertionError("unexpected request")):
            replay = self.run_job(allow_network=True)
        self.assertEqual(replay["selected"], 0)
        self.assertEqual(ContentMapping.objects.count(), 1)
        self.assertEqual(CanonicalBootstrapEvidence.objects.count(), 1)
        self.assertEqual(ProviderContent.objects.count(), 1)
        self.assertFalse(ProviderContent.objects.get().currently_available)
        self.assertFalse(Offer.objects.exists())

    def test_animeworld_rss_guid_missing_year_and_later_evidence(self):
        xml = '''<rss xmlns:anime="https://www.animeworld.ac/"><channel><item>
        <guid isPermaLink="false">rss-9001</guid><title>Feed update</title>
        <anime:name>New Anime</anime:name><anime:link>https://www.animeworld.ac/play/new-anime</anime:link>
        <anime:type>anime</anime:type></item></channel></rss>'''
        item = AnimeWorldScraper().parse_rss(xml)[0]
        for _ in range(2):
            _persist_unclassified_observation(provider=self.anime, item=item, partition="rss-animes")
        record = DiscoveredCatalogueRecord.objects.get(provider=self.anime)
        self.assertEqual(record.provider_native_id, "rss-9001")
        with patch("requests.Session.get", side_effect=AssertionError("missing year must not trigger TMDb")):
            first = run_incremental_reconciliation("animeworld", apply=True)
            replay = run_incremental_reconciliation("animeworld", apply=True)
        self.assertEqual(first["records"][0]["status"], "UNMATCHED")
        self.assertEqual(replay["selected"], 0)
        record.refresh_from_db()
        self.assertEqual(record.classified_content_type, "series")
        self.assertFalse(ContentMapping.objects.exists())
        record.original_metadata["year"] = 2020
        record.save()
        self.cache(record)
        result = run_incremental_reconciliation("animeworld", apply=True)
        self.assertEqual(result["records"][0]["status"], "ASSOCIATED")
        self.assertEqual(ContentMapping.objects.get().content_type, "series")
        self.assertFalse(Offer.objects.exists())

    def test_unknown_anime_type_stays_explicitly_ambiguous(self):
        record = DiscoveredCatalogueRecord.objects.create(provider=self.anime, title="Unknown Type",
            source_url="https://www.animeworld.ac/play/unknown", provider_native_id="rss-unknown",
            original_metadata={"source_kind": "official_rss", "anime_type": "unknown", "year": 2020})
        with patch("requests.Session.get", side_effect=AssertionError("unknown type")):
            result = run_incremental_reconciliation("animeworld", apply=True, allow_network=True)
        self.assertEqual(result["records"][0]["status"], "AMBIGUOUS")
        self.assertEqual(record.classification_state, "PENDING")
        self.assertFalse(ContentMapping.objects.exists())

    def test_only_meaningful_metadata_changes_trigger_reconciliation(self):
        record = self.discovery()
        self.cache(record)
        self.run_job()
        record.refresh_from_db()
        record.original_metadata.update(listing_page=25, source_urls_seen=["noise"], feed_last_build_date="later")
        record.last_seen_at = timezone.now()
        record.save()
        self.assertEqual(list(eligible_records("hydrahd")), [])
        record.original_metadata["external_ids_trusted"] = True
        record.save()
        self.assertEqual(list(eligible_records("hydrahd"))[0][1], "meaningful_metadata_changed")

    def test_changed_title_cannot_replace_existing_canonical_association(self):
        record = self.discovery()
        self.cache(record)
        self.run_job()
        mapping_before = list(ContentMapping.objects.values())
        evidence_before = list(CanonicalBootstrapEvidence.objects.values())
        record.refresh_from_db()
        record.title = "Different Film"
        record.save()
        self.cache(record, tmdb_id=992)
        result = self.run_job()
        self.assertEqual(result["records"][0]["status"], "CONFLICTING")
        self.assertEqual(list(ContentMapping.objects.values()), mapping_before)
        self.assertEqual(list(CanonicalBootstrapEvidence.objects.values()), evidence_before)
        self.assertFalse(Movie.objects.filter(tmdb_id=992).exists())
        self.assertEqual(self.run_job()["selected"], 0)

    def test_same_identity_can_be_reverified_after_meaningful_edit(self):
        record = self.discovery()
        self.cache(record)
        self.run_job()
        record.refresh_from_db()
        record.original_metadata["overview"] = "New source summary"
        record.save()
        result = self.run_job()
        self.assertEqual(result["records"][0]["status"], "ASSOCIATED")
        self.assertEqual(ContentMapping.objects.count(), 1)
        self.assertEqual(CanonicalBootstrapEvidence.objects.get().evidence["input_fingerprint"], fingerprint_v1(record))

    def test_existing_canonical_identity_and_fresh_details_need_no_search(self):
        record = self.discovery()
        movie = Movie.objects.create(tmdb_id=991, content_type="movie", year=2020, title=record.title, overview="Curated")
        self.cache(record)
        TmdbSearchCache.objects.all().delete()
        with patch("requests.Session.get", side_effect=AssertionError("cached canonical identity")):
            result = self.run_job()
        self.assertEqual(result["records"][0]["status"], "ASSOCIATED")
        self.assertEqual(result["tmdb_budget"]["attempted"], 0)
        movie.refresh_from_db()
        self.assertEqual(movie.overview, "Curated")
        self.assertEqual(Movie.objects.count(), 1)

    def test_expired_cache_and_expired_proof_cannot_authorize_association(self):
        record = self.discovery()
        self.cache(record)
        self.run_job()
        ContentMapping.objects.all().delete()
        CanonicalBootstrapEvidence.objects.update(verified_at=timezone.now() - timedelta(days=31))
        TmdbSearchCache.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        TmdbDetailsCache.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        with patch("requests.Session.get", side_effect=AssertionError("network disabled")):
            result = self.run_job()
        self.assertEqual(result["records"][0]["status"], "CACHE_MISS")
        self.assertFalse(ContentMapping.objects.exists())

    def test_unmatched_is_deferred_until_retry_due_or_new_evidence(self):
        record = self.discovery()
        TmdbSearchCache.objects.create(query="new film", year=2020, content_type="movie", is_negative=True)
        self.assertEqual(self.run_job()["records"][0]["status"], "UNMATCHED")
        self.assertEqual(self.run_job()["selected"], 0)
        state = CatalogueReconciliationRecord.objects.get(discovered_record=record)
        self.assertGreater(state.next_attempt_at, timezone.now() + timedelta(days=29))
        self.cache(record)
        TmdbSearchCache.objects.update(is_negative=False)
        self.assertEqual(list(eligible_records("hydrahd"))[0][1], "new_verification_evidence")
        self.assertEqual(self.run_job()["records"][0]["status"], "ASSOCIATED")

    def test_due_retry_and_bounded_explicit_review(self):
        record = self.discovery(year=None)
        self.run_job()
        with self.assertRaises(ValueError):
            self.run_job(review=True)
        explicit = self.run_job(review=True, record_ids=[record.pk])
        self.assertEqual(explicit["records"][0]["reason"], "explicit_review")
        CatalogueReconciliationRecord.objects.update(next_attempt_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.run_job()["records"][0]["reason"], "retry_due")

    def test_budget_exhaustion_resumes_cached_search_without_duplicates(self):
        record = self.discovery()
        search = FakeResponse(200, {"results": [{"id": 991, "title": record.title, "release_date": "2020-01-01"}]})
        details = FakeResponse(200, {"id": 991, "title": record.title, "release_date": "2020-01-01"})
        with patch("requests.Session.get", return_value=search) as request:
            stopped = self.run_job(allow_network=True, request_budget=1)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(stopped["tmdb_budget"]["attempted"], 1)
        self.assertEqual(stopped["records"][0]["status"], "BUDGET_EXHAUSTED")
        self.assertIsNone(CatalogueReconciliationRun.objects.get().last_completed_record_id)
        self.assertFalse(CanonicalBootstrapEvidence.objects.exists())
        with patch("requests.Session.get", return_value=details) as request:
            resumed = self.run_job(allow_network=True, request_budget=1, review=True, record_ids=[record.pk])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(resumed["records"][0]["status"], "ASSOCIATED")
        self.assertEqual(self.run_job()["selected"], 0)

    def test_rate_limit_sets_durable_cooldown_and_stops_other_records(self):
        self.discovery()
        self.discovery("Second Film", "native-second")
        with patch("requests.Session.get", return_value=FakeResponse(429, headers={"Retry-After": "900"})) as request:
            report = self.run_job(allow_network=True, request_budget=4, retries=2)
            second = self.run_job(allow_network=True)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(second["status"], "COOLDOWN")
        self.assertGreater(CatalogueReconciliationRun.objects.get().cooldown_until, timezone.now() + timedelta(minutes=14))
        self.assertEqual(CatalogueReconciliationRecord.objects.count(), 1)
        self.assertFalse(TmdbSearchCache.objects.exists())

    def test_transport_errors_have_backoff_and_are_not_negative_cached(self):
        self.discovery()
        with patch("requests.Session.get", side_effect=requests.ConnectionError("fixture")) as request:
            result = self.run_job(allow_network=True, retries=0)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result["records"][0]["status"], "ERROR")
        self.assertGreater(CatalogueReconciliationRecord.objects.get().next_attempt_at, timezone.now())
        self.assertFalse(TmdbSearchCache.objects.exists())
        self.assertEqual(self.run_job()["selected"], 0)

    def test_interruption_keeps_previous_record_commit_and_resumes_remaining(self):
        first = self.discovery()
        second = self.discovery("Second Film", "native-second")
        self.cache(first)
        from tracker.services.incremental_catalogue_reconciliation import _commit_record
        def interrupt(record, *args, **kwargs):
            if record.pk == second.pk:
                raise KeyboardInterrupt()
            return _commit_record(record, *args, **kwargs)
        with patch(f"{MODULE}._commit_record", side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.run_job()
        run = CatalogueReconciliationRun.objects.get()
        self.assertEqual(run.status, "INTERRUPTED")
        self.assertEqual(run.last_completed_record_id, first.pk)
        self.assertEqual(CatalogueReconciliationRecord.objects.count(), 1)
        self.cache(second, tmdb_id=992)
        report = self.run_job()
        self.assertEqual([row["record_id"] for row in report["records"]], [second.pk])
        self.assertEqual(ContentMapping.objects.count(), 2)

    def test_crash_in_association_rolls_back_evidence_and_checkpoint_together(self):
        self.discovery()
        record = DiscoveredCatalogueRecord.objects.get()
        self.cache(record)
        with patch(f"{MODULE}._apply_classification", side_effect=RuntimeError("crash before commit")), self.assertRaises(RuntimeError):
            self.run_job()
        self.assertFalse(CanonicalBootstrapEvidence.objects.exists())
        self.assertFalse(Movie.objects.exists())
        self.assertFalse(ContentMapping.objects.exists())
        self.assertFalse(CatalogueReconciliationRecord.objects.exists())
        self.assertIsNone(CatalogueReconciliationRun.objects.get().last_completed_record_id)
        self.assertEqual(self.run_job()["records"][0]["status"], "ASSOCIATED")

    def test_expired_lease_is_recovered_but_live_lease_excludes_reentry(self):
        self.discovery(year=None)
        run = CatalogueReconciliationRun.objects.create(provider=self.provider, status="RUNNING", lease_owner="other",
            lease_expires_at=timezone.now() + timedelta(minutes=5))
        self.assertEqual(self.run_job()["status"], "LOCKED")
        run.refresh_from_db()
        self.assertEqual(run.lease_owner, "other")
        run.lease_expires_at = timezone.now() - timedelta(seconds=1)
        run.save()
        report = self.run_job()
        self.assertTrue(report["recovered_expired_lease"])
        self.assertEqual(report["selected"], 1)

    def test_lost_lease_cannot_commit(self):
        record = self.discovery()
        self.cache(record)
        from tracker.services.incremental_catalogue_reconciliation import _commit_record
        def lose_lease(*args, **kwargs):
            CatalogueReconciliationRun.objects.update(lease_owner="replacement")
            return _commit_record(*args, **kwargs)
        with patch(f"{MODULE}._commit_record", side_effect=lose_lease), self.assertRaises(RuntimeError):
            self.run_job()
        self.assertFalse(CanonicalBootstrapEvidence.objects.exists())
        self.assertFalse(ContentMapping.objects.exists())
        self.assertFalse(CatalogueReconciliationRecord.objects.exists())
        self.assertEqual(CatalogueReconciliationRun.objects.get().lease_owner, "replacement")

    def test_baseline_acknowledges_without_matching_or_writes_to_catalogue(self):
        record = self.discovery()
        module = importlib.import_module("tracker.migrations.0058_baseline_completed_catalogue")
        with patch.object(module, "BASELINE_UPDATED_THROUGH", timezone.now() + timedelta(minutes=1)), patch.object(
                module, "BASELINE_MAX_RECORD_ID", record.pk), connection.schema_editor(atomic=False) as editor:
            module.baseline(apps, editor)
            module.baseline(apps, editor)
        self.assertEqual(CatalogueReconciliationRecord.objects.get().status, "BASELINED")
        with patch("requests.Session.get", side_effect=AssertionError("bulk must not repeat")):
            self.assertEqual(self.run_job(allow_network=True)["selected"], 0)
        record.title = "New Metadata"
        record.save()
        self.assertEqual(list(eligible_records("hydrahd"))[0][1], "meaningful_metadata_changed")

    def test_deployment_baseline_excludes_later_creates_and_metadata_edits(self):
        module = importlib.import_module("tracker.migrations.0058_baseline_completed_catalogue")
        old = self.discovery("Acknowledged", "acknowledged")
        edited = self.discovery("Edited after snapshot", "edited")
        cutoff = timezone.now() - timedelta(hours=1)
        DiscoveredCatalogueRecord.objects.filter(pk=old.pk).update(updated_at=cutoff - timedelta(seconds=1))
        later = self.discovery("Created after snapshot", "later")
        # Even a backdated new import cannot enter the acknowledged ID range.
        DiscoveredCatalogueRecord.objects.filter(pk=later.pk).update(updated_at=cutoff - timedelta(seconds=1))
        with patch.object(module, "BASELINE_MAX_RECORD_ID", edited.pk), patch.object(
                module, "BASELINE_UPDATED_THROUGH", cutoff), connection.schema_editor(atomic=False) as editor:
            module.baseline(apps, editor)
        self.assertEqual(list(CatalogueReconciliationRecord.objects.values_list("discovered_record_id", flat=True)), [old.pk])
        self.assertEqual({r.pk for r, _ in eligible_records("hydrahd")}, {edited.pk, later.pk})

    def test_dry_run_and_status_do_not_write(self):
        record = self.discovery()
        self.cache(record)
        report = run_incremental_reconciliation("hydrahd")
        self.assertEqual(report["records"][0]["status"], "VERIFIED")
        output = StringIO()
        call_command("reconcile_incremental_catalogue", "--provider", "hydrahd", "--status", stdout=output)
        self.assertEqual(json.loads(output.getvalue())["eligible"][0]["record_id"], record.pk)
        for model in (CatalogueReconciliationRecord, CatalogueReconciliationRun, Movie, ContentMapping, CanonicalBootstrapEvidence, ProviderContent, Offer):
            self.assertFalse(model.objects.exists())

    def test_multiple_remote_candidates_and_duplicate_source_releases_stay_ambiguous(self):
        record = self.discovery()
        self.cache(record)
        search = TmdbSearchCache.objects.get()
        search.results.append({"tmdb_id": 992, "title": record.title, "year": 2020, "content_type": "movie"})
        search.save()
        self.assertEqual(self.run_job()["records"][0]["status"], "AMBIGUOUS")
        self.assertFalse(ContentMapping.objects.exists())

    def test_movie_and_tv_numeric_id_separation(self):
        record = self.discovery()
        self.cache(record)
        Movie.objects.create(tmdb_id=991, title="Different Series", year=2020, content_type="series")
        self.assertEqual(self.run_job()["records"][0]["status"], "ASSOCIATED")
        self.assertEqual(Movie.objects.filter(tmdb_id=991).count(), 2)
        self.assertEqual(ContentMapping.objects.get().content_type, "movie")

    def test_new_details_evidence_wakes_deferred_record_without_search_cache(self):
        record = self.discovery()
        self.assertEqual(self.run_job()["records"][0]["status"], "CACHE_MISS")
        Movie.objects.create(tmdb_id=991, title=record.title, year=2020, content_type="movie")
        self.cache(record)
        TmdbSearchCache.objects.all().delete()
        self.assertEqual(list(eligible_records("hydrahd"))[0][1], "new_verification_evidence")
        result = self.run_job()
        self.assertEqual(result["records"][0]["status"], "ASSOCIATED")
        self.assertEqual(CanonicalBootstrapEvidence.objects.get().source_kind, "tmdb_cached_identity_details")

    def test_verified_alternative_title_can_reuse_existing_identity(self):
        record = self.discovery()
        Movie.objects.create(tmdb_id=991, title="Canonical Title", year=2020, content_type="movie")
        self.cache(record, title="Canonical Title", original_title=record.title)
        TmdbSearchCache.objects.all().delete()
        self.assertEqual(self.run_job()["records"][0]["status"], "ASSOCIATED")
        self.assertEqual(Movie.objects.count(), 1)

    def test_cached_canonical_must_not_bypass_ambiguous_search_evidence(self):
        record = self.discovery()
        Movie.objects.create(tmdb_id=991, title=record.title, year=2020, content_type="movie")
        self.cache(record)
        cache = TmdbSearchCache.objects.get()
        cache.results.append({"tmdb_id": 992, "title": record.title, "year": 2020, "content_type": "movie"})
        cache.save()
        self.assertEqual(self.run_job()["records"][0]["status"], "AMBIGUOUS")
        self.assertFalse(ContentMapping.objects.exists())

    def test_metadata_change_during_verification_never_commits_stale_proof(self):
        record = self.discovery()
        self.cache(record)
        from tracker.services.incremental_catalogue_reconciliation import _commit_record
        def mutate(*args, **kwargs):
            DiscoveredCatalogueRecord.objects.filter(pk=record.pk).update(title="Changed During Verification")
            return _commit_record(*args, **kwargs)
        with patch(f"{MODULE}._commit_record", side_effect=mutate), self.assertRaises(CommandError):
            self.run_job()
        self.assertFalse(CanonicalBootstrapEvidence.objects.exists())
        self.assertFalse(ContentMapping.objects.exists())
        self.assertEqual(list(eligible_records("hydrahd"))[0][0].title, "Changed During Verification")

    def test_failure_retries_share_the_same_hard_request_budget(self):
        self.discovery()
        with patch("requests.Session.get", return_value=FakeResponse(503)) as http:
            report = self.run_job(allow_network=True, retries=2, request_budget=2)
        self.assertEqual(http.call_count, 2)
        self.assertEqual(report["tmdb_budget"]["attempted"], 2)
        self.assertEqual(report["records"][0]["status"], "BUDGET_EXHAUSTED")
        self.assertFalse(ContentMapping.objects.exists())

    def test_new_external_trust_evidence_is_a_meaningful_collector_update(self):
        item = ScrapedItem(title="Trusted New", provider_slug="hydrahd", content_type="movie",
            source_url="https://hydrahd.gd/movie/trusted-new", raw_payload={"year": 2020})
        _persist_unclassified_observation(provider=self.provider, item=item, partition="movies")
        item.raw_payload["external_ids_trusted"] = True
        changed = _persist_unclassified_observation(provider=self.provider, item=item, partition="movies")
        self.assertEqual(changed, "updated")

    def test_network_dry_run_does_not_write_caches_or_progress(self):
        record = self.discovery()
        responses = [FakeResponse(200, {"results": [{"id": 991, "title": record.title, "release_date": "2020-01-01"}]}),
                     FakeResponse(200, {"id": 991, "title": record.title, "release_date": "2020-01-01"})]
        with patch("requests.Session.get", side_effect=responses):
            report = run_incremental_reconciliation("hydrahd", allow_network=True, request_budget=2, interval=0)
        self.assertEqual(report["records"][0]["status"], "VERIFIED")
        for model in (TmdbSearchCache, TmdbDetailsCache, CatalogueReconciliationRecord,
                      CatalogueReconciliationRun, CanonicalBootstrapEvidence, Movie, ContentMapping, Offer):
            self.assertFalse(model.objects.exists())

    def test_classification_error_is_durable_without_repeating_failed_mutation(self):
        record = self.discovery()
        self.cache(record)
        with patch(f"{MODULE}._apply_classification", side_effect=ValueError("damaged classification")) as classify:
            result = self.run_job()
        self.assertEqual(classify.call_count, 1)
        self.assertEqual(result["records"][0]["status"], "ERROR")
        self.assertEqual(CatalogueReconciliationRecord.objects.get().status, "ERROR")
        self.assertFalse(CanonicalBootstrapEvidence.objects.exists())
        self.assertFalse(ContentMapping.objects.exists())
        self.assertIsNone(CatalogueReconciliationRun.objects.get().last_completed_record_id)
        self.assertEqual(self.run_job()["selected"], 0)

    def test_final_reporting_failure_still_releases_provider_lock(self):
        from tracker.services.bulk_refresh import release_source_lock
        with patch(f"{MODULE}.logger.info", side_effect=RuntimeError("reporting failure")), patch(
                f"{MODULE}.release_source_lock", wraps=release_source_lock) as release, self.assertRaises(RuntimeError):
            self.run_job()
        release.assert_called_once_with("unofficial-canonical:hydrahd")


class IncrementalConcurrencyTests(TransactionTestCase):
    def test_independent_postgres_session_cannot_enter_active_provider(self):
        if connection.vendor != "postgresql":
            self.skipTest("Real PostgreSQL advisory-lock test")
        from tracker.services.bulk_refresh import try_source_lock, release_source_lock
        from concurrent.futures import ThreadPoolExecutor
        Provider.objects.get_or_create(slug="hydrahd", defaults={"name": "HydraHD"})
        key = "unofficial-canonical:hydrahd"
        self.assertTrue(try_source_lock(key))
        try:
            def competing_job():
                try:
                    return run_incremental_reconciliation("hydrahd", apply=True)
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(competing_job).result(timeout=10)
            self.assertEqual(result["status"], "LOCKED")
            self.assertFalse(CatalogueReconciliationRun.objects.exists())
        finally:
            release_source_lock(key)
