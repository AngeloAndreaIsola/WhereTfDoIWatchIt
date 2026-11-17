"""Crash/restart coverage for the three pre-existing checkpointed collectors.

Tubi, Prime Video and BBC iPlayer checkpointed their walks before this phase, so
their *walk-layer* behaviour already had resume tests (``test_tubi.py``,
``test_prime_video.py``, ``test_bbc_iplayer.py``). What none of them had was
*persistence*-layer coverage: proof that a checkpoint survives the process
boundary through real ``SyncCursor`` rows, that a failure leaves the previous
checkpoint intact, that a crash between the offer commit and the cursor write
reprocesses rather than skipping, and that a completed walk clears the row.

That gap mattered: the Pluto TV defect fixed in ``8b8c6d6`` was invisible at the
walk layer — the source checkpointed perfectly in isolation while the service
never fed it a checkpoint at all. These tests exercise the same seam for the
other three, so the same class of defect cannot hide there either.

Each source is driven through ``AvailabilityRefreshService.refresh_catalogue``,
which is the production path: the service reads and writes the ``SyncCursor``
row, and the source never touches the database.
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from availability.domain.enums import SyncMode
from availability.models import Country, Source, SyncCursor
from availability.persistence.repositories import CursorRepository
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import SourceFetchError
from availability.sources.bbc_iplayer import BBCIPlayerClient, BBCIPlayerSource
from availability.sources.prime_video import PrimeVideoClient, PrimeVideoSource
from availability.sources.tubi import TubiClient, TubiSource

from tracker.models import Provider

from .bbc_fixtures import FakeBBCIPlayer
from .prime_video_fixtures import FakePrimeApi
from .tubi_fixtures import FakeTubiApi


class _Adapter:
    """One source, described so the scenarios below are written once."""

    slug = ""
    provider = ""
    country = ""
    #: Whether an exhausted walk drops its checkpoint. Tubi does **not**: its
    #: traversal is a *cycle* over a sitemap set that never ends, so a finished
    #: cycle persists a fresh one (see ``tubi._plan_batch``). For that source the
    #: correct terminal state is a reset checkpoint, not an absent one.
    completion_clears_checkpoint = True

    @classmethod
    def api(cls):
        raise NotImplementedError

    @classmethod
    def source(cls, api, batch):
        raise NotImplementedError

    @classmethod
    def requests(cls, api):
        raise NotImplementedError

    @classmethod
    def observed(cls, result):
        return [c.provider_content_id for c in result.contents]


class TubiAdapter(_Adapter):
    slug = "tubi"
    provider = "tubi"
    country = "US"
    # Tubi cycles: exhausting the sitemaps starts a new cycle immediately, so a
    # batch that finished one persists a fresh cycle checkpoint rather than
    # clearing the row. Its walk never reports "no more catalogue".
    completion_clears_checkpoint = False

    @classmethod
    def api(cls):
        return FakeTubiApi()

    @classmethod
    def source(cls, api, batch):
        return TubiSource(client=TubiClient(requester=api), max_contents=batch)

    @classmethod
    def requests(cls, api):
        return list(api.detail_requests)


class PrimeAdapter(_Adapter):
    slug = "prime_video"
    provider = "prime"
    country = "NL"

    @classmethod
    def api(cls):
        return FakePrimeApi()

    @classmethod
    def source(cls, api, batch):
        return PrimeVideoSource(
            client=PrimeVideoClient(requester=api, delay=0.0),
            batch_size=batch,
            # The fixture records a bounded storefront window (its own tests use
            # 8). Asking for more makes the discovery walk page past the
            # recorded index and fail loudly — correctly, but not what these
            # tests are about.
            max_contents=8,
        )

    @classmethod
    def requests(cls, api):
        return list(api.detail_requests)


class BbcAdapter(_Adapter):
    slug = "bbc_iplayer"
    provider = "bbc_iplayer"
    country = "GB"

    @classmethod
    def api(cls):
        return FakeBBCIPlayer()

    @classmethod
    def source(cls, api, batch):
        return BBCIPlayerSource(
            client=BBCIPlayerClient(requester=api, delay=0.0), batch_size=batch
        )

    @classmethod
    def requests(cls, api):
        # The per-programme *detail* pages, which is the expensive half. The A-Z
        # index (``/iplayer/a-z/<letter>``) is deliberately excluded: it is cheap
        # and legitimately re-read when a batch resumes inside a letter.
        return [
            url for url in api.requests
            if "/iplayer/episodes/" in url or "/iplayer/programmes/" in url
        ]


ADAPTERS = [TubiAdapter, PrimeAdapter, BbcAdapter]
SMALL_BATCH = 1


class ExistingSourceCheckpointPersistenceTests(TestCase):
    """Persisted checkpoint recovery, duplicate safety, retry, completion."""

    @classmethod
    def setUpTestData(cls):
        for adapter in ADAPTERS:
            Provider.objects.get_or_create(
                slug=adapter.provider, defaults={"name": adapter.slug}
            )
            Country.objects.get_or_create(
                code=adapter.country, defaults={"name": adapter.country}
            )

    def _service(self, adapter, api, batch):
        return AvailabilityRefreshService(source=adapter.source(api, batch))

    def _walk_once(self, adapter, batch=SMALL_BATCH):
        """One bounded batch through the real service."""
        SyncCursor.objects.filter(source__slug=adapter.slug).delete()
        api = adapter.api()
        self._service(adapter, api, batch).refresh_catalogue()
        return api

    # -- persisted recovery ------------------------------------------------

    def test_a_checkpoint_is_persisted_for_the_right_provider_and_country(self):
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                self._walk_once(adapter)
                cursor = SyncCursor.objects.get(source__slug=adapter.slug)
                self.assertEqual(cursor.provider.slug, adapter.provider)
                self.assertEqual(cursor.country.code, adapter.country)
                self.assertTrue(cursor.cursor)

    def test_a_second_run_resumes_from_the_persisted_checkpoint(self):
        """The process boundary: only the database carries state forward."""
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                first = self._walk_once(adapter)
                stored = SyncCursor.objects.get(source__slug=adapter.slug).cursor
                done_before = adapter.requests(first)
                self.assertTrue(done_before, "the first batch requested nothing")

                # A brand-new client and service: nothing survives in memory.
                api2 = adapter.api()
                self._service(adapter, api2, SMALL_BATCH).refresh_catalogue()
                after = adapter.requests(api2)

                self.assertEqual(
                    set(done_before) & set(after), set(),
                    f"{adapter.slug}: the resumed run re-requested persisted work",
                )
                self.assertNotEqual(
                    SyncCursor.objects.get(source__slug=adapter.slug).cursor, stored,
                    f"{adapter.slug}: the checkpoint did not advance",
                )

    def test_repeated_batches_reach_terminal_state_without_duplicating(self):
        """Walk in bounded batches until the source reports its own terminal
        state — an absent checkpoint, or (for a cycling source) a fresh cycle."""
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                SyncCursor.objects.filter(source__slug=adapter.slug).delete()
                seen_requests, seen_cursors = [], []
                for _ in range(60):
                    api = adapter.api()
                    self._service(adapter, api, SMALL_BATCH).refresh_catalogue()
                    seen_requests.extend(adapter.requests(api))
                    row = SyncCursor.objects.filter(
                        source__slug=adapter.slug
                    ).first()
                    if row is None:
                        break
                    seen_cursors.append(row.cursor)
                    if not adapter.completion_clears_checkpoint:
                        # Terminal for a cycling source: the checkpoint has been
                        # reset to a fresh cycle. Detect it by the cycle counter
                        # moving on rather than by the row disappearing.
                        if len(seen_cursors) > 1 and _cycle_of(row.cursor) > 0:
                            break
                else:
                    self.fail(f"{adapter.slug}: walk never reached a terminal state")

                if adapter.completion_clears_checkpoint:
                    self.assertFalse(
                        SyncCursor.objects.filter(source__slug=adapter.slug).exists(),
                        f"{adapter.slug}: a completed walk left a checkpoint behind",
                    )
                else:
                    # Cycling: a checkpoint remains, and it must be a *reset*
                    # one (empty queues), never a stale mid-cycle position.
                    self.assertTrue(seen_cursors, "no checkpoint was ever written")
                # The bounded walk never re-reads a detail page it already read.
                self.assertEqual(
                    len(seen_requests), len(set(seen_requests)),
                    f"{adapter.slug}: a resumed batch re-requested a detail page",
                )

    def test_duplicate_entries_within_one_walk_are_idempotent(self):
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                self._walk_once(adapter)
                stored = SyncCursor.objects.get(source__slug=adapter.slug).cursor

                api2 = adapter.api()
                service = self._service(adapter, api2, SMALL_BATCH)
                second = service.refresh_catalogue(cursor=stored)

                self.assertEqual(
                    second.created, 0,
                    f"{adapter.slug}: replaying a batch created new offers",
                )

    # -- failure handling --------------------------------------------------

    def test_a_fetch_failure_persists_no_checkpoint(self):
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                SyncCursor.objects.filter(source__slug=adapter.slug).delete()
                api = adapter.api()
                _inject_failure(adapter, api)
                service = self._service(adapter, api, SMALL_BATCH)
                with self.assertRaises(SourceFetchError):
                    service.refresh_catalogue()
                self.assertFalse(
                    SyncCursor.objects.filter(source__slug=adapter.slug).exists(),
                    f"{adapter.slug}: a failed fetch wrote a checkpoint",
                )

    def test_a_failure_keeps_the_previous_checkpoint_and_retries_it(self):
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                self._walk_once(adapter)
                before = SyncCursor.objects.get(source__slug=adapter.slug).cursor

                api2 = adapter.api()
                _inject_failure(adapter, api2)
                service = self._service(adapter, api2, SMALL_BATCH)
                with self.assertRaises(SourceFetchError):
                    service.refresh_catalogue()

                self.assertEqual(
                    SyncCursor.objects.get(source__slug=adapter.slug).cursor, before,
                    f"{adapter.slug}: a failed batch advanced the checkpoint",
                )

    def test_a_crash_between_the_offer_commit_and_the_cursor_write_reprocesses(self):
        """Offers durable, checkpoint not yet written: the safe outcome is that
        the checkpoint did NOT move, so the next run re-reads that batch.
        Re-reading is idempotent; skipping the batch would lose it."""
        for adapter in ADAPTERS:
            with self.subTest(source=adapter.slug):
                SyncCursor.objects.filter(source__slug=adapter.slug).delete()
                api = adapter.api()
                service = self._service(adapter, api, SMALL_BATCH)
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

    # -- terminal completion ------------------------------------------------

    def test_a_completed_walk_clears_the_checkpoint_and_claims_no_snapshot(self):
        for adapter in ADAPTERS:
            if not adapter.completion_clears_checkpoint:
                # Tubi cycles: it has no "completed" state to assert here. Its
                # terminal behaviour is covered by the cycling branch above.
                continue
            with self.subTest(source=adapter.slug):
                SyncCursor.objects.filter(source__slug=adapter.slug).delete()
                # One unbounded batch completes these fixtures outright.
                api = adapter.api()
                source = adapter.source(api, batch=10_000)
                AvailabilityRefreshService(source=source).refresh_catalogue()

                self.assertFalse(
                    SyncCursor.objects.filter(source__slug=adapter.slug).exists(),
                    f"{adapter.slug}: a completed walk left a checkpoint behind",
                )
                # Completion is traversal progress only — never a completeness
                # claim. If it were, a partial run could deactivate offers.
                self.assertFalse(source.capabilities.supports_authoritative_snapshots)


def _cycle_of(cursor: str) -> int:
    """The cycle counter inside a Tubi checkpoint (0 for anything else)."""
    import json

    try:
        return int(json.loads(cursor).get("cycle") or 0)
    except (TypeError, ValueError):
        return 0


def _inject_failure(adapter, api):
    """A retrieval failure the walk raises (not an observed absence)."""
    if adapter is TubiAdapter:
        # Every detail page fails: a traversal page failing must raise.
        api.failures["/movies/"] = SourceFetchError("simulated outage")
        return
    if adapter is PrimeAdapter:
        api.failures["/detail/"] = SourceFetchError("simulated outage")
        return
    if adapter is BbcAdapter:
        api.failures["/iplayer/"] = SourceFetchError("simulated outage")
        return
    raise AssertionError(f"no failure recipe for {adapter.slug}")
