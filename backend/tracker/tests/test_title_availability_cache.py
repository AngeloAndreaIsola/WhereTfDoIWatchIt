from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from django.test import TestCase

from availability.domain.enums import FreshnessState, SyncMode, SyncStatus
from availability.freshness import freshness_for, freshness_state
from availability.models import Country, Offer, OfferEvidence, Source, SyncRun
from availability.sources.watchmode_client import WatchmodeClient
from tracker.models import (
    Movie, Provider, RefreshRequest, RefreshRequestOutcome, TitleAvailabilityCheck, WatchlistItem,
)
from tracker.services.availability_reads import get_offer_evidence, get_title_offer_evidence
from tracker.services.availability_report import title_source_report
from tracker.services.bulk_refresh import is_stale, process_watchlist_refresh
from tracker.services.rate_limit import RateLimiter
from tracker.services.refresh_requests import process_refresh_queue, request_title_refresh
from tracker.tests.availability_factories import reviewed_test_sources

NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
JW = "justwatch"
WM = "watchmode"
EMPTY_SEARCH = {"data": {"popularTitles": {"edges": []}}}


class TitleAvailabilityCacheTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.movie = Movie.objects.create(tmdb_id=194, title="Film", content_type="movie")
        cls.series = Movie.objects.create(tmdb_id=194, title="Series", content_type="series")
        cls.nl = Country.objects.get(code="NL")
        reviewed_test_sources(NOW)
        cls.provider, _ = Provider.objects.get_or_create(slug="tubi", defaults={"name": "Tubi"})
        cls.direct, _ = Source.objects.get_or_create(slug="tubi", defaults={"name": "Tubi"})

    def setUp(self):
        self.clock = self.enterContext(patch("django.utils.timezone.now", return_value=NOW))
        headers = {"Content-Type": "application/json"}
        self.jw_post = self.enterContext(patch(
            "availability.sources.justwatch_client._requests_post",
            return_value=Mock(status_code=200, headers=headers, json=Mock(return_value=EMPTY_SEARCH)),
        ))
        self.wm_get = Mock(
            return_value=Mock(status_code=200, headers=headers, json=Mock(return_value={"title_results": []})),
        )
        self.enterContext(patch(
            "availability.sources.watchmode.WatchmodeClient",
            side_effect=lambda: WatchmodeClient(http_get=self.wm_get, api_key="test-only"),
        ))

    def offer(self, source, *, title=None, country=None, age=timedelta(), expiry=None,
              present=True, tag="", sync_run=None):
        title = title or self.movie
        return Offer.objects.create(
            source=Source.objects.get(slug=source), provider=self.provider, country=country or self.nl,
            tmdb_id=title.tmdb_id, content_type=title.content_type, offer_type="free_with_ads",
            provider_content_id=f"{source}-{tag}", last_seen=NOW - age, available_until=expiry,
            currently_available=present, sync_run=sync_run,
        )

    def queue_jw(self, *, title=None, country="NL"):
        return request_title_refresh(title or self.movie, sources=[JW], country=country)["queued"]

    def test_seven_day_boundary_and_evidence_read_window(self):
        old = self.offer(JW, age=timedelta(days=7))
        for delta, due in [
            (timedelta(microseconds=-1), False),
            (timedelta(), True),
            (timedelta(microseconds=1), True),
        ]:
            with self.subTest(delta=delta):
                self.assertEqual(is_stale(self.movie, source=JW, country="NL", now=NOW + delta), due)
        self.assertEqual(freshness_state(JW, old.last_seen, now=NOW), FreshnessState.STALE)
        current, last_known = get_offer_evidence(self.movie)
        self.assertEqual(current, [])
        self.assertEqual(last_known[0].last_seen_at, old.last_seen.isoformat())

    def _title_report(self, source=JW, *, age=timedelta(days=3), title=None, country=None):
        title = title or self.movie
        country = country or self.nl
        seen = NOW - age
        run = SyncRun.objects.create(
            provider=self.provider, country=country, source=Source.objects.get(slug=source),
            mode=SyncMode.TARGETED, status=SyncStatus.SUCCEEDED,
        )
        row = self.offer(source, title=title, country=country, age=age, sync_run=run)
        row.observed_at = seen
        row.save(update_fields=["observed_at"])
        check = TitleAvailabilityCheck.objects.create(
            movie=title, tmdb_id=title.tmdb_id, content_type=title.content_type,
            country=country.code, source=source,
            last_attempted_at=seen, last_succeeded_at=seen + timedelta(seconds=1),
            successful_run_ids=[run.pk],
        )
        return row, check

    def test_title_report_is_visible_up_to_seven_days_with_original_times(self):
        row, check = self._title_report(age=timedelta(days=7))
        for age in (timedelta(hours=12), timedelta(hours=24), timedelta(days=3),
                    timedelta(days=6), timedelta(days=7)):
            with self.subTest(age=age):
                self.clock.return_value = row.last_seen + age
                evidence = get_title_offer_evidence(self.movie, country="NL")
                self.assertEqual(evidence.current, [])
                [report] = evidence.source_reported
                self.assertEqual(report.availability_evidence, "source_reported")
                self.assertEqual(report.last_seen_at, row.last_seen.isoformat())
                self.assertEqual(report.observed_at, row.last_seen.isoformat())
                self.assertEqual(report.last_checked_at, check.last_succeeded_at.isoformat())
        self.clock.return_value += timedelta(microseconds=1)
        evidence = get_title_offer_evidence(self.movie, country="NL")
        self.assertEqual(evidence.source_reported, [])
        self.assertEqual(evidence.last_known[0].availability_evidence, "stale")

    def test_title_api_returns_source_report_without_access_or_playback_claim(self):
        row, check = self._title_report(WM, age=timedelta(days=6))
        with patch("tracker.views.resolve_or_create_movie", side_effect=AssertionError("remote metadata")):
            data = self.client.get("/api/prime/movie/194/?current_country=NL").json()
            other = self.client.get("/api/prime/movie/194/?current_country=US").json()
            self.assertEqual(self.client.get("/api/prime/movie/999999/").status_code, 404)
        [report] = data["source_reported_offers"]
        self.assertEqual(report["reporting_source"], WM)
        self.assertEqual(report["last_seen_at"], row.last_seen.isoformat())
        self.assertEqual(report["last_checked_at"], check.last_succeeded_at.isoformat())
        self.assertIsNone(report["is_entitled"])
        self.assertIsNone(report["is_accessible"])
        self.assertFalse(report["playback_verified"])
        self.assertEqual(data["offers"], [])
        self.assertEqual(other["source_reported_offers"], [])
        self.jw_post.assert_not_called()
        self.wm_get.assert_not_called()

    def test_later_empty_failed_or_conflicting_check_never_reuses_positive(self):
        row, check = self._title_report(age=timedelta(days=3))
        self.offer("tubi", present=False)
        self.assertEqual(get_title_offer_evidence(self.movie).source_reported, [])
        Offer.objects.filter(source=self.direct).delete()
        self.assertEqual(len(get_title_offer_evidence(self.movie).source_reported), 1)

        # A later successful-but-empty check and a failed attempt both leave
        # the original observation intact and visible only as history.
        for completed in (False, True):
            with self.subTest(completed=completed):
                check.last_attempted_at = NOW
                if completed:
                    check.last_succeeded_at = NOW
                    check.successful_run_ids = []
                check.save()
                evidence = get_title_offer_evidence(self.movie)
                self.assertEqual(evidence.source_reported, [])
                self.assertEqual(evidence.current, [])
                self.assertEqual(evidence.last_known[0].last_seen_at, row.last_seen.isoformat())
        row.refresh_from_db()
        self.assertTrue(row.currently_available)
        self.assertEqual(row.observed_at, row.last_seen)

    def test_catalogue_and_unattributed_pilot_are_not_promoted(self):
        pilot, check = self._title_report(age=timedelta(hours=1))
        check.delete()  # TARGETED alone is not proof of the routine path.
        run = SyncRun.objects.create(
            provider=self.provider, country=self.nl, source=Source.objects.get(slug=WM),
            mode=SyncMode.INCREMENTAL, status=SyncStatus.SUCCEEDED,
        )
        self.offer(WM, age=timedelta(days=3), sync_run=run)
        self.offer("tubi", age=timedelta(days=3))
        evidence = get_title_offer_evidence(self.movie)
        self.assertEqual(evidence.source_reported, [])
        self.assertEqual(evidence.current, [])
        self.assertEqual(len(evidence.last_known), 1)
        self.assertIn(pilot.last_seen.isoformat(), [offer.last_seen_at for offer in evidence.last_known])

    def test_authorization_required_when_queueing_and_rechecked_before_execution(self):
        from availability.services.source_permissions import PERMISSION_KEY

        endpoint = "/api/movie/194/refresh/?content_type=movie&current_country=NL"
        original = Source.objects.get(slug=JW).configuration
        Source.objects.filter(slug__in=(JW, WM, "streaming_availability")).update(configuration={})
        self.assertEqual(self.client.post(endpoint).status_code, 409)
        self.assertEqual(self.queue_jw(), 0)
        self.assertEqual(RefreshRequest.objects.count(), 0)

        Source.objects.filter(slug=JW).update(configuration=original)
        self.assertEqual(self.client.post(endpoint).status_code, 202)
        self.assertEqual(RefreshRequest.objects.get().source, JW)
        original[PERMISSION_KEY]["valid_until"] = NOW.isoformat()
        Source.objects.filter(slug=JW).update(configuration=original)
        self.assertEqual(process_refresh_queue(budget=1)["refreshed"], 0)
        self.assertEqual(RefreshRequest.objects.get().blocked_reason, "source_disabled")
        self.jw_post.assert_not_called()
        self.wm_get.assert_not_called()

    def test_interrupted_check_does_not_send_again(self):
        check = TitleAvailabilityCheck.objects.create(
            movie=self.movie, tmdb_id=self.movie.tmdb_id, content_type=self.movie.content_type,
            country="NL", source=JW, last_attempted_at=NOW - timedelta(days=8),
            in_progress=True,
        )
        RefreshRequest.objects.create(movie=self.movie, country="NL", source=JW)
        self.assertEqual(process_refresh_queue(budget=1)["errors"], 1)
        self.assertEqual(self.queue_jw(), 0)
        check.refresh_from_db()
        self.assertTrue(check.in_progress)
        self.jw_post.assert_not_called()

    def test_catalogue_observation_does_not_fill_title_cache(self):
        run = SyncRun.objects.create(
            provider=self.provider, country=self.nl, source=Source.objects.get(slug=JW),
            mode=SyncMode.INCREMENTAL,
        )
        self.offer(JW, sync_run=run)
        self.assertEqual(self.queue_jw(), 1)

    def test_empty_search_is_cached_without_negative_evidence_or_offer_edits(self):
        old = self.offer(JW, age=timedelta(days=8))
        self.assertEqual(self.queue_jw(), 1)
        self.assertEqual(self.queue_jw(), 1)
        self.assertEqual(RefreshRequest.objects.count(), 1)
        self.jw_post.assert_not_called()
        self.assertEqual(process_refresh_queue(budget=1)["refreshed"], 1)
        self.jw_post.assert_called_once()
        self.assertEqual(self.queue_jw(), 0)
        self.assertEqual(RefreshRequest.objects.get().outcome, RefreshRequestOutcome.INCONCLUSIVE)
        old.refresh_from_db()
        self.assertEqual(old.last_seen, NOW - timedelta(days=8))
        self.assertTrue(old.currently_available)
        self.assertEqual(title_source_report([self.movie.pk], country="NL")[self.movie.pk]["state"], "unknown")

    def test_scopes_are_typed_and_cannot_supply_another_market_or_source(self):
        self.offer(JW, age=timedelta(days=1))
        self.offer(WM, age=timedelta(days=8))
        queued = request_title_refresh(self.movie, sources=[JW, WM], country="NL")
        self.assertEqual(queued["queued"], 1)
        self.assertEqual(self.queue_jw(title=self.series), 1)
        self.assertEqual(self.queue_jw(country="US"), 1)
        self.assertEqual(self.queue_jw(), 0)
        self.assertCountEqual(
            RefreshRequest.objects.values_list("movie_id", "country", "source"),
            [(self.movie.pk, "NL", WM), (self.series.pk, "NL", JW), (self.movie.pk, "US", JW)],
        )

    def test_relinked_movie_does_not_reuse_the_old_typed_identity_cache(self):
        self.assertEqual(self.queue_jw(), 1)
        self.assertEqual(process_refresh_queue(budget=1)["refreshed"], 1)
        self.assertEqual(self.queue_jw(), 0)
        self.movie.tmdb_id = 6200
        self.movie.save(update_fields=["tmdb_id"])
        self.assertEqual(self.queue_jw(), 1)

    def test_detail_open_only_queues_the_active_title_and_market(self):
        with patch("tracker.services.enrichment.enrich_movie_from_tmdb"):
            for _ in range(2):
                response = self.client.get(f"/api/movie/{self.movie.pk}/?current_country=NL")
                self.assertEqual(response.status_code, 200)
        self.jw_post.assert_not_called()
        self.wm_get.assert_not_called()
        self.assertEqual(
            set(RefreshRequest.objects.values_list("movie_id", "country")),
            {(self.movie.pk, "NL")},
        )
        self.assertEqual(RefreshRequest.objects.filter(source=JW).count(), 1)

    def test_confirmation_boundaries_and_final_day_once(self):
        self.offer(JW, age=timedelta(days=2))
        direct = self.offer("tubi", expiry=NOW + timedelta(days=7, microseconds=1))
        self.assertFalse(is_stale(self.movie, source=JW, country="NL"))

        for ahead in (timedelta(days=7), timedelta(days=3)):
            with self.subTest(ahead=ahead):
                Offer.objects.filter(pk=direct.pk).update(available_until=NOW + ahead)
                for age, due in [(timedelta(hours=24, microseconds=-1), False),
                                 (timedelta(hours=24), True),
                                 (timedelta(hours=24, microseconds=1), True)]:
                    Offer.objects.filter(source__slug=JW, tmdb_id=self.movie.tmdb_id).update(last_seen=NOW - age)
                    self.assertEqual(is_stale(self.movie, source=JW, country="NL"), due)

        Offer.objects.filter(pk=direct.pk).update(available_until=NOW + timedelta(hours=24))
        Offer.objects.filter(source__slug=JW, tmdb_id=self.movie.tmdb_id).update(last_seen=NOW - timedelta(hours=12))
        self.assertEqual(self.queue_jw(), 1)
        self.assertEqual(process_refresh_queue(budget=1)["refreshed"], 1)
        self.clock.return_value += timedelta(hours=6)
        self.assertEqual(self.queue_jw(), 0)
        self.jw_post.assert_called_once()

    def test_only_current_independent_dates_qualify(self):
        self.offer(JW, age=timedelta(days=2), expiry=NOW + timedelta(days=4))
        self.offer(WM, expiry=NOW + timedelta(days=4))
        self.assertEqual(self.queue_jw(), 0)  # Two aggregator reports are insufficient.
        self.offer("tubi", country=Country.objects.get(code="US"), expiry=NOW + timedelta(days=4))
        self.offer("tubi", title=self.series, expiry=NOW + timedelta(days=4))
        direct = self.offer("tubi", expiry=NOW)
        self.assertEqual(self.queue_jw(), 0)
        Offer.objects.filter(pk=direct.pk).update(available_until=NOW + timedelta(days=3))
        self.assertEqual(self.queue_jw(), 0)  # Dates conflict.
        Offer.objects.filter(pk=direct.pk).update(available_until=NOW + timedelta(days=4))
        OfferEvidence.objects.create(
            offer_identity=direct.identity, source=self.direct, is_present=False,
            provider_slug=self.provider.slug, country_code="NL", content_type="movie",
            tmdb_id=self.movie.tmdb_id, offer_type="free_with_ads",
            provider_content_id=direct.provider_content_id,
        )
        self.assertEqual(self.queue_jw(), 0)  # The supporter withdrew.
        OfferEvidence.objects.filter(offer_identity=direct.identity).update(is_present=True)
        self.assertEqual(self.queue_jw(), 1)

    def test_final_day_failed_call_is_spent_and_preserves_last_known(self):
        old = self.offer(JW, age=timedelta(days=1))
        self.offer("tubi", expiry=NOW + timedelta(hours=12))
        self.jw_post.return_value = Mock(status_code=403, text="denied")
        self.assertEqual(self.queue_jw(), 1)
        self.assertEqual(process_refresh_queue(budget=5)["errors"], 1)
        self.assertIsNone(process_refresh_queue(budget=5))
        self.assertEqual(self.queue_jw(), 0)
        self.jw_post.assert_called_once()
        check = TitleAvailabilityCheck.objects.get(movie=self.movie, source=JW, country="NL")
        self.assertEqual(check.last_attempted_at, NOW)
        self.assertIsNone(check.last_succeeded_at)
        old.refresh_from_db()
        self.assertEqual(old.last_seen, NOW - timedelta(days=1))
        self.assertTrue(old.currently_available)

    def test_manual_scoped_refresh_deduplicates_before_and_after_drain(self):
        self.offer(JW)
        self.offer(WM)
        endpoint = "/api/movie/194/refresh/?content_type=movie&current_country=NL"
        self.assertEqual(self.client.post(endpoint).status_code, 202)
        self.assertEqual(self.client.post(endpoint).status_code, 202)
        self.assertEqual(RefreshRequest.objects.filter(movie=self.movie, country="NL").count(), 3)
        self.jw_post.assert_not_called()
        self.wm_get.assert_not_called()
        default = Mock()
        process_refresh_queue(budget=3, service=default)
        self.jw_post.assert_called_once()
        self.wm_get.assert_called_once()
        default.refresh_content.assert_called_once_with(self.movie, country="NL")
        response = self.client.post(endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["refresh_pending"])
        process_refresh_queue(budget=3, service=default)
        self.jw_post.assert_called_once()
        self.wm_get.assert_called_once()

    def test_quota_blocks_only_affected_source_without_consuming_a_check(self):
        self.offer("tubi", expiry=NOW + timedelta(hours=12))
        request_title_refresh(self.movie, sources=[JW, WM], country="NL")
        with patch(
            "tracker.services.refresh_requests.rate_limiter_for",
            side_effect=lambda slug: RateLimiter(slug, daily_limit=0 if slug == WM else None),
        ):
            result = process_refresh_queue(budget=1)
        self.assertEqual(result["refreshed"], 1)
        self.assertIn(WM, result["rate_limited_sources"])
        self.assertFalse(TitleAvailabilityCheck.objects.filter(source=WM).exists())
        self.wm_get.assert_not_called()
        self.jw_post.assert_called_once()

    def test_sweep_prioritizes_confirmed_departure_and_respects_its_cache(self):
        ordinary = Movie.objects.create(tmdb_id=195, title="Ordinary")
        WatchlistItem.objects.bulk_create([
            WatchlistItem(user_id=1, movie=ordinary), WatchlistItem(user_id=1, movie=self.movie),
        ])
        self.offer(JW, title=ordinary, age=timedelta(days=8))
        self.offer("tubi", expiry=NOW + timedelta(hours=12))
        Source.objects.filter(slug=WM).update(enabled=False)
        with patch("tracker.services.bulk_refresh.resolve_source_country", return_value="NL"):
            result = process_watchlist_refresh(budget=1)
        self.assertEqual(result["refreshed"], 1)
        payload = self.jw_post.call_args.kwargs["json"]
        self.assertEqual(payload["variables"]["searchTitlesFilter"]["searchQuery"], self.movie.title)
        self.assertEqual(self.queue_jw(), 0)

    def test_deterministic_week_uses_30_instead_of_210_aggregator_checks(self):
        movies = Movie.objects.bulk_create([
            Movie(tmdb_id=3000 + i, title=f"Ordinary {i}") for i in range(15)
        ])
        old_last_seen = NOW - timedelta(days=8)
        for day in range(7):
            moment = NOW + day * timedelta(days=1, minutes=1)
            self.clock.return_value = moment
            # Counterfactual: the old 12h/24h windows would both age out each
            # day even if every check positively updated the offer observation.
            for source in (JW, WM):
                previous = old_last_seen if day == 0 else moment - timedelta(days=1, minutes=1)
                self.assertGreater(moment - previous, freshness_for(source))

            for movie in movies:
                request_title_refresh(movie, sources=[JW, WM], country="NL")
            summary = process_refresh_queue(budget=30)
            self.assertEqual(summary["refreshed"] if summary else 0, 30 if day == 0 else 0)

        self.assertEqual(self.jw_post.call_count, 15)
        self.assertEqual(self.wm_get.call_count, 15)
