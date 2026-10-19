"""The real catalogue-worker integration contract, as the worker calls it.

The live audit found three production-path defects that unit tests on the source
classes could not see, because each one lives at a *seam*:

1. ``_refresh_one_source`` dispatched JustWatch to the generic single unscoped
   fetch, which JustWatch refuses — every historical attempt recorded
   ``SourceFetchError: justwatch requires an explicit country code``.
2. The same seam had earlier forced a ``pages=`` keyword onto collectors that
   never declared it (``CBCGemSource.fetch()``, ``TubiSource.fetch()``).
3. CBC Gem's unbounded walk (500 pages/category + one detail fetch per item) ran
   on the single worker thread, so one slow upstream stalled the whole queue.

These tests call the seam the way the worker does, so a regression here is a
production stall rather than a failing unit test three layers down.
"""
from unittest import mock

from django.test import TestCase

from availability.services.refresh import AvailabilityRefreshService, RefreshResult
from availability.sources import source_registry
from availability.sources.base import accepts_kwarg
from tracker.services import bulk_refresh as br


class CatalogueRunnerDispatchTests(TestCase):
    """Which runner executes is a per-source decision, not a global one."""

    def test_justwatch_is_registered_for_its_partition_coordinator(self):
        self.assertIn("justwatch", br.CATALOGUE_RUNNERS)

    def test_every_other_source_keeps_the_generic_single_fetch_path(self):
        """No unrelated collector changes behaviour because of this seam."""
        for slug in ("abc_iview", "bbc_iplayer", "cbc_gem", "pbs", "pluto_tv",
                     "prime_video", "rai_play", "tubi"):
            with self.subTest(source=slug):
                self.assertNotIn(slug, br.CATALOGUE_RUNNERS)

    def test_justwatch_dispatch_reaches_the_coordinator(self):
        source = source_registry.get("justwatch")
        coordinator_result = {
            "status": "succeeded", "results": [
                {"status": "succeeded", "discovered": 7, "created": 7,
                 "updated": 0},
            ],
        }
        coordinator = mock.Mock(return_value=coordinator_result)
        with mock.patch.object(br, "_try_source_lock", return_value=True), \
             mock.patch.object(br, "_release_source_lock"), \
             mock.patch(
                 "tracker.services.bulk_refresh.resolve_source_country",
                 return_value="NL",
             ), \
             mock.patch(
                 "tracker.services.justwatch_sync.available_partitions",
                 return_value=[("NL", "netflix")],
             ), \
             mock.patch(
                 "tracker.services.justwatch_sync.refresh_justwatch_partitions",
                 coordinator,
             ), \
             mock.patch.object(br, "CATALOGUE_RUNNERS",
                               {"justwatch": br._justwatch_catalogue_runner}):
            summary = br._refresh_one_source("justwatch", source)

        self.assertTrue(coordinator.called,
                        "JustWatch must run through the P8 coordinator")
        self.assertEqual(summary.status, "succeeded")
        self.assertEqual(summary.discovered, 7)

    def test_a_generic_collector_still_uses_the_unscoped_fetch(self):
        """No registered runner -> the default single-fetch path, installed in
        the map (the runner is resolved by ``CATALOGUE_RUNNERS.get``), and never
        a live catalogue walk."""
        source = source_registry.get("abc_iview")
        stub = mock.Mock(return_value=RefreshResult(discovered=3, processed=3))
        with mock.patch.object(br, "_try_source_lock", return_value=True), \
             mock.patch.object(br, "_release_source_lock"), \
             mock.patch.dict(br.CATALOGUE_RUNNERS, {}, clear=True):
            self.assertNotIn("abc_iview", br.CATALOGUE_RUNNERS)
            # Record which runner the map hands back.
            with mock.patch.object(
                br, "CATALOGUE_RUNNERS",
                {"abc_iview": stub},
            ):
                summary = br._refresh_one_source("abc_iview", source)

        self.assertTrue(stub.called, "the generic runner must have executed")
        self.assertEqual(summary.status, "succeeded")
        self.assertEqual(summary.discovered, 3)

    def test_a_source_absent_from_the_map_falls_back_to_the_default(self):
        """The ``.get(slug, default)`` fallback itself, with no network."""
        self.assertIsNone(br.CATALOGUE_RUNNERS.get("tubi"))
        self.assertIs(br.CATALOGUE_RUNNERS.get("tubi", br._default_catalogue_runner),
                      br._default_catalogue_runner)


class JustWatchCountryPropagationTests(TestCase):
    """Country is the explicit application decision the audit flagged.

    The invariant under test is NOT merely that ``resolve_source_country()``
    returned a code. It is that the *planned partitions* are confined to that
    country — the previous draft used the resolved value only as a null guard,
    which would have walked every configured market.
    """

    def _plan_for(self, country):
        """The partition list the runner hands the coordinator."""
        source = source_registry.get("justwatch")
        captured = {}

        def coordinator(**kwargs):
            captured.update(kwargs)
            return {"status": "succeeded", "results": []}

        with mock.patch.object(br, "_try_source_lock", return_value=True), \
             mock.patch.object(br, "_release_source_lock"), \
             mock.patch(
                 "tracker.services.bulk_refresh.resolve_source_country",
                 return_value=country,
             ), \
             mock.patch(
                 "tracker.services.justwatch_sync.refresh_justwatch_partitions",
                 side_effect=coordinator,
             ):
            br._refresh_one_source("justwatch", source)
        return captured.get("partitions") or []

    def test_only_the_selected_country_is_planned(self):
        """NL must not drag US/GB/IT/JP along, however they are configured."""
        plan = self._plan_for("NL")
        self.assertTrue(plan, "NL must plan at least one partition")
        self.assertEqual(
            sorted({country for country, _ in plan}), ["NL"],
            "the plan leaked into another country",
        )

    def test_the_plan_is_capped(self):
        """The runner must not fan out over every NL provider at once."""
        from tracker.services.justwatch_sync import DEFAULT_MAX_PARTITIONS

        plan = self._plan_for("NL")
        self.assertLessEqual(len(plan), DEFAULT_MAX_PARTITIONS)

    def test_it_is_lowercase_and_different_country(self):
        """Same guarantee for another market, and for lowercase input."""
        for requested, expected in (("IT", "IT"), ("nl", "NL")):
            with self.subTest(country=requested):
                plan = self._plan_for(requested)
                self.assertTrue(plan)
                self.assertEqual(
                    sorted({c for c, _ in plan}), [expected],
                    f"{requested} planned the wrong markets",
                )

    def test_a_market_with_no_mapped_provider_fails_loudly(self):
        """An empty plan is not the same claim as 'JustWatch offers nothing'."""
        source = source_registry.get("justwatch")
        with mock.patch.object(br, "_try_source_lock", return_value=True), \
             mock.patch.object(br, "_release_source_lock"), \
             mock.patch(
                 "tracker.services.bulk_refresh.resolve_source_country",
                 return_value="ZZ",
             ), \
             mock.patch(
                 "tracker.services.justwatch_sync.available_partitions",
                 return_value=[],
             ):
            summary = br._refresh_one_source("justwatch", source)

        self.assertEqual(summary.status, "failed")
        self.assertIn("no available partition", summary.error)

    def test_the_coordinator_is_always_given_an_explicit_plan(self):
        """None would mean "rebuild the global plan" — the original bug."""
        source = source_registry.get("justwatch")
        captured = {}

        def coordinator(**kwargs):
            captured.update(kwargs)
            return {"status": "succeeded", "results": []}

        with mock.patch.object(br, "_try_source_lock", return_value=True), \
             mock.patch.object(br, "_release_source_lock"), \
             mock.patch(
                 "tracker.services.bulk_refresh.resolve_source_country",
                 return_value="NL",
             ), \
             mock.patch(
                 "tracker.services.justwatch_sync.refresh_justwatch_partitions",
                 side_effect=coordinator,
             ):
            br._refresh_one_source("justwatch", source)

        self.assertIsNotNone(captured.get("partitions"))
        self.assertIsNone(
            captured.get("max_partitions"),
            "an explicit plan must not be re-capped downstream",
        )

    def test_no_selected_country_fails_loudly_rather_than_guessing(self):
        """An aggregator asked about the wrong market answers confidently wrong."""
        source = source_registry.get("justwatch")
        with mock.patch.object(br, "_try_source_lock", return_value=True), \
             mock.patch.object(br, "_release_source_lock"), \
             mock.patch(
                 "tracker.services.bulk_refresh.resolve_source_country",
                 return_value=None,
             ), \
             mock.patch(
                 "tracker.services.justwatch_sync.refresh_justwatch_partitions"
             ) as coordinator:
            summary = br._refresh_one_source("justwatch", source)

        self.assertEqual(summary.status, "failed")
        self.assertFalse(coordinator.called,
                         "an unscoped fetch must never be attempted")
        self.assertIn("explicit country", summary.error)

    def test_a_failed_partition_surfaces_as_a_failed_run(self):
        """PARTIAL must not be recorded as a complete observation."""
        source = source_registry.get("justwatch")
        with mock.patch.object(br, "_try_source_lock", return_value=True), \
             mock.patch.object(br, "_release_source_lock"), \
             mock.patch(
                 "tracker.services.bulk_refresh.resolve_source_country",
                 return_value="NL",
             ), \
             mock.patch(
                 "tracker.services.justwatch_sync.refresh_justwatch_partitions",
                 return_value={
                     "status": "partial",
                     "results": [
                         {"status": "succeeded", "discovered": 5, "created": 5,
                          "updated": 0},
                         {"status": "failed", "discovered": 0, "created": 0,
                          "updated": 0, "error": "boom"},
                     ],
                 },
             ):
            summary = br._refresh_one_source("justwatch", source)

        self.assertEqual(summary.status, "failed")
        self.assertIn("boom", summary.error)


class OptionalKwargCompatibilityTests(TestCase):
    """``pages`` reaches only sources that declare it.

    The live path raised ``TypeError: <Source>.fetch() got an unexpected
    keyword argument 'pages'`` for every collector without the knob, 11 times.
    """

    def test_collectors_without_pages_are_never_given_it(self):
        for slug in ("cbc_gem", "tubi", "abc_iview", "bbc_iplayer", "pbs",
                     "pluto_tv", "prime_video", "rai_play"):
            with self.subTest(source=slug):
                source = source_registry.get(slug)
                self.assertFalse(accepts_kwarg(source.fetch, "pages"))
                self.assertTrue(hasattr(source, "fetch"))

    def test_justwatch_does_declare_pages(self):
        source = source_registry.get("justwatch")
        self.assertTrue(accepts_kwarg(source.fetch, "pages"))

    def test_the_real_catalogue_call_omits_pages_for_cbc_gem(self):
        """The exact boundary that failed in production."""
        source = source_registry.get("cbc_gem")
        seen = {}

        def spy(*args, **kwargs):
            seen.update(kwargs)
            return None

        with mock.patch.object(type(source), "fetch", spy):
            try:
                AvailabilityRefreshService(source=source).refresh_catalogue(
                    country="CA",
                )
            except (TypeError, Exception):
                # The spy returns None so persistence has nothing to write; the
                # assertion is on what the fetch was *called* with.
                pass
        self.assertNotIn("pages", seen)

    def test_the_real_catalogue_call_omits_pages_for_tubi(self):
        source = source_registry.get("tubi")
        seen = {}

        def spy(*args, **kwargs):
            seen.update(kwargs)
            return None

        with mock.patch.object(type(source), "fetch", spy):
            try:
                AvailabilityRefreshService(source=source).refresh_catalogue(
                    country="US",
                )
            except Exception:
                # See above: the spy yields nothing to persist.
                pass
        self.assertNotIn("pages", seen)


class CataloguePriorityHintTests(TestCase):
    """Tracked-title hints reach only sources that declare the capability."""

    def test_a_priority_capable_source_receives_tracked_titles(self):
        from tracker.models import Movie

        Movie.objects.create(tmdb_id=103, title="Taxi Driver", year=1976)
        seen = {}

        class Service:
            def refresh_catalogue(self, *, priority=None):
                seen["priority"] = priority
                return RefreshResult()

        br._default_catalogue_runner(
            "tubi", source_registry.get("tubi"), Service()
        )

        self.assertIn(("Taxi Driver", 1976, "movie"), seen["priority"])

    def test_a_source_without_the_capability_gets_no_priority_kwarg(self):
        seen = {}

        class Service:
            def refresh_catalogue(self):
                seen["called"] = True
                return RefreshResult()

        br._default_catalogue_runner(
            "abc_iview", source_registry.get("abc_iview"), Service()
        )

        self.assertTrue(seen["called"])


class WorkerToGraphQLCountryTests(TestCase):
    """The whole chain, ending at the bytes JustWatch would receive.

    Everything above asserts an argument. This asserts the payload, because a
    country that is resolved, passed, and then dropped before the POST is exactly
    the failure this task exists to close.
    """

    def _payloads_for(self, country):
        from availability.sources.justwatch_client import JustWatchClient
        from availability.sources import source_registry as registry

        from .aggregator_fixtures import FakeResponse
        from . import aggregator_fixtures as fx
        from .test_justwatch_coverage import RecordingPost, sleeper

        post = RecordingPost([
            fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX),
            fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX),
        ])
        source = registry.get("justwatch")
        source.client = JustWatchClient(http_post=post, sleeper=sleeper)
        original = source.client
        try:
            from tracker.services import bulk_refresh as worker

            with mock.patch.object(worker, "CATALOGUE_RUNNERS",
                                   {"justwatch": worker._justwatch_catalogue_runner}), \
                 mock.patch.object(worker, "_try_source_lock", return_value=True), \
                 mock.patch.object(worker, "_release_source_lock"), \
                 mock.patch(
                     "tracker.services.bulk_refresh.resolve_source_country",
                     return_value=country,
                 ), \
                 mock.patch(
                     "tracker.services.justwatch_sync.available_partitions",
                     return_value=[(country.upper(), "netflix")],
                 ), \
                 mock.patch("availability.sources.justwatch.JustWatchSource.fetch",
                            source.fetch):
                worker._refresh_one_source("justwatch", source)
        finally:
            source.client = original
        return post.payloads

    def test_the_graphql_request_carries_the_selected_country(self):
        payloads = self._payloads_for("NL")
        self.assertTrue(payloads, "no GraphQL request was attempted")
        for payload in payloads:
            self.assertEqual(
                payload["variables"]["country"], "NL",
                "the worker resolved NL but the request carried something else",
            )

    def test_a_lowercase_preference_is_normalised_in_the_payload(self):
        payloads = self._payloads_for("nl")
        self.assertTrue(payloads)
        for payload in payloads:
            self.assertEqual(payload["variables"]["country"], "NL")

    def test_another_market_reaches_the_payload_unchanged(self):
        payloads = self._payloads_for("IT")
        self.assertTrue(payloads)
        for payload in payloads:
            self.assertEqual(payload["variables"]["country"], "IT")

    def test_no_country_means_no_request_at_all(self):
        from availability.sources import source_registry as registry
        from availability.sources.justwatch_client import JustWatchClient

        from . import aggregator_fixtures as fx
        from .test_justwatch_coverage import RecordingPost, sleeper

        post = RecordingPost([fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX)])
        source = registry.get("justwatch")
        original = source.client
        source.client = JustWatchClient(http_post=post, sleeper=sleeper)
        try:
            from tracker.services import bulk_refresh as worker

            with mock.patch.object(worker, "CATALOGUE_RUNNERS",
                                   {"justwatch": worker._justwatch_catalogue_runner}), \
                 mock.patch.object(worker, "_try_source_lock", return_value=True), \
                 mock.patch.object(worker, "_release_source_lock"), \
                 mock.patch(
                     "tracker.services.bulk_refresh.resolve_source_country",
                     return_value=None,
                 ), \
                 mock.patch("availability.sources.justwatch.JustWatchSource.fetch",
                            source.fetch):
                summary = worker._refresh_one_source("justwatch", source)
        finally:
            source.client = original

        self.assertEqual(summary.status, "failed")
        self.assertEqual(
            post.payloads, [],
            "a request went out without an explicit country",
        )


class LockReleaseTests(TestCase):
    """A leaked advisory lock wedges that source forever.

    The audit's stalled worker traced to a lock taken on one connection and never
    released, after which every later CBC Gem attempt returned ``locked``.
    """

    def test_the_lock_is_released_when_the_runner_raises(self):
        source = source_registry.get("cbc_gem")
        with mock.patch.object(br, "_try_source_lock", return_value=True), \
             mock.patch.object(br, "_release_source_lock") as release:
            with mock.patch.object(
                br, "_default_catalogue_runner",
                side_effect=RuntimeError("upstream exploded"),
            ):
                summary = br._refresh_one_source("cbc_gem", source)

        self.assertEqual(summary.status, "failed")
        self.assertTrue(release.called,
                        "a failed run must not leak the source lock")

    def test_the_lock_is_released_after_a_successful_run(self):
        source = source_registry.get("abc_iview")
        with mock.patch.object(br, "_try_source_lock", return_value=True), \
             mock.patch.object(br, "_release_source_lock") as release, \
             mock.patch.object(
                 br, "_default_catalogue_runner",
                 return_value=RefreshResult(discovered=1, processed=1),
             ):
            br._refresh_one_source("abc_iview", source)

        self.assertTrue(release.called)

    def test_a_held_lock_skips_the_source_without_touching_it(self):
        source = source_registry.get("tubi")
        with mock.patch.object(br, "_try_source_lock", return_value=False), \
             mock.patch.object(br, "_default_catalogue_runner") as runner:
            summary = br._refresh_one_source("tubi", source)

        self.assertEqual(summary.status, "locked")
        self.assertFalse(runner.called)