"""The explicit override and scheduled sweep share the production source policy."""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from availability.models import Source
from tracker.models import Movie, RefreshPriority, RefreshRequest, RefreshRequestStatus, WatchlistItem
from tracker.services import bulk_refresh as bulk, refresh_requests as rr, source_policy
from tracker.services.bulk_refresh import BulkRefreshSummary
from tracker.tests.availability_factories import local_offer
from tracker.tests.test_refresh_source_policy_drain import unlimited

SA = "streaming_availability"


class BackgroundMeteredGateTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=965001, title="Background gate")
        WatchlistItem.objects.create(user_id=1, movie=self.movie)
        offer = local_offer(self.movie, country="NL")
        offer.source, _ = Source.objects.get_or_create(slug="watchmode", defaults={"name": "Watchmode"})
        offer.save(update_fields=["source"])

    def _sweep(self, gate):
        service = MagicMock()
        with patch.object(source_policy, "consult_metered_only_when_unresolved", return_value=gate), patch.object(bulk, "resolve_source_country", return_value="NL"), patch.object(bulk, "stale_refresh_scopes", return_value=[SA]):
            result = bulk.process_watchlist_refresh(budget=5, user_id=1, refresh_service=service, limiter=unlimited())
        return result, service

    def test_background_sweep_respects_gate_on(self):
        result, service = self._sweep(True)
        service.refresh_content.assert_not_called()
        self.assertEqual(result["skipped_policy_gated"], 1)
        self.assertEqual(result["refreshed"], 0)
        self.assertEqual(result["remaining_budget"], 5)

    def test_background_sweep_preserves_gate_off_baseline(self):
        result, service = self._sweep(False)
        service.refresh_content.assert_called_once_with(self.movie, country="NL")
        self.assertEqual(result["skipped_policy_gated"], 0)
        self.assertEqual(result["refreshed"], 1)

    def test_explicit_single_and_bulk_upgrade_deduplicate_and_execute_with_gate_on(self):
        for bulk_request in (False, True):
            with self.subTest(bulk_request=bulk_request):
                original = RefreshRequest.objects.create(movie=self.movie, source=SA, country="NL", priority=RefreshPriority.BACKGROUND)
                service = MagicMock()
                with patch.object(source_policy, "consult_metered_only_when_unresolved", return_value=True):
                    for _ in range(2):
                        if bulk_request:
                            rr.request_watchlist_refresh(user_id=1, sources=[SA], country="NL")
                        else:
                            rr.request_title_refresh(self.movie, priority=RefreshPriority.EXPLICIT, if_stale=False, sources=[SA], country="NL")
                    pending = RefreshRequest.objects.get(movie=self.movie, source=SA, country="NL", status=RefreshRequestStatus.PENDING)
                    self.assertEqual(pending.pk, original.pk)
                    self.assertEqual(pending.priority, RefreshPriority.EXPLICIT)
                    with patch.object(rr, "_service_for_scope", return_value=service):
                        rr.drain_refresh_requests(summary=BulkRefreshSummary(budget=5, remaining_budget=5), service=service, limiter=unlimited())
                service.refresh_content.assert_called_once_with(self.movie, country="NL")
                pending.refresh_from_db()
                self.assertEqual(pending.status, RefreshRequestStatus.DONE)

    def test_explicit_single_and_bulk_still_obey_quota(self):
        for bulk_request in (False, True):
            with self.subTest(bulk_request=bulk_request):
                with patch.object(source_policy, "consult_metered_only_when_unresolved", return_value=True):
                    if bulk_request:
                        rr.request_watchlist_refresh(user_id=1, sources=[SA], country="NL")
                    else:
                        rr.request_title_refresh(self.movie, priority=RefreshPriority.EXPLICIT, if_stale=False, sources=[SA], country="NL")
                    request = RefreshRequest.objects.get(movie=self.movie, source=SA, status=RefreshRequestStatus.PENDING)
                    service = MagicMock()
                    limiter = unlimited()
                    limiter.acquired = limiter.daily_limit
                    with patch.object(rr, "_service_for_scope", return_value=service):
                        rr._drain_one(request, summary=BulkRefreshSummary(budget=5, remaining_budget=5), service=service, limiter=limiter, rate_limit_hit=False)
                service.refresh_content.assert_not_called()
                request.refresh_from_db()
                self.assertEqual(request.status, RefreshRequestStatus.BLOCKED)
                self.assertEqual(request.blocked_reason, "quota_exhausted")

    def test_explicit_still_obeys_rate_window(self):
        request = RefreshRequest.objects.create(movie=self.movie, source=SA, country="NL", priority=RefreshPriority.EXPLICIT)
        service = MagicMock()
        limiter = unlimited()
        limiter.acquire = MagicMock(side_effect=rr.RateLimitExceeded("wait"))
        with patch.object(source_policy, "consult_metered_only_when_unresolved", return_value=True), patch.object(rr, "_service_for_scope", return_value=service):
            rr._drain_one(request, summary=BulkRefreshSummary(budget=5, remaining_budget=5), service=service, limiter=limiter, rate_limit_hit=False)
        service.refresh_content.assert_not_called()
        request.refresh_from_db()
        self.assertEqual(request.status, RefreshRequestStatus.PENDING)
        self.assertEqual(request.attempts, 0)
