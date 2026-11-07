"""Database-backed regression tests for B2.3b negative-evidence precedence."""

import uuid
from unittest.mock import MagicMock, patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tracker.models import (
    Movie,
    RefreshPriority,
    RefreshRequest,
    RefreshRequestOutcome,
    RefreshRequestStatus,
)
from tracker.services import availability_report as ar
from tracker.services import refresh_requests as rr, source_policy
from tracker.services.bulk_refresh import BulkRefreshSummary
from tracker.tests.availability_factories import local_offer
from tracker.tests.test_refresh_source_policy_drain import unlimited


def make_movie(tmdb_id: int) -> Movie:
    return Movie.objects.create(tmdb_id=tmdb_id, title="T", year=2000)


class NegativeEvidencePrecedenceDbTests(TestCase):
    def test_done_unknown_does_not_supersede_older_permanent_miss(self):
        movie = make_movie(1)
        older = RefreshRequest.objects.create(
            movie=movie,
            country="NL",
            source="streaming_availability",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        newer = RefreshRequest.objects.create(
            movie=movie,
            country="NL",
            source="streaming_availability",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.UNKNOWN,
        )
        self.assertLess(older.id, newer.id)
        misses = ar._permanent_miss_sources([movie.id], country="NL")
        self.assertIn("streaming_availability", misses[movie.id])

    def test_done_no_offer_supersedes_older_permanent_miss(self):
        movie = make_movie(2)
        RefreshRequest.objects.create(
            movie=movie,
            country="NL",
            source="streaming_availability",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        RefreshRequest.objects.create(
            movie=movie,
            country="NL",
            source="streaming_availability",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.NO_OFFER,
        )
        misses = ar._permanent_miss_sources([movie.id], country="NL")
        self.assertNotIn("streaming_availability", misses.get(movie.id, set()))

    def test_offers_found_supersedes_older_permanent_miss(self):
        movie = make_movie(3)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.OFFERS_FOUND,
        )
        misses = ar._permanent_miss_sources([movie.id], country="NL")
        self.assertNotIn("streaming_availability", misses.get(movie.id, set()))

    def test_failed_upstream_title_not_found_is_permanent(self):
        movie = make_movie(4)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.FAILED, outcome=RefreshRequestOutcome.UNKNOWN,
            last_error="UpstreamTitleNotFound: no record",
        )
        misses = ar._permanent_miss_sources([movie.id], country="NL")
        self.assertIn("streaming_availability", misses[movie.id])

    def test_transient_404_not_permanent(self):
        movie = make_movie(5)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.FAILED, outcome=RefreshRequestOutcome.UNKNOWN,
            last_error="HTTP 404 not found (transient)",
        )
        misses = ar._permanent_miss_sources([movie.id], country="NL")
        self.assertNotIn("streaming_availability", misses.get(movie.id, set()))

    def test_country_isolation_NL_vs_IT(self):
        movie = make_movie(6)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        misses_nl = ar._permanent_miss_sources([movie.id], country="NL")
        misses_it = ar._permanent_miss_sources([movie.id], country="IT")
        self.assertIn("streaming_availability", misses_nl[movie.id])
        self.assertNotIn("streaming_availability", misses_it.get(movie.id, set()))

    def test_source_isolation(self):
        movie = make_movie(7)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="source_a",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        misses = ar._permanent_miss_sources([movie.id], country="NL")
        self.assertIn("source_a", misses[movie.id])
        self.assertNotIn("source_b", misses.get(movie.id, set()))

    def test_movie_isolation(self):
        m1 = make_movie(8)
        m2 = make_movie(9)
        RefreshRequest.objects.create(
            movie=m1, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        misses = ar._permanent_miss_sources([m1.id, m2.id], country="NL")
        self.assertIn("streaming_availability", misses[m1.id])
        self.assertNotIn("streaming_availability", misses.get(m2.id, set()))

    def test_no_country_filter_aggregates(self):
        movie = make_movie(10)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        misses = ar._permanent_miss_sources([movie.id])
        self.assertIn("streaming_availability", misses[movie.id])

    def test_unknown_does_not_manufacture_no_offer(self):
        # Cached DONE/UNKNOWN must not be treated as a definitive negative.
        movie = make_movie(11)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.UNKNOWN,
        )
        misses = ar._permanent_miss_sources([movie.id], country="NL")
        self.assertNotIn("streaming_availability", misses.get(movie.id, set()))


    def test_request_evidence_isolates_two_batches_for_same_scope(self):
        movie = make_movie(12)
        batches = [uuid.uuid4(), uuid.uuid4()]
        rows = []
        for batch, status, outcome in (
            (batches[0], RefreshRequestStatus.DONE, RefreshRequestOutcome.NO_OFFER),
            (batches[1], RefreshRequestStatus.BLOCKED, RefreshRequestOutcome.UNKNOWN),
        ):
            rows.append(RefreshRequest.objects.create(
                movie=movie, country="NL", source="streaming_availability",
                batch=batch, status=status, outcome=outcome,
                blocked_reason="quota_exhausted" if status == RefreshRequestStatus.BLOCKED else "",
            ))
        for batch, row in zip(batches, rows):
            evidence = ar._latest_request_evidence([movie.pk], batch=batch, country="NL")
            self.assertEqual(evidence[movie.pk][row.source]["request_id"], row.pk)
            self.assertEqual(evidence[movie.pk][row.source]["outcome"], row.outcome)
            self.assertEqual(evidence[movie.pk][row.source]["blocked_reason"], row.blocked_reason)
            self.assertEqual(ar._terminal_status_evidence([movie.pk], batch=batch, country="NL"),
                             {movie.pk: {row.source: row.status}})
        self.assertEqual(ar._latest_request_evidence([movie.pk], country="NL")[movie.pk][rows[1].source]["request_id"], rows[1].pk)

    def test_blocked_reasons_are_preserved_without_creating_absence(self):
        movie = make_movie(13)
        for blocked_reason in ("quota_exhausted", "policy_not_needed", "source_disabled", "cancelled", "superseded"):
            with self.subTest(blocked_reason=blocked_reason):
                batch = uuid.uuid4()
                RefreshRequest.objects.create(
                    movie=movie, country="NL", source="streaming_availability",
                    batch=batch, status=RefreshRequestStatus.BLOCKED,
                    outcome=RefreshRequestOutcome.UNKNOWN, blocked_reason=blocked_reason,
                )
                entry = ar.title_source_report([movie.pk], batch=batch, country="NL")[movie.pk]
                row = next(row for row in entry["sources"] if row["source"] == "streaming_availability")
                self.assertEqual(row["blocked_reason"], blocked_reason)
                self.assertNotIn(row["reason"], ar.ABSENCE_REASONS)
                self.assertNotEqual(entry["state"], "unavailable")
                self.assertNotEqual(entry["evidence"], "absence")
                detail = self.client.get(f"/api/v2/unofficial/movie/{movie.tmdb_id}/offers/?country=NL").json()["availability"]
                detail_row = next(row for row in detail["sources"] if row["source"] == "streaming_availability")
                self.assertEqual(detail_row["blocked_reason"], blocked_reason)

    def test_only_definitive_newer_evidence_supersedes_permanent_negative(self):
        movie = make_movie(14)
        RefreshRequest.objects.create(
            movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        for status, outcome, country, source in (
            (RefreshRequestStatus.DONE, RefreshRequestOutcome.UNKNOWN, "NL", "streaming_availability"),
            (RefreshRequestStatus.FAILED, RefreshRequestOutcome.UNKNOWN, "NL", "streaming_availability"),
            (RefreshRequestStatus.PENDING, RefreshRequestOutcome.UNKNOWN, "NL", "streaming_availability"),
            (RefreshRequestStatus.BLOCKED, RefreshRequestOutcome.NO_OFFER, "NL", "streaming_availability"),
            (RefreshRequestStatus.DONE, RefreshRequestOutcome.OFFERS_FOUND, "JP", "streaming_availability"),
            (RefreshRequestStatus.DONE, RefreshRequestOutcome.NO_OFFER, "NL", "watchmode"),
        ):
            with self.subTest(status=status, country=country, source=source):
                newer = RefreshRequest.objects.create(movie=movie, country=country, source=source,
                    status=status, outcome=outcome, last_error="Transient connection failure",
                    blocked_reason="source_disabled" if status == RefreshRequestStatus.BLOCKED else "")
                self.assertIn("streaming_availability", ar._permanent_miss_sources([movie.pk], country="NL")[movie.pk])
                self.assertIn("streaming_availability", ar._permanent_miss_sources([movie.pk])[movie.pk])
                self.assertTrue(rr._has_reusable_permanent_negative(newer if source == "streaming_availability" and country == "NL" else
                    RefreshRequest(movie=movie, source="streaming_availability", country="NL")))
                newer.delete()

    def test_explicit_bypasses_cached_negative_and_still_obeys_quota(self):
        movie = make_movie(15)
        RefreshRequest.objects.create(movie=movie, country="NL", source="streaming_availability",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND)
        for priority, spent, expected_calls, expected_status in (
            (RefreshPriority.STALE, False, 0, RefreshRequestStatus.DONE),
            (RefreshPriority.EXPLICIT, True, 0, RefreshRequestStatus.BLOCKED),
            (RefreshPriority.EXPLICIT, False, 1, RefreshRequestStatus.DONE),
        ):
            with self.subTest(priority=priority, spent=spent):
                request = RefreshRequest.objects.create(movie=movie, source="streaming_availability", country="NL", priority=priority)
                limiter = unlimited()
                if spent:
                    limiter.acquired = limiter.daily_limit
                summary = BulkRefreshSummary(remaining_budget=10, budget=10)
                service = MagicMock()
                with patch.object(rr, "is_stale", return_value=True), patch.object(rr, "_service_for_scope", return_value=service):
                    rr._drain_one(request, summary=summary, service=service, limiter=limiter, rate_limit_hit=False)
                request.refresh_from_db()
                self.assertEqual(service.refresh_content.call_count, expected_calls)
                self.assertEqual(request.status, expected_status)
                if priority == RefreshPriority.STALE:
                    self.assertEqual(request.outcome, RefreshRequestOutcome.TITLE_NOT_FOUND)
                    self.assertEqual(summary.skipped_upstream_missing, 1)
                if spent:
                    self.assertEqual(request.blocked_reason, "quota_exhausted")

    def test_stale_metered_only_offer_cannot_suppress_background_refresh(self):
        from datetime import timedelta
        from availability.freshness import freshness_for
        movie = make_movie(16)
        offer = local_offer(movie, country="NL")
        offer.last_seen = timezone.now() - freshness_for("streaming_availability") - timedelta(seconds=1)
        offer.save(update_fields=["last_seen"])
        request = RefreshRequest.objects.create(movie=movie, source="streaming_availability", country="NL", priority=RefreshPriority.STALE)
        service = MagicMock()
        summary = BulkRefreshSummary(remaining_budget=10, budget=10)
        with patch.object(source_policy, "consult_metered_only_when_unresolved", return_value=True), patch.object(rr, "_service_for_scope", return_value=service):
            entry = ar.title_source_report([movie.pk], country="NL")[movie.pk]
            decision = source_policy.decide_sources_for_title(entry, applicable_slugs=["justwatch", "streaming_availability"])
            self.assertIn("streaming_availability", decision["allowed_sources"])
            rr._drain_one(request, summary=summary, service=service, limiter=unlimited(), rate_limit_hit=False)
        service.refresh_content.assert_called_once_with(movie, country="NL")
        request.refresh_from_db()
        self.assertEqual(request.status, RefreshRequestStatus.DONE)
        self.assertEqual(summary.skipped_policy_gated, 0)

    def test_permanent_negative_query_growth(self):
        self.assertEqual(connection.vendor, "postgresql", "Query measurements require isolated PostgreSQL")
        movies = [make_movie(1000 + i) for i in range(200)]
        RefreshRequest.objects.bulk_create([
            RefreshRequest(movie=movie, source="streaming_availability", country="NL",
                status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND)
            for movie in movies
        ])
        for size in (1, 100, 200):
            requests = [RefreshRequest(movie=movie, source="streaming_availability", country="NL") for movie in movies[:size]]
            with CaptureQueriesContext(connection) as per_request:
                for request in requests:
                    self.assertTrue(rr._has_reusable_permanent_negative(request))
            with CaptureQueriesContext(connection) as batched:
                scopes = rr._permanent_negative_scopes(requests)
                self.assertEqual(len(scopes), size)
                for request in requests:
                    self.assertTrue(rr._has_reusable_permanent_negative(request, permanent_negative_scopes=scopes))
            self.assertEqual(len(per_request), 2 * size)
            self.assertEqual(len(batched), 2)
            print(f"NEGATIVE_QUERY_COUNTS titles={size} per_request={len(per_request)} batched={len(batched)}")


    def test_page_negative_snapshot_preserves_scope_and_is_reloaded(self):
        movie = make_movie(17)
        RefreshRequest.objects.create(movie=movie, source="streaming_availability", country="NL",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND)
        requests = [RefreshRequest(movie=movie, source=source, country=country) for country, source in (
            ("NL", "streaming_availability"), ("JP", "streaming_availability"), ("NL", "watchmode"), ("", "streaming_availability"))]
        scopes = rr._permanent_negative_scopes(requests)
        self.assertEqual(scopes, {(movie.pk, "streaming_availability", "NL")})
        self.assertEqual([rr._has_reusable_permanent_negative(r, permanent_negative_scopes=scopes) for r in requests], [True, False, False, False])
        RefreshRequest.objects.create(movie=movie, source="streaming_availability", country="NL",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.NO_OFFER)
        self.assertEqual(rr._permanent_negative_scopes(requests), set())

    def test_worker_batches_history_reads_and_reuses_negatives(self):
        movie = make_movie(18)
        RefreshRequest.objects.create(movie=movie, source="streaming_availability", country="NL",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND)
        request = RefreshRequest.objects.create(movie=movie, source="streaming_availability", country="NL", priority=RefreshPriority.STALE)
        summary = BulkRefreshSummary(remaining_budget=10, budget=10)
        service = MagicMock()
        with patch.object(rr, "is_stale", return_value=True), patch.object(rr, "_permanent_negative_scopes", wraps=rr._permanent_negative_scopes) as batch_reader:
            rr.drain_refresh_requests(summary=summary, service=service, limiter=unlimited())
        batch_reader.assert_called_once()
        service.refresh_content.assert_not_called()
        request.refresh_from_db()
        self.assertEqual(request.outcome, RefreshRequestOutcome.TITLE_NOT_FOUND)
        self.assertEqual(summary.skipped_upstream_missing, 1)


    def test_worker_query_growth_for_1_100_200_titles(self):
        self.assertEqual(connection.vendor, "postgresql")
        movies = [make_movie(2000 + i) for i in range(200)]
        RefreshRequest.objects.bulk_create([
            RefreshRequest(movie=movie, source="streaming_availability", country="NL",
                status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND)
            for movie in movies
        ])
        for size in (1, 100, 200):
            RefreshRequest.objects.bulk_create([
                RefreshRequest(movie=movie, source="streaming_availability", country="NL", priority=RefreshPriority.STALE)
                for movie in movies[:size]
            ])
            service = MagicMock()
            summary = BulkRefreshSummary(remaining_budget=size + 1, budget=size + 1)
            with patch.object(rr, "is_stale", return_value=True), patch.object(rr, "rate_limiter_for", side_effect=lambda source: unlimited()), patch.object(rr, "_service_for_scope", return_value=service):
                with CaptureQueriesContext(connection) as measured:
                    rr.drain_refresh_requests(summary=summary, service=service, limiter=unlimited())
            service.refresh_content.assert_not_called()
            self.assertEqual(summary.skipped_upstream_missing, size)
            self.assertLessEqual(len(measured), size + 6)
            print(f"NEGATIVE_WORKER_QUERIES titles={size} total={len(measured)} basis=mocked_freshness_and_unlimited_budgets")


    def test_explicit_answer_invalidates_page_negative_for_legacy_alias(self):
        movie = make_movie(19)
        RefreshRequest.objects.create(movie=movie, source="streaming_availability", country="NL",
            status=RefreshRequestStatus.DONE, outcome=RefreshRequestOutcome.TITLE_NOT_FOUND)
        RefreshRequest.objects.create(movie=movie, source="streaming_availability", country="NL", priority=RefreshPriority.EXPLICIT)
        background = RefreshRequest.objects.create(movie=movie, source="", country="NL", priority=RefreshPriority.STALE)
        service = MagicMock()
        summary = BulkRefreshSummary(budget=5, remaining_budget=5)
        with patch.object(rr, "is_stale", return_value=True), patch.object(rr, "_service_for_scope", return_value=service):
            rr.drain_refresh_requests(summary=summary, service=service, limiter=unlimited())
        self.assertEqual(service.refresh_content.call_count, 2)
        background.refresh_from_db()
        self.assertEqual(background.outcome, RefreshRequestOutcome.NO_OFFER)
        self.assertEqual(summary.skipped_upstream_missing, 0)
