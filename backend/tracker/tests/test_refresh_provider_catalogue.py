"""Tests for the refresh_provider_catalogue command (Phases 5/6).

Cron entry point for catalogue-capable sources: one refresh_catalogue call per
attempted source, one legacy RefreshRun audit row each, usage errors failing
loudly *before* any run is recorded.

Phase 6 scheduling contract covered here:

- discovery is capability-driven (``supports_catalogue_sync``) and skips
  disabled database ``Source`` rows without deleting them;
- failures are isolated per source and surfaced through exit codes
  (0 success / 1 all failed / 2 partial);
- an overlapping run for the same source skips cleanly under the per-source
  advisory lock while other sources stay independent;
- a scheduled run initializes the source's declared country scope, so writes
  are never silently skipped for a missing ``Country`` row;
- repeating the same refresh never duplicates canonical records;
- the HTTP/read path never touches CBC (or any scheduled source).
"""
import io
import json
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.test import TestCase

from availability.domain.enums import ContentType, SyncStatus
from availability.domain.models import DiscoveredContent
from availability.models import (
    ContentMapping,
    Country,
    Offer,
    ProviderContent,
    Source,
    SyncRun,
)
from availability.services.refresh import RefreshResult
from availability.sources import (
    SourceFetchError,
    SourceRegistry,
    SourceFetchResult,
    UnknownSourceError,
    source_registry,
)
from availability.sources.base import SourceCapabilities
from availability.sources.cbc_gem import CBCGemClient, CBCGemSource
from availability.tests.cbc_fixtures import FakeCbcApi
from availability.tests.source_stubs import (
    STUB_CAPABILITIES,
    StubSource,
    fetch_result,
    normalized_offer,
)
from tracker.models import Movie, Provider, RefreshRun
from tracker.services.bulk_refresh import _catalogue_lock_key

REFRESH_CLS = "tracker.services.bulk_refresh.AvailabilityRefreshService"
REGISTRY = "tracker.services.bulk_refresh.source_registry"


def make_service(**result_kwargs):
    service = MagicMock()
    service.refresh_catalogue.return_value = RefreshResult(**result_kwargs)
    return service


def summary_from(output: str) -> dict:
    """Parse the JSON summary out of the command's stdout."""
    return json.loads(output[output.index("{"):])


def registry_of(*sources) -> SourceRegistry:
    """A registry containing exactly these source instances."""
    registry = SourceRegistry()
    for source in sources:
        registry.register(source.slug, lambda source=source: source)
    return registry


def catalogue_slugs() -> list[str]:
    """Every source the scheduler discovers, derived from the real registry.

    Discovery is capability-driven, so these tests must not hardcode a source
    list: adding a catalogue source (Phase 9 added the second one) changes
    how many sources a bare ``refresh_provider_catalogue`` run attempts.
    """
    return sorted(
        slug
        for slug in source_registry.slugs()
        if source_registry.get(slug).capabilities.supports_catalogue_sync
    )


def read_path_files() -> list[Path]:
    """Backend files on the HTTP/read boundary (views, serializers, services)."""
    tracker_dir = Path(__file__).resolve().parents[1]
    return [
        tracker_dir / name
        for name in (
            "views.py",
            "api_v2.py",
            "urls.py",
            "serializers.py",
            "services/availability_reads.py",
            "services/offer_pipeline.py",
            "services/cache.py",
            "services/filtering.py",
            "services/streaming_api.py",
        )
    ]


class RefreshProviderCatalogueCommandTests(TestCase):
    @staticmethod
    def _service(**result_kwargs):
        return make_service(**result_kwargs)

    @patch(REFRESH_CLS)
    def test_command_refreshes_requested_source_and_records_a_run(self, mock_cls):
        mock_cls.return_value = self._service(processed=43, created=43, skipped=23)

        out = io.StringIO()
        call_command("refresh_provider_catalogue", "--source", "cbc_gem", stdout=out)

        run = RefreshRun.objects.get()
        self.assertEqual(run.service, "cbc_gem")
        self.assertEqual(run.rows_refreshed, 43)
        self.assertEqual(run.rows_skipped, 23)
        self.assertEqual(run.error, "")
        self.assertFalse(run.rate_limited)
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.finished_at)
        self.assertGreaterEqual(run.finished_at, run.started_at)
        mock_cls.assert_called_once()
        self.assertEqual(mock_cls.call_args.kwargs["source"].slug, "cbc_gem")
        self.assertIn("cbc_gem", out.getvalue())

    @patch(REFRESH_CLS)
    def test_default_runs_every_catalogue_capable_source(self, mock_cls):
        mock_cls.return_value = self._service()

        call_command("refresh_provider_catalogue", stdout=io.StringIO())

        slugs = [call.kwargs["source"].slug for call in mock_cls.call_args_list]
        # streaming_availability is title-targeted and must not run here.
        self.assertEqual(slugs, catalogue_slugs())
        self.assertEqual(RefreshRun.objects.count(), len(slugs))

    def test_unknown_source_fails_before_any_run_is_recorded(self):
        with self.assertRaises(UnknownSourceError):
            call_command(
                "refresh_provider_catalogue", "--source", "nope",
                stdout=io.StringIO(),
            )
        self.assertEqual(RefreshRun.objects.count(), 0)

    def test_non_catalogue_source_fails_before_any_run_is_recorded(self):
        with self.assertRaisesMessage(ValueError, "catalogue refresh"):
            call_command(
                "refresh_provider_catalogue", "--source", "streaming_availability",
                stdout=io.StringIO(),
            )
        self.assertEqual(RefreshRun.objects.count(), 0)

    @patch(REFRESH_CLS)
    def test_source_failure_is_recorded_and_reported_as_an_error(self, mock_cls):
        service = MagicMock()
        service.refresh_catalogue.side_effect = SourceFetchError("boom")
        mock_cls.return_value = service

        out = io.StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command(
                "refresh_provider_catalogue", "--source", "cbc_gem", stdout=out
            )

        # Complete failure of the only attempted source -> exit code 1.
        self.assertEqual(ctx.exception.returncode, 1)
        run = RefreshRun.objects.get()
        self.assertEqual(run.service, "cbc_gem")
        self.assertEqual(run.rows_refreshed, 0)
        self.assertIn("SourceFetchError", run.error)
        self.assertIn("boom", run.error)
        self.assertIsNotNone(run.finished_at)
        self.assertIn("errors", out.getvalue())


class SchedulerDiscoveryTests(TestCase):
    """Discovery: catalogue-capable + enabled, nothing else, nothing hardcoded."""

    @patch(REFRESH_CLS)
    def test_disabled_source_is_not_scheduled(self, mock_cls):
        mock_cls.return_value = make_service()
        slugs = catalogue_slugs()
        for slug in slugs:
            Source.objects.create(slug=slug, name=slug, enabled=False)

        out = io.StringIO()
        call_command("refresh_provider_catalogue", stdout=out)

        mock_cls.assert_not_called()
        self.assertEqual(RefreshRun.objects.count(), 0)
        summary = summary_from(out.getvalue())
        self.assertEqual(summary["disabled"], len(slugs))
        self.assertEqual(summary["attempted"], 0)
        self.assertEqual(summary["errors"], 0)
        self.assertTrue(
            all(item["status"] == "disabled" for item in summary["results"])
        )
        # A disabled source is skipped, never deleted.
        self.assertTrue(Source.objects.filter(slug="cbc_gem", enabled=False).exists())

    @patch(REFRESH_CLS)
    def test_disabled_source_can_still_be_targeted_manually(self, mock_cls):
        mock_cls.return_value = make_service()
        Source.objects.create(slug="cbc_gem", name="CBC Gem", enabled=False)

        out = io.StringIO()
        call_command("refresh_provider_catalogue", "--source", "cbc_gem", stdout=out)

        mock_cls.assert_called_once()
        self.assertEqual(RefreshRun.objects.count(), 1)
        self.assertEqual(summary_from(out.getvalue())["attempted"], 1)

    @patch(REFRESH_CLS)
    def test_source_without_a_database_row_counts_as_enabled(self, mock_cls):
        mock_cls.return_value = make_service()
        slugs = catalogue_slugs()
        # Never refreshed before -> no Source row -> default enabled=True.
        for slug in slugs:
            self.assertFalse(Source.objects.filter(slug=slug).exists(), slug)

        out = io.StringIO()
        call_command("refresh_provider_catalogue", stdout=out)

        self.assertEqual(len(mock_cls.call_args_list), len(slugs))
        self.assertEqual(summary_from(out.getvalue())["attempted"], len(slugs))

    @patch(REFRESH_CLS)
    def test_nothing_to_do_is_a_success(self, mock_cls):
        for slug in catalogue_slugs():
            Source.objects.create(slug=slug, name=slug, enabled=False)

        out = io.StringIO()
        call_command("refresh_provider_catalogue", stdout=out)

        mock_cls.assert_not_called()
        summary = summary_from(out.getvalue())
        self.assertEqual(summary["attempted"], 0)
        self.assertEqual(summary["errors"], 0)


class MultiSourceSchedulingTests(TestCase):
    """Two catalogue-capable sources: both run, one failing never blocks the other."""

    def setUp(self):
        self.first = StubSource(slug="aaa_source", capabilities=STUB_CAPABILITIES)
        self.second = StubSource(slug="zzz_source", capabilities=STUB_CAPABILITIES)
        self.targeted = StubSource(
            slug="targeted_source",
            capabilities=SourceCapabilities(supports_catalogue_sync=False),
        )
        self.registry = registry_of(self.first, self.second, self.targeted)

    def test_every_catalogue_capable_source_runs(self):
        out = io.StringIO()
        with patch(REGISTRY, self.registry):
            call_command("refresh_provider_catalogue", stdout=out)

        self.assertEqual(self.first.fetches, [None])
        self.assertEqual(self.second.fetches, [None])
        # A registered but non-catalogue source is skipped.
        self.assertEqual(self.targeted.fetches, [])

        runs = {run.service: run for run in RefreshRun.objects.all()}
        self.assertEqual(set(runs), {"aaa_source", "zzz_source"})
        self.assertTrue(all(run.error == "" for run in runs.values()))

        summary = summary_from(out.getvalue())
        self.assertEqual(summary["attempted"], 2)
        self.assertEqual(summary["refreshed"], 2)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(summary["sources"], ["aaa_source", "zzz_source"])
        self.assertEqual(
            [item["status"] for item in summary["results"]],
            ["succeeded", "succeeded"],
        )

    def test_one_failing_source_does_not_stop_the_others(self):
        # aaa_source sorts first, so its failure must not abort zzz_source.
        self.first.error = SourceFetchError("boom")

        out = io.StringIO()
        with patch(REGISTRY, self.registry):
            with self.assertRaises(CommandError) as ctx:
                call_command("refresh_provider_catalogue", stdout=out)

        self.assertEqual(ctx.exception.returncode, 2)
        self.assertEqual(self.second.fetches, [None])

        runs = {run.service: run for run in RefreshRun.objects.all()}
        self.assertIn("boom", runs["aaa_source"].error)
        self.assertEqual(runs["zzz_source"].error, "")
        self.assertIsNotNone(runs["zzz_source"].finished_at)

        summary = summary_from(out.getvalue())
        self.assertEqual(summary["attempted"], 2)
        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["sources"], ["zzz_source"])

    def test_complete_failure_exits_with_code_one(self):
        registry = registry_of(
            StubSource(slug="aaa_source", error=SourceFetchError("down"),
                       capabilities=STUB_CAPABILITIES),
            StubSource(slug="zzz_source", error=SourceFetchError("down"),
                       capabilities=STUB_CAPABILITIES),
        )

        with patch(REGISTRY, registry):
            with self.assertRaises(CommandError) as ctx:
                call_command("refresh_provider_catalogue", stdout=io.StringIO())

        self.assertEqual(ctx.exception.returncode, 1)
        self.assertEqual(RefreshRun.objects.filter(error="").count(), 0)


class CatalogueFailureBehaviorTests(TestCase):
    """Fetch/normalization/mapping failures are isolated, recorded, and loud."""

    @staticmethod
    def _stub(slug, **kwargs):
        kwargs.setdefault("capabilities", STUB_CAPABILITIES)
        return StubSource(slug=slug, **kwargs)

    def _run(self, *sources):
        out = io.StringIO()
        with patch(REGISTRY, registry_of(*sources)):
            try:
                call_command("refresh_provider_catalogue", stdout=out)
            except CommandError as exc:
                return exc.returncode, summary_from(out.getvalue())
            return 0, summary_from(out.getvalue())

    def test_fetch_failure_is_recorded_and_leaves_no_sync_run(self):
        code, summary = self._run(
            self._stub("cbc_like", error=SourceFetchError("upstream down"))
        )

        self.assertEqual(code, 1)
        run = RefreshRun.objects.get()
        self.assertIn("SourceFetchError", run.error)
        self.assertIn("upstream down", run.error)
        self.assertIsNotNone(run.finished_at)
        # A failed fetch writes nothing: no SyncRun may claim a refresh and no
        # provider-native row may pretend the catalogue was observed.
        self.assertEqual(SyncRun.objects.count(), 0)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(summary["errors"], 1)

    def test_normalization_failure_is_a_failed_run_not_an_empty_success(self):
        code, summary = self._run(
            self._stub("norm_source", error=ValueError("bad country token"))
        )

        self.assertEqual(code, 1)
        run = RefreshRun.objects.get()
        self.assertIn("ValueError: bad country token", run.error)
        self.assertEqual(run.rows_refreshed, 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        self.assertEqual(summary["refreshed"], 0)

    def test_mapping_failure_fails_the_source_without_writing_offers(self):
        Provider.objects.get_or_create(slug="map_source", defaults={"name": "Map"})
        Movie.objects.create(
            tmdb_id=9003, title="Beirut", year=2018, content_type="movie"
        )
        content = DiscoveredContent(
            provider_id="map_source",
            provider_content_id="beirut",
            content_type=ContentType.MOVIE,
            title="Beirut",
            release_date=date(2018, 1, 1),
        )
        offer = normalized_offer(
            provider_id="map_source", provider_content_id="beirut", tmdb_id=None
        )
        stub = self._stub("map_source", response=fetch_result(offer, contents=[content]))

        with patch(
            "availability.services.refresh.match_local_catalog",
            side_effect=RuntimeError("mapper exploded"),
        ):
            code, _summary = self._run(stub)

        self.assertEqual(code, 1)
        run = RefreshRun.objects.get()
        self.assertIn("mapper exploded", run.error)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(ContentMapping.objects.count(), 0)
        # Discovery shares the mapping transaction: a broken mapper also rolls
        # back the provider-native rows written before it ran.
        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)

    def test_successful_empty_catalogue_is_a_success(self):
        stub = self._stub("empty_source", response=SourceFetchResult())

        code, summary = self._run(stub)

        self.assertEqual(code, 0)
        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(summary["results"][0]["discovered"], 0)
        run = RefreshRun.objects.get()
        self.assertEqual(run.error, "")
        self.assertEqual(run.rows_refreshed, 0)
        self.assertIsNotNone(run.finished_at)

    def test_scheduled_run_logs_what_when_and_how_long(self):
        stub = self._stub("logged_source", response=SourceFetchResult())

        with self.assertLogs("tracker.services.bulk_refresh", level="INFO") as logs:
            code, _summary = self._run(stub)

        self.assertEqual(code, 0)
        text = "\n".join(logs.output)
        self.assertIn("catalogue_refresh_started source=logged_source", text)
        self.assertIn("started_at=", text)
        self.assertIn("catalogue_refresh_completed source=logged_source", text)
        self.assertIn("discovered=0", text)
        self.assertIn("offers=0", text)
        self.assertIn("duration_s=", text)
        self.assertIn("catalogue_refresh_summary", text)


class CatalogueLockingTests(TestCase):
    """Overlapping runs of the same source skip cleanly; sources stay independent."""

    @patch("tracker.services.bulk_refresh._release_source_lock")
    @patch("tracker.services.bulk_refresh._try_source_lock", return_value=False)
    @patch(REFRESH_CLS)
    def test_second_run_skips_while_the_first_holds_the_lock(
        self, mock_cls, try_lock, release_lock
    ):
        out = io.StringIO()
        call_command("refresh_provider_catalogue", stdout=out)

        mock_cls.assert_not_called()
        # Nothing ran, so nothing must be recorded and nothing must unlock.
        release_lock.assert_not_called()
        self.assertEqual(RefreshRun.objects.count(), 0)
        summary = summary_from(out.getvalue())
        self.assertEqual(summary["locked"], len(catalogue_slugs()))
        self.assertEqual(summary["attempted"], 0)
        self.assertEqual(summary["errors"], 0)
        self.assertTrue(
            all(item["status"] == "locked" for item in summary["results"])
        )

    def test_lock_keys_are_distinct_per_source(self):
        self.assertNotEqual(
            _catalogue_lock_key("cbc_gem"), _catalogue_lock_key("other_source")
        )
        self.assertEqual(
            _catalogue_lock_key("cbc_gem"), _catalogue_lock_key("cbc_gem")
        )

    @unittest.skipUnless(
        connection.vendor == "postgresql", "advisory locks require Postgres"
    )
    @patch(REFRESH_CLS)
    def test_peer_session_holding_the_lock_blocks_the_next_run(self, mock_cls):
        import psycopg

        mock_cls.return_value = make_service()
        key = _catalogue_lock_key("cbc_gem")
        db = connections["default"].settings_dict
        peer = psycopg.connect(
            dbname=db["NAME"], user=db["USER"], password=db["PASSWORD"],
            host=db["HOST"], port=db["PORT"],
        )
        try:
            with peer.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
                self.assertTrue(cursor.fetchone()[0])

            # A second scheduled run for the same source skips cleanly.
            out = io.StringIO()
            call_command(
                "refresh_provider_catalogue", "--source", "cbc_gem", stdout=out
            )
            self.assertEqual(summary_from(out.getvalue())["locked"], 1)
            self.assertEqual(RefreshRun.objects.count(), 0)
            mock_cls.assert_not_called()

            with peer.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [key])

            # Lock released -> the next run refreshes for real.
            out = io.StringIO()
            call_command(
                "refresh_provider_catalogue", "--source", "cbc_gem", stdout=out
            )
            self.assertEqual(summary_from(out.getvalue())["attempted"], 1)
            self.assertEqual(RefreshRun.objects.count(), 1)
            mock_cls.assert_called_once()
        finally:
            peer.close()


class CatalogueCountryTests(TestCase):
    """The declared country scope must exist before a scheduled run writes."""

    def test_required_country_exists_after_migrations(self):
        self.assertTrue(Country.objects.filter(code="CA", enabled=True).exists())

    def test_missing_country_is_initialized_so_writes_are_not_skipped(self):
        Provider.objects.get_or_create(slug="cbc_gem", defaults={"name": "CBC Gem"})
        Country.objects.all().delete()
        offer = normalized_offer(provider_id="cbc_gem", country="CA", tmdb_id=550)
        stub = StubSource(
            slug="ca_catalogue",
            response=fetch_result(offer),
            capabilities=SourceCapabilities(
                supported_countries=frozenset({"CA"}),
                supports_catalogue_sync=True,
            ),
        )

        out = io.StringIO()
        with patch(REGISTRY, registry_of(stub)):
            with self.assertLogs(
                "tracker.services.bulk_refresh", level="WARNING"
            ) as logs:
                call_command("refresh_provider_catalogue", stdout=out)

        self.assertTrue(Country.objects.filter(code="CA", enabled=True).exists())
        self.assertIn(
            "catalogue_refresh_country_initialized source=ca_catalogue country=CA",
            "\n".join(logs.output),
        )
        # The offer is persisted instead of being silently skipped.
        self.assertEqual(Offer.objects.count(), 1)
        summary = summary_from(out.getvalue())
        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["skipped"], 0)

    def test_unrestricted_country_scope_initializes_nothing(self):
        # No declared countries -> nothing to initialize, behavior unchanged.
        registry = registry_of(
            StubSource(
                slug="any_country_source",
                response=fetch_result(normalized_offer(tmdb_id=550, country="US")),
                capabilities=STUB_CAPABILITIES,
            )
        )
        Provider.objects.get_or_create(slug="netflix", defaults={"name": "Netflix"})

        with patch(REGISTRY, registry), self.assertLogs(
            "tracker.services.bulk_refresh", level="INFO"
        ) as sched_logs, self.assertLogs(
            "availability.services.refresh", level="WARNING"
        ) as refresh_logs:
            call_command("refresh_provider_catalogue", stdout=io.StringIO())

        # No declared countries -> the scheduler initializes nothing...
        self.assertNotIn(
            "catalogue_refresh_country_initialized", "\n".join(sched_logs.output)
        )
        # ...and the offer is skipped loudly by the refresh service...
        self.assertIn("unsupported_country", "\n".join(refresh_logs.output))
        # ...while the run itself is still a success (existing semantics).
        self.assertEqual(RefreshRun.objects.get().error, "")


class CbcSchedulingTests(TestCase):
    """CBC is reached only through the catalogue command, never the read path."""

    @patch("availability.sources.cbc_gem.CBCGemSource.fetch")
    def test_command_invokes_cbc_with_its_declared_scope(self, fetch):
        fetch.return_value = SourceFetchResult()

        out = io.StringIO()
        call_command("refresh_provider_catalogue", "--source", "cbc_gem", stdout=out)

        fetch.assert_called_once()
        args, kwargs = fetch.call_args
        self.assertEqual(args, (None,))  # catalogue mode, never title-targeted
        # Scope comes from the declared capabilities, not from the scheduler.
        self.assertEqual(kwargs.get("provider"), "cbc_gem")
        self.assertEqual(kwargs.get("country"), "CA")

        run = RefreshRun.objects.get()
        self.assertEqual(run.service, "cbc_gem")
        self.assertEqual(run.error, "")
        self.assertIsNotNone(run.finished_at)
        self.assertTrue(Source.objects.filter(slug="cbc_gem").exists())
        self.assertEqual(Offer.objects.count(), 0)
        summary = summary_from(out.getvalue())
        self.assertEqual(summary["refreshed"], 1)
        self.assertGreaterEqual(summary["results"][0]["duration_seconds"], 0)
        self.assertTrue(summary["results"][0]["started_at"])

    def test_cbc_fetch_failure_is_reported_as_a_failed_scheduled_run(self):
        registry = registry_of(
            StubSource(slug="cbc_gem", error=SourceFetchError("boom"),
                       capabilities=STUB_CAPABILITIES)
        )

        with patch(REGISTRY, registry):
            with self.assertRaises(CommandError) as ctx:
                call_command("refresh_provider_catalogue", stdout=io.StringIO())

        self.assertEqual(ctx.exception.returncode, 1)
        run = RefreshRun.objects.get(service="cbc_gem")
        self.assertIn("boom", run.error)
        self.assertFalse(Source.objects.filter(slug="cbc_gem").exists())
        self.assertEqual(SyncRun.objects.count(), 0)

    def test_read_path_files_never_reference_cbc(self):
        for path in read_path_files():
            self.assertTrue(path.exists(), path)
            self.assertNotIn(
                "cbc", path.read_text().lower(),
                f"{path.name} must not reference the CBC source",
            )


class CatalogueIdempotencyTests(TestCase):
    """Repeating the same scheduled refresh never duplicates canonical rows."""

    def setUp(self):
        self.api = FakeCbcApi()
        self.source = CBCGemSource(client=CBCGemClient(requester=self.api))
        self.registry = registry_of(self.source)
        Provider.objects.get_or_create(slug="cbc_gem", defaults={"name": "CBC Gem"})
        Country.objects.get_or_create(code="CA", defaults={"name": "Canada"})
        Movie.objects.create(
            tmdb_id=9001, title="North of North", year=2024, content_type="series"
        )
        Movie.objects.create(
            tmdb_id=9002, title="Murdoch Mysteries", year=2008, content_type="series"
        )
        Movie.objects.create(
            tmdb_id=9003, title="Beirut", year=2018, content_type="movie"
        )

    @staticmethod
    def _canonical_counts() -> dict:
        return {
            "offers": Offer.objects.count(),
            "contents": ProviderContent.objects.count(),
            "mappings": ContentMapping.objects.count(),
            "providers": Provider.objects.count(),
            "sources": Source.objects.count(),
            "countries": Country.objects.count(),
        }

    def test_two_consecutive_scheduled_refreshes_add_no_duplicates(self):
        with patch(REGISTRY, self.registry):
            first_out = io.StringIO()
            with self.assertLogs("tracker.services.bulk_refresh", level="INFO") as logs:
                call_command("refresh_provider_catalogue", stdout=first_out)
            first = summary_from(first_out.getvalue())
            snapshot = self._canonical_counts()
            first_seen = {
                offer.pk: offer.first_seen for offer in Offer.objects.all()
            }
            content_first_seen = {
                row.pk: row.first_seen for row in ProviderContent.objects.all()
            }
            self.assertIn(
                "catalogue_refresh_completed source=cbc_gem",
                "\n".join(logs.output),
            )

            second_out = io.StringIO()
            call_command("refresh_provider_catalogue", stdout=second_out)
            second = summary_from(second_out.getvalue())

        # Canonical identity is owned by the repository, not the scheduler.
        self.assertEqual(self._canonical_counts(), snapshot)
        # All 5 catalogue contents persisted, including the 23 unresolved
        # offers' content (unresolved != discarded).
        self.assertEqual(snapshot["contents"], 5)
        self.assertEqual(
            {row.pk: row.first_seen for row in ProviderContent.objects.all()},
            content_first_seen,
        )
        self.assertEqual(
            {offer.pk: offer.first_seen for offer in Offer.objects.all()},
            first_seen,
        )

        self.assertGreater(first["created"], 0)
        self.assertEqual(first["updated"], 0)
        self.assertEqual(first["discovered"], 66)
        self.assertEqual(first["unresolved"], 23)
        # Second pass re-touches every offer instead of creating new rows.
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], first["created"])
        self.assertEqual(second["discovered"], first["discovered"])

        # Both runs are recorded; neither SyncRun claims a failure.
        self.assertEqual(RefreshRun.objects.count(), 2)
        self.assertTrue(all(run.error == "" for run in RefreshRun.objects.all()))
        self.assertFalse(
            SyncRun.objects.filter(status=SyncStatus.FAILED).exists()
        )
        self.assertTrue(
            SyncRun.objects.filter(status=SyncStatus.SUCCEEDED).exists()
        )
