"""Durable catalogue checkpoints: the four Phase-2 sources, end to end.

This module exists because "the cursor is not None" proves nothing. Every test
here asserts a *durable* property, in one of two layers:

- **walk layer** (``SimpleTestCase``, fixtures only): the checkpoint's content,
  what a resume does and does not re-request, and how malformed input degrades.
- **persistence layer** (``TestCase``, real ``SyncCursor`` rows through
  ``AvailabilityRefreshService``): that a checkpoint written by one *process*
  is read by the next, that a failure leaves the previous checkpoint intact, and
  that a crash between the offer commit and the cursor write reprocesses rather
  than silently skipping.

Coverage per source, deliberately the same list for each of the four so a gap is
visible rather than implied: initial traversal, checkpoint creation, resume after
a simulated crash, resume across a process restart, failure before the checkpoint
is persisted, failure after the batch but before the checkpoint, duplicate-entry
idempotency, end-of-catalogue completion, invalid/obsolete checkpoints, a mutable
catalogue (pagination drift), and repeated resume cycles.
"""
from __future__ import annotations

import copy
import json
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from availability.domain.enums import SyncMode
from availability.models import Country, Source, SyncCursor
from availability.persistence.repositories import CursorRepository
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import (
    CBCGemSource,
    PbsSource,
    PlutoTvSource,
    RaiPlaySource,
    SourceCapabilities,
    SourceFetchError,
)
from availability.sources.cbc_gem import CBCGemClient
from availability.sources.pluto_tv import PlutoTvClient
from availability.sources.pbs import PbsClient
from availability.sources.rai_play import RaiPlayClient
from tracker.models import Provider

from .cbc_fixtures import FakeCbcApi
from .pluto_fixtures import FakePlutoTvApi
from .pbs_fixtures import FakePbsApi
from .rai_play_fixtures import PROGRAMME_URLS, FakeRaiPlayApi, url_entry


# ---------------------------------------------------------------- adapters
#
# One small adapter per source, so the 11 scenarios below are written once and
# run against all four. Each adapter answers the same four questions: build a
# source with a bounded batch, run it, read what it requested, and report which
# entries it observed.


class CbcAdapter:
    slug = "cbc_gem"
    provider = "cbc_gem"
    country = "CA"

    @staticmethod
    def api():
        return FakeCbcApi()

    @staticmethod
    def source(api, batch):
        return CBCGemSource(client=CBCGemClient(requester=api), batch_size=batch)

    @staticmethod
    def requests(api):
        """Detail (per-card) requests, which is the expensive part of the walk."""
        return api.urls_containing("/show/")

    @staticmethod
    def observed(result):
        return [c.provider_content_id for c in result.contents]


class PbsAdapter:
    slug = "pbs"
    provider = "pbs"
    country = "US"

    @staticmethod
    def api():
        return FakePbsApi()

    @staticmethod
    def source(api, batch):
        return PbsSource(client=PbsClient(http_post=api.post), batch_size=batch)

    @staticmethod
    def requests(api):
        """The slugs each showsBatch call asked about."""
        out = []
        for payload in api.payloads:
            if payload.get("operationName") == "PbsShowsBatch":
                out.extend(str(s) for s in (payload.get("variables") or {}).get("slugs") or [])
        return out

    @staticmethod
    def observed(result):
        return [c.provider_content_id for c in result.contents]


class PlutoAdapter:
    slug = "pluto_tv"
    provider = "pluto_tv"
    country = "US"

    @staticmethod
    def api():
        # ``pad_past_end`` lets an offset advance past the last recorded grid
        # page the way the real API does (empty result set), which is the state
        # a checkpoint holding a page-final anchor resumes into.
        return FakePlutoTvApi(pad_past_end=True)

    @staticmethod
    def source(api, batch):
        return PlutoTvSource(client=PlutoTvClient(requester=api), batch_pages=batch)

    @staticmethod
    def requests(api):
        """Grid page offsets requested, as ``"<token>@<start>"``."""
        return [
            f"{json.dumps(v.get('token'))}@{v['carouselParams']['start']}"
            for (op, v) in api.requests if op == "carouselData"
        ]

    @staticmethod
    def observed(result):
        return [c.provider_content_id for c in result.contents]


class RaiPlayAdapter:
    slug = "rai_play"
    provider = "raiplay"
    country = "IT"

    @staticmethod
    def api():
        return FakeRaiPlayApi()

    @staticmethod
    def source(api, batch):
        return RaiPlaySource(client=RaiPlayClient(requester=api), max_contents=batch)

    @staticmethod
    def requests(api):
        return list(api.programme_requests)

    @staticmethod
    def observed(result):
        return [c.provider_content_id for c in result.contents]


ADAPTERS = [CbcAdapter, PbsAdapter, PlutoAdapter, RaiPlayAdapter]

#: A batch size that forces each source's fixture into several batches, so a
#: resume actually has something left to walk. Each fixture is small (1-6
#: entries); 1 or 2 keeps every scenario meaningful without inventing data.
SMALL_BATCH = 1


def walk_to_exhaustion(adapter, batch=SMALL_BATCH, limit=40):
    """Walk one source in bounded batches until it reports completion.

    Returns ``(steps, requests, observed)`` where ``steps`` is the list of
    per-batch results. Raises if the walk does not terminate within *limit*
    batches — a resume loop that never advances is a failure, not a slow test.
    """
    cursor = None
    steps, requests, observed = [], [], []
    for _ in range(limit):
        api = adapter.api()
        source = adapter.source(api, batch)
        result = source.fetch(cursor=cursor)
        steps.append(result)
        requests.extend(adapter.requests(api))
        observed.extend(adapter.observed(result))
        if result.cursor is None:
            return steps, requests, observed
        cursor = result.cursor
    raise AssertionError(
        f"{adapter.slug}: walk did not terminate in {limit} batches "
        f"(last cursor: {cursor!r})"
    )


# ------------------------------------------------------- walk-layer scenarios


class InitialTraversalTests(SimpleTestCase):
    """The unbounded walk still observes the whole catalogue."""

    def test_each_source_observes_its_whole_catalogue_in_one_batch(self):
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                api = adapter.api()
                source = adapter.source(api, batch=None if adapter is RaiPlayAdapter else 10_000)
                result = source.fetch()
                self.assertTrue(adapter.observed(result), "walk observed nothing")
                # An exhausted walk reports no checkpoint, so the refresh service
                # drops any leftover row instead of resuming a finished catalogue.
                self.assertIsNone(
                    result.cursor,
                    f"{adapter.slug}: exhausted walk must not leave a checkpoint",
                )


class CheckpointCreationTests(SimpleTestCase):
    """A truncated walk hands back a usable, versioned checkpoint."""

    def test_a_truncated_walk_emits_a_versioned_checkpoint(self):
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                api = adapter.api()
                result = adapter.source(api, SMALL_BATCH).fetch()
                self.assertIsNotNone(
                    result.cursor, f"{adapter.slug}: truncated walk left no checkpoint"
                )
                payload = json.loads(result.cursor)
                self.assertEqual(payload["v"], 1)
                self.assertIsInstance(payload.get("parts", {}), dict)

    def test_a_checkpoint_carries_progress_and_stays_bounded(self):
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                api = adapter.api()
                result = adapter.source(api, SMALL_BATCH).fetch()
                # A checkpoint is a pointer, not a record set: it must stay small
                # however large the catalogue is (SyncCursor.cursor is an
                # unbounded TextField, so nothing else bounds it).
                self.assertLess(len(result.cursor), 4096)
                payload = json.loads(result.cursor)
                self.assertTrue(
                    payload.get("parts") or payload.get("done"),
                    f"{adapter.slug}: checkpoint records neither progress nor completion",
                )


class ResumeAfterCrashTests(SimpleTestCase):
    """An interrupted walk resumes; it does not re-request what it finished."""

    def test_resume_does_not_re_request_completed_work(self):
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                # Batch 1: interrupt after one batch by simply stopping.
                api1 = adapter.api()
                first = adapter.source(api1, SMALL_BATCH).fetch()
                done_before = adapter.requests(api1)
                self.assertIsNotNone(first.cursor)

                # Batch 2: a *fresh* source (a fresh process) resumes.
                api2 = adapter.api()
                second = adapter.source(api2, SMALL_BATCH).fetch(cursor=first.cursor)
                after = adapter.requests(api2)

                overlap = set(done_before) & set(after)
                if adapter is PlutoAdapter:
                    # Pluto's offset+anchor deliberately re-reads the one page
                    # that held the anchor. That is the *point* of the anchor:
                    # re-reading a page is how the resume proves the window has
                    # not shifted under it. One page is the whole allowance.
                    self.assertLessEqual(
                        len(overlap), 1,
                        f"pluto_tv re-read more than the anchor page: {overlap}",
                    )
                else:
                    self.assertEqual(
                        overlap, set(),
                        f"{adapter.slug}: resume re-requested completed work",
                    )
                # Whatever was re-read, the batch made progress.
                self.assertNotEqual(second.cursor, first.cursor)

    def test_a_resumed_walk_costs_no_more_requests_than_one_pass(self):
        """The measurable form of "avoid unnecessary repeat requests".

        Comparing against the single unbounded pass is the honest bound: it
        holds for every source regardless of how each one pages, and it is
        exactly the property an operator cares about — resuming must not cost
        more upstream work than walking the catalogue once.
        """
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                steps, requests, observed = walk_to_exhaustion(adapter)

                single_api = adapter.api()
                single = adapter.source(
                    single_api, batch=None if adapter is RaiPlayAdapter else 10_000
                ).fetch()
                single_requests = adapter.requests(single_api)
                expected = sorted(adapter.observed(single))

                # Nothing lost, nothing invented.
                self.assertEqual(
                    sorted(set(observed)), expected,
                    f"{adapter.slug}: resumed walk lost or invented catalogue entries",
                )
                # No more upstream work than one pass, plus the documented
                # per-batch allowance of a single extra request (Pluto re-reads
                # the anchor's page; a CBC card listed in two category indices is
                # inspected once per index). What this rules out is a resume that
                # re-reads the window it already walked.
                self.assertLessEqual(
                    len(requests), len(single_requests) + len(steps),
                    f"{adapter.slug}: resumed walk issued {len(requests)} requests "
                    f"for a catalogue one pass reads in {len(single_requests)} "
                    f"across {len(steps)} batches",
                )
                self.assertIsNone(steps[-1].cursor)


class MutableCatalogueTests(SimpleTestCase):
    """A checkpointed anchor survives the catalogue changing underneath it."""

    def test_a_head_insertion_does_not_shift_the_resume_point(self):
        """New entries above the anchor are picked up, not skipped, and do not
        cause the already-walked window to be re-requested."""
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                api1 = adapter.api()
                first = adapter.source(api1, SMALL_BATCH).fetch()
                done_before = adapter.requests(api1)
                cursor = first.cursor
                self.assertIsNotNone(cursor)

                # Insert a brand-new entry at the head of the walk's source list.
                api2 = adapter.api()
                _insert_head(adapter, api2)

                second = adapter.source(
                    api2, batch=None if adapter is RaiPlayAdapter else 10_000
                ).fetch(cursor=cursor)
                after = adapter.requests(api2)

                overlap = set(done_before) & set(after)
                # An insertion above the anchor cannot move the resume point: the
                # anchor is an identity, not a position. A *bounded* number of
                # already-walked entries may legitimately be re-read — Pluto
                # re-reads the page holding the anchor by design (that re-read is
                # how it proves the window has not shifted), and a card listed in
                # two of CBC's three category indices is inspected once per index.
                # What must never happen is the whole walked window coming back,
                # which is what a position-based resume point would do.
                self.assertLessEqual(
                    len(overlap), 2,
                    f"{adapter.slug}: a head insertion moved the resume point "
                    f"({len(overlap)} already-walked entries re-requested)",
                )

    def test_an_anchor_that_left_the_catalogue_restarts_instead_of_skipping(self):
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                api = adapter.api()
                # An anchor naming an entry that no longer exists, per part.
                parts = {
                    "programmes": "https://example.invalid/gone",
                    "shows": "gone-show",
                    "movies-all-movies": json.dumps({"start": 0, "after": "gone"}),
                    "shows-all-shows": json.dumps({"start": 0, "after": "gone"}),
                }
                cursor = json.dumps({"v": 1, "parts": parts})
                source = adapter.source(
                    api, batch=None if adapter is RaiPlayAdapter else 10_000
                )
                result = source.fetch(cursor=cursor)
                # It restarts from the top and still observes the catalogue: an
                # unresolvable anchor is never a reason to skip or to look done.
                self.assertTrue(
                    adapter.observed(result),
                    f"{adapter.slug}: an unresolvable anchor observed nothing",
                )
                self.assertIsNone(result.cursor)


class EndOfCatalogueTests(SimpleTestCase):
    """Completion is reported only when the traversal is genuinely exhausted."""

    def test_completion_only_after_every_entry_was_walked(self):
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                steps, _requests, observed = walk_to_exhaustion(adapter)
                # Every step but the last leaves a checkpoint; the last does not.
                for step in steps[:-1]:
                    self.assertIsNotNone(
                        step.cursor,
                        f"{adapter.slug}: a non-final batch claimed completion",
                    )
                self.assertIsNone(
                    steps[-1].cursor,
                    f"{adapter.slug}: the final batch left a stale checkpoint",
                )

    def test_a_batch_that_observes_nothing_still_advances(self):
        """Progress is a property of the batch, not of the offers it produced."""
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                # Batch size 1 on a catalogue whose entries mostly produce no
                # offers: the walk must still terminate rather than stall on an
                # entry that yields nothing.
                steps, _requests, _observed = walk_to_exhaustion(adapter, batch=1)
                self.assertGreater(len(steps), 1)
                self.assertIsNone(steps[-1].cursor)


class InvalidCheckpointTests(SimpleTestCase):
    """Every unreadable checkpoint restarts safely — never skips, never 'done'."""

    def test_garbage_checkpoints_restart_and_still_walk_everything(self):
        bad_values = [
            "{not-json",
            json.dumps({"v": 99, "parts": {"shows": "x"}}),   # version bump
            json.dumps({"v": 1, "parts": "not-a-dict"}),
            json.dumps({"v": 1, "done": "not-a-list"}),
            json.dumps({"v": 1}),
            json.dumps({"v": 1, "parts": {"unknown-part": "x"}}),
            "[]",
            "null",
        ]
        for adapter in ADAPTERS:
            for bad in bad_values:
                with self.subTest(source=adapter.slug, cursor=bad):
                    whole_api = adapter.api()
                    whole = adapter.source(
                        whole_api, batch=None if adapter is RaiPlayAdapter else 10_000
                    ).fetch()
                    expected = sorted(adapter.observed(whole))

                    api = adapter.api()
                    result = adapter.source(
                        api, batch=None if adapter is RaiPlayAdapter else 10_000
                    ).fetch(cursor=bad)

                    self.assertEqual(
                        sorted(adapter.observed(result)), expected,
                        f"{adapter.slug}: a bad checkpoint changed what was walked",
                    )
                    self.assertIsNone(result.cursor)

    def test_a_foreign_checkpoint_from_another_source_is_ignored(self):
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                # A cursor shaped like JustWatch's partition map.
                foreign = "netflix|NL|movie|2026|2026=200&amazonprime|NL|movie=100"
                api = adapter.api()
                result = adapter.source(
                    api, batch=None if adapter is RaiPlayAdapter else 10_000
                ).fetch(cursor=foreign)
                self.assertTrue(adapter.observed(result))
                self.assertIsNone(result.cursor)


class PlutoOffsetAnchorTests(SimpleTestCase):
    """Pluto's offset+anchor pairing, which the generic scenarios cannot express.

    The offset is the fast path and the anchor is the correctness guard, and the
    pairing that makes it work is easy to get wrong: the anchor must be the last
    item walked *and* the offset must name the page that **contained** it. Any
    other combination either skips entries or oscillates for ever.
    """

    def test_the_anchor_names_a_page_the_resume_actually_revisits(self):
        api = PlutoAdapter.api()
        result = PlutoAdapter.source(api, 1).fetch()
        self.assertIsNotNone(result.cursor)
        payload = json.loads(result.cursor)
        hub_state = json.loads(payload["parts"]["movies-all-movies"])
        self.assertIn("start", hub_state)
        self.assertIn("after", hub_state)

        # Resuming must find the anchor, which means the offset it records has to
        # be a page that contains it.
        api2 = PlutoAdapter.api()
        resumed = PlutoAdapter.source(api2, 1).fetch(cursor=result.cursor)
        self.assertIsNotNone(resumed.cursor)
        # The walk advances: the recorded offset must not be identical for ever.
        self.assertNotEqual(resumed.cursor, result.cursor)

    def test_an_insertion_above_the_offset_does_not_skip_entries(self):
        """The declared total drifting is what the anchor exists to survive."""
        api = PlutoAdapter.api()
        first = PlutoAdapter.source(api, 1).fetch()
        cursor = first.cursor
        self.assertIsNotNone(cursor)

        # Shift the movies grid: re-serve page 1 with an extra item prepended,
        # exactly as a declared-total drift does upstream.
        api2 = PlutoAdapter.api()
        page1 = copy.deepcopy(api2.grids["all-movies:fixture@v1"]["0"])
        rows = page1["data"]["carouselData"]["result"]["data"]
        rows.insert(0, dict(rows[0], id="inserted-at-head"))
        api2.grids["all-movies:fixture@v1"]["0"] = page1

        walked = list(PlutoAdapter.observed(first))
        for _ in range(20):
            source = PlutoAdapter.source(api2, 1)
            result = source.fetch(cursor=cursor)
            walked.extend(PlutoAdapter.observed(result))
            if result.cursor is None:
                break
            cursor = result.cursor
        else:
            self.fail("Pluto walk did not terminate after a head insertion")

        # Every original entry is still observed: the insertion did not push the
        # resume point past anything.
        whole_api = PlutoAdapter.api()
        expected = set(PlutoAdapter.observed(PlutoAdapter.source(whole_api, 100).fetch()))
        self.assertTrue(
            expected.issubset(set(walked)),
            f"insertion lost entries: missing {expected - set(walked)}",
        )


def _insert_head(adapter, api):
    """Add one new entry at the head of *adapter*'s walk order."""
    if adapter is RaiPlayAdapter:
        new = "https://www.raiplay.it/programmi/zz-brand-new"
        api.pages[new] = api.pages[PROGRAMME_URLS[0]]
        for sm in api.sitemaps:
            api.sitemaps[sm] = api.sitemaps[sm].replace(
                "</urlset>", url_entry(new) + "</urlset>"
            )
        return
    if adapter is CbcAdapter:
        # Prepend a card to the first category's first page.
        pages = api.categories["shows"]
        page = pages["1"]
        page["results"] = [{"url": "brand-new", "title": "Brand New"}] + page["results"]
        api.details["brand-new"] = api.details["float"]
        return
    if adapter is PbsAdapter:
        page = api.pages[None]
        page["data"]["allShows"]["edges"] = [
            {"cursor": "new", "node": {"slug": "brand-new", "title": "Brand New"}}
        ] + page["data"]["allShows"]["edges"]
        api.batches["brand-new"] = {"data": {"showsBatch": []}}
        return
    if adapter is PlutoAdapter:
        # Prepend an item to the first movies grid page.
        token = "all-movies:fixture@v1"
        page = api.grids[token]["0"]
        rows = page["data"]["carouselData"]["result"]["data"]
        rows.insert(0, dict(rows[0], id="brand-new"))
        return
    raise AssertionError(f"no head-insertion recipe for {adapter.slug}")


# -------------------------------------------------- persistence-layer scenarios


class CatalogueCheckpointPersistenceTests(TestCase):
    """The durable half: real SyncCursor rows through the refresh service."""

    @classmethod
    def setUpTestData(cls):
        for adapter in ADAPTERS:
            Provider.objects.get_or_create(slug=adapter.provider, defaults={"name": adapter.slug})
            Country.objects.get_or_create(code=adapter.country, defaults={"name": adapter.country})

    def _service(self, adapter, api, batch):
        source = adapter.source(api, batch)
        # The service resolves the checkpoint's owning rows from capabilities,
        # exactly as production does.
        return AvailabilityRefreshService(source=source), source

    def test_a_checkpoint_is_persisted_for_the_right_provider_and_country(self):
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                SyncCursor.objects.all().delete()
                api = adapter.api()
                service, _source = self._service(adapter, api, SMALL_BATCH)
                service.refresh_catalogue()

                cursor = SyncCursor.objects.get(source__slug=adapter.slug)
                self.assertEqual(cursor.provider.slug, adapter.provider)
                self.assertEqual(cursor.country.code, adapter.country)
                self.assertTrue(cursor.cursor)

    def test_a_second_run_resumes_from_the_persisted_checkpoint(self):
        """The process boundary: only the database carries state forward."""
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                SyncCursor.objects.all().delete()
                api1 = adapter.api()
                service1, _ = self._service(adapter, api1, SMALL_BATCH)
                service1.refresh_catalogue()
                done_before = adapter.requests(api1)
                stored = SyncCursor.objects.get(source__slug=adapter.slug).cursor

                # A brand-new source object and service: nothing in memory.
                api2 = adapter.api()
                service2, _ = self._service(adapter, api2, SMALL_BATCH)
                service2.refresh_catalogue()
                after = adapter.requests(api2)

                # The resumed run must not re-walk what it already persisted.
                # Pluto re-reads the one page holding its anchor by design, so a
                # single overlapping request is allowed there; anything more
                # means the resume point moved backwards.
                overlap = set(done_before) & set(after)
                allowance = 1 if adapter is PlutoAdapter else 0
                self.assertLessEqual(
                    len(overlap), allowance,
                    f"{adapter.slug}: the resumed run re-requested persisted work "
                    f"({len(overlap)} overlapping requests)",
                )
                # The checkpoint moved forward rather than being re-derived.
                self.assertNotEqual(
                    SyncCursor.objects.get(source__slug=adapter.slug).cursor, stored
                )

    def test_a_completed_walk_drops_its_checkpoint(self):
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                SyncCursor.objects.all().delete()
                # Walk to exhaustion through the service, one batch at a time.
                for _ in range(40):
                    api = adapter.api()
                    service, _ = self._service(
                        adapter, api, batch=None if adapter is RaiPlayAdapter else 10_000
                    )
                    # Resume from whatever the database holds.
                    service.refresh_catalogue()
                    if not SyncCursor.objects.filter(source__slug=adapter.slug).exists():
                        break
                else:
                    self.fail(f"{adapter.slug}: service walk never completed")
                self.assertFalse(
                    SyncCursor.objects.filter(source__slug=adapter.slug).exists(),
                    f"{adapter.slug}: a finished walk left a checkpoint behind",
                )

    def test_a_fetch_failure_persists_no_checkpoint(self):
        """Failure before the checkpoint is written leaves the row absent."""
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                SyncCursor.objects.all().delete()
                api = adapter.api()
                _inject_midwalk_failure(adapter, api)
                service, _ = self._service(adapter, api, SMALL_BATCH)
                with self.assertRaises(SourceFetchError):
                    service.refresh_catalogue()
                self.assertFalse(
                    SyncCursor.objects.filter(source__slug=adapter.slug).exists(),
                    f"{adapter.slug}: a failed fetch wrote a checkpoint",
                )

    def test_a_failure_keeps_the_previous_checkpoint_and_retries_it(self):
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                SyncCursor.objects.all().delete()
                api1 = adapter.api()
                service1, _ = self._service(adapter, api1, SMALL_BATCH)
                service1.refresh_catalogue()
                before = SyncCursor.objects.get(source__slug=adapter.slug).cursor

                # Now fail mid-walk while resuming from that checkpoint.
                api2 = adapter.api()
                _inject_midwalk_failure(adapter, api2)
                service2, _ = self._service(adapter, api2, SMALL_BATCH)
                with self.assertRaises(SourceFetchError):
                    service2.refresh_catalogue()

                self.assertEqual(
                    SyncCursor.objects.get(source__slug=adapter.slug).cursor, before,
                    f"{adapter.slug}: a failed batch advanced the checkpoint",
                )

    def test_a_crash_between_the_offer_commit_and_the_cursor_write_reprocesses(self):
        """The dangerous gap: rows committed, checkpoint not yet written.

        The offers are already durable at that point, so the only safe outcome is
        that the checkpoint did NOT move and the next run re-reads that batch.
        Re-reading is idempotent; skipping the batch would lose it silently.
        """
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                SyncCursor.objects.all().delete()
                api = adapter.api()
                service, _ = self._service(adapter, api, SMALL_BATCH)
                with patch.object(
                    CursorRepository, "set",
                    side_effect=RuntimeError("crash after commit, before cursor write"),
                ):
                    with self.assertRaises(RuntimeError):
                        service.refresh_catalogue()
                self.assertFalse(
                    SyncCursor.objects.filter(source__slug=adapter.slug).exists(),
                    f"{adapter.slug}: the checkpoint advanced despite the crash",
                )

    def test_duplicate_entries_within_one_walk_are_idempotent(self):
        """Re-walking a window writes the same rows, not duplicates."""
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                SyncCursor.objects.all().delete()
                api1 = adapter.api()
                service1, _ = self._service(adapter, api1, SMALL_BATCH)
                first = service1.refresh_catalogue()
                stored = SyncCursor.objects.get(source__slug=adapter.slug).cursor

                # Replay the identical batch (same cursor in, same window).
                api2 = adapter.api()
                service2, _ = self._service(adapter, api2, SMALL_BATCH)
                second = service2.refresh_catalogue(cursor=stored)

                self.assertEqual(
                    second.created, 0,
                    f"{adapter.slug}: replaying a batch created new offers",
                )
                self.assertGreaterEqual(second.updated, first.created)


def _inject_midwalk_failure(adapter, api):
    """Make the walk fail part-way, after at least one entry was requested.

    The failure has to be a *retrieval* failure (the walk raises), not an
    observed absence: RaiPlay answers 404 as ``None`` and a missing PBS show is
    skipped by design, and neither may fail a run. So each injection uses the
    transport/parse error the source treats as a failed fetch.
    """
    if adapter is RaiPlayAdapter:
        # Match *any* programme page, not one specific URL. The preceding
        # successful run leaves a checkpoint, so the resumed run starts after
        # whichever page that run walked — naming a single page would make the
        # injection depend on which page that happened to be, and the test would
        # silently stop failing (or stop exercising the failure) if the walk
        # order changed. ``"/programmi/"`` cannot match a sitemap URL
        # (``sitemap.raiplay.programmi.NN.YYYY.xml`` has no ``/programmi/``).
        api.failures["/programmi/"] = SourceFetchError("simulated outage")
        return
    if adapter is CbcAdapter:
        api.failures["/show/"] = SourceFetchError("simulated outage")
        return
    if adapter is PbsAdapter:
        api.failures["PbsShowsBatch"] = SourceFetchError("simulated outage")
        return
    if adapter is PlutoAdapter:
        api.failures["carouselData"] = SourceFetchError("simulated outage")
        return
    raise AssertionError(f"no failure recipe for {adapter.slug}")
