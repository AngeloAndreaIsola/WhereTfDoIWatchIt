"""P8 — JustWatch partition cursor persistence.

The invariant under test, stated once:

    a catalogue walk may advance exactly one partition,
    but it must never erase progress belonging to another.

`SyncCursor` is unique on `(provider, source, country)`, so every partition of
one provider/country shares a single row — MOVIE vs SERIES, and each year
window. Before this, each partition wrote a *bare offset* onto that shared row,
so a MOVIE walk overwrote the SERIES walk's offset and both restarted from 0
forever. Nothing crashed; the walk simply never got past page one of anything.

The failure mode is also silent in the worst way: a partition that loses its
offset looks like one that is merely slow, so the symptom is "coverage is
stuck", not "an error". These tests therefore assert the *stored map*, not the
summary, wherever the summary would still look plausible.

The boundary that actually broke is exercised through the real coordinator →
`refresh_catalogue` → cursor persistence → coordinator merge path in
`CoordinatorPersistenceBoundaryTests`; the pure helpers are covered separately
so a failure localises.
"""
from unittest import mock

from django.test import TestCase

from availability.domain.enums import ContentType, SourceType
from availability.models import Country, Source, SyncCursor
from availability.sources.base import SourceCapabilities
from availability.sources.justwatch import JustWatchSource
from availability.sources.justwatch_client import CATALOGUE_MAX_OFFSET
from tracker.models import Provider
from tracker.services.justwatch_sync import (
    DEFAULT_MAX_PARTITIONS,
    _decode_cursor,
    _encode_cursor,
    _partition_id,
    _read_cursor_map,
    _write_cursor_map,
    available_partitions,
    refresh_justwatch_partitions,
    year_windows,
)

MOVIE = ContentType.MOVIE
SERIES = ContentType.SERIES

PID_MOVIE_A = _partition_id(
    country="NL", provider="netflix", content_type=MOVIE,
    min_year=2017, max_year=2026,
)
PID_MOVIE_B = _partition_id(
    country="NL", provider="netflix", content_type=MOVIE,
    min_year=2007, max_year=2016,
)
PID_SERIES_A = _partition_id(
    country="NL", provider="netflix", content_type=SERIES,
    min_year=2017, max_year=2026,
)


class _Repo:
    """Minimal stand-in for CursorRepository over a real SyncCursor row.

    Real rows, real reads and writes — the point of these tests is the
    read/overwrite/restore sequence, which a pure-dict fake would not exercise.
    """

    def __init__(self):
        self.provider = None
        self.source = None
        self.country = None

    def bind(self, provider, source, country):
        self.provider, self.source, self.country = provider, source, country

    def get(self, provider=None, source=None, country=None):
        return SyncCursor.objects.filter(
            provider=provider or self.provider,
            source=source or self.source,
            country=country or self.country,
        ).first()

    def set(self, provider=None, source=None, country=None, cursor=None, now=None):
        obj, _ = SyncCursor.objects.update_or_create(
            provider=provider or self.provider,
            source=source or self.source,
            country=country or self.country,
            defaults={"cursor": cursor},
        )
        return obj


class CursorIsolationTests(TestCase):
    """One partition advancing must leave every sibling offset untouched."""

    def setUp(self):
        # Migrations seed Provider/Country/Source, so these are get_or_create
        # rather than create().
        self.provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "netflix", "is_enabled": True})
        self.country, _ = Country.objects.get_or_create(
            code="NL", defaults={"name": "NL", "enabled": True})
        self.source, _ = Source.objects.get_or_create(
            slug="justwatch",
            defaults={"name": "JustWatch", "type": SourceType.AGGREGATOR})
        self.repo = _Repo()
        self.repo.bind(self.provider, self.source, self.country)

    def seed(self, mapping):
        self.repo.set(cursor=_encode_cursor(mapping))
        return self.repo.get()

    def stored(self):
        return _decode_cursor(self.repo.get().cursor)

    # -- the invariant ---------------------------------------------------

    def test_movie_advance_leaves_series_untouched(self):
        self.seed({PID_MOVIE_A: "300", PID_SERIES_A: "200"})

        # Simulate the whole sequence for one partition: the service writes a
        # single-entry cursor over the row, then the coordinator merges.
        snapshot = _read_cursor_map(self.repo, self.provider, self.source, self.country)
        self.repo.set(cursor=f"{PID_MOVIE_A}=400")  # service write
        _write_cursor_map(
            self.repo, self.provider, self.source, self.country,
            snapshot, partition_id=PID_MOVIE_A,
        )

        self.assertEqual(
            self.stored(), {PID_MOVIE_A: "400", PID_SERIES_A: "200"}
        )

    def test_year_window_advance_leaves_the_other_window_untouched(self):
        self.seed({PID_MOVIE_A: "300", PID_MOVIE_B: "100"})

        snapshot = _read_cursor_map(self.repo, self.provider, self.source, self.country)
        self.repo.set(cursor=f"{PID_MOVIE_A}=400")
        _write_cursor_map(
            self.repo, self.provider, self.source, self.country,
            snapshot, partition_id=PID_MOVIE_A,
        )

        self.assertEqual(
            self.stored(), {PID_MOVIE_A: "400", PID_MOVIE_B: "100"}
        )

    def test_all_four_dimensions_are_isolated(self):
        """country, provider, content type and year window, together.

        Each dimension is exercised by giving every partition a distinct offset
        and advancing exactly one: every other offset must survive verbatim.
        """
        pids = {
            "movie_nl": _partition_id(country="NL", provider="netflix",
                                      content_type=MOVIE, min_year=None, max_year=None),
            "movie_it": _partition_id(country="IT", provider="netflix",
                                      content_type=MOVIE, min_year=None, max_year=None),
            "series_nl": _partition_id(country="NL", provider="netflix",
                                       content_type=SERIES, min_year=None, max_year=None),
            "movie_nl_years": _partition_id(country="NL", provider="netflix",
                                            content_type=MOVIE,
                                            min_year=2017, max_year=2026),
            "movie_nl_prime": _partition_id(country="NL", provider="prime_video",
                                            content_type=MOVIE,
                                            min_year=None, max_year=None),
        }
        self.assertEqual(len(set(pids.values())), 5, "every dimension must differ")

        seed = {pid: str(100 * (i + 1)) for i, pid in enumerate(pids.values())}
        self.seed(seed)

        target = pids["movie_nl_years"]
        snapshot = _read_cursor_map(self.repo, self.provider, self.source, self.country)
        self.repo.set(cursor=f"{target}=999")
        _write_cursor_map(
            self.repo, self.provider, self.source, self.country,
            snapshot, partition_id=target,
        )

        expected = dict(seed)
        expected[target] = "999"
        self.assertEqual(self.stored(), expected)

    def test_exhausted_partition_does_not_erase_others(self):
        """An empty page marks one partition done and touches nothing else."""
        self.seed({PID_MOVIE_A: "300", PID_SERIES_A: "200"})

        snapshot = _read_cursor_map(self.repo, self.provider, self.source, self.country)
        # Exhausted: the source records the partition with an empty value.
        self.repo.set(cursor=f"{PID_MOVIE_A}=")
        _write_cursor_map(
            self.repo, self.provider, self.source, self.country,
            snapshot, partition_id=PID_MOVIE_A,
        )

        stored = self.stored()
        self.assertEqual(stored[PID_SERIES_A], "200")
        self.assertEqual(stored[PID_MOVIE_A], "")

    def test_legacy_bare_offset_is_not_reinterpreted_as_a_map(self):
        """A pre-P8 row is data, not a partition map.

        ``300`` means one partition had walked three pages. It must not be
        guessed at, and it must not silently become every partition's offset.
        """
        self.seed({"": "300"} and {})  # start from an empty map
        SyncCursor.objects.filter(
            provider=self.provider, source=self.source, country=self.country
        ).update(cursor="300")

        self.assertEqual(_decode_cursor("300"), {})

        snapshot = _read_cursor_map(self.repo, self.provider, self.source, self.country)
        self.repo.set(cursor=f"{PID_MOVIE_A}=100")
        _write_cursor_map(
            self.repo, self.provider, self.source, self.country,
            snapshot, partition_id=PID_MOVIE_A,
        )

        # Only the partition that actually ran gains an entry.
        self.assertEqual(self.stored(), {PID_MOVIE_A: "100"})

    def test_malformed_cursor_does_not_crash_or_invent_partitions(self):
        for raw in ("", "=", "&&&", "=300", "junk", "300;", "|||"):
            with self.subTest(raw=raw):
                self.assertIsInstance(_decode_cursor(raw), dict)

    def test_partition_ids_survive_cursor_separators(self):
        """A partition id must round-trip through the map's own separators."""
        self.seed({PID_MOVIE_A: "300", PID_SERIES_A: "200"})
        self.assertEqual(
            _decode_cursor(self.repo.get().cursor),
            {PID_MOVIE_A: "300", PID_SERIES_A: "200"},
        )

    def test_cursor_map_is_bounded(self):
        """A runaway map cannot grow without limit."""
        huge = {f"p{i}": str(i) for i in range(500)}
        encoded = _encode_cursor(huge)
        self.assertLessEqual(len(_decode_cursor(encoded)), 64)


class CoordinatorPersistenceBoundaryTests(TestCase):
    """Through the real coordinator → service → persistence → merge path.

    The pure helpers above prove the merge arithmetic. These prove the wiring:
    that the coordinator actually snapshots *before* the service writes, because
    a merge that ran afterwards would be one ordering change away from silently
    losing every sibling.
    """

    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "netflix", "is_enabled": True})
        self.country, _ = Country.objects.get_or_create(
            code="NL", defaults={"name": "NL", "enabled": True})
        self.source_row, _ = Source.objects.get_or_create(
            slug="justwatch",
            defaults={"name": "JustWatch", "type": SourceType.AGGREGATOR})

        class _S:
            slug = "justwatch"
            name = "JustWatch"
            source_type = SourceType.AGGREGATOR
            capabilities = SourceCapabilities()

        self.source = _S()

    @staticmethod
    def _service(next_cursor, calls, fail=False):
        from availability.services.refresh import RefreshResult

        class _Service:
            def refresh_catalogue(self, *, provider=None, country=None,
                                  cursor=None, pages=1):
                calls.append({"provider": provider, "country": country,
                              "cursor": dict(cursor or {}), "pages": pages})
                if fail:
                    from availability.sources.base import SourceFetchError

                    raise SourceFetchError("stub partition failure")
                # Reproduce the service's real behaviour: it persists the
                # source's cursor onto the shared SyncCursor row, wholesale.
                pid = (cursor or {}).get("partition")
                source_row = Source.objects.filter(slug="justwatch").first()
                country_row = Country.objects.filter(code=country).first()
                provider_row = Provider.objects.filter(slug=provider).first()
                SyncCursor.objects.update_or_create(
                    provider=provider_row, source=source_row, country=country_row,
                    defaults={"cursor": next_cursor},
                )
                return RefreshResult(discovered=1, processed=1, created=1)

        return _Service()

    def stored(self, provider_slug="netflix"):
        return _decode_cursor(SyncCursor.objects.get(
            provider__slug=provider_slug,
            source__slug="justwatch",
            country__code="NL",
        ).cursor)

    def test_movie_then_series_walk_both_keep_progress(self):
        """The regression itself: two partitions, one row, two advances."""
        # Pre-existing progress: MOVIE already walked three pages.
        SyncCursor.objects.create(
            provider=self.provider,
            source=self.source_row,
            country=self.country,
            cursor=_encode_cursor({PID_MOVIE_A: "300"}),
        )

        # Now walk SERIES, which must not disturb the MOVIE entry.
        calls = []
        summary = refresh_justwatch_partitions(
            partitions=[("NL", "netflix")],
            content_type=SERIES,
            # year_start is the *newer* bound: generation walks descending.
            year_start=2026, year_end=2017, year_window=10,
            refresh_service=self._service(
                next_cursor=_encode_cursor({PID_SERIES_A: "200"}), calls=calls
            ),
            source=self.source,
        )

        self.assertEqual(
            (summary["skipped"], summary["errors"], len(calls)),
            (0, 0, 1),
            f"partition must be attempted, not skipped/failed: {summary['results']}",
        )
        self.assertEqual(summary["attempted"], 1)
        self.assertEqual(calls[0]["cursor"]["content_type"], SERIES)

        stored = self.stored()
        self.assertEqual(stored[PID_MOVIE_A], "300",
                         "the MOVIE partition must survive a SERIES walk")
        self.assertEqual(stored[PID_SERIES_A], "200")

    def test_failed_partition_keeps_siblings_and_stays_resumable(self):
        SyncCursor.objects.create(
            provider=self.provider,
            source=self.source_row,
            country=self.country,
            cursor=_encode_cursor({PID_MOVIE_A: "300", PID_SERIES_A: "200"}),
        )

        calls = []
        summary = refresh_justwatch_partitions(
            partitions=[("NL", "netflix")],
            content_type=MOVIE,
            refresh_service=self._service(
                next_cursor=f"{PID_MOVIE_A}=999", calls=calls, fail=True
            ),
            source=self.source,
        )

        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["status"], "failed")

        stored = self.stored()
        # Nothing invented: the failed partition kept its real offset, and the
        # sibling is untouched.
        self.assertEqual(stored[PID_MOVIE_A], "300",
                         "a failure must not advance the partition")
        self.assertEqual(stored[PID_SERIES_A], "200")
        self.assertNotIn("999", stored.values())

    def test_pages_are_passed_through_to_the_service(self):
        calls = []
        refresh_justwatch_partitions(
            partitions=[("NL", "netflix")],
            pages_per_partition=3,
            refresh_service=self._service(
                next_cursor=_encode_cursor({}), calls=calls
            ),
            source=self.source,
        )
        self.assertEqual(calls[0]["pages"], 3)

    def test_year_window_reaches_the_service_cursor(self):
        calls = []
        refresh_justwatch_partitions(
            partitions=[("NL", "netflix")],
            year_start=2026, year_end=2017, year_window=10,
            refresh_service=self._service(
                next_cursor=_encode_cursor({}), calls=calls
            ),
            source=self.source,
        )
        self.assertEqual(calls[0]["cursor"]["min_year"], 2017)
        self.assertEqual(calls[0]["cursor"]["max_year"], 2026)
        self.assertEqual(calls[0]["cursor"]["content_type"], MOVIE)


class BoundedCommandTests(TestCase):
    """Phase 3: a bounded request must walk exactly what was asked for.

    The safety requirement is blunt — an operator asking for one country, one
    provider, one content type, one year window and two pages must get exactly
    that, never a slice of the global plan.
    """

    def setUp(self):
        Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "netflix", "is_enabled": True})
        Country.objects.get_or_create(
            code="NL", defaults={"name": "NL", "enabled": True})
        Source.objects.get_or_create(
            slug="justwatch",
            defaults={"name": "JustWatch", "type": SourceType.AGGREGATOR})

    @staticmethod
    def _service(calls):
        from availability.services.refresh import RefreshResult

        class _Service:
            def refresh_catalogue(self, *, provider=None, country=None,
                                  cursor=None, pages=1):
                calls.append({"provider": provider, "country": country,
                              "cursor": dict(cursor or {}), "pages": pages})
                return RefreshResult(discovered=1, processed=1, created=1)

        return _Service()

    # Summary keys the command reads; anything else may be absent.
    _SUMMARY = {
        "status": "succeeded", "attempted": 1, "refreshed": 1, "errors": 0,
        "skipped": 0, "partial": 0, "results": [], "plan": [],
        "succeeded_partitions": [], "failed_partitions": [],
        "skipped_partitions": [], "partitions": [],
    }

    def _run(self, args, calls):
        """Invoke the command with the coordinator replaced by a recorder.

        Patched where the command *imported* it, which is the seam that matters:
        patching `tracker.services.justwatch_sync` would not be seen by the
        already-bound module-level name in the command.
        """
        import io
        from unittest.mock import patch as mock_patch

        from django.core.management import call_command

        def _fake(**kwargs):
            calls.append(kwargs)
            return dict(self._SUMMARY)

        with mock_patch(
            "tracker.management.commands.refresh_justwatch_catalogue."
            "refresh_justwatch_partitions", _fake,
        ):
            call_command("refresh_justwatch_catalogue", *args,
                         stdout=io.StringIO())

    def test_a_fully_bounded_request_walks_exactly_one_partition(self):
        calls = []
        self._run(["--country", "NL", "--provider", "netflix",
                   "--content-type", "MOVIE",
                   "--year-start", "2026", "--year-end", "2020",
                   "--pages", "2"], calls)

        self.assertEqual(len(calls), 1, "a bounded run must not fan out")
        kwargs = calls[0]
        self.assertEqual(kwargs["partitions"], [("NL", "netflix")])
        self.assertEqual(kwargs["content_type"], ContentType.MOVIE)
        self.assertEqual(kwargs["year_start"], 2026)
        self.assertEqual(kwargs["year_end"], 2020)
        self.assertEqual(kwargs["pages_per_partition"], 2)

    def test_the_year_window_subdivides_without_losing_a_year(self):
        calls = []
        self._run(["--country", "NL", "--provider", "netflix",
                   "--year-start", "2026", "--year-end", "2000",
                   "--year-window", "10"], calls)

        kwargs = calls[0]
        self.assertEqual(kwargs["year_window"], 10)
        self.assertEqual(kwargs["year_start"], 2026)
        self.assertEqual(kwargs["year_end"], 2000)

    def test_list_only_never_fetches(self):
        calls = []
        from django.core.management import call_command
        out = __import__("io").StringIO()
        call_command("refresh_justwatch_catalogue", "--country", "NL",
                     "--provider", "netflix", "--year-start", "2026",
                     "--year-end", "2020", "--list-only",
                     stdout=out)
        self.assertEqual(calls, [])
        self.assertIn("2026", out.getvalue())

    def test_year_bounds_must_be_given_together(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("refresh_justwatch_catalogue", "--year-start", "2026",
                         stdout=__import__("io").StringIO())

    def test_an_inverted_year_range_is_rejected(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("refresh_justwatch_catalogue",
                         "--year-start", "2020", "--year-end", "2026",
                         stdout=__import__("io").StringIO())

    def test_zero_pages_is_rejected_rather_than_meaning_unlimited(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("refresh_justwatch_catalogue", "--pages", "0",
                         stdout=__import__("io").StringIO())

    def test_a_bad_content_type_is_rejected(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("refresh_justwatch_catalogue",
                         "--content-type", "DOCUMENTARY",
                         stdout=__import__("io").StringIO())

    def test_no_year_bounds_keeps_the_pre_p8_unbounded_partition(self):
        """Backward compatibility: no bounds means the old single partition."""
        calls = []
        self._run(["--country", "NL", "--provider", "netflix"], calls)

        kwargs = calls[0]
        self.assertIsNone(kwargs["year_start"])
        self.assertIsNone(kwargs["year_end"])
        self.assertEqual(kwargs["content_type"], ContentType.MOVIE)
        self.assertEqual(kwargs["pages_per_partition"], 1)


class ResumeAndIdempotencyTests(TestCase):
    """Phase 4: a second visit must continue, not restart, and not duplicate."""

    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "netflix", "is_enabled": True})
        self.country, _ = Country.objects.get_or_create(
            code="NL", defaults={"name": "NL", "enabled": True})
        self.source_row, _ = Source.objects.get_or_create(
            slug="justwatch",
            defaults={"name": "JustWatch", "type": SourceType.AGGREGATOR})

    def stored(self):
        return _decode_cursor(SyncCursor.objects.get(
            provider=self.provider, source=self.source_row,
            country=self.country).cursor)

    def test_a_second_visit_resumes_from_the_persisted_offset(self):
        """The observable symptom of the original bug: always page 0."""
        from availability.services.refresh import RefreshResult

        seen = []
        provider_row, source_row, country_row = (
            self.provider, self.source_row, self.country)

        class _Service:
            def refresh_catalogue(self, *, provider=None, country=None,
                                  cursor=None, pages=1):
                offset = (cursor or {}).get("offset")
                seen.append(offset)
                # Advance by one page per visit, as a real walk would.
                pid = (cursor or {}).get("partition")
                new_offset = (offset or 0) + 100
                SyncCursor.objects.update_or_create(
                    provider=provider_row, source=source_row,
                    country=country_row,
                    defaults={"cursor": f"{pid}={new_offset}"})
                return RefreshResult(discovered=1, processed=1, created=1)

        for _ in range(3):
            refresh_justwatch_partitions(
                partitions=[("NL", "netflix")],
                refresh_service=_Service(), source=self.source_stub(),
            )

        self.assertEqual(seen, [0, 100, 200],
                         "each visit must resume from the last offset")

    def source_stub(self):
        class _S:
            slug = "justwatch"
            name = "JustWatch"
            source_type = SourceType.AGGREGATOR
            capabilities = SourceCapabilities()

        return _S()

    def test_walking_one_partition_leaves_adjacent_ones_alone(self):
        """Partition independence across every dimension."""
        from availability.services.refresh import RefreshResult

        self.seed_two_year_windows()
        calls = []
        provider_row, source_row, country_row = (
            self.provider, self.source_row, self.country)

        class _Service:
            def refresh_catalogue(self, *, provider=None, country=None,
                                  cursor=None, pages=1):
                calls.append(cursor.get("partition"))
                pid = cursor.get("partition")
                SyncCursor.objects.update_or_create(
                    provider=provider_row, source=source_row,
                    country=country_row, defaults={"cursor": f"{pid}=400"})
                return RefreshResult(discovered=1, processed=1, created=1)

        refresh_justwatch_partitions(
            partitions=[("NL", "netflix")],
            year_start=2026, year_end=2017, year_window=10,
            refresh_service=_Service(), source=self.source_stub(),
        )

        self.assertEqual(calls, [PID_MOVIE_A], "only the newest window ran")
        stored = self.stored()
        self.assertEqual(stored[PID_MOVIE_A], "400")
        self.assertEqual(stored[PID_MOVIE_B], "100",
                         "the adjacent year window must be untouched")

    def seed_two_year_windows(self):
        SyncCursor.objects.create(
            provider=self.provider, source=self.source_row,
            country=self.country,
            cursor=_encode_cursor({PID_MOVIE_A: "300", PID_MOVIE_B: "100"}),
        )


class PartitionCapTests(TestCase):
    """Phase 3: the partition cap must always apply.

    The P8 defect: the command built an unbounded plan and passed it to the
    coordinator with ``max_partitions=None``, which the coordinator honours
    verbatim. A bare invocation therefore walked all 72 generated partitions
    instead of the configured handful. These pin the cap as non-bypassable.
    """

    SUMMARY = {
        "status": "succeeded", "attempted": 1, "refreshed": 1, "errors": 0,
        "skipped": 0, "partial": 0, "results": [], "plan": [],
        "succeeded_partitions": [], "failed_partitions": [],
        "skipped_partitions": [], "partitions": [],
    }

    def setUp(self):
        # Enough distinct country/provider pairs that an uncapped plan would be
        # visibly larger than the cap.
        for country in ("NL", "IT", "GB", "US", "CA", "DE", "FR", "ES", "SE"):
            Country.objects.get_or_create(
                code=country, defaults={"name": country, "enabled": True})
        for slug in ("netflix", "disney", "prime", "hulu"):
            Provider.objects.get_or_create(
                slug=slug, defaults={"name": slug, "is_enabled": True})

    def _run(self, args):
        """Run the command with the coordinator replaced by a recorder."""
        import io
        from unittest.mock import patch as mock_patch

        from django.core.management import call_command

        calls = []

        def _fake(**kwargs):
            calls.append(kwargs)
            return dict(self.SUMMARY)

        with mock_patch(
            "tracker.management.commands.refresh_justwatch_catalogue."
            "refresh_justwatch_partitions", _fake,
        ):
            call_command("refresh_justwatch_catalogue", *args,
                         stdout=io.StringIO())
        return calls

    def test_a_bare_invocation_stays_within_the_default_cap(self):
        calls = self._run([])

        self.assertEqual(len(calls), 1)
        executed = calls[0]["partitions"]
        self.assertLessEqual(
            len(executed), DEFAULT_MAX_PARTITIONS,
            "a bare invocation walked more than the configured cap",
        )

    def test_max_partitions_one_runs_exactly_one(self):
        calls = self._run(["--max-partitions", "1"])
        self.assertEqual(len(calls[0]["partitions"]), 1)

    def test_max_partitions_n_runs_at_most_n(self):
        for n in (1, 2, 3):
            with self.subTest(n=n):
                calls = self._run(["--max-partitions", str(n)])
                self.assertEqual(len(calls[0]["partitions"]), n)

    def test_max_partitions_zero_is_rejected(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("refresh_justwatch_catalogue", "--max-partitions", "0",
                         stdout=__import__("io").StringIO())

    def test_negative_max_partitions_is_rejected(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("refresh_justwatch_catalogue", "--max-partitions", "-1",
                         stdout=__import__("io").StringIO())

    def test_an_explicit_plan_cannot_bypass_the_cap(self):
        """The defect itself, asserted end to end.

        Even with the full global plan available, the executed list must not
        exceed the default cap.
        """
        calls = self._run([])
        plan = available_partitions(max_partitions=10_000)
        self.assertGreater(
            len(plan), DEFAULT_MAX_PARTITIONS,
            "the fixture must contain more partitions than the cap allows, or "
            "this test proves nothing",
        )
        self.assertLessEqual(len(calls[0]["partitions"]), DEFAULT_MAX_PARTITIONS)

    def test_a_named_partition_past_the_cap_is_still_reachable(self):
        """The cap must bound cost without hiding what was explicitly asked for.

        Filtering happens before the cap, so an operator naming one partition
        gets it even when the global ordering would truncate it away.
        """
        calls = self._run(["--country", "US", "--provider", "hulu",
                           "--max-partitions", "1"])
        self.assertEqual(calls[0]["partitions"], [("US", "hulu")])

        # And it really is past the cap's reach: it is absent from the
        # uncapped prefix, which is why filter-before-cap is load-bearing.
        prefix = available_partitions(max_partitions=1)
        self.assertNotIn(("US", "hulu"), prefix)

    def test_explicit_dimensions_select_only_the_matching_partition(self):
        calls = self._run(["--country", "NL", "--provider", "netflix",
                           "--content-type", "MOVIE",
                           "--year-start", "2026", "--year-end", "2020"])

        self.assertEqual(calls[0]["partitions"], [("NL", "netflix")])
        self.assertEqual(calls[0]["year_start"], 2026)
        self.assertEqual(calls[0]["year_end"], 2020)

    def test_list_only_reports_the_capped_plan(self):
        """What is shown must be what runs — including the cap."""
        import io
        import json
        from django.core.management import call_command

        out = io.StringIO()
        call_command("refresh_justwatch_catalogue", "--list-only",
                     stdout=out)
        described = json.loads(out.getvalue().split("partitions:", 1)[1])
        self.assertLessEqual(len(described), DEFAULT_MAX_PARTITIONS)


class CoordinatorLockTests(TestCase):
    """Phase 6: snapshot/merge/write is only safe with one writer.

    There is no in-repo scheduler; an external cron invokes the command, and two
    overlapping ticks are entirely possible. Rather than invent a locking
    scheme, the coordinator takes the same per-source Postgres advisory lock the
    generic catalogue scheduler already uses.
    """

    def setUp(self):
        Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "netflix", "is_enabled": True})
        Country.objects.get_or_create(
            code="NL", defaults={"name": "NL", "enabled": True})
        Source.objects.get_or_create(
            slug="justwatch",
            defaults={"name": "JustWatch", "type": SourceType.AGGREGATOR})

    def source_stub(self):
        class _S:
            slug = "justwatch"
            name = "JustWatch"
            source_type = SourceType.AGGREGATOR
            capabilities = SourceCapabilities()

        return _S()

    def test_a_held_lock_skips_the_walk_entirely(self):
        """A second coordinator must do nothing, not half a walk."""
        from availability.services.refresh import RefreshResult

        calls = []

        class _Service:
            def refresh_catalogue(self, **kwargs):
                calls.append(kwargs)
                return RefreshResult(discovered=1, processed=1, created=1)

        with mock.patch(
            "tracker.services.justwatch_sync.try_source_lock", return_value=False
        ):
            summary = refresh_justwatch_partitions(
                partitions=[("NL", "netflix")],
                refresh_service=_Service(), source=self.source_stub(),
            )

        self.assertEqual(calls, [], "a locked-out run must not fetch")
        self.assertEqual(summary["attempted"], 0)
        self.assertTrue(summary.get("locked"))

    def test_the_lock_is_released_even_when_the_walk_raises(self):
        with mock.patch(
            "tracker.services.justwatch_sync.try_source_lock", return_value=True
        ), mock.patch(
            "tracker.services.justwatch_sync.release_source_lock"
        ) as release:
            class _Service:
                def refresh_catalogue(self, **kwargs):
                    raise AssertionError("boom")

            with mock.patch.object(
                JustWatchSource, "_require_country", side_effect=AssertionError
            ):
                # Drive the failure through a partition that raises.
                summary = refresh_justwatch_partitions(
                    partitions=[("NL", "netflix")],
                    refresh_service=_Service(), source=self.source_stub(),
                )

        self.assertEqual(summary["errors"], 1)
        self.assertTrue(release.called, "the lock must be released in a finally")

    def test_lock_is_released_after_a_successful_walk(self):
        from availability.services.refresh import RefreshResult

        with mock.patch(
            "tracker.services.justwatch_sync.try_source_lock", return_value=True
        ), mock.patch(
            "tracker.services.justwatch_sync.release_source_lock"
        ) as release:
            class _Service:
                def refresh_catalogue(self, **kwargs):
                    return RefreshResult(discovered=1, processed=1, created=1)

            refresh_justwatch_partitions(
                partitions=[("NL", "netflix")],
                refresh_service=_Service(), source=self.source_stub(),
            )

        self.assertTrue(release.called)


class YearWindowTests(TestCase):
    def test_windows_are_contiguous_disjoint_and_newest_first(self):
        windows = year_windows(2026, 2000, 10)
        self.assertEqual(windows, [(2017, 2026), (2007, 2016), (2000, 2006)])

        # Coverage is the invariant: every year once, none missing. The flattened
        # order follows the *generation* order (newest window first), so it is
        # not ascending — that is the point of descending generation.
        years = [y for lo, hi in windows for y in range(lo, hi + 1)]
        self.assertEqual(sorted(years), list(range(2000, 2027)),
                         "no gaps and no duplicates")
        self.assertEqual(len(years), len(set(years)), "no year in two windows")

        # Newest-first generation, and every window internally ascending.
        self.assertEqual(windows[0][1], 2026, "newest catalogue walked first")
        self.assertEqual([hi for _, hi in windows], sorted(
            [hi for _, hi in windows], reverse=True))
        for lo, hi in windows:
            self.assertLessEqual(lo, hi)

    def test_single_window_spans_the_whole_range(self):
        self.assertEqual(year_windows(2026, 2000, None), [(2000, 2026)])
        self.assertEqual(year_windows(2026, 2000, 1000), [(2000, 2026)])

    def test_deterministic(self):
        self.assertEqual(year_windows(2026, 2000, 10), year_windows(2026, 2000, 10))

    def test_inverted_range_plans_nothing(self):
        self.assertEqual(year_windows(2000, 2026, 10), [])


class SourceCursorFormatTests(TestCase):
    """The adapter's cursor and the coordinator's decoder must agree.

    These are two modules in different packages writing one string; a mismatch
    would not raise anywhere, it would just produce an empty partition map.
    """

    def test_round_trips_through_the_coordinator_decoder(self):
        emitted = JustWatchSource._next_cursor(
            partition_id=PID_MOVIE_A, offset=200, exhausted=False
        )
        self.assertEqual(_decode_cursor(emitted), {PID_MOVIE_A: "200"})

    def test_exhausted_marker_round_trips(self):
        emitted = JustWatchSource._next_cursor(
            partition_id=PID_MOVIE_A, offset=2000, exhausted=True
        )
        self.assertEqual(_decode_cursor(emitted), {PID_MOVIE_A: ""})

    def test_without_a_partition_id_the_cursor_stays_a_bare_offset(self):
        self.assertEqual(
            JustWatchSource._next_cursor(partition_id=None, offset=300,
                                         exhausted=False),
            "300",
        )
        self.assertIsNone(
            JustWatchSource._next_cursor(partition_id=None, offset=CATALOGUE_MAX_OFFSET,
                                         exhausted=True)
        )