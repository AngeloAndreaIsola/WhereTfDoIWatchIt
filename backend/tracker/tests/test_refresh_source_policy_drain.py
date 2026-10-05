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
import os
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from availability.models import ContentType, Country, Offer, Source
from tracker.models import (
    BlockedReason,
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


class MeteredTerminologyTests(TestCase):
    """The two "is this metered?" questions must stay separate.

    There were two functions answering the same-sounding question:

    - ``availability_report.has_configured_rate_limit`` — *does a technical
      quota exist?* A deployment fact.
    - ``source_policy.is_policy_metered`` — *should the architecture treat this
      source as scarce?* A deliberate policy statement.

    They currently agree for all 13 registered sources, which is precisely why
    the duplication was dangerous: with no disagreement to notice, swapping
    one for the other would pass every existing test and only surface later as
    scheduling that changes when someone adds or removes a rate limit. So the
    tests below pin the *questions*, not today's answers.
    """

    def test_the_two_functions_are_independent_predicates(self):
        """Distinct callables reading distinct sources of truth.

        Asserted behaviourally by moving each source's own input and
        checking the *other* function does not react — comparing docstrings
        would only prove the two prose blocks differ, which is not the
        property that matters.
        """
        from tracker.services import availability_report as ar
        from tracker.services import rate_limit as rate

        # Move the policy input: the configuration question must not notice.
        saved_role = source_policy.SOURCE_ROLES["youtube"]
        try:
            source_policy.SOURCE_ROLES["youtube"] = (
                source_policy.AvailabilityRole.METERED_AGGREGATOR
            )
            self.assertTrue(source_policy.is_policy_metered("youtube"))
            self.assertFalse(ar.has_configured_rate_limit("youtube"))
        finally:
            source_policy.SOURCE_ROLES["youtube"] = saved_role

        # Move the configuration input: the policy question must not notice.
        saved_limits = dict(getattr(rate, "SOURCE_DAILY_LIMITS", {}) or {})
        try:
            if hasattr(rate, "SOURCE_DAILY_LIMITS"):
                rate.SOURCE_DAILY_LIMITS["mediaset_infinity"] = 5
                self.assertTrue(ar.has_configured_rate_limit("mediaset_infinity"))
            self.assertFalse(source_policy.is_policy_metered("mediaset_infinity"))
        finally:
            if hasattr(rate, "SOURCE_DAILY_LIMITS"):
                rate.SOURCE_DAILY_LIMITS.clear()
                rate.SOURCE_DAILY_LIMITS.update(saved_limits)

    def test_policy_metered_reads_roles_not_rate_limits(self):
        """A source declared metered in policy is metered even if no rate
        limit is configured — the policy is the authority, so a deployment
        that forgets to set a quota cannot silently un-gate the hierarchy."""
        saved = source_policy.SOURCE_ROLES["watchmode"]
        try:
            source_policy.SOURCE_ROLES["watchmode"] = (
                source_policy.AvailabilityRole.METERED_AGGREGATOR
            )
            self.assertTrue(source_policy.is_policy_metered("watchmode"))
            from tracker.services import availability_report as ar

            self.assertFalse(ar.has_configured_rate_limit("watchmode"))
        finally:
            source_policy.SOURCE_ROLES["watchmode"] = saved

    def test_a_configured_rate_limit_does_not_make_a_source_policy_metered(self):
        """The converse: a quota on a free source must not make the scheduler
        treat it as scarce. Gating on configuration would mean a rate limit —
        a reliability measure — silently became a scheduling priority."""
        self.assertTrue(source_policy.is_policy_metered("streaming_availability"))
        self.assertNotIn(
            "mediaset_infinity",
            {
                slug
                for slug in source_policy.SOURCE_ROLES
                if source_policy.is_policy_metered(slug)
            },
        )

    def test_the_old_ambiguous_names_are_gone(self):
        """Guard the rename itself, so the ambiguity cannot be reintroduced by
        a well-meaning alias."""
        for module in (source_policy,):
            self.assertFalse(hasattr(module, "is_metered_role"))
            self.assertFalse(hasattr(module, "is_metered"))
        from tracker.services import availability_report as ar

        self.assertFalse(hasattr(ar, "is_metered"))


class DrainGateTests(TestCase):
    """The gate, exercised through the real `_drain_one` code path."""

    def setUp(self):
        self.summary = BulkRefreshSummary(remaining_budget=10, budget=10)
        self.movie = make_movie(900, "Gate")

    def _drain(self, request, *, policy_on: bool):
        """Drain one request with the metered gate forced on or off.

        Two things are patched, and the second is not optional:

        - ``consult_metered_only_when_unresolved`` — the gate switch under
          test, patched as a *callable* so the test declares the policy it
          wants rather than inheriting whatever the environment set;
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
            source_policy,
            "consult_metered_only_when_unresolved",
            return_value=policy_on,
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
        must not execute.

        Settles as ``blocked``/``policy_not_needed``, which is the important
        half of the contract. This previously asserted ``done``, which claimed
        the metered source had been consulted and had produced an answer — it
        had not been consulted at all, so the row recorded a source answering
        when the whole point of the ladder is that it never asked.
        """
        offer_for(self.movie, WM, available=True)
        request = self._request()

        with patch.object(
            rr.AvailabilityRefreshService, "refresh_content"
        ) as refresh:
            self._drain(request, policy_on=True)

        refresh.assert_not_called()
        request.refresh_from_db()
        self.assertEqual(request.status, RefreshRequestStatus.BLOCKED)
        self.assertEqual(request.blocked_reason, BlockedReason.POLICY_NOT_NEEDED)
        self.assertIsNotNone(request.completed_at)
        self.assertEqual(self.summary.skipped_policy_gated, 1)
        self.assertEqual(self.summary.refreshed, 0)

    def test_a_policy_blocked_title_is_still_reported_available(self):
        """The layers stay separate: a blocked *request* is not an unknown title.

        This is the interaction P6a had to preserve. The ladder blocks the
        metered request precisely *because* a free source already answered, so
        treating the block as missing information would demote a resolved title
        to unknown — undoing the resolution-aware work in the other direction.
        """
        offer_for(self.movie, WM, available=True)
        request = self._request()

        with patch.object(rr.AvailabilityRefreshService, "refresh_content"):
            self._drain(request, policy_on=True)

        from tracker.services.availability_report import title_source_report

        entry = title_source_report([self.movie.pk])[self.movie.pk]
        self.assertEqual(entry["state"], "available")

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
    """The switch is read live from the environment, not captured at import.

    These replace a test that asserted the module-level constant, which was
    both wrong and unfixable: a constant is read when the module is imported,
    so the assertion could only ever report the state of whatever environment
    launched the test runner. Enabling the ladder in the dev containers broke
    it, and the test could not be made to pass by fixing the test — the thing
    it asserted no longer existed.

    Reading on demand fixes both halves. The default is still asserted, but
    against a controlled environment, and the "changing the environment
    changes the policy without a restart" property is now directly testable
    because it *wasn't* before.
    """

    def test_the_gate_defaults_to_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(source_policy.LADDER_ENV_VAR, None)
            self.assertFalse(source_policy.consult_metered_only_when_unresolved())

    def test_the_gate_is_on_when_the_environment_says_so(self):
        with patch.dict(
            os.environ, {source_policy.LADDER_ENV_VAR: "1"}, clear=False
        ):
            self.assertTrue(source_policy.consult_metered_only_when_unresolved())

    def test_changing_the_environment_takes_effect_without_a_restart(self):
        """The property the import-time constant made untestable.

        Same module object, same interpreter, no reload — which is what a
        deployment needs, since changing a variable should not require
        recycling the process to take effect.
        """
        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "1"}):
            self.assertTrue(source_policy.consult_metered_only_when_unresolved())
        with patch.dict(os.environ, {source_policy.LADDER_ENV_VAR: "0"}):
            self.assertFalse(source_policy.consult_metered_only_when_unresolved())

    def test_the_metered_source_is_the_only_gated_one(self):
        metered = [
            slug
            for slug in source_policy.SOURCE_ROLES
            if source_policy.is_policy_metered(slug)
        ]
        self.assertEqual(metered, [SA])


class GenericRequestHasNoMeteredIdentityTests(TestCase):
    """A generic refresh request must not acquire metered semantics.

    ``DEFAULT_SOURCE_SLUG`` is literally ``'streaming_availability'``, and
    ``resolve_request_source`` maps an empty ``source`` onto it. So a request
    that names no source was being read as a Streaming Availability request
    by the drain gate — the one metered source in the system.

    The visible damage: with the ladder switched on, nine unrelated drain and
    freshness tests began settling requests as policy-gated instead of
    executing them, because a generic request had silently become metered.
    The tests were right; the identity resolution was wrong.
    """

    def test_a_generic_request_is_not_gated_as_a_metered_source(self):
        movie = make_movie(950, "Generic")
        request = RefreshRequest.objects.create(
            movie=movie,
            source="",  # a generic refresh: no source named
            priority=RefreshPriority.STALE,
            status=RefreshRequestStatus.PENDING,
        )
        resolved_source = rr.resolve_request_source(request)

        with patch.object(
            source_policy,
            "consult_metered_only_when_unresolved",
            return_value=True,
        ):
            gated = rr._metered_source_is_unnecessary(
                movie,
                resolved_source,
                RefreshPriority.STALE,
                # The raw field, which is what the drain passes: a generic
                # request names no source even though it *resolves* to SA.
                explicitly_named_source=bool(request.source),
            )

        self.assertFalse(
            gated,
            "a request naming no source must not be gated as metered",
        )

    def test_an_explicit_sa_request_is_still_gated(self):
        """The fix must not disable the gate — only stop it misfiring."""
        movie = make_movie(951, "Explicit SA")
        offer_for(movie, WM, available=True)  # resolved by a cheaper source
        with patch.object(
            source_policy,
            "consult_metered_only_when_unresolved",
            return_value=True,
        ):
            gated = rr._metered_source_is_unnecessary(
                movie, SA, RefreshPriority.STALE
            )

        self.assertTrue(
            gated,
            "an explicit metered request for a resolved title must be gated",
        )


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
            source_policy,
            "consult_metered_only_when_unresolved",
            return_value=True,
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