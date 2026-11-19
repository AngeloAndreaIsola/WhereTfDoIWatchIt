"""Manual review uses fixture evidence; every real HTTP seam is a tripwire."""
from dataclasses import replace
from datetime import date, timedelta
from unittest.mock import patch, Mock

import requests
from django.apps import apps
from django.test import TestCase, override_settings
from django.core.cache.backends.locmem import LocMemCache
from django.utils import timezone
from rest_framework.test import APIClient

from availability.models import (TmdbSearchCache, TmdbDetailsCache, ProviderContentCandidate,
                                 ContentMapping, ProviderContent, Source, Offer)
from availability.services.legacy_ingest import ingest_scraped_mapping
from availability.services.candidate_gateway import TMDbCandidateGateway
from availability.services.candidate_acceptance import CandidateAcceptanceService, CandidateAcceptanceError
from tracker.models import (Movie, Provider, ScrapedContentMapping, ApiCallCounter, ApiCallLedger,
                            CatalogueReconciliationRecord)
from tracker.services.manual_review_policy import TMDB_REVIEW_GRANTS, TMDbReviewGrant
from tracker.services.discovery_policy import UNOFFICIAL_PROVIDERS
from tracker.services.legacy_manual_review import stored_review


class ManualScraperReviewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.provider = Provider.objects.get(slug="nhk_world")
        self.mapping = ScrapedContentMapping.objects.create(
            provider=self.provider, scraped_title="Fixture Identity", scraped_year=2020,
            scraped_content_type="movie", raw_payload={"content_type": "movie"},
        )
        self.content = ingest_scraped_mapping(self.mapping)
        self.http = self.enterContext(patch("requests.adapters.HTTPAdapter.send", side_effect=AssertionError("HTTP forbidden")))
        self.search = self.enterContext(patch("tracker.services.tmdb.TMDbClient.search", side_effect=AssertionError("legacy TMDb forbidden")))
        self.details = self.enterContext(patch("tracker.services.tmdb.TMDbClient.details", side_effect=AssertionError("legacy TMDb forbidden")))
        self.control = LocMemCache(f"manual-fixture-{self.id()}", {})
        self.control.clear()

    def tearDown(self):
        self.http.assert_not_called()
        self.search.assert_not_called()
        self.details.assert_not_called()

    def snapshot(self, exclude=()):
        return {m._meta.label: list(m.objects.order_by("pk").values())
                for m in apps.get_models() if m._meta.label not in exclude}

    def cache_search(self, results=None, **overrides):
        values = dict(query=TMDbCandidateGateway._normalize_query(self.mapping.scraped_title),
                      year=self.mapping.scraped_year, content_type=self.mapping.scraped_content_type,
                      results=results if results is not None else [self.candidate()])
        values.update(overrides)
        return TmdbSearchCache.objects.create(**values)

    def candidate(self, tmdb_id=420, kind="movie", **extra):
        return {"tmdb_id": tmdb_id, "title": "Fixture Identity", "year": 2020,
                "content_type": kind, **extra}

    def cache_details(self, tmdb_id=420, kind="movie", **overrides):
        details = self.candidate(tmdb_id, kind)
        details.update(overrides)
        return TmdbDetailsCache.objects.create(tmdb_id=tmdb_id, content_type=kind, details=details)

    def confirm(self, **overrides):
        data = {"mapping_id": self.mapping.pk, "tmdb_id": 420}
        data.update(overrides)
        return self.client.post("/api/scrapers/confirm-match/", data, format="json")

    def lookup(self, **overrides):
        data = {"mapping_id": self.mapping.pk}
        data.update(overrides)
        return self.client.post("/api/scrapers/candidates/lookup/", data, format="json")

    def test_cached_suggestions_accessible_and_get_has_zero_writes(self):
        self.cache_search()
        before = self.snapshot()
        response = self.client.get("/api/scrapers/unresolved/")
        self.assertEqual(response.status_code, 200)
        row = response.json()[0]
        self.assertEqual(row["candidate_state"], "cached")
        self.assertEqual(row["candidates"][0]["tmdb_id"], 420)
        self.assertEqual(row["candidates"][0]["content_type"], "movie")
        self.assertTrue(row["manual_review"]["allowed"])
        self.assertFalse(row["candidate_search"]["allowed"])
        self.assertEqual(self.snapshot(), before)

    def test_not_searched_empty_negative_and_stale_are_distinct(self):
        self.assertEqual(stored_review(self.mapping)["candidate_state"], "not_searched")
        self.assertEqual(stored_review(self.mapping)["candidate_search"]["state"], "blocked")
        entry = self.cache_search([], is_negative=True)
        self.assertEqual(stored_review(self.mapping)["candidate_state"], "no_results")
        entry.expires_at = timezone.now() - timedelta(seconds=1)
        entry.save()
        result = stored_review(self.mapping)
        self.assertEqual(result["candidate_state"], "stale")
        self.assertEqual(result["candidates"], [])

    def test_reliable_persisted_candidates_remain_visible(self):
        candidate = ProviderContentCandidate.objects.create(
            provider_content=self.content, tmdb_id=420, title="Fixture Identity", year=2020,
            content_type="movie", scoring_evidence={"content_title": "Fixture Identity",
                "content_year": 2020, "content_type": "movie"},
        )
        before = self.snapshot()
        self.assertEqual(stored_review(self.mapping)["candidate_state"], "persisted")
        self.assertEqual(self.snapshot(), before)
        candidate.scoring_evidence["content_title"] = "Unrelated Observation"
        candidate.save()
        self.assertEqual(stored_review(self.mapping)["candidates"], [])

    def test_review_timestamp_cannot_make_old_persisted_evidence_fresh(self):
        candidate = ProviderContentCandidate.objects.create(
            provider_content=self.content, tmdb_id=420, title="Fixture Identity", year=2020,
            content_type="movie", scoring_evidence={"content_title": "Fixture Identity",
                "content_year": 2020, "content_type": "movie"},
        )
        ProviderContentCandidate.objects.filter(pk=candidate.pk).update(
            created_at=timezone.now() - timedelta(days=31), updated_at=timezone.now())
        before = self.snapshot()
        result = stored_review(self.mapping)
        self.assertEqual(result["candidate_state"], "stale")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(self.snapshot(), before)

    def test_cached_candidates_are_bounded_and_typed(self):
        self.cache_search([self.candidate(i) for i in range(1, 12)] + [self.candidate(20, "series")])
        result = stored_review(self.mapping)
        self.assertEqual(len(result["candidates"]), 5)
        self.assertEqual({c["content_type"] for c in result["candidates"]}, {"movie"})

    def test_unauthorized_lookup_before_http_or_any_writes(self):
        before = self.snapshot()
        response = self.lookup(authorize_network=True, max_http_requests=99999)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "tmdb_review_permission_missing")
        self.assertEqual(self.snapshot(), before)

    def grant(self, **overrides):
        values = dict(provider="nhk_world", status="VERIFIED", evidence="fixture only",
                      reviewed_on=date.today(), expires_on=date.today() + timedelta(days=1),
                      daily_limit=2, monthly_limit=5)
        values.update(overrides)
        self.enterContext(patch.dict(TMDB_REVIEW_GRANTS, {"nhk_world": TMDbReviewGrant(**values)}))
        self.enterContext(patch("tracker.services.legacy_manual_review._shared_control_cache", return_value=self.control))

    def test_denied_expired_disabled_and_shared_control_requirements(self):
        today = date.today()
        for grant, code in (
            (TMDbReviewGrant("nhk_world", status="DENIED"), "tmdb_review_permission_denied"),
            (TMDbReviewGrant("nhk_world", expires_on=today - timedelta(days=1)), "tmdb_review_permission_expired"),
        ):
            with patch.dict(TMDB_REVIEW_GRANTS, {"nhk_world": grant}):
                before = self.snapshot()
                response = self.lookup()
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["code"], code)
                self.assertEqual(self.snapshot(), before)
        grant = TMDbReviewGrant("nhk_world", status="VERIFIED", evidence="fixture", reviewed_on=today,
            expires_on=today, daily_limit=2, monthly_limit=5)
        with patch.dict(TMDB_REVIEW_GRANTS, {"nhk_world": grant}):
            self.assertEqual(self.lookup().json()["code"], "tmdb_review_shared_controls_required")

    @override_settings(TMDB_API_KEY="fixture-key")
    def test_authorized_explicit_lookup_uses_tv_gateway_and_only_cache_quota_writes(self):
        self.mapping.scraped_content_type = "series"
        self.mapping.raw_payload = {"content_type": "series"}
        self.mapping.save()
        self.grant()
        before = self.snapshot(exclude=("availability.TmdbSearchCache", "tracker.ApiCallCounter", "tracker.ApiCallLedger"))
        response = Mock(status_code=200, headers={})
        response.json.return_value = {"results": [{"id": 420, "name": "Fixture Identity", "first_air_date": "2020-01-01"}]}
        with patch("requests.Session.get", return_value=response) as get:
            result = self.lookup()
        self.assertEqual(result.status_code, 200, result.json())
        self.assertTrue(get.call_args.args[0].endswith("/search/tv"))
        self.assertEqual(get.call_args.kwargs["params"]["first_air_date_year"], 2020)
        self.assertIs(get.call_args.kwargs["allow_redirects"], False)
        self.assertEqual(result.json()["candidates"][0]["content_type"], "series")
        self.assertEqual(result.json()["budget"]["attempted"], 1)
        self.assertEqual(self.snapshot(exclude=("availability.TmdbSearchCache", "tracker.ApiCallCounter", "tracker.ApiCallLedger")), before)

    @override_settings(TMDB_API_KEY="fixture-key")
    def test_retry_after_cooldown_survives_next_lookup_without_retry(self):
        self.grant()
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "900"
        with patch("requests.Session.get", return_value=response) as get:
            self.assertEqual(self.lookup().json()["code"], "tmdb_review_cooldown")
            self.assertEqual(self.lookup().json()["code"], "tmdb_review_cooldown")
        self.assertEqual(get.call_count, 1)
        self.assertFalse(TmdbSearchCache.objects.exists())
        self.assertEqual(ApiCallCounter.objects.get(service="tmdb_manual_review").count, 1)

    def test_quota_exhaustion_blocks_dispatch(self):
        self.grant(daily_limit=1)
        from tracker.services.rate_limit import RateLimiter
        RateLimiter("tmdb_manual_review", daily_limit=1, monthly_limit=5).acquire()
        with override_settings(TMDB_API_KEY="fixture-key"), patch("requests.Session.get") as get:
            self.assertEqual(self.lookup().json()["code"], "tmdb_review_quota_exhausted")
        get.assert_not_called()

    def test_manual_confirmation_from_fresh_cache_has_only_intended_identity_writes(self):
        from tracker.models import DiscoveredCatalogueRecord
        deferred = DiscoveredCatalogueRecord.objects.create(provider=self.provider,
            title="Deferred fixture", source_url="https://fixture.invalid/deferred",
            identity_url="https://fixture.invalid/deferred")
        CatalogueReconciliationRecord.objects.create(discovered_record=deferred,
            input_fingerprint="fixture", status="BASELINED")
        self.cache_details()
        exclude = ("tracker.Movie", "tracker.ScrapedContentMapping", "availability.ContentMapping", "availability.CandidateReviewAudit")
        before = self.snapshot(exclude=exclude)
        response = self.confirm()
        self.assertEqual(response.status_code, 200, response.json())
        self.mapping.refresh_from_db()
        self.assertTrue(self.mapping.is_confirmed)
        self.assertEqual((self.mapping.movie.tmdb_id, self.mapping.movie.content_type), (420, "movie"))
        self.assertEqual(ContentMapping.objects.get(content=self.content).match_method, "manual")
        self.assertEqual(self.snapshot(exclude=exclude), before)
        self.assertFalse(Offer.objects.exists())
        self.assertEqual(CatalogueReconciliationRecord.objects.get().status, "BASELINED")
        # Repeated explicit confirmation preserves every row/timestamp.
        before = self.snapshot()
        self.assertEqual(self.confirm().status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_legacy_decimal_string_ids_work_without_loosening_identity_checks(self):
        self.cache_details()
        response = self.confirm(mapping_id=str(self.mapping.pk), tmdb_id="420")
        self.assertEqual(response.status_code, 200, response.json())
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.movie.tmdb_id, 420)
        self.assertEqual(self.confirm(tmdb_id="420.0").status_code, 400)
        self.assertEqual(self.confirm(tmdb_id=2**63).status_code, 400)

    def test_same_numeric_id_movie_and_series_remain_distinct(self):
        movie = Movie.objects.create(tmdb_id=420, title="Other Film", year=2001, content_type="movie")
        self.mapping.scraped_content_type = "series"
        self.mapping.raw_payload = {"content_type": "series"}
        self.mapping.save()
        self.content.content_type = "series"
        self.content.save()
        self.cache_details(kind="series")
        result = self.confirm(content_type="series")
        self.assertEqual(result.status_code, 200, result.json())
        self.mapping.refresh_from_db()
        self.assertNotEqual(self.mapping.movie_id, movie.pk)
        movie.refresh_from_db()
        self.assertEqual((movie.title, movie.content_type), ("Other Film", "movie"))

    def test_missing_or_expired_details_block_without_mutation(self):
        before = self.snapshot()
        self.assertEqual(self.confirm().json()["code"], "manual_review_fresh_details_required")
        self.assertEqual(self.snapshot(), before)
        entry = self.cache_details()
        entry.expires_at = timezone.now() - timedelta(days=1)
        entry.save()
        before = self.snapshot()
        self.assertEqual(self.confirm().json()["code"], "manual_review_fresh_details_required")
        self.assertEqual(self.snapshot(), before)

    def test_ambiguous_or_conflicting_title_year_type_cannot_be_confirmed(self):
        entry = self.cache_details()
        for overrides, expected in (
            ({"title": "Other Identity"}, "manual_review_identity_conflict"),
            ({"year": 1998}, "manual_review_identity_conflict"),
            ({"content_type": "series"}, "manual_review_fresh_details_required"),
        ):
            entry.details = self.candidate(**overrides)
            entry.save()
            before = self.snapshot()
            self.assertEqual(self.confirm().json()["code"], expected)
            self.assertEqual(self.snapshot(), before)
        entry.details = self.candidate()
        entry.save()
        self.cache_search([self.candidate(), self.candidate(421)])
        before = self.snapshot()
        self.assertEqual(self.confirm().json()["code"], "manual_review_ambiguous_identity")
        self.assertEqual(self.snapshot(), before)

    def test_hidden_or_truncated_ambiguity_does_not_allow_confirmation(self):
        self.cache_details()
        values = [self.candidate(420)] + [self.candidate(i, title=f"Unrelated {i}") for i in range(421, 427)]
        values.append(self.candidate(428))
        cache = self.cache_search(values)
        before = self.snapshot()
        self.assertEqual(self.confirm().json()["code"], "manual_review_ambiguous_identity")
        self.assertEqual(self.snapshot(), before)
        cache.results = [self.candidate(search_truncated=True)]
        cache.save()
        self.assertEqual(self.confirm().json()["code"], "manual_review_ambiguous_identity")

    def test_saved_automatic_same_target_can_be_manually_confirmed_without_metadata_changes(self):
        movie = Movie.objects.create(tmdb_id=420, title="Fixture Identity", year=2020, overview="Keep me")
        ContentMapping.objects.create(provider=self.provider, source=self.content.source,
            provider_content_id=self.content.provider_content_id, content=self.content,
            content_type="movie", tmdb_id=420, match_method="title_year", confidence=1.0)
        self.cache_details(overview="Do not overwrite")
        saved_movie = list(Movie.objects.values())
        self.assertEqual(self.confirm().status_code, 200)
        self.assertEqual(list(Movie.objects.values()), saved_movie)
        self.assertEqual(ContentMapping.objects.get(content=self.content).match_method, "manual")
        movie.refresh_from_db()
        self.assertEqual(movie.overview, "Keep me")

    def test_rejected_and_conflicting_persisted_evidence_is_not_verified(self):
        self.cache_details()
        candidate = ProviderContentCandidate.objects.create(provider_content=self.content,
            tmdb_id=420, title="Fixture Identity", year=2020, content_type="movie", review_state="conflict",
            scoring_evidence={"content_title": "Fixture Identity", "content_year": 2020, "content_type": "movie"})
        self.assertEqual(self.confirm().json()["code"], "manual_review_candidate_decision_conflict")
        candidate.review_state = "rejected"
        candidate.save()
        self.assertEqual(stored_review(self.mapping)["candidates"], [])
        self.assertEqual(self.confirm().json()["code"], "manual_review_rejected_candidate")

    @override_settings(TMDB_API_KEY="fixture-key")
    def test_explicit_details_lookup_writes_only_details_cache_and_quota(self):
        self.grant()
        before = self.snapshot(exclude=("availability.TmdbDetailsCache", "tracker.ApiCallCounter", "tracker.ApiCallLedger"))
        response = Mock(status_code=200, headers={})
        response.json.return_value = {"id": 420, "title": "Fixture Identity", "release_date": "2020-01-01"}
        with patch("requests.Session.get", return_value=response) as get:
            result = self.lookup(tmdb_id=420)
        self.assertEqual(result.status_code, 200, result.json())
        self.assertTrue(result.json()["details_cached"])
        self.assertTrue(get.call_args.args[0].endswith("/movie/420"))
        self.assertEqual(self.snapshot(exclude=("availability.TmdbDetailsCache", "tracker.ApiCallCounter", "tracker.ApiCallLedger")), before)

    def test_existing_manual_and_canonical_associations_cannot_be_replaced(self):
        existing = Movie.objects.create(tmdb_id=419, title="Saved Manual", content_type="movie")
        self.mapping.movie, self.mapping.is_confirmed = existing, True
        self.mapping.save()
        before = self.snapshot()
        self.assertEqual(self.confirm().json()["code"], "manual_review_existing_mapping_conflict")
        self.assertEqual(self.snapshot(), before)
        self.mapping.movie, self.mapping.is_confirmed = None, False
        self.mapping.save()
        ContentMapping.objects.create(provider=self.provider, source=self.content.source,
            provider_content_id=self.content.provider_content_id, content=self.content,
            content_type="series", tmdb_id=420, match_method="manual")
        before = self.snapshot()
        self.assertEqual(self.confirm().json()["code"], "manual_review_canonical_mapping_conflict")
        self.assertEqual(self.snapshot(), before)

    def test_unofficial_and_generic_review_stay_blocked_even_with_cache(self):
        self.cache_details()
        for slug in (*UNOFFICIAL_PROVIDERS, "generic-import-fixture"):
            provider, _ = Provider.objects.get_or_create(slug=slug, defaults={"name": slug, "category": "scraper"})
            self.mapping.provider = provider
            self.mapping.save()
            before = self.snapshot()
            self.assertEqual(self.confirm().status_code, 409)
            self.assertEqual(self.lookup().status_code, 409)
            self.assertEqual(self.snapshot(), before)

    def test_candidate_admin_cannot_bypass_six_provider_policy(self):
        for slug in UNOFFICIAL_PROVIDERS:
            provider, _ = Provider.objects.get_or_create(slug=slug, defaults={"name": slug})
            content = ProviderContent.objects.create(provider=provider, source=self.content.source,
                provider_content_id="restricted-fixture", title="Fixture Identity", content_type="movie")
            before = self.snapshot()
            with self.assertRaises(CandidateAcceptanceError) as error:
                CandidateAcceptanceService().accept(provider_content_id=content.pk, tmdb_id=420,
                    expected_revision=None, reason="explicit fixture", authorize_network=True, max_http_requests=1)
            self.assertEqual(error.exception.code, "source_permission_unverified")
            self.assertEqual(self.snapshot(), before)

    def test_operator_and_input_guards_block_unintended_mutations(self):
        before = self.snapshot()
        response = self.client.post("/api/scrapers/candidates/lookup/", {"mapping_id": self.mapping.pk},
            format="json", HTTP_SEC_FETCH_SITE="cross-site")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.confirm(tmdb_id=True).status_code, 400)
        self.assertEqual(self.confirm(content_type="series").json()["code"], "manual_review_content_type_conflict")
        self.assertEqual(self.confirm(expected_updated_at="old").json()["code"], "manual_review_stale_observation")
        self.assertEqual(self.snapshot(), before)
