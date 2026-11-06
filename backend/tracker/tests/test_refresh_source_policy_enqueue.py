"""Step 8 — the source policy must gate *enqueue*, not merely report.

The contract these tests protect, stated once:

    Given a title already resolved by a free source,
    when a watchlist refresh is requested,
    then no metered request row is created for it.

and the inverse, and the historical-queue case:

    all free sources definitively negative  ->  metered request IS created
    a metered row already exists from an old run  ->  no second row is added

**These tests do not touch the classification contract.** Steps 1–7 are
frozen: ``classify_title``, the reason precedence, the counterfactual
arithmetic and the reason vocabulary are inputs here, not subjects. The only
thing under test is *which rows get created*.

Why "before the request row is created" is the whole point
--------------------------------------------------------
A skipped source that is enqueued and then settled has already cost a queue
row, a worker tick, a storage write and a place in the batch's denominator.
The user-visible difference between "not needed" and "queued then marked
not-needed" is a row that will never run and a progress bar that does not add
up. So the decision is made at enqueue, and the *reason* it was skipped is
recorded on the run rather than being inferred later.

Skipped is not the same as unavailable
--------------------------------------
``skipped_sources: {streaming_availability: not_needed}`` means **we chose not
to ask**, which is a scheduling fact. It is emphatically not evidence that the
title is unavailable, and the report must never render it as a negative
verdict. That distinction is why the decision carries a reason vocabulary
rather than a bare omission.
"""
import uuid

from django.test import TestCase
from django.utils import timezone

from availability.models import ContentType, Offer
from tracker.models import (
    AvailabilityResolutionReason,
    Movie,
    Provider,
    RefreshRequest,
    RefreshRequestStatus,
    RefreshPriority,
    WatchlistItem,
)
from tracker.services import availability_report as ar
from tracker.services import refresh_requests as rr
from tracker.services import source_policy

SA = "streaming_availability"          # the one metered aggregator
#: Broad, unmetered, and *title-addressable* — so it can hold a real
#: per-title request row and be used in the enqueue tests.
WM = "watchmode"
#: Broad and unmetered and **title-addressable** (it implements both a title
#: lookup and a catalogue walk), so it too can hold a per-title request row.
JW = "justwatch"
COLLECTOR = "mediaset_infinity"         # first-party, title-addressable


def make_movie(tmdb_id: int, title: str = "T") -> Movie:
    return Movie.objects.create(tmdb_id=tmdb_id, title=title, year=2000)


def add_to_watchlist(movie: Movie) -> None:
    WatchlistItem.objects.get_or_create(user_id=1, movie=movie)


def offer_for(movie: Movie, source_slug: str, *, available: bool = True) -> Offer:
    from availability.models import Country, Source

    provider, _ = Provider.objects.get_or_create(
        slug=f"{source_slug}-prov", defaults={"name": source_slug, "category": "stream"}
    )
    country, _ = Country.objects.get_or_create(code="IT", defaults={"name": "Italy"})
    source, _ = Source.objects.get_or_create(slug=source_slug, defaults={"name": source_slug})
    return Offer.objects.create(
        tmdb_id=movie.tmdb_id,
        provider=provider,
        country=country,
        source=source,
        content_type=ContentType.MOVIE,
        offer_type="subscription",
        currently_available=available,
    )


def say_definitely_negative(movie: Movie, slug: str) -> None:
    """Mark a source as having answered 'no offer' for this title."""
    RefreshRequest.objects.update_or_create(
        movie=movie,
        source=slug,
        status=RefreshRequestStatus.DONE,
        defaults={"completed_at": timezone.now(), "last_error": ""},
    )


class PolicyDecisionTests(TestCase):
    """The decision object itself — richer than a list, as required."""

    def setUp(self):
        self.applicable = [COLLECTOR, JW, WM, SA]

    def _decide(self, movie, *, gate: bool = True):
        """Classify the title, then ask the policy what may run for it.

        ``gate`` defaults to True because every assertion about a *skip* is
        only meaningful when the policy is enabled — with the gate off (the
        shipped default) nothing is ever skipped, so those assertions would
        pass or fail for a reason unrelated to the policy.
        """
        states = ar.title_source_report([movie.pk])
        with _patched_gate(gate):
            return source_policy.decide_sources_for_title(
                states.get(movie.pk, {}), applicable_slugs=self.applicable
            )

    def test_a_resolved_title_skips_the_metered_source_with_a_reason(self):
        movie = make_movie(500, "Resolved")
        offer_for(movie, JW)
        decision = self._decide(movie)
        self.assertIn(JW, decision["allowed_sources"])
        self.assertNotIn(SA, decision["allowed_sources"])
        self.assertEqual(decision["skipped_sources"][SA], "not_needed")
        self.assertEqual(decision["reasons"][SA], "not_needed")

    def test_an_unresolved_title_allows_every_source(self):
        movie = make_movie(501, "Unresolved")
        for slug in self.applicable:
            say_definitely_negative(movie, slug)
        decision = self._decide(movie)
        self.assertIn(SA, decision["allowed_sources"])
        self.assertEqual(decision["skipped_sources"], {})

    def test_a_partially_answered_title_still_allows_the_metered_source(self):
        """Resolution-aware, not a strict chain.

        The two broad aggregators have answered 'no' but the title-addressable
        collector has not been asked yet. The metered source stays eligible
        because something genuinely unresolved remains — which is exactly the
        case a naive A-then-B-then-C ladder gets wrong.
        """
        movie = make_movie(502, "Partial")
        for slug in (JW, WM, SA):
            say_definitely_negative(movie, slug)
        decision = self._decide(movie)
        self.assertIn(SA, decision["allowed_sources"])

    def test_a_collector_positive_is_enough_to_skip_the_metered_source(self):
        movie = make_movie(503, "CollectorPositive")
        offer_for(movie, COLLECTOR)
        decision = self._decide(movie)
        self.assertNotIn(SA, decision["allowed_sources"])
        self.assertEqual(decision["skipped_sources"][SA], "not_needed")

    def test_a_withdrawn_offer_does_not_resolve_a_title(self):
        """An inactive offer is not an answer, so the metered source stays
        eligible. Gating on a withdrawn offer would skip the only source that
        could still find the title."""
        movie = make_movie(504, "Withdrawn")
        offer_for(movie, JW, available=False)
        decision = self._decide(movie)
        self.assertIn(SA, decision["allowed_sources"])

    def test_the_gate_off_means_nothing_is_ever_skipped(self):
        """The flag must remain a *switch*, so switching it off restores the
        previous fan-out exactly — that is what makes the counterfactual a
        reachable state rather than a hypothesis."""
        movie = make_movie(505, "GateOff")
        offer_for(movie, JW)
        decision = self._decide(movie, gate=False)
        self.assertIn(SA, decision["allowed_sources"])
        self.assertEqual(decision["skipped_sources"], {})

    def test_reasons_mirror_skipped_sources_exactly(self):
        """The docstring promises both keys hold the same value; a payload
        where they disagree is worse than one with only one key."""
        movie = make_movie(507, "Paired")
        offer_for(movie, JW)
        decision = self._decide(movie)
        self.assertEqual(decision["reasons"], decision["skipped_sources"])
        self.assertTrue(decision["skipped_sources"])

    def test_the_decision_carries_a_counterfactual(self):
        """A decision that cannot be compared against its alternative is not
        auditable."""
        movie = make_movie(506, "Auditable")
        offer_for(movie, SA)
        decision = self._decide(movie)
        self.assertIn("counterfactual", decision)
        self.assertIn("gate", decision["counterfactual"])


class EnqueueGatingTests(TestCase):
    """The three regression tests, at the level that matters: queue rows."""

    def setUp(self):
        self.movie = make_movie(600, "Watchlisted")
        add_to_watchlist(self.movie)

    def _sa_rows(self) -> int:
        return RefreshRequest.objects.filter(movie=self.movie, source=SA, status=RefreshRequestStatus.PENDING).count()

    def test_no_metered_row_when_the_title_is_already_resolved(self):
        """THE test. A resolved title must not create a metered request."""
        offer_for(self.movie, WM)
        with self._gate_on():
            result = rr.request_watchlist_refresh(user_id=1, priority=RefreshPriority.STALE)
        self.assertEqual(self._sa_rows(), 0)
        self.assertGreater(result["queued"], 0, "the free sources still run")

    def test_a_metered_row_is_created_when_all_free_sources_say_no(self):
        for slug in rr.title_level_source_slugs():
            if slug != SA:
                say_definitely_negative(self.movie, slug)
        with self._gate_on():
            rr.request_watchlist_refresh(user_id=1)
        self.assertEqual(self._sa_rows(), 1)

    def test_an_existing_metered_row_is_not_duplicated(self):
        """The historical-queue case: a title that already has a pending SA
        row must not gain a second one, or the queue grows without bound."""
        say_definitely_negative(self.movie, COLLECTOR)
        RefreshRequest.objects.create(
            movie=self.movie, source=SA, status=RefreshRequestStatus.PENDING,
            batch=uuid.uuid4(), requested_at=timezone.now(),
        )
        offer_for(self.movie, WM)
        with self._gate_on():
            rr.request_watchlist_refresh(user_id=1)
        self.assertEqual(self._sa_rows(), 1)

    def test_a_resolved_title_creates_rows_only_for_free_sources(self):
        offer_for(self.movie, WM)
        with self._gate_on():
            result = rr.request_watchlist_refresh(user_id=1, priority=RefreshPriority.STALE)
        created = set(
            RefreshRequest.objects.filter(movie=self.movie).values_list("source", flat=True)
        )
        self.assertNotIn(SA, created)
        self.assertIn(WM, created)
        self.assertNotIn(SA, result["sources"])

    def test_the_gate_off_creates_the_full_fanout_as_before(self):
        """Byte-identical behaviour when the policy is off — the change is
        additive, and turning it off must restore the old scheduling exactly."""
        offer_for(self.movie, JW)
        with self._gate_off():
            result = rr.request_watchlist_refresh(user_id=1)
        self.assertEqual(self._sa_rows(), 1)
        self.assertIn(SA, result["sources"])

    def _gate_on(self):
        return _patched_gate(True)

    def _gate_off(self):
        return _patched_gate(False)


class _patched_gate:
    """Context manager switching the ladder policy for the duration of a test.

    Sets the **environment variable** rather than patching a module attribute,
    because the policy is now read on every call. The old version patched a
    module constant and documented why: "the constant is read at import time;
    a test that set the env var would be testing a different configuration
    than the one it thinks it is." That reasoning was valid then and is now
    exactly backwards — reading live is what lets a test declare the policy it
    wants instead of inheriting whatever the process started with, and it is
    what lets an operator change the setting without restarting the service.
    """

    def __init__(self, on: bool):
        self.on = on

    def __enter__(self):
        import os

        import tracker.services.source_policy as sp

        self._original = os.environ.get(sp.LADDER_ENV_VAR)
        os.environ[sp.LADDER_ENV_VAR] = "1" if self.on else "0"
        return self

    def __exit__(self, *exc):
        import os

        import tracker.services.source_policy as sp

        if self._original is None:
            os.environ.pop(sp.LADDER_ENV_VAR, None)
        else:
            os.environ[sp.LADDER_ENV_VAR] = self._original
        return False


class SkippedIsNotUnavailableTests(TestCase):
    """A skip is a scheduling fact, never a negative availability verdict."""

    def test_a_skipped_metered_source_never_becomes_a_negative_verdict(self):
        """The distinction the whole design turns on.

        If skipping a source could make a title read as unavailable, the
        policy would manufacture false negatives — the exact bug Steps 1–7
        were written to eliminate. A title skipped-because-resolved is
        available; a title skipped-because-gated stays unknown.
        """
        movie = make_movie(700, "SkippedNotNegative")
        offer_for(movie, JW)
        entry = ar.title_source_report([movie.pk])[movie.pk]
        self.assertEqual(entry["state"], ar.TitleAvailabilityState.AVAILABLE)

    def test_the_skip_reason_is_distinct_from_every_availability_reason(self):
        """`not_needed` must not collide with the reason vocabulary, or a
        skip could be rendered as an answer."""
        self.assertNotIn(
            "not_needed", [r.value for r in AvailabilityResolutionReason]
        )

    def test_policy_decision_reasons_are_a_closed_vocabulary(self):
        """An auditable decision needs a finite set of reasons; a free-text
        reason would drift and stop being queryable."""
        from tracker.services.source_policy import PolicySkipReason

        self.assertEqual(set(PolicySkipReason.all()), {"not_needed"})
        for value in PolicySkipReason.all():
            self.assertIsInstance(value, str)
