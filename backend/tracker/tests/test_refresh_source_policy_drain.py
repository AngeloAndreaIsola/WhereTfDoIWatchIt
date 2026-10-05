"""Step 8b — the source policy must gate *drain*, not just enqueue.

:mod:`test_refresh_source_policy_enqueue` proves the row is never created.
This module proves the other half, which is the half that actually spends
money: **a metered request that already exists must not be executed** once a
cheaper source has resolved the title.

Why both halves are needed
--------------------------
Enqueue-only enforcement has a hole exactly the width of the queue's latency.
A metered row can be written while the title is still unresolved, and land
after a free source has since answered it — either because a cheap request
from the same run finished first, or because the title was resolved out of
band (an event-driven refresh, a catalogue sync, the user watching it
elsewhere). Then the run spends scarce, quota-limited budget re-deriving an
answer that is already in the database.

So the invariant has to hold at **execution** time as well:

    no metered request may execute for a title that an applicable
    non-metered source has already definitively resolved

Tests here pin, specifically:

- a resolved title's metered row settles without an upstream call;
- an unresolved title's metered row still runs (the gate is not "off");
- a *withdrawn* offer does not gate — it is not an answer;
- a free source is never gated, whatever the title's state;
- an ``EXPLICIT`` request is never gated — a manual "check now" is honoured;
- the gate is inert while the policy is off, which is the default.

The last one matters as much as the first: switching the gate on must be a
measured, reversible decision, not a side effect of deploying this module.
"""
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from availability.models import ContentType, Country, Offer, Source
from tracker.models import (
    Movie,
    Provider,
    RefreshPriority,
    RefreshRequest,
    RefreshRequestStatus,
    WatchlistItem,
)
from tracker.services import refresh_requests as rr
from tracker.services import source_policy
from tracker.services.bulk_refresh import BulkRefreshSummary

SA = "streaming_availability"
WM = "watchmode"
COLLECTOR = "mediaset_infinity"


def make_movie(tmdb_id: int, title: str = "T") -> Movie:
    return Movie.objects.create(tmdb_id=tmdb_id, title=title, year=2000)


def offer_for(movie: Movie, source_slug: str, *, available: bool = True) -> Offer:
    provider, _ = Provider.objects.get_or_create(
        slug=f"{source_slug}-prov",
        defaults={"name": source_slug, "category": "stream"},
    )
    country, _ = Country.objects.get_or_create(code="IT", defaults={"name": "Italy"})
    source, _ = Source.objects.get_or_create(
        slug=source_slug, defaults={"name": source_slug}
    )
    return Offer.objects.create(
        tmdb_id=movie.tmdb_id,
        provider=provider,
        country=country,
        source=source,
        content_type=ContentType.MOVIE,
        offer_type="subscription",
        currently_available=available,
    )


def answered(movie: Movie, slug: str) -> None:
    """Record that ``slug`` definitively answered for this title."""
    RefreshRequest.objects.update_or_create(
        movie=movie,
        source=slug,
        status=RefreshRequestStatus.DONE,
        defaults={"completed_at": timezone.now(), "last_error": ""},
    )


class _UnlimitedLimiter:
    """A source with budget to spare.

    ``remaining()`` returns a real ``int`` (never ``None``) because
    ``None`` means "this source declares no quota" and takes a different path
    through ``_is_spent``. Reported as a large number rather than
    ``math.inf`` so ``isinstance(remaining, int)`` holds.
    """

    daily_limit = 100_000

    def __init__(self):
        self.acquired = 0

    def remaining(self) -> int:
        return self.daily_limit - self.acquired

    def is_exhausted(self) -> bool:
        return self.remaining() <= 0

    def acquire(self, *args, **kwargs) -> None:
        self.acquired += 1


def unlimited() -> _UnlimitedLimiter:
    return _UnlimitedLimiter()


class DrainGateTests(TestCase):
    """The gate, exercised through the real `_drain_one` code path."""

    def setUp(self):
        self.summary = BulkRefreshSummary(remaining_budget=10, budget=10)
        self.movie = make_movie(900, "Gate")

    def _drain(self, request, *, policy_on: bool):
        """Drain one request with the metered gate forced on or off.

        Two things are patched, and the second is not optional:

        - ``CONSULT_METERED_ONLY_WHEN_UNRESOLVED`` — the gate switch under test;
        - the SA **limiter** — the drain refuses to call upstream for a spent
          source (``_is_spent``), and the real limiter reads the shared
          ``ApiCallCounter`` rows. In a test database those are not isolated
          from the live quota, so an exhausted day would make every
          "should still run" assertion fail for a reason that has nothing to
          do with the gate. The override gives the source a known budget so
          each test measures the gate and nothing else.

        ``AvailabilityRefreshService`` is the real class with only
        ``refresh_content`` patched, so the drain resolves a real service and
        the call we assert on is the one the drain actually makes. A stub
        object here would pass while the drain built its service differently.

        ``is_stale`` is forced **True** — read the guard, not the name:
        ``not is_stale(...)`` means "already fresh, settle for free", so a
        title is only re-fetched when it *is* stale. Returning False silently
        routes every case into the freshness short-circuit, which is why an
        early version of this harness saw zero upstream calls in all four
        "should still run" tests and looked like a broken gate.
        """
        from availability.services.refresh import AvailabilityRefreshService

        # The drain only falls back to this service for the *empty* source
        # scope; every named scope builds its own from the registry
        # (`_service_for_scope`). Both are covered by the same
        # ``refresh_content`` patch below, so the instance is only here to
        # satisfy the signature.
        service = AvailabilityRefreshService()
        with patch.object(
            source_policy, "CONSULT_METERED_ONLY_WHEN_UNRESOLVED", policy_on
        ), patch.object(rr, "is_stale", return_value=True):
            rr._drain_one(
                request,
                summary=self.summary,
                service=service,
                limiter=unlimited(),
                rate_limit_hit=False,
            )
        request.refresh_from_db()
        return request

    def _request(self, source=SA, priority=RefreshPriority.STALE):
        return RefreshRequest.objects.create(
            movie=self.movie,
            source=source,
            priority=priority,
            status=RefreshRequestStatus.PENDING,
        )

    # -- the invariant ----------------------------------------------------

    def test_a_resolved_title_settles_without_an_upstream_call(self):
        """The core contract: a free source answered, so the metered request
        must not execute."""
        offer_for(self.movie, WM, available=True)
        request = self._request()

        with patch.object(
            rr.AvailabilityRefreshService, "refresh_content"
        ) as refresh:
            self._drain(request, policy_on=True)

        refresh.assert_not_called()
        self.assertEqual(request.status, RefreshRequestStatus.DONE)
        self.assertEqual(self.summary.skipped_policy_gated, 1)
        self.assertEqual(self.summary.refreshed, 0)

    def test_an_unresolved_title_still_runs_the_metered_source(self):
        """The gate must not simply disable the metered source.

        A title nothing has answered is precisely the case the scarce budget
        exists for. Gating it would leave the title permanently unknown.
        """
        request = self._request()

        with patch.object(
            rr.AvailabilityRefreshService, "refresh_content"
        ) as refresh:
            self._drain(request, policy_on=True)

        refresh.assert_called_once()
        self.assertEqual(self.summary.skipped_policy_gated, 0)

    def test_a_withdrawn_offer_does_not_gate(self):
        """An inactive offer is not an answer.

        Gating on it would skip the only source that could still find the
        title, turning a resolvable unknown into a permanent one.
        """
        offer_for(self.movie, WM, available=False)
        request = self._request()

        with patch.object(
            rr.AvailabilityRefreshService, "refresh_content"
        ) as refresh:
            self._drain(request, policy_on=True)

        refresh.assert_called_once()
        self.assertEqual(self.summary.skipped_policy_gated, 0)

    def test_a_free_source_is_never_gated(self):
        """Free sources run early on purpose — that is what produces the
        evidence the metered source would otherwise pay for."""
        offer_for(self.movie, WM, available=True)
        request = self._request(source=COLLECTOR)

        with patch.object(
            rr.AvailabilityRefreshService, "refresh_content"
        ) as refresh:
            self._drain(request, policy_on=True)

        refresh.assert_called_once()
        self.assertEqual(self.summary.skipped_policy_gated, 0)

    def test_an_explicit_request_is_never_gated(self):
        """A manual "Refresh availability" is a deliberate ask.

        The gate must not silently override a direct instruction; this mirrors
        the existing freshness contract, where EXPLICIT also bypasses the
        short-circuit.
        """
        offer_for(self.movie, WM, available=True)
        request = self._request(priority=RefreshPriority.EXPLICIT)

        with patch.object(
            rr.AvailabilityRefreshService, "refresh_content"
        ) as refresh:
            self._drain(request, policy_on=True)

        refresh.assert_called_once()
        self.assertEqual(self.summary.skipped_policy_gated, 0)

    def test_the_gate_is_inert_while_the_policy_is_off(self):
        """Default behaviour is unchanged: deploying this module must not
        alter a single scheduled request until the gate is switched on."""
        offer_for(self.movie, WM, available=True)
        request = self._request()

        with patch.object(
            rr.AvailabilityRefreshService, "refresh_content"
        ) as refresh:
            self._drain(request, policy_on=False)

        refresh.assert_called_once()
        self.assertEqual(self.summary.skipped_policy_gated, 0)


class DrainGateRespectsPolicySwitchTests(TestCase):
    """The default really is off — checked on the module, not on a patch."""

    def test_the_gate_defaults_to_off(self):
        self.assertFalse(source_policy.CONSULT_METERED_ONLY_WHEN_UNRESOLVED)

    def test_the_metered_source_is_the_only_gated_one(self):
        metered = [
            slug
            for slug in source_policy.SOURCE_ROLES
            if source_policy.is_metered_role(slug)
        ]
        self.assertEqual(metered, [SA])


class EnqueueAndDrainAgreeTests(TestCase):
    """Enqueue and drain must not disagree about the same title.

    A disagreement is the worst possible failure mode here: the enqueue side
    says "skip" while the drain side says "run", so the row is never created
    *and* the gate looks like it is working while the budget is spent anyway.
    Both halves ask the same policy function for the same title, so this pins
    that they agree rather than reimplementing the decision.
    """

    def setUp(self):
        self.movie = make_movie(901, "Agreement")
        WatchlistItem.objects.get_or_create(user_id=1, movie=self.movie)

    def test_a_resolved_title_is_skipped_by_both_halves(self):
        offer_for(self.movie, WM, available=True)
        with patch.object(
            source_policy, "CONSULT_METERED_ONLY_WHEN_UNRESOLVED", True
        ):
            from tracker.services.availability_report import title_source_report

            entry = title_source_report([self.movie.pk])[self.movie.pk]
            decision = source_policy.decide_sources_for_title(entry)
            enqueue_allows = SA in decision["allowed_sources"]
            drain_gates = rr._metered_source_is_unnecessary(
                self.movie, SA, RefreshPriority.STALE
            )

        self.assertFalse(enqueue_allows, "enqueue must not request SA")
        self.assertTrue(drain_gates, "drain must gate SA if a row existed")