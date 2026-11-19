"""Collector -> discovery -> existing exact reconciliation, without network/offers."""
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase, SimpleTestCase
from django.utils import timezone

from availability.domain.models import DiscoveredContent
from availability.models import Country, ContentMapping, Offer, ProviderContent, Source, TmdbDetailsCache, TmdbSearchCache
from availability.sources.base import SourceFetchResult
from availability.services.candidate_gateway import TMDbCandidateGateway
from availability.tests.tubi_fixtures import BLUE_WATER_HIGH, I_ROBOT
from tracker.models import (
    CanonicalBootstrapEvidence, CatalogueDiscoveryState, CatalogueReconciliationRecord,
    CatalogueReconciliationRun, DiscoveredCatalogueRecord, Movie, Provider,
)
from tracker.services.catalogue_only import (
    CHECKPOINT_KEY, import_catalogue_batch, reconcile_catalogue_records,
)
from tracker.services.catalogue_reconciliation_inputs import fingerprint_v1
from tracker.services.discovery_http import RequestBudgetReached
from tracker.services.discovery_policy import DiscoveryPolicyError
from tracker.services.incremental_catalogue_reconciliation import eligible_records, run_incremental_reconciliation
from tracker.tests.catalogue_only_fixtures import PERMISSION_FIXTURE, one_page
from tracker.tests.test_discovery_controls import response

MODULE = "tracker.services.catalogue_only"


def table_rows():
    return {m._meta.label: list(m.objects.order_by("pk").values())
            for m in apps.get_models() if m._meta.app_label in {"tracker", "availability"}}


class BoundedCollectorTests(SimpleTestCase):
    def test_real_collector_one_page_with_shared_spacing_and_accounting(self):
        result, accounting = one_page()
        self.assertEqual(len(result.contents), 1)
        self.assertEqual(result.contents[0].provider_content_id, "578912")
        self.assertEqual(result.contents[0].release_date.year, 2004)
        self.assertEqual(accounting["pages_attempted"], 1)
        self.assertEqual(accounting["http"]["attempted"], 4)
        self.assertEqual([e["started"] for e in accounting["http"]["events"]], [0, 2, 4, 6])
        self.assertEqual(accounting["http"]["counts"], {"robots": 1, "metadata": 2, "listing": 1,
                                                        "redirects": 0, "retries": 0})

    def test_redirect_is_fifth_attempt_and_robots_can_tighten_spacing(self):
        _, accounting = one_page(redirect=True, robots="User-agent: *\nAllow: /\nCrawl-delay: 4\n")
        self.assertEqual(accounting["http"]["attempted"], 5)
        self.assertEqual(accounting["http"]["counts"]["redirects"], 1)
        self.assertEqual([e["started"] for e in accounting["http"]["events"]], [0, 4, 8, 12, 16])

    def test_attempt_page_robots_and_dns_fail_closed(self):
        with self.assertRaises(RequestBudgetReached):
            one_page(request_budget=3)
        with self.assertRaises(ValueError):
            one_page(page_limit=0)
        with self.assertRaises(DiscoveryPolicyError):
            one_page(robots="User-agent: *\nDisallow: /\n")
        with self.assertRaises(DiscoveryPolicyError):
            one_page(resolver=lambda _: ["93.184.216.34", "127.0.0.1"])

    def test_unsafe_host_url_and_redirect_rejected_before_destination(self):
        for url in ("https://other.invalid/movies/578912/i-robot", "http://tubitv.com/movies/578912/i-robot",
                    "https://secret:password@tubitv.com/movies/578912/i-robot"):
            with self.subTest(url=url), self.assertRaises(DiscoveryPolicyError):
                one_page(detail_url=url)
        calls = []
        def send(url, *_):
            calls.append(url)
            return response(302, Location="https://other.invalid/robots.txt")
        with self.assertRaises(DiscoveryPolicyError):
            one_page(send=send)
        self.assertEqual(calls, ["https://tubitv.com/robots.txt"])


class CatalogueOnlyTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.get(slug="tubi")
        self.permission = patch(MODULE+".permission_for", return_value=PERMISSION_FIXTURE)
        self.permission.start()
        self.addCleanup(self.permission.stop)
        for target in ("requests.sessions.Session.request", "requests.get", "requests.post"):
            p = patch(target, side_effect=AssertionError("No external HTTP is authorized"))
            p.start()
            self.addCleanup(p.stop)
        self.result, self.accounting = one_page()
        self.observed = timezone.now()-timedelta(minutes=1)

    def import_batch(self, result=None, **kwargs):
        return import_catalogue_batch("tubi", "tubi", result or self.result,
            expected_cursor=kwargs.pop("expected_cursor", None), **kwargs)

    def record(self):
        self.import_batch(apply=True, observed_at=self.observed)
        return DiscoveredCatalogueRecord.objects.get(provider=self.provider)

    def cache(self, record, ids=(1999996501,), title=None):
        title = title or record.title
        TmdbSearchCache.objects.update_or_create(query=TMDbCandidateGateway._normalize_query(record.title), year=2004, content_type="movie",
            defaults={"results": [dict(tmdb_id=i, title=title, year=2004, content_type="movie") for i in ids],
                      "is_negative": not bool(ids), "expires_at": timezone.now()+timedelta(days=1)})
        for pk in ids:
            TmdbDetailsCache.objects.update_or_create(tmdb_id=pk, content_type="movie", defaults={
                "details": dict(tmdb_id=pk, title=title, year=2004, content_type="movie"),
                "is_negative": False, "expires_at": timezone.now()+timedelta(days=1)})

    def reconcile(self, record, **kwargs):
        return reconcile_catalogue_records("tubi", record_ids=[record.pk], **kwargs)

    def test_import_preview_changes_no_application_table(self):
        before = table_rows()
        preview = self.import_batch()
        self.assertEqual(preview["inserted"], 1)
        self.assertEqual(table_rows(), before)

    def test_import_preserves_identity_provenance_times_and_checkpoint(self):
        record = self.record()
        self.assertEqual((record.provider_native_id, record.title, record.identity_url), ("578912", "I, Robot", I_ROBOT))
        self.assertEqual(record.provenance_state, "PROVIDER_REPORTED")
        self.assertEqual(record.discovered_at, self.observed)
        self.assertEqual(record.last_seen_at, self.observed)
        self.assertEqual(record.original_metadata["collector_source"], "tubi")
        self.assertEqual(record.original_metadata["external_ids"], {"imdb_id": "tt0343818"})
        self.assertFalse(record.original_metadata["external_ids_trusted"])
        self.assertIn("offline fixture", record.original_metadata["permission_evidence"])
        self.assertEqual(record.classification_state, "PENDING")
        self.assertEqual([(r.pk, why) for r,why in eligible_records("tubi")], [(record.pk, "new_discovery")])
        self.assertEqual(CatalogueDiscoveryState.objects.get(provider=self.provider).cursor[CHECKPOINT_KEY]["cursor"], self.result.cursor)
        self.assertFalse(Offer.objects.exists())

    def test_import_replay_does_not_invalidate_outcome_or_reset_first_seen(self):
        record = self.record()
        self.cache(record)
        self.reconcile(record, apply=True)
        before = fingerprint_v1(record)
        replay = self.import_batch(apply=True)
        record.refresh_from_db()
        self.assertEqual(replay["unchanged"], 1)
        self.assertEqual(record.discovered_at, self.observed)
        self.assertEqual(before, fingerprint_v1(record))
        self.assertEqual(self.reconcile(record, apply=True)["selected"], 0)
        self.assertEqual(DiscoveredCatalogueRecord.objects.filter(provider=self.provider).count(), 1)
        self.assertEqual(CatalogueReconciliationRecord.objects.count(), 1)
        self.assertEqual(ContentMapping.objects.count(), 1)

    def test_offers_and_withdrawals_are_never_consumed(self):
        class Forbidden:
            def __iter__(self): raise AssertionError("Offer values must not be consumed")
        result = replace(self.result, offers=Forbidden(), offer_withdrawals=Forbidden())
        self.import_batch(result, apply=True)
        self.assertFalse(Offer.objects.exists())

    def test_exact_cached_match_preview_apply_and_replay(self):
        record = self.record()
        self.cache(record)
        before = table_rows()
        preview = self.reconcile(record)
        self.assertEqual(preview["records"][0]["status"], "VERIFIED")
        self.assertEqual(table_rows(), before)
        applied = self.reconcile(record, apply=True)
        self.assertEqual(applied["records"][0]["status"], "ASSOCIATED")
        self.assertEqual(applied["records"][0]["tmdb_id"], preview["records"][0]["tmdb_id"])
        self.assertEqual(applied["tmdb_budget"]["attempted"], 0)
        self.assertEqual(CanonicalBootstrapEvidence.objects.count(), 1)
        self.assertFalse(ProviderContent.objects.get().currently_available)
        self.assertEqual(self.reconcile(record)["selected"], 0)
        self.assertFalse(Offer.objects.exists())

    def test_ambiguous_preview_apply_and_deferral(self):
        record = self.record()
        self.cache(record, ids=(1999996501,1999996502))
        preview = self.reconcile(record)
        applied = self.reconcile(record, apply=True)
        self.assertEqual(preview["records"][0]["status"], "AMBIGUOUS")
        self.assertEqual(applied["records"][0]["status"], "AMBIGUOUS")
        self.assertIsNone(CatalogueReconciliationRecord.objects.get().next_attempt_at)
        self.assertEqual(self.reconcile(record)["selected"], 0)
        self.assertFalse(ContentMapping.objects.exists())

    def test_unmatched_negative_cache_and_missing_series_year(self):
        record = self.record()
        self.cache(record, ids=())
        self.assertEqual(self.reconcile(record)["records"][0]["status"], "UNMATCHED")
        self.assertEqual(self.reconcile(record, apply=True)["records"][0]["status"], "UNMATCHED")
        self.assertGreater(CatalogueReconciliationRecord.objects.get().next_attempt_at, timezone.now()+timedelta(days=29))
        series_result, _ = one_page(detail_url=BLUE_WATER_HIGH)
        self.assertEqual(series_result.contents[0].provider_content_id, "2185")
        self.assertIsNone(series_result.contents[0].release_date)
        state = CatalogueDiscoveryState.objects.get(provider=self.provider)
        self.import_batch(series_result, apply=True,
                          expected_cursor=state.cursor[CHECKPOINT_KEY]["cursor"])
        series = DiscoveredCatalogueRecord.objects.get(provider_native_id="2185")
        self.assertEqual(self.reconcile(series, apply=True)["records"][0]["status"], "UNMATCHED")
        self.assertIsNone(CatalogueReconciliationRecord.objects.get(discovered_record=series).next_attempt_at)
        self.assertFalse(ContentMapping.objects.exists())

    def test_existing_mapping_conflict_preserved_in_both_modes(self):
        record = self.record()
        self.cache(record)
        source = Source.objects.get_or_create(slug="tubi", defaults={"name": "Tubi", "type": "web"})[0]
        mapping = ContentMapping.objects.create(provider=self.provider, provider_content_id="578912",
            source=source, content_type="movie", tmdb_id=1999996599, match_method="manual")
        before = table_rows()
        self.assertEqual(self.reconcile(record)["records"][0]["status"], "CONFLICTING")
        self.assertEqual(table_rows(), before)
        self.assertEqual(self.reconcile(record, apply=True)["records"][0]["status"], "CONFLICTING")
        mapping.refresh_from_db()
        self.assertEqual(mapping.tmdb_id, 1999996599)
        self.assertFalse(CanonicalBootstrapEvidence.objects.exists())
        self.assertFalse(ProviderContent.objects.exists())

    def test_changed_details_conflict_preserves_canonical_proof_and_mapping(self):
        record = self.record()
        self.cache(record)
        self.reconcile(record, apply=True)
        before_mapping = list(ContentMapping.objects.values())
        before_proof = list(CanonicalBootstrapEvidence.objects.values())
        item = replace(self.result.contents[0], title="Changed title")
        cursor = CatalogueDiscoveryState.objects.get(provider=self.provider).cursor[CHECKPOINT_KEY]["cursor"]
        self.import_batch(replace(self.result, contents=[item]), apply=True, expected_cursor=cursor)
        record.refresh_from_db()
        self.cache(record, ids=(1999996502,))
        for apply in (False, True):
            self.assertEqual(self.reconcile(record, apply=apply)["records"][0]["status"], "CONFLICTING")
        self.assertEqual(list(ContentMapping.objects.values()), before_mapping)
        self.assertEqual(list(CanonicalBootstrapEvidence.objects.values()), before_proof)

    def test_checkpoint_is_atomic_on_interruption_and_can_resume(self):
        before = table_rows()
        with patch.object(CatalogueDiscoveryState, "save", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.import_batch(apply=True)
        self.assertEqual(table_rows(), before)
        self.assertEqual(self.import_batch(apply=True)["inserted"], 1)

    def test_reconciliation_interruption_resumes_only_uncommitted_identity(self):
        first = self.result.contents[0]
        second = replace(first, provider_content_id="582004", title="Permission",
                         url="https://tubitv.com/movies/582004/permission")
        imported = self.import_batch(replace(self.result, contents=[first,second]), apply=True)
        records = list(DiscoveredCatalogueRecord.objects.order_by("pk"))
        for n,record in enumerate(records):
            self.cache(record, ids=(1999996501+n,))
        from tracker.services.incremental_catalogue_reconciliation import _commit_record
        calls = 0
        def interrupted(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            return _commit_record(*args, **kwargs)
        with patch("tracker.services.incremental_catalogue_reconciliation._commit_record", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                reconcile_catalogue_records("tubi", record_ids=imported["record_ids"], apply=True)
        self.assertEqual(ContentMapping.objects.count(), 1)
        resumed = reconcile_catalogue_records("tubi", record_ids=imported["record_ids"], apply=True)
        self.assertEqual(resumed["selected"], 1)
        self.assertEqual(ContentMapping.objects.count(), 2)
        self.assertEqual(CatalogueReconciliationRecord.objects.count(), 2)
        self.assertFalse(Offer.objects.exists())

    def test_existing_playback_offer_is_unchanged_by_import_and_apply(self):
        source = Source.objects.get_or_create(slug="tubi", defaults={"name": "Tubi", "type": "web"})[0]
        country = Country.objects.get_or_create(code="US", defaults={"name": "United States"})[0]
        Offer.objects.create(provider=self.provider, source=source, country=country, tmdb_id=1999996590,
                             content_type="movie", offer_type="free", provider_content_id="untouched")
        before = list(Offer.objects.values())
        record = self.record()
        self.cache(record)
        self.reconcile(record, apply=True)
        self.assertEqual(list(Offer.objects.values()), before)

    def test_record_denial_and_provider_operator_denial_are_preserved(self):
        record = self.record()
        record.access_policy_state = "DENIED"
        record.save()
        before = table_rows()
        with self.assertRaises(DiscoveryPolicyError):
            self.import_batch(apply=True)
        self.assertEqual(table_rows(), before)
        self.provider.config["discovery_permission"] = "DENIED"
        self.provider.save()
        with self.assertRaises(DiscoveryPolicyError):
            self.reconcile(record, apply=True)

    def test_existing_checkpoint_namespaces_preserved_and_stale_batch_refused(self):
        other = {"modes": {"historical": {"parts": {"movies": 99}}}}
        CatalogueDiscoveryState.objects.create(provider=self.provider, cursor=other)
        self.record()
        state = CatalogueDiscoveryState.objects.get(provider=self.provider)
        self.assertEqual(state.cursor["modes"], other["modes"])
        before = table_rows()
        changed = replace(self.result, cursor="changed")
        with self.assertRaisesRegex(ValueError, "Stale"):
            self.import_batch(changed, apply=True)
        self.assertEqual(table_rows(), before)

    def test_bad_batch_and_contradictory_identity_never_commit_prefix(self):
        item = self.result.contents[0]
        for invalid in (replace(item, provider_id="pbs"), replace(item, source_id="pbs"),
                        replace(item, content_type="episode"), replace(item, title=""),
                        replace(item, url="https://other.invalid/movie/1")):
            before = table_rows()
            with self.assertRaises((ValueError, DiscoveryPolicyError)):
                self.import_batch(replace(self.result, contents=[item,invalid]), apply=True)
            self.assertEqual(table_rows(), before)
        with self.assertRaises(ValueError):
            self.import_batch(replace(self.result, contents=[item,replace(item,title="Contradiction")]), apply=True)
        with self.assertRaises(ValueError):
            self.import_batch(replace(self.result, contents=[item,replace(item,provider_content_id="different")]), apply=True)
        self.assertFalse(DiscoveredCatalogueRecord.objects.exists())

    def test_metadata_and_robots_urls_cannot_become_catalogue_title_identities(self):
        from availability.tests.tubi_fixtures import MOVIES_1

        item = self.result.contents[0]
        for url in (MOVIES_1, "https://tubitv.com/sitemap.xml", "https://tubitv.com/robots.txt"):
            before = table_rows()
            with self.subTest(url=url), self.assertRaises(DiscoveryPolicyError):
                self.import_batch(
                    replace(self.result, contents=[replace(item, url=url)]), apply=True)
            self.assertEqual(table_rows(), before)
        self.assertFalse(DiscoveredCatalogueRecord.objects.exists())

    def test_unknown_denied_expired_permission_and_disabled_provider_block(self):
        for policy in (replace(PERMISSION_FIXTURE, status="UNVERIFIED"),
                       replace(PERMISSION_FIXTURE, status="DENIED"),
                       replace(PERMISSION_FIXTURE, expires_on=timezone.now().date()-timedelta(days=1))):
            before = table_rows()
            with patch(MODULE+".permission_for", return_value=policy), self.assertRaises(DiscoveryPolicyError):
                self.import_batch(apply=True)
            self.assertEqual(table_rows(), before)
        self.provider.is_enabled = False
        self.provider.save()
        with self.assertRaises(DiscoveryPolicyError):
            self.import_batch(apply=True)

    def test_actual_registry_blocks_import_and_reconciliation_without_bypass(self):
        self.permission.stop()
        with self.assertRaisesRegex(DiscoveryPolicyError, "permission_unverified"):
            self.import_batch(apply=True)
        with self.assertRaises(DiscoveryPolicyError):
            reconcile_catalogue_records("tubi", record_ids=[1], apply=True)
        self.assertFalse(CatalogueReconciliationRun.objects.exists())

    def test_new_provider_not_exposed_to_legacy_cli_or_scheduler(self):
        with self.assertRaises(ValueError):
            run_incremental_reconciliation("tubi", apply=True)
        from tracker.services.catalogue_discovery import SCHEDULED_DISCOVERY_PROVIDERS
        self.assertNotIn("tubi", SCHEDULED_DISCOVERY_PROVIDERS)

    def test_reconciliation_scope_is_bounded_and_provider_specific(self):
        record = self.record()
        for ids in ([], [record.pk,record.pk], [record.pk+999], list(range(251))):
            with self.assertRaises(ValueError):
                reconcile_catalogue_records("tubi", record_ids=ids, apply=True)
