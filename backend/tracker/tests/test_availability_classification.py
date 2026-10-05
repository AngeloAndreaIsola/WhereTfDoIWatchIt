"""The central availability invariant, pinned as a truth table.

    AVAILABLE
        if ANY applicable source has a currently-active offer

    UNAVAILABLE
        only if EVERY applicable source has definitively answered
        "not available"

    UNKNOWN
        if AT LEAST ONE applicable source is still pending, failed
        transiently, quota-blocked, or not yet run

The rule under all of these: **an unanswered question is not evidence of
absence.** A source that has not run — because it is queued, because it is
out of budget, or because it failed transiently — has not said "no", and its
silence can only ever move a title towards ``unknown``.

These tests are pure: :func:`classify_title` takes a list of per-source
verdicts and returns a state, so the whole specification is exercised
without a database, without a provider, and without a fixture. The DB-backed
report tests further down prove the *inputs* are read correctly; separating
the two is deliberate, because a bug in the classifier and a bug in the
evidence reader otherwise present identically as "the report is wrong".
"""
from django.test import SimpleTestCase

from tracker.models import AvailabilityResolutionReason
from tracker.services.availability_report import (
    ABSENCE_REASONS,
    INFORMATION_GAP_REASONS,
    TitleAvailabilityState,
    classify_source,
    classify_title,
)

R = AvailabilityResolutionReason


def row(source: str, reason: str, **extra) -> dict:
    """One per-source verdict, in the shape ``title_source_report`` emits."""
    return {"source": source, "reason": reason, "label": source, **extra}


class ClassifySourceTests(SimpleTestCase):
    """One source's verdict from its evidence. The order is the spec."""

    def test_active_offer_resolves_the_source(self):
        self.assertEqual(
            classify_source(
                produced_active_offer=True,
                permanent_miss=False,
                request_status="done",
                quota_exhausted=True,
            ),
            ("completed", R.RESOLVED),
        )

    def test_permanent_miss_is_a_definitive_negative_from_that_source(self):
        status, reason = classify_source(
            produced_active_offer=False,
            permanent_miss=True,
            request_status="failed",
            quota_exhausted=False,
        )
        self.assertEqual((status, reason), ("completed", R.TITLE_NOT_FOUND))
        # It is an *absence* answer, which is what makes the parent's
        # "everyone answered" rule reachable for this source.
        self.assertIn(reason, ABSENCE_REASONS)

    def test_done_without_offer_is_a_definitive_negative(self):
        self.assertEqual(
            classify_source(
                produced_active_offer=False,
                permanent_miss=False,
                request_status="done",
                quota_exhausted=False,
            ),
            ("completed", R.NO_OFFER),
        )

    def test_transient_failure_is_not_a_definitive_negative(self):
        status, reason = classify_source(
            produced_active_offer=False,
            permanent_miss=False,
            request_status="failed",
            quota_exhausted=False,
        )
        self.assertEqual((status, reason), ("failed", R.SOURCE_UNAVAILABLE))
        self.assertIn(reason, INFORMATION_GAP_REASONS)
        self.assertNotIn(reason, ABSENCE_REASONS)

    def test_pending_under_an_exhausted_quota_names_the_quota(self):
        self.assertEqual(
            classify_source(
                produced_active_offer=False,
                permanent_miss=False,
                request_status="pending",
                quota_exhausted=True,
            ),
            ("queued", R.QUOTA_EXHAUSTED),
        )

    def test_pending_without_quota_pressure_is_simply_queued(self):
        self.assertEqual(
            classify_source(
                produced_active_offer=False,
                permanent_miss=False,
                request_status="pending",
                quota_exhausted=False,
            ),
            ("queued", R.QUEUED),
        )

    def test_never_asked_is_not_checked(self):
        self.assertEqual(
            classify_source(
                produced_active_offer=False,
                permanent_miss=False,
                request_status=None,
                quota_exhausted=False,
            ),
            ("not_checked", R.NOT_CHECKED),
        )

    def test_a_source_never_asked_but_out_of_budget_says_so(self):
        self.assertEqual(
            classify_source(
                produced_active_offer=False,
                permanent_miss=False,
                request_status=None,
                quota_exhausted=True,
            ),
            ("quota_exhausted", R.QUOTA_EXHAUSTED),
        )


class CentralInvariantTests(SimpleTestCase):
    """The three worked examples from the specification, verbatim."""

    def test_one_source_available_beats_a_quota_blocked_aggregate(self):
        state, reason = classify_title(
            [
                row("bbc_iplayer", R.RESOLVED),
                row("streaming_availability", R.QUOTA_EXHAUSTED),
            ]
        )
        self.assertEqual(state, TitleAvailabilityState.AVAILABLE)
        self.assertEqual(reason, "")

    def test_every_source_negative_is_unavailable(self):
        state, reason = classify_title(
            [
                row("justwatch", R.NO_OFFER),
                row("watchmode", R.NO_OFFER),
                row("streaming_availability", R.NO_OFFER),
            ]
        )
        self.assertEqual(state, TitleAvailabilityState.UNAVAILABLE)
        self.assertEqual(reason, R.NO_OFFER)

    def test_aggregators_negative_but_quota_blocked_is_unknown(self):
        state, reason = classify_title(
            [
                row("justwatch", R.NO_OFFER),
                row("watchmode", R.NO_OFFER),
                row("streaming_availability", R.QUOTA_EXHAUSTED),
            ]
        )
        self.assertEqual(state, TitleAvailabilityState.UNKNOWN)
        self.assertEqual(reason, R.QUOTA_EXHAUSTED)

    def test_one_definitive_negative_among_three_is_not_enough(self):
        """The specific trap: one answering "no" must not prove "unavailable"."""
        state, _ = classify_title(
            [
                row("justwatch", R.NO_OFFER),
                row("watchmode", R.QUEUED),
                row("youtube", R.NOT_CHECKED),
            ]
        )
        self.assertEqual(state, TitleAvailabilityState.UNKNOWN)

    def test_a_permanent_miss_alone_does_not_make_a_title_unavailable(self):
        """A source saying "no such title" says nothing about the others."""
        state, reason = classify_title(
            [
                row("streaming_availability", R.TITLE_NOT_FOUND),
                row("justwatch", R.QUEUED),
            ]
        )
        self.assertEqual(state, TitleAvailabilityState.UNKNOWN)
        # The headline names the most definitive statement available.
        self.assertEqual(reason, R.TITLE_NOT_FOUND)

    def test_a_permanent_miss_among_all_negatives_is_unavailable(self):
        state, _ = classify_title(
            [
                row("streaming_availability", R.TITLE_NOT_FOUND),
                row("justwatch", R.NO_OFFER),
            ]
        )
        self.assertEqual(state, TitleAvailabilityState.UNAVAILABLE)

    def test_every_gap_still_reports_unknown(self):
        state, _ = classify_title(
            [
                row("justwatch", R.QUEUED),
                row("watchmode", R.SOURCE_UNAVAILABLE),
                row("streaming_availability", R.QUOTA_EXHAUSTED),
            ]
        )
        self.assertEqual(state, TitleAvailabilityState.UNKNOWN)

    def test_no_evidence_at_all_is_unknown_not_unavailable(self):
        state, _ = classify_title([])
        self.assertEqual(state, TitleAvailabilityState.UNKNOWN)

    def test_unavailable_survives_a_source_that_never_had_a_request(self):
        """A source with no request at all keeps the title unknown.

        This is the rule that changed the real answer for 28 titles, so it
        is pinned from both directions: a title where the only negative is
        joined by silence must be unknown, and only when *every* source has
        spoken may it be unavailable.
        """
        state, _ = classify_title(
            [
                row("justwatch", R.NO_OFFER),
                row("watchmode", R.NO_OFFER),
                row("youtube", R.NOT_CHECKED),
            ]
        )
        self.assertEqual(state, TitleAvailabilityState.UNKNOWN)


class HeadlineReasonTests(SimpleTestCase):
    def test_quota_beats_a_generic_never_checked(self):
        state, reason = classify_title(
            [
                row("justwatch", R.NOT_CHECKED),
                row("streaming_availability", R.QUOTA_EXHAUSTED),
            ]
        )
        self.assertEqual(state, TitleAvailabilityState.UNKNOWN)
        self.assertEqual(reason, R.QUOTA_EXHAUSTED)

    def test_a_transient_failure_outranks_a_quota_block(self):
        """A failure needs a human; a quota block just needs midnight.

        Without this, every genuinely broken title is filed under
        "quota_exhausted" — a reason that always reads as "nothing is
        wrong, just wait" — and the real faults disappear from the summary.
        """
        state, reason = classify_title(
            [
                row("streaming_availability", R.SOURCE_UNAVAILABLE),
                row("justwatch", R.QUOTA_EXHAUSTED),
            ]
        )
        self.assertEqual(state, TitleAvailabilityState.UNKNOWN)
        self.assertEqual(reason, R.SOURCE_UNAVAILABLE)

    def test_a_permanent_miss_outranks_everything(self):
        state, reason = classify_title(
            [
                row("streaming_availability", R.TITLE_NOT_FOUND),
                row("justwatch", R.SOURCE_UNAVAILABLE),
                row("watchmode", R.QUOTA_EXHAUSTED),
            ]
        )
        self.assertEqual(state, TitleAvailabilityState.UNKNOWN)
        self.assertEqual(reason, R.TITLE_NOT_FOUND)

    def test_unknown_reasons_sum_to_the_unknown_count(self):
        """The breakdown must partition the unknown population.

        A headline reason is chosen per title, so the counts have to add up
        to the number of unknown titles — otherwise "why are 26 films
        unknown?" is answered with a breakdown that does not explain 26.
        """
        rows = [
            [row("a", R.RESOLVED)],
            [row("a", R.NO_OFFER), row("b", R.NO_OFFER)],
            [row("a", R.QUOTA_EXHAUSTED)],
            [row("a", R.QUOTA_EXHAUSTED), row("b", R.SOURCE_UNAVAILABLE)],
            [row("a", R.TITLE_NOT_FOUND), row("b", R.SOURCE_UNAVAILABLE)],
        ]
        from collections import Counter

        states, reasons = [], Counter()
        for source_rows in rows:
            state, reason = classify_title(source_rows)
            states.append(state)
            if state == TitleAvailabilityState.UNKNOWN:
                reasons[reason] += 1
        self.assertEqual(sum(reasons.values()), states.count(TitleAvailabilityState.UNKNOWN))

    def test_never_checked_is_the_headline_when_nothing_better_exists(self):
        state, reason = classify_title([row("justwatch", R.NOT_CHECKED)])
        self.assertEqual(state, TitleAvailabilityState.UNKNOWN)
        self.assertEqual(reason, R.NOT_CHECKED)

    def test_a_state_is_always_one_of_three(self):
        for rows in (
            [row("a", R.RESOLVED)],
            [row("a", R.NO_OFFER)],
            [row("a", R.QUEUED)],
            [],
        ):
            state, _ = classify_title(rows)
            self.assertIn(state, TitleAvailabilityState.ALL)


class ReasonVocabularyTests(SimpleTestCase):
    """The two reason sets must partition the vocabulary, or the rule breaks.

    If a reason were in neither set, ``classify_title`` would silently treat
    it as neither an answer nor a gap; if one were in both, a silence could
    count as absence — which is precisely the bug being fixed.
    """

    def test_absence_and_gap_sets_are_disjoint(self):
        self.assertEqual(ABSENCE_REASONS & INFORMATION_GAP_REASONS, frozenset())

    def test_every_definitive_reason_is_an_absence(self):
        for reason in (R.NO_OFFER, R.TITLE_NOT_FOUND):
            self.assertIn(reason, ABSENCE_REASONS)

    def test_every_informational_reason_is_a_gap(self):
        for reason in (
            R.NOT_CHECKED,
            R.QUEUED,
            R.QUOTA_EXHAUSTED,
            R.SOURCE_UNAVAILABLE,
            R.COLLECTOR_NOT_RUN,
            R.NOT_SUPPORTED,
            R.NOT_APPLICABLE,
        ):
            self.assertIn(reason, INFORMATION_GAP_REASONS)
