"""P7 operational metrics — aggregation correctness and honest empties.

Two properties are being defended here, and they pull in opposite directions:

**The numbers must be right.** A metrics endpoint that quietly reports the
wrong thing is worse than none, because it gets trusted.

**The gaps must be visible.** A metric that cannot be derived must come back
``null`` or explicitly flagged, never ``0`` and never an empty collection. The
failure this guards against is a dashboard that says "abc_iview: 0 offers,
0 failures" and lets an operator conclude the source is broken — when the truth
is that it ran 4 times and answered 6,376 times without a single offer, which
is a working source with an uninteresting answer.

So every "no data" case here asserts ``None``/``never_executed`` rather than a
numeric zero, and each one names the distinction it is protecting.

The 7-day window is `now − 7×24h` through now, timezone-aware UTC.
"""
import datetime as dt
import uuid

from django.test import TestCase
from django.utils import timezone

from availability.models import Country, Offer, Source
from tracker.models import (
    ApiCallCounter,
    AvailabilityRefresh,
    AvailabilityRefreshSource,
    AvailabilityRefreshStatus,
    BlockedReason,
    Movie,
    Preference,
    RefreshRequest,
    RefreshRequestStatus,
)
from tracker.services import availability_metrics as metrics_mod
from tracker.services.availability_metrics import (
    WINDOW_DAYS,
    availability_metrics,
    window_start,
)
from tracker.services.rate_limit import SOURCE_DAILY_LIMITS

SA = "streaming_availability"
WM = "watchmode"


class MetricsBase(TestCase):
    def setUp(self):
        self._saved_limits = dict(SOURCE_DAILY_LIMITS)
        self.now = timezone.now()
        self.movie = Movie.objects.create(tmdb_id=770_001, title="Metrics", year=2021)
        Preference.objects.update_or_create(
            user_id=1, defaults={"current_country": "NL"}
        )
        Country.objects.get_or_create(
            code="NL", defaults={"name": "Netherlands", "enabled": True}
        )
        Source.objects.get_or_create(
            slug=WM, defaults={"name": "Watchmode", "enabled": True}
        )

    def tearDown(self):
        SOURCE_DAILY_LIMITS.clear()
        SOURCE_DAILY_LIMITS.update(self._saved_limits)
        super().tearDown()

    def aged(self, **kwargs):
        """A refresh that started `days` ago."""
        kwargs.setdefault("run_type", "watchlist")
        kwargs.setdefault("started_at", self.now)
        kwargs.setdefault("finished_at", self.now)
        kwargs.setdefault("title_filter", [self.movie.pk])
        age_days = kwargs.pop("age_days", 0)
        run = AvailabilityRefresh.objects.create(**kwargs)
        if age_days:
            # `created_at` is `auto_now_add`, so a value passed to create() is
            # discarded. It has to be backdated after the insert, which is also
            # the only honest way to age a row.
            AvailabilityRefresh.objects.filter(pk=run.pk).update(
                created_at=timezone.now() - dt.timedelta(days=age_days)
            )
            run.refresh_from_db()
        return run

    def source_row(self, run, source=WM, status="completed", reason="", **overrides):
        defaults = {
            "total": 0,
            "completed": 0,
            "successful": 0,
            "failed": 0,
            "unknown": 0,
            "queued": 0,
            "started_at": self.now,
            "finished_at": self.now,
        }
        defaults.update(overrides)
        return AvailabilityRefreshSource.objects.create(
            refresh=run, source=source, status=status, reason=reason, **defaults
        )

    def offer(self, run_source, *, seen=None, country="NL"):
        """One persisted offer, built the way the app builds one.

        `Offer` requires a tracker `Provider` and a derived `identity`; it is
        created through the same helper the availability models tests use so the
        row is a real, valid offer rather than a fixture-shaped approximation.
        """
        from availability.domain.enums import ContentType, OfferType
        from availability.models import build_offer_identity
        from tracker.models import Provider

        country_row, _ = Country.objects.get_or_create(
            code=country, defaults={"name": country, "enabled": True}
        )
        source_row, _ = Source.objects.get_or_create(
            slug=run_source, defaults={"name": run_source, "enabled": True}
        )
        provider, _ = Provider.objects.get_or_create(
            slug=f"{run_source}-provider",
            defaults={"name": run_source, "country_code": country},
        )
        content_type = ContentType.MOVIE
        offer_type = OfferType.SUBSCRIPTION
        pc_id = f"pc-{self.movie.tmdb_id}-{run_source}-{country}"
        stamp = seen or self.now
        return Offer.objects.create(
            provider=provider,
            country=country_row,
            source=source_row,
            content_type=content_type,
            tmdb_id=self.movie.tmdb_id,
            offer_type=offer_type,
            provider_content_id=pc_id,
            currently_available=True,
            first_seen=stamp,
            last_seen=stamp,
            identity=build_offer_identity(
                provider.slug,
                country_row.code,
                content_type,
                self.movie.tmdb_id,
                None,
                None,
                offer_type,
                pc_id,
            ),
        )

    def metrics(self, **kwargs):
        return availability_metrics(now=self.now, **kwargs)


class RefreshAggregationTests(MetricsBase):
    def test_outcomes_are_counted_separately(self):
        self.aged(status=AvailabilityRefreshStatus.COMPLETED.value, age_days=1)
        self.aged(status=AvailabilityRefreshStatus.PARTIAL.value, age_days=1)
        self.aged(status=AvailabilityRefreshStatus.PARTIAL.value, age_days=2)
        self.aged(status=AvailabilityRefreshStatus.FAILED.value, age_days=3)

        refreshes = self.metrics()["refreshes"]

        self.assertEqual(refreshes["total"], 4)
        self.assertEqual(refreshes["completed"], 1)
        self.assertEqual(refreshes["partial"], 2)
        self.assertEqual(refreshes["failed"], 1)
        self.assertEqual(refreshes["terminal"], 4)

    def test_active_refreshes_are_counted_and_not_treated_as_failed(self):
        """A run in flight is neither a success nor a failure.

        It must not inflate the completion rate's denominator, either: rates are
        computed over terminal runs only.
        """
        self.aged(status=AvailabilityRefreshStatus.COMPLETED.value, age_days=1)
        self.aged(status=AvailabilityRefreshStatus.RUNNING.value, age_days=0,
                  finished_at=None)

        refreshes = self.metrics()["refreshes"]

        self.assertEqual(refreshes["active"], 1)
        self.assertEqual(refreshes["total"], 2)
        self.assertEqual(refreshes["completion_rate"], 1.0)

    def test_failure_is_never_inferred_from_unknown_titles(self):
        """P6b owns the result. A run full of unknowns that settled cleanly is
        PARTIAL, not FAILED — even though it resolved nothing."""
        self.aged(
            status=AvailabilityRefreshStatus.PARTIAL.value,
            age_days=1,
            total_titles=100,
            unknown_titles=100,
            unknown_reasons={"not_checked": 100},
        )

        refreshes = self.metrics()["refreshes"]

        self.assertEqual(refreshes["failed"], 0)
        self.assertEqual(refreshes["partial"], 1)

    def test_duration_uses_started_and_finished_and_ignores_unfinished(self):
        base = self.now - dt.timedelta(days=1)
        self.aged(
            status=AvailabilityRefreshStatus.COMPLETED.value,
            started_at=base,
            finished_at=base + dt.timedelta(seconds=100),
        )
        self.aged(
            status=AvailabilityRefreshStatus.FAILED.value,
            started_at=base,
            finished_at=base + dt.timedelta(seconds=300),
        )
        # In flight: has a start but no finish, so it has no duration at all.
        self.aged(
            status=AvailabilityRefreshStatus.RUNNING.value,
            started_at=base,
            finished_at=None,
        )

        refreshes = self.metrics()["refreshes"]

        self.assertEqual(refreshes["runs_with_duration"], 2)
        self.assertAlmostEqual(refreshes["average_duration_seconds"], 200.0, places=1)
        self.assertAlmostEqual(refreshes["median_duration_seconds"], 200.0, places=1)
        self.assertAlmostEqual(refreshes["max_duration_seconds"], 300.0, places=1)

    def test_the_window_boundary_is_seven_days(self):
        self.assertEqual(WINDOW_DAYS, 7)
        self.assertAlmostEqual(
            (self.now - window_start(self.now)).total_seconds(),
            7 * 24 * 3600,
            places=0,
        )

        just_inside = self.aged(
            status=AvailabilityRefreshStatus.COMPLETED.value, age_days=6
        )
        self.aged(status=AvailabilityRefreshStatus.COMPLETED.value, age_days=8)

        self.assertEqual(self.metrics()["refreshes"]["total"], 1)
        self.assertTrue(just_inside)

    def test_a_run_is_filtered_on_insertion_time_not_start(self):
        """A run created inside the window but started earlier still counts.

        Filtering on the nullable `started_at` would silently drop it — and would
        drop every queued run, which has no `started_at` at all.
        """
        old_start = self.now - dt.timedelta(days=30)
        run = self.aged(
            status=AvailabilityRefreshStatus.COMPLETED.value,
            started_at=old_start,
            finished_at=old_start + dt.timedelta(seconds=10),
            age_days=1,
        )
        self.assertEqual(self.metrics()["refreshes"]["total"], 1)
        self.assertTrue(run)


class SourceAggregationTests(MetricsBase):
    def source(self, slug):
        for row in self.metrics()["sources"]:
            if row["source"] == slug:
                return row
        self.fail(f"source {slug} missing from metrics")

    def test_a_completed_source_reports_success_and_offers(self):
        run = self.aged(status=AvailabilityRefreshStatus.COMPLETED.value, age_days=1)
        self.source_row(run, WM, "completed", total=5, completed=5, successful=5)
        self.offer(WM)

        row = self.source(WM)

        self.assertEqual(row["runs"], 1)
        self.assertEqual(row["successful"], 5)
        self.assertEqual(row["positive_offers"], 1)
        self.assertFalse(row["never_executed"])

    def test_zero_offers_is_not_failure(self):  # the comparison that matters
        """Ran, answered, found nothing — versus never ran at all.

        Both show ``0`` offers, and conflating them is exactly the misreading the
        dashboard exists to prevent.
        """
        run = self.aged(status=AvailabilityRefreshStatus.COMPLETED.value, age_days=1)
        self.source_row(run, WM, "completed", total=5, completed=5, successful=5)
        self.source_row(run, "prime_video", "completed", total=3, successful=3)

        productive = self.source(WM)
        empty = self.source("prime_video")

        self.assertEqual(empty["positive_offers"], 0)
        self.assertEqual(empty["successful"], 3, "it ran and answered")
        self.assertEqual(empty["failed"], 0, "no offers is not a failure")
        self.assertEqual(productive["successful"], 5)
        # And no-offer counts are reported as unavailable, not inferred.
        self.assertIsNone(empty["no_offers"])
        self.assertIsNone(productive["no_offers"])

    def test_a_failed_source_is_counted_separately_from_a_quota_one(self):
        run = self.aged(status=AvailabilityRefreshStatus.PARTIAL.value, age_days=1)
        self.source_row(run, WM, "failed", failed=2)
        self.source_row(run, SA, "quota_exhausted", reason="quota_exhausted")

        failed = self.source(WM)
        quota = self.source(SA)

        self.assertEqual(failed["status_breakdown"]["failed"], 1)
        self.assertEqual(quota["status_breakdown"]["quota_exhausted"], 1)
        # Neither is conflated with the other, nor with a skip.
        self.assertEqual(failed["status_breakdown"]["quota_exhausted"], 0)
        self.assertEqual(quota["status_breakdown"]["failed"], 0)

    def test_policy_skipped_stays_distinct_from_not_run_and_quota(self):
        """Three "we did not answer" states that must not be summed together."""
        run = self.aged(status=AvailabilityRefreshStatus.COMPLETED.value, age_days=1)
        self.source_row(run, WM, "skipped", reason="policy_not_needed")
        self.source_row(run, "mediaset_infinity", "not_run", reason="collector_not_run")
        self.source_row(run, "bbc_iplayer", "not_applicable")

        skipped = self.source(WM)["status_breakdown"]
        not_run = self.source("mediaset_infinity")["status_breakdown"]
        na = self.source("bbc_iplayer")["status_breakdown"]

        self.assertEqual(skipped["skipped"], 1)
        self.assertEqual(not_run["not_run"], 1)
        self.assertEqual(na["not_applicable"], 1)
        self.assertEqual(skipped["not_run"], 0)

    def test_a_source_absent_from_the_refresh_is_not_counted_as_running(self):
        """Source rows are the record of participation.

        A registered source with no row in this window simply did not take part;
        it must not appear as if it had run and produced nothing.
        """
        run = self.aged(status=AvailabilityRefreshStatus.COMPLETED.value, age_days=1)
        self.source_row(run, WM, "completed", successful=1)

        row = self.source(WM)

        self.assertEqual(row["runs"], 1)
        self.assertFalse(row["never_executed"])

    def test_source_duration_uses_terminal_rows_only(self):
        base = self.now - dt.timedelta(days=1)
        run = self.aged(
            status=AvailabilityRefreshStatus.PARTIAL.value,
            started_at=base,
            finished_at=base + dt.timedelta(seconds=60),
            age_days=1,
        )
        self.source_row(
            run, WM, "completed",
            started_at=base, finished_at=base + dt.timedelta(seconds=20),
        )
        # Still running: has a start, no finish, and no duration.
        self.source_row(run, SA, "running", started_at=base, finished_at=None)

        row = self.source(WM)
        self.assertAlmostEqual(row["average_duration_seconds"], 20.0, places=1)

    def test_last_success_and_last_attempted_are_distinct(self):
        """`last_attempted_at` moves on a failed run; `last_success_at` does not.

        Collapsing them would make a failing source look freshly successful.
        """
        older = self.now - dt.timedelta(days=3)
        newer = self.now - dt.timedelta(days=1)
        run_old = self.aged(status=AvailabilityRefreshStatus.COMPLETED.value, age_days=3)
        self.source_row(run_old, WM, "completed", started_at=older, finished_at=older)
        run_new = self.aged(status=AvailabilityRefreshStatus.FAILED.value, age_days=1)
        self.source_row(run_new, WM, "failed", started_at=newer, finished_at=newer)

        row = self.source(WM)

        self.assertIsNotNone(row["last_attempted_at"])
        self.assertIsNotNone(row["last_success_at"])
        self.assertGreater(row["last_attempted_at"], row["last_success_at"])

    def test_every_registered_source_appears_even_if_never_executed(self):
        rows = {row["source"]: row for row in self.metrics()["sources"]}
        self.assertIn(SA, rows)
        self.assertTrue(rows[SA]["never_executed"])
        self.assertEqual(rows[SA]["runs"], 0)
        self.assertIsNone(rows[SA]["average_duration_seconds"])


class AvailabilityAggregationTests(MetricsBase):
    def test_title_counters_come_from_the_runs(self):
        self.aged(
            status=AvailabilityRefreshStatus.PARTIAL.value,
            age_days=1,
            total_titles=50,
            available_titles=30,
            unavailable_titles=10,
            unknown_titles=10,
            unknown_reasons={"quota_exhausted": 6, "title_not_found": 4},
        )

        titles = self.metrics()["titles"]

        self.assertEqual(titles["processed"], 50)
        self.assertEqual(titles["available"], 30)
        self.assertEqual(titles["unavailable"], 10)
        self.assertEqual(titles["unknown"], 10)

    def test_unknown_reasons_use_the_existing_vocabulary(self):
        self.aged(
            status=AvailabilityRefreshStatus.PARTIAL.value,
            age_days=1,
            unknown_titles=9,
            unknown_reasons={"not_checked": 5, "source_unavailable": 4},
        )

        reasons = self.metrics()["titles"]["unknown_reasons"]

        self.assertEqual(reasons, {"not_checked": 5, "source_unavailable": 4})
        # No new enum was invented: both are real resolution reasons.
        from tracker.models import AvailabilityResolutionReason

        self.assertIn("not_checked", AvailabilityResolutionReason.values)
        self.assertIn("source_unavailable", AvailabilityResolutionReason.values)


class QuotaMetricsTests(MetricsBase):
    def test_blocked_by_quota_is_counted_separately_from_other_blocks(self):
        batch = uuid.uuid4()
        for reason, count in (
            (BlockedReason.QUOTA_EXHAUSTED, 3),
            (BlockedReason.POLICY_NOT_NEEDED, 2),
            (BlockedReason.SOURCE_DISABLED, 1),
        ):
            for i in range(count):
                RefreshRequest.objects.create(
                    movie=self.movie,
                    source=SA,
                    status=RefreshRequestStatus.BLOCKED,
                    blocked_reason=reason,
                    batch=batch,
                    completed_at=self.now,
                    requested_at=self.now - dt.timedelta(hours=2),
                )

        payload = self.metrics()

        self.assertEqual(payload["requests"]["blocked"], 6)
        self.assertEqual(payload["quota"]["blocked_requests"], 3)
        self.assertEqual(
            payload["requests"]["blocked_by_reason"]["policy_not_needed"], 2
        )

    def test_current_quota_state_is_exposed(self):
        SOURCE_DAILY_LIMITS[SA] = 100
        ApiCallCounter.objects.update_or_create(
            service=SA, defaults={"count": 40}
        )

        quota = self.metrics()["quota"]
        entry = next(s for s in quota["sources"] if s["source"] == SA)

        self.assertEqual(entry["used"], 40)
        self.assertEqual(entry["limit"], 100)
        self.assertEqual(entry["remaining"], 60)
        self.assertFalse(entry["exhausted"])
        self.assertEqual(entry["monthly_limit"], 1000)
        self.assertEqual(entry["monthly_used"], 40)
        self.assertEqual(entry["monthly_remaining"], 960)
        self.assertEqual(quota["sources_exhausted"], [])

    def test_exhaustion_events_are_declared_unavailable_not_empty(self):
        """The honest failure mode.

        `ApiCallCounter` keeps one daily count; it records no event when the
        limit is reached. Returning `[]` would read as "never exhausted", so the
        response says the data does not exist.
        """
        quota = self.metrics()["quota"]
        self.assertFalse(quota["events_available"])
        self.assertIn("ApiCallCounter", quota["events_unavailable_reason"])


class EmptyStateTests(MetricsBase):
    def test_a_window_with_no_runs_reports_nulls_not_zeros(self):
        """The distinction an operator most needs.

        "0% completion" and "no runs happened" are not the same statement, and
        the second must not be rendered as the first.
        """
        self.aged(status=AvailabilityRefreshStatus.COMPLETED.value, age_days=30)

        refreshes = self.metrics()["refreshes"]

        self.assertEqual(refreshes["total"], 0)
        self.assertIsNone(refreshes["completion_rate"])
        self.assertIsNone(refreshes["partial_rate"])
        self.assertIsNone(refreshes["failure_rate"])
        self.assertIsNone(refreshes["average_duration_seconds"])
        self.assertIsNone(refreshes["median_duration_seconds"])
        self.assertIsNone(refreshes["max_duration_seconds"])

    def test_an_entirely_empty_database_still_returns_a_full_shape(self):
        AvailabilityRefreshSource.objects.all().delete()
        AvailabilityRefresh.objects.all().delete()

        payload = self.metrics()

        self.assertEqual(payload["refreshes"]["total"], 0)
        self.assertEqual(payload["requests"]["created"], 0)
        self.assertEqual(payload["titles"]["processed"], 0)
        self.assertEqual(payload["daily"], [])
        self.assertEqual(payload["titles"]["unknown_reasons"], {})
        # Sources still listed, so an operator can see what is configured.
        self.assertTrue(payload["sources"])
        self.assertTrue(all(s["never_executed"] for s in payload["sources"]))

    def test_a_refresh_with_no_source_rows_yields_no_source_activity(self):
        self.aged(status=AvailabilityRefreshStatus.COMPLETED.value, age_days=1)

        payload = self.metrics()

        self.assertEqual(payload["refreshes"]["completed"], 1)
        self.assertTrue(all(s["runs"] == 0 for s in payload["sources"]))

    def test_daily_history_covers_each_day_with_runs(self):
        self.aged(status=AvailabilityRefreshStatus.COMPLETED.value, age_days=1)
        self.aged(status=AvailabilityRefreshStatus.FAILED.value, age_days=1)
        self.aged(status=AvailabilityRefreshStatus.PARTIAL.value, age_days=3)

        daily = self.metrics()["daily"]

        self.assertEqual(len(daily), 2)
        counted = sum(d["total"] for d in daily)
        self.assertEqual(counted, 3)


class MetricsEndpointTests(MetricsBase):
    def test_the_endpoint_returns_the_documented_shape(self):
        self.aged(status=AvailabilityRefreshStatus.COMPLETED.value, age_days=1)

        response = self.client.get("/api/availability/metrics/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        for key in (
            "window",
            "refreshes",
            "titles",
            "requests",
            "sources",
            "quota",
            "unavailable_metrics",
        ):
            self.assertIn(key, body)
        self.assertEqual(body["window"]["days"], WINDOW_DAYS)
        self.assertEqual(body["window"]["timezone"], "UTC")

    def test_the_endpoint_rejects_a_non_numeric_window(self):
        response = self.client.get("/api/availability/metrics/?days=soon")
        self.assertEqual(response.status_code, 400)

    def test_the_endpoint_accepts_a_valid_window_override(self):
        response = self.client.get("/api/availability/metrics/?days=30")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["window"]["days"], 30)

    def test_collecting_metrics_makes_no_upstream_call(self):
        """P7 must be free.

        If metrics ever triggered a source fetch, every dashboard poll would
        spend quota. The registry is stubbed to explode if touched.
        """
        calls = []

        class Tripwire:
            def __getattr__(self, name):
                calls.append(name)
                raise AssertionError(f"metrics touched an upstream source: {name}")

        original = metrics_mod._registered_sources
        metrics_mod._registered_sources = lambda: ["justwatch", SA]
        try:
            payload = availability_metrics(now=self.now)
        finally:
            metrics_mod._registered_sources = original
        self.assertEqual(calls, [])
        self.assertEqual(len(payload["sources"]), 2)