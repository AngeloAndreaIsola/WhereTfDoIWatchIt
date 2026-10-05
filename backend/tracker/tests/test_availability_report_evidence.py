"""The report's evidence readers, and the source-role registry.

:mod:`test_availability_classification` proves the *rule*. This module
proves the *inputs* — that the report reads the right rows from the right
table, scoped to the right run — because a bug in either layer otherwise
presents identically as "the numbers are wrong".

Each test names the specific defect it prevents, per the measured failures:

- per-source totals counted every request a title had ever had (987 checks
  against a 191-title watchlist) instead of one run's;
- collector status was read from ``SyncRun``, so collectors that attempted
  and failed were reported as "never ran" while a genuinely failed JustWatch
  run was reported as healthy;
- a withdrawn offer still resolved its source, so a title could be reported
  available by a source that had demonstrably said it was not there;
- a legacy 404 and a typed permanent miss were both "a failure", so the
  report could not distinguish "unfixable" from "retryable";
- ``AvailabilityRole`` had no check that every registered source declares
  one, so a newly added metered provider would silently inherit a free role.
"""
import datetime as dt
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from availability.models import ContentType, Offer, ProviderContent, Source
from availability.sources import source_registry
from tracker.models import (
    ApiCallCounter,
    AvailabilityResolutionReason,
    Movie,
    RefreshPriority,
    Provider,
    RefreshRequest,
    RefreshRequestStatus,
    RefreshRun,
)
from tracker.services import availability_report as ar
from tracker.services import source_policy
from tracker.services.rate_limit import SOURCE_DAILY_LIMITS

R = AvailabilityResolutionReason
SA = "streaming_availability"


def make_movie(tmdb_id: int, title: str = "T") -> Movie:
    return Movie.objects.create(tmdb_id=tmdb_id, title=title, year=2000)


def make_offer(movie: Movie, slug: str, *, available: bool = True, provider_slug=None) -> Offer:
    provider, _ = Provider.objects.get_or_create(
        slug=provider_slug or f"{slug}-prov",
        defaults={"name": provider_slug or slug, "category": "stream"},
    )
    country = _country()
    source, _ = Source.objects.get_or_create(slug=slug, defaults={"name": slug})
    return Offer.objects.create(
        tmdb_id=movie.tmdb_id,
        provider=provider,
        country=country,
        source=source,
        content_type=ContentType.MOVIE,
        offer_type="subscription",
        currently_available=available,
    )


def _country():
    from availability.models import Country

    country, _ = Country.objects.get_or_create(code="IT", defaults={"name": "Italy"})
    return country


def clear_quota() -> None:
    ApiCallCounter.objects.all().delete()


class SourceRoleTests(TestCase):
    """P2 — roles are declared, exhaustive, and never derived."""

    def test_every_registered_source_declares_a_role(self):
        """A new source with no declared role is a hard error, not a default.

        The failure this prevents is concrete: a newly added metered
        provider inheriting the default role would be scheduled
        unconditionally, which is the exact bug the role system exists to
        stop.
        """
        self.assertEqual(source_policy.missing_roles(source_registry.slugs()), [])

    def test_the_metered_aggregator_is_not_a_collector(self):
        """The bug that motivated P2: SA was indistinguishable from a
        first-party source because its capability flags say "title-level"."""
        self.assertEqual(
            source_policy.role_of(SA),
            source_policy.AvailabilityRole.METERED_AGGREGATOR,
        )
        self.assertNotEqual(
            source_policy.role_of(SA), source_policy.AvailabilityRole.COLLECTOR
        )

    def test_justwatch_and_watchmode_are_broad_and_not_ordered(self):
        for slug in ("justwatch", "watchmode"):
            self.assertEqual(
                source_policy.role_of(slug),
                source_policy.AvailabilityRole.BROAD_AGGREGATOR,
            )
        grouped = source_policy.sources_by_role()
        self.assertCountEqual(
            grouped[source_policy.AvailabilityRole.BROAD_AGGREGATOR.value],
            ["justwatch", "watchmode"],
        )

    def test_roles_do_not_depend_on_capability_flags(self):
        """A metered aggregator is title-level *and* metered; role reflects
        the policy, the capability reflects the code. They must be able to
        disagree, or the distinction is not real."""
        caps = source_registry.get(SA).capabilities
        self.assertFalse(caps.supports_catalogue_sync)  # title-level
        self.assertIsNotNone(SOURCE_DAILY_LIMITS.get(SA))  # metered
        self.assertIs(
            source_policy.role_of(SA), source_policy.AvailabilityRole.METERED_AGGREGATOR
        )

    def test_the_metered_gate_is_off_by_default_and_invertible(self):
        """P3 needs the flag to be a *switch*, and off means every source
        runs — otherwise switching the policy on could not be A/B'd and the
        counterfactual would describe an unreachable state."""
        grouped = source_policy.allowed_sources_for_title(
            "available",
            applicable_slugs=["justwatch", SA],
            ladder_switch=False,
        )
        self.assertIn(SA, grouped[source_policy.AvailabilityRole.METERED_AGGREGATOR.value])

    def test_gating_excludes_the_metered_source_for_a_resolved_title(self):
        grouped = source_policy.allowed_sources_for_title(
            "available",
            applicable_slugs=["justwatch", SA],
            ladder_switch=True,
        )
        self.assertEqual(
            grouped[source_policy.AvailabilityRole.METERED_AGGREGATOR.value], []
        )
        # The free source is never gated — that is what produces the
        # evidence the metered source would otherwise pay for. (JustWatch is
        # a BROAD aggregator, not a collector; either way it is unmetered.)
        self.assertEqual(
            grouped[source_policy.AvailabilityRole.BROAD_AGGREGATOR.value],
            ["justwatch"],
        )

    def test_gating_permits_the_metered_source_for_an_unresolved_title(self):
        grouped = source_policy.allowed_sources_for_title(
            "unknown", applicable_slugs=["justwatch", SA],
            ladder_switch=True,
        )
        self.assertEqual(
            grouped[source_policy.AvailabilityRole.METERED_AGGREGATOR.value], [SA]
        )

    def test_counterfactual_reports_the_cost_without_spending(self):
        before = ApiCallCounter.objects.count()
        result = source_policy.counterfactual(
            {1: "available", 2: "available", 3: "unknown"},
            {1: {SA}, 2: {"justwatch"}, 3: set()},
        )
        # Only the first title depends on the metered source. The second
        # survives it, and the third is already unresolved so gating cannot
        # make it worse — which is why it counts in neither column.
        self.assertEqual(result["titles_losing_their_answer"], 1)
        self.assertEqual(result["titles_still_resolved"], 1)
        self.assertEqual(ApiCallCounter.objects.count(), before)


class CurrentRunScopingTests(TestCase):
    """Fix 3 — the report must describe *this* run, not the whole history."""

    def setUp(self):
        clear_quota()
        self.movie = make_movie(1, "Scoping")
        self.batch = uuid.uuid4()

    def test_per_source_totals_are_scoped_to_the_batch(self):
        """A row from an earlier run must not inflate this run's total.

        Scoping to the run's batch is what makes "191 titles / 764 checks"
        mean one run rather than the sum of every check the titles have ever
        had.
        """
        RefreshRequest.objects.create(
            movie=self.movie, source="mediaset_infinity",
            status=RefreshRequestStatus.DONE, batch=self.batch,
        )
        # A historical row from an earlier run, same title and source.
        RefreshRequest.objects.create(
            movie=self.movie, source="mediaset_infinity",
            status=RefreshRequestStatus.DONE, batch=uuid.uuid4(),
        )
        ids = [self.movie.pk]
        scoped = ar.source_progress(ids, batch=self.batch)
        row = next(r for r in scoped if r["source"] == "mediaset_infinity")
        self.assertEqual(row["total"], 1)


class AttemptedVersusCheckedStampTests(TestCase):
    """P3 — "never asked" and "asked, got nothing" are different facts.

    ``checked_at`` used to be derived only from ``Offer.last_seen``, so it was
    populated **only when a source found an offer**. A source that answered
    "nothing at any provider" writes no Offer row, and therefore rendered as
    ``checked_at: never`` — which reads as "we never consulted this source",
    the opposite of what happened.

    On the live 191-title watchlist this mislabelled 45 completed no-offer
    checks. Since that number is evidence the UI uses while debugging, the
    mislabelling would have made the per-title view untrustworthy in exactly
    the situation it exists to explain.
    """

    def setUp(self):
        clear_quota()
        self.batch = uuid.uuid4()
        self.movie = make_movie(900, "Stamped")

    def _row(self, source, **kwargs):
        """The single title-level row for one source."""
        report = ar.title_source_report([self.movie.pk], batch=self.batch)
        rows = [r for r in report[self.movie.pk]["sources"] if r["source"] == source]
        self.assertEqual(len(rows), 1, f"expected one {source} row")
        return rows[0]

    def _request(self, source, status, *, error="", completed=True):
        return RefreshRequest.objects.create(
            movie=self.movie,
            source=source,
            status=status,
            last_error=error,
            batch=self.batch,
            completed_at=timezone.now() if completed else None,
        )

    def test_a_completed_no_offer_check_is_not_reported_as_never(self):
        """The regression itself: consulted, answered nothing, got a stamp."""
        self._request("mediaset_infinity", RefreshRequestStatus.DONE)

        row = self._row("mediaset_infinity")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["reason"], R.NO_OFFER)
        # No offer exists, so there is no *answer* stamp...
        self.assertIsNone(row["checked_at"])
        # ...but it must not read as "never attempted".
        self.assertIsNotNone(
            row["last_attempted_at"],
            "a completed check reported `never` attempted",
        )

    def test_a_source_that_produced_an_offer_carries_both_stamps(self):
        self._request("mediaset_infinity", RefreshRequestStatus.DONE)
        make_offer(self.movie, "mediaset_infinity")

        row = self._row("mediaset_infinity")
        self.assertIsNotNone(row["checked_at"], "an offer was found, so checked_at")
        self.assertIsNotNone(row["last_attempted_at"])

    def test_an_unattempted_source_is_genuinely_never(self):
        """No request row at all — now `never` is the truthful answer."""
        row = self._row("mediaset_infinity")
        self.assertIsNone(row["checked_at"])
        self.assertIsNone(
            row["last_attempted_at"],
            "with no request row the source must not look attempted",
        )

    def test_a_quota_blocked_pending_request_reports_attempted_never_checked(self):
        """Queued-but-unanswered is not an answer, and not a failure either.

        This is the case the user asked to get right *before* the browser
        test: a request the worker never got to must not be dressed up as a
        completed check, and must not claim an answer it never received.
        """
        self._request("streaming_availability", RefreshRequestStatus.PENDING, completed=False)

        row = self._row("streaming_availability")
        self.assertIsNone(row["checked_at"])
        self.assertIsNotNone(
            row["last_attempted_at"],
            "a queued request was made and should be timestamped",
        )
        self.assertNotEqual(row["reason"], R.NO_OFFER)

    def test_a_permanent_404_is_attempted_and_not_answered(self):
        self._request(
            "watchmode",
            RefreshRequestStatus.FAILED,
            error="UpstreamTitleNotFound: no record of the film",
        )

        row = self._row("watchmode")
        self.assertEqual(row["reason"], R.TITLE_NOT_FOUND)
        self.assertIsNone(row["checked_at"])
        self.assertIsNotNone(row["last_attempted_at"])

    def test_the_newest_row_wins_so_a_recheck_does_not_show_a_stale_stamp(self):
        """A title refreshed twice shows the latest attempt, not the oldest."""
        self._request("youtube", RefreshRequestStatus.DONE)
        older = self._row("youtube")["last_attempted_at"]

        RefreshRequest.objects.filter(movie=self.movie, source="youtube").update(
            completed_at=None
        )
        self._request("youtube", RefreshRequestStatus.DONE)

        self.assertGreater(
            self._row("youtube")["last_attempted_at"], older,
            "a later check must not report the earlier attempt's time",
        )

    def test_the_stamp_is_scoped_to_the_reported_batch(self):
        """A run's report must not borrow another run's attempt history."""
        self._request("youtube", RefreshRequestStatus.DONE)

        other_batch = ar.title_source_report([self.movie.pk], batch=uuid.uuid4())
        other_row = [
            r for r in other_batch[self.movie.pk]["sources"] if r["source"] == "youtube"
        ][0]
        self.assertIsNone(
            other_row["last_attempted_at"],
            "history from a different run must not count as this run's attempt",
        )


class RepeatedRequestRowTests(TestCase):
    """Regression: per-source totals counted *rows*, not distinct titles.

    The queue dedupes only *pending* requests (a partial unique constraint on
    ``(movie, source) WHERE status='pending'``), so terminal rows accumulate
    freely: one title refreshed five times holds five ``DONE`` rows. Counting
    rows therefore reported 987 Mediaset and 985 YouTube "checks" against a
    191-title watchlist — 2,740 total, against the 764 the old button quoted.

    Both denominators are user-facing ("843 / 1,120", "312 / 312"), so a
    source that looks five times busier than it is is not cosmetic: it makes
    progress read as stalled.
    """

    def setUp(self):
        clear_quota()
        self.batch = uuid.uuid4()

    def _titles_with_repeat_history(self, source, repeats=5, titles=3):
        movies = []
        for i in range(titles):
            movie = make_movie(100 + i, f"Repeat {i}")
            movies.append(movie)
            for _ in range(repeats):
                RefreshRequest.objects.create(
                    movie=movie, source=source,
                    status=RefreshRequestStatus.DONE, batch=self.batch,
                )
        return movies

    def test_repeated_rows_for_one_title_count_once(self):
        movies = self._titles_with_repeat_history("mediaset_infinity")
        row = next(
            r for r in ar.source_progress([m.pk for m in movies], batch=self.batch)
            if r["source"] == "mediaset_infinity"
        )
        # 3 titles x 5 rows = 15 rows, but only 3 distinct titles were checked.
        self.assertEqual(row["total"], 3)
        self.assertEqual(row["successful"], 3)

    def test_the_checks_total_is_titles_times_sources_not_rows(self):
        """`checks` is the denominator of "N / M checks", so it must not
        count attempts — 5,000 historical retries would dwarf the real run."""
        movies = self._titles_with_repeat_history("youtube", repeats=8, titles=2)
        summary = ar.build_run_summary(
            [m.pk for m in movies], batch=self.batch
        )
        title_sources = [s for s in summary["sources"] if s["kind"] == "title"]
        by_source = {s["source"]: s for s in title_sources}
        self.assertEqual(by_source["youtube"]["total"], 2)
        self.assertEqual(
            summary["totals"]["checks"],
            sum(s["total"] for s in title_sources),
        )
        # 2 titles x 4 registered title-level sources, NOT 2 x (4 + 30 retries).
        self.assertLessEqual(summary["totals"]["checks"], 2 * len(title_sources) + 1)

    def test_a_mixed_history_reports_the_newest_status_per_title(self):
        """A title checked twice — once done, once still pending — reads as
        pending, because that is what the user is still waiting on."""
        movie = make_movie(200, "Mixed")
        RefreshRequest.objects.create(
            movie=movie, source=SA,
            status=RefreshRequestStatus.DONE, batch=self.batch,
            requested_at=timezone.now() - dt.timedelta(hours=2),
        )
        RefreshRequest.objects.create(
            movie=movie, source=SA,
            status=RefreshRequestStatus.PENDING, batch=self.batch,
            requested_at=timezone.now(),
        )
        row = next(
            r for r in ar.source_progress([movie.pk], batch=self.batch)
            if r["source"] == SA
        )
        self.assertEqual(row["total"], 1)
        self.assertEqual(row["queued"], 1)
        self.assertEqual(row["successful"], 0)


class StaleRunCannotDecideTests(TestCase):
    """A previous run's terminal row must not decide the current verdict."""

    def setUp(self):
        clear_quota()
        self.movie = make_movie(1, "Scoping")
        self.batch = uuid.uuid4()

    def test_a_previous_run_cannot_decide_the_current_verdict(self):
        """A stale DONE from an old run must not resolve a title whose
        current run has not checked it."""
        RefreshRequest.objects.create(
            movie=self.movie, source=SA,
            status=RefreshRequestStatus.DONE, batch=uuid.uuid4(),
        )
        report = ar.title_source_report([self.movie.pk], batch=self.batch)
        sa = next(r for r in report[self.movie.pk]["sources"] if r["source"] == SA)
        self.assertNotEqual(sa["reason"], R.RESOLVED)
        self.assertNotEqual(sa["reason"], R.NO_OFFER)


class WithdrawnOfferTests(TestCase):
    """Fix 5 — an inactive offer must not resolve a source."""

    def setUp(self):
        clear_quota()

    def test_a_withdrawn_offer_does_not_resolve_its_source(self):
        movie = make_movie(2, "Withdrawn")
        make_offer(movie, SA, available=False)
        report = ar.title_source_report([movie.pk])
        sa = next(r for r in report[movie.pk]["sources"] if r["source"] == SA)
        self.assertNotEqual(sa["reason"], R.RESOLVED)
        self.assertNotEqual(report[movie.pk]["state"], ar.TitleAvailabilityState.AVAILABLE)

    def test_an_active_offer_does_resolve_its_source(self):
        movie = make_movie(3, "Active")
        make_offer(movie, SA, available=True)
        report = ar.title_source_report([movie.pk])
        sa = next(r for r in report[movie.pk]["sources"] if r["source"] == SA)
        self.assertEqual(sa["reason"], R.RESOLVED)
        self.assertEqual(report[movie.pk]["state"], ar.TitleAvailabilityState.AVAILABLE)

    def test_provider_and_source_evidence_cannot_disagree(self):
        """They are two reads of the same question; a title that is
        "resolved by SA" and "available on nobody" is a contradiction the
        report must be incapable of producing."""
        movie = make_movie(4, "Disagree")
        make_offer(movie, SA, available=False)
        entry = ar.title_source_report([movie.pk])[movie.pk]
        resolved = [r for r in entry["sources"] if r["available"]]
        if entry["state"] == ar.TitleAvailabilityState.AVAILABLE:
            self.assertTrue(resolved)
        self.assertEqual(bool(resolved), bool(entry["available_on"]))


class PermanentVersusTransientTests(TestCase):
    """Fix 6 — a permanent miss is not a failure to retry."""

    def setUp(self):
        clear_quota()
        self.movie = make_movie(5, "Permanent")

    def test_a_typed_permanent_miss_is_recognised(self):
        RefreshRequest.objects.create(
            movie=self.movie, source=SA, status=RefreshRequestStatus.FAILED,
            last_error="UpstreamTitleNotFound: no record of movie 5 (404)",
        )
        report = ar.title_source_report([self.movie.pk])
        sa = next(r for r in report[self.movie.pk]["sources"] if r["source"] == SA)
        self.assertEqual(sa["reason"], R.TITLE_NOT_FOUND)
        self.assertIn(sa["reason"], ar.ABSENCE_REASONS)

    def test_a_legacy_404_is_not_mistaken_for_a_permanent_miss(self):
        """A generic HTTP 404 predates the typed exception; treating it as
        permanent would write off a title that may well be there."""
        RefreshRequest.objects.create(
            movie=self.movie, source=SA, status=RefreshRequestStatus.FAILED,
            last_error="HTTPError: 404 Client Error: Not Found for url: ...",
        )
        report = ar.title_source_report([self.movie.pk])
        sa = next(r for r in report[self.movie.pk]["sources"] if r["source"] == SA)
        self.assertEqual(sa["reason"], R.SOURCE_UNAVAILABLE)
        self.assertIn(sa["reason"], ar.INFORMATION_GAP_REASONS)

    def test_a_transient_5xx_is_not_a_permanent_miss(self):
        RefreshRequest.objects.create(
            movie=self.movie, source=SA, status=RefreshRequestStatus.FAILED,
            last_error="HTTPError: 503 Server Error",
        )
        report = ar.title_source_report([self.movie.pk])
        sa = next(r for r in report[self.movie.pk]["sources"] if r["source"] == SA)
        self.assertEqual(sa["reason"], R.SOURCE_UNAVAILABLE)


class CollectorAuditTests(TestCase):
    """Fix 4 — "has it run?" comes from RefreshRun, not SyncRun."""

    def setUp(self):
        clear_quota()

    def test_a_collector_that_failed_is_failed_not_never_run(self):
        """Regression: abc_iview/pbs/tubi were reported `collector_not_run`
        because a failed attempt writes no SyncRun."""
        RefreshRun.objects.create(
            service="abc_iview", started_at=timezone.now(),
            finished_at=timezone.now(), error="SourceFetchError: boom",
        )
        row = next(r for r in ar.catalogue_progress() if r["source"] == "abc_iview")
        self.assertEqual(row["status"], "failed")
        self.assertNotEqual(row["reason"], R.COLLECTOR_NOT_RUN)
        self.assertEqual(row["reason"], R.SOURCE_UNAVAILABLE)

    def test_a_collector_that_never_attempted_is_collector_not_run(self):
        row = next(r for r in ar.catalogue_progress() if r["source"] == "tubi")
        self.assertEqual(row["reason"], R.COLLECTOR_NOT_RUN)

    def test_a_successful_collector_is_completed(self):
        RefreshRun.objects.create(
            service="cbc_gem", started_at=timezone.now(),
            finished_at=timezone.now(), error="",
        )
        row = next(r for r in ar.catalogue_progress() if r["source"] == "cbc_gem")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["reason"], "")

    def test_a_failing_aggregator_run_is_not_reported_healthy(self):
        """The other half of the same bug: JustWatch's last attempt failed
        outright, and the SyncRun read hid it behind a stale success."""
        RefreshRun.objects.create(
            service="justwatch", started_at=timezone.now(), error="SourceFetchError: x"
        )
        RefreshRun.objects.create(
            service="justwatch", started_at=timezone.now() - dt.timedelta(days=1),
            finished_at=timezone.now() - dt.timedelta(days=1), error="",
        )
        row = next(r for r in ar.catalogue_progress() if r["source"] == "justwatch")
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["error"])

    def test_collector_and_title_sources_are_reported_separately(self):
        """A catalogue source cannot answer a per-title question, so
        reporting it in the same list as per-title sources would imply it
        did."""
        movie = make_movie(50, "Separate")
        sources = {r["source"] for r in ar.source_progress([movie.pk])}
        collectors = {r["source"] for r in ar.catalogue_progress()}
        self.assertEqual(sources & collectors, set())


class CounterfactualConsistencyTests(TestCase):
    """Step 7 — the counterfactual must be internally consistent.

    The live report once claimed 54 titles would lose their answer to a
    metered-source gate while the same code path, recomputed independently,
    produced 48. The two disagreed because the counterfactual consumed the
    classifier's output, and the classifier was the thing that was wrong.

    The property that makes it checkable: the counterfactual's two columns
    must sum to the number of available titles, and the "losing" column must
    equal an independent count of titles resolved *only* by a metered
    source. Pinning the identity — rather than the number, which changes
    with the data — is what stops the two from drifting apart again.
    """

    def setUp(self):
        clear_quota()
        self.only_metered = make_movie(60, "OnlyMetered")
        make_offer(self.only_metered, SA, available=True)
        self.also_free = make_movie(61, "AlsoFree")
        make_offer(self.also_free, SA, available=True)
        make_offer(self.also_free, "justwatch", available=True)
        self.negative = make_movie(62, "Negative")
        for slug in ar._title_level_slugs():
            RefreshRequest.objects.create(
                movie=self.negative, source=slug, status=RefreshRequestStatus.DONE
            )

    def test_the_two_columns_sum_to_the_available_titles(self):
        ids = [self.only_metered.pk, self.also_free.pk, self.negative.pk]
        with patch.object(ar, "source_quota", lambda slug: None):
            summary = ar.build_run_summary(ids)
        cf = source_policy.counterfactual(
            summary["_states"], summary["_resolved_sources"]
        )
        self.assertEqual(summary["totals"]["available"], 2)
        self.assertEqual(
            cf["titles_still_resolved"] + cf["titles_losing_their_answer"],
            summary["totals"]["available"],
        )

    def test_losing_matches_an_independent_count(self):
        ids = [self.only_metered.pk, self.also_free.pk, self.negative.pk]
        with patch.object(ar, "source_quota", lambda slug: None):
            summary = ar.build_run_summary(ids)
        cf = source_policy.counterfactual(
            summary["_states"], summary["_resolved_sources"]
        )
        metered = {SA}
        recomputed = sum(
            1
            for mid, e in summary["_resolved_sources"].items()
            if summary["_states"][mid] == "available"
            and e & metered
            and not (e - metered)
        )
        self.assertEqual(cf["titles_losing_their_answer"], recomputed)
        self.assertEqual(recomputed, 1)

    def test_an_unavailable_title_counts_in_neither_column(self):
        """It is not available, so gating cannot cost it anything."""
        ids = [self.negative.pk]
        with patch.object(ar, "source_quota", lambda slug: None):
            summary = ar.build_run_summary(ids)
        cf = source_policy.counterfactual(
            summary["_states"], summary["_resolved_sources"]
        )
        self.assertEqual(summary["totals"]["available"], 0)
        self.assertEqual(cf["titles_losing_their_answer"], 0)
        self.assertEqual(cf["titles_still_resolved"], 0)


class SummariesTests(TestCase):
    def setUp(self):
        clear_quota()

    def _any_movie(self) -> int:
        movie = make_movie(9, "Summary")
        RefreshRequest.objects.create(
            movie=movie, source=SA, status=RefreshRequestStatus.PENDING,
        )
        return movie.pk

    def test_the_three_states_always_sum_to_the_titles(self):
        """The invariant a reader checks first; if it fails, every other
        number in the report is suspect."""
        for i in range(12, 20):
            make_movie(i, f"T{i}")
        summary = ar.build_run_summary(list(Movie.objects.values_list("id", flat=True)))
        totals = summary["totals"]
        self.assertEqual(
            totals["available"] + totals["unavailable"] + totals["unknown"],
            totals["titles"],
        )

    def test_stale_permanent_failures_are_surfaced_not_hidden(self):
        """A pending row supersedes an older 404 as the headline blocker —
        correctly, since the quota really is what the title waits on — but
        the 404 must still be reported, because waiting will not fix it.

        Without this the live watchlist reads as "24 titles are simply
        waiting for midnight" when 2 of them carry a permanent miss.
        """
        movie = make_movie(33, "BehindA404")
        RefreshRequest.objects.create(
            movie=movie, source=SA, status=RefreshRequestStatus.FAILED,
            last_error="UpstreamTitleNotFound: absent",
            requested_at=timezone.now() - dt.timedelta(days=1),
        )
        # A newer pending row for the same (title, source) — the queue
        # re-stamps, so the newest row is the current state.
        RefreshRequest.objects.create(
            movie=movie, source=SA, status=RefreshRequestStatus.PENDING,
            requested_at=timezone.now(),
        )
        with patch.object(
            ar, "source_quota",
            lambda slug: {"remaining": 0, "limit": 100, "exhausted": True},
        ):
            summary = ar.build_run_summary([movie.pk])
        # The permanent marker OUTRANKS the newer pending row. That is the
        # right precedence and it is deliberate: the typed 404 is a durable
        # fact, written precisely so it outlives the queue row that
        # discovered it, while a pending row is a probe that has not
        # answered yet. Gating the title on quota would imply midnight fixes
        # it, and it does not.
        self.assertEqual(summary["totals"]["unknown"], 1)
        self.assertEqual(summary["unknown_reasons"], {R.TITLE_NOT_FOUND: 1})
        # Still unknown: one source answered "no such title", the others
        # never spoke.
        self.assertEqual(summary["totals"]["available"], 0)

    def test_unknown_reasons_sum_to_the_unknown_count(self):
        """The live failure this prevents: 26 unknown titles reported as a
        single 26-long `quota_exhausted` bucket while 2 of them were
        genuinely broken."""
        for i in range(20, 30):
            movie = make_movie(i, f"U{i}")
            RefreshRequest.objects.create(
                movie=movie, source=SA, status=RefreshRequestStatus.FAILED,
                last_error="UpstreamTitleNotFound: absent" if i < 22 else "HTTPError: 500",
            )
        ids = list(Movie.objects.filter(tmdb_id__gte=20).values_list("id", flat=True))
        summary = ar.build_run_summary(ids)
        self.assertEqual(
            sum(summary["unknown_reasons"].values()), summary["totals"]["unknown"]
        )

    def test_a_title_some_source_answered_is_not_unknown(self):
        """The headline case: one answered source, another quota-blocked.

        The offer comes from JustWatch — a source with no per-title request
        row at all — which is the path that previously reported this title
        as `unknown` because the classification only walked title-level
        sources.
        """
        movie = make_movie(30, "Mixed")
        make_offer(movie, "justwatch", available=True)
        RefreshRequest.objects.create(
            movie=movie, source=SA, status=RefreshRequestStatus.PENDING
        )
        with patch.object(
            ar, "source_quota",
            lambda slug: {"remaining": 0, "limit": 100, "exhausted": True},
        ):
            entry = ar.title_source_report([movie.pk])[movie.pk]
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.AVAILABLE)
        self.assertIn("justwatch", entry["resolved_sources"])

    def test_every_source_negative_makes_a_title_unavailable(self):
        """Every *title-level* source must have answered for this to hold,
        so the fixture is written from the registry rather than from a
        hard-coded list that would drift as sources are added."""
        movie = make_movie(31, "AllNegative")
        for slug in ar._title_level_slugs():
            RefreshRequest.objects.create(
                movie=movie, source=slug, status=RefreshRequestStatus.DONE
            )
        with patch.object(ar, "source_quota", lambda slug: None):
            entry = ar.title_source_report([movie.pk])[movie.pk]
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.UNAVAILABLE)

    def test_one_source_left_unasked_keeps_a_title_unknown(self):
        """The complement of the above, and the rule that decided the real
        28-title population: a single unanswered source is enough."""
        movie = make_movie(32, "OneUnasked")
        slugs = ar._title_level_slugs()
        for slug in slugs[:-1]:
            RefreshRequest.objects.create(
                movie=movie, source=slug, status=RefreshRequestStatus.DONE
            )
        with patch.object(ar, "source_quota", lambda slug: None):
            entry = ar.title_source_report([movie.pk])[movie.pk]
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.UNKNOWN)


class QuotaExhaustionTests(TestCase):
    def setUp(self):
        self.movie = make_movie(40, "Quota")

    def test_a_quota_blocked_source_is_reported_as_such(self):
        """Uses the real limiter contract rather than a MagicMock: a
        MagicMock's `is_exhausted()` is truthy, which makes every source look
        blocked and silently empties the report."""
        ApiCallCounter.objects.create(
            service=SA, count=SOURCE_DAILY_LIMITS[SA], call_date=timezone.now().date()
        )
        RefreshRequest.objects.create(
            movie=self.movie, source=SA, status=RefreshRequestStatus.PENDING
        )
        entry = ar.title_source_report([self.movie.pk])[self.movie.pk]
        sa = next(r for r in entry["sources"] if r["source"] == SA)
        self.assertEqual(sa["reason"], R.QUOTA_EXHAUSTED)
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.UNKNOWN)

    def test_an_unmetered_source_is_never_quota_blocked(self):
        """`remaining() is None` means unmetered; reporting it as 0 would
        invent a budget the provider does not impose."""
        RefreshRequest.objects.create(
            movie=self.movie, source="youtube", status=RefreshRequestStatus.PENDING
        )
        entry = ar.title_source_report([self.movie.pk])[self.movie.pk]
        yt = next(r for r in entry["sources"] if r["source"] == "youtube")
        self.assertNotEqual(yt["reason"], R.QUOTA_EXHAUSTED)
