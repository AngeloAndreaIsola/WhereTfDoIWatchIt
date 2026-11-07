"""Per-source refresh endpoint + the square-card fields of ``GET /api/providers/``.

The subscriptions page is one request deep: every card (SVOD, BVOD, imported)
must render its last run, its available-item count, its refresh target and its
refreshing state straight from the list response, so no card may trigger a
follow-up query or a per-card HTTP call. This module pins that contract:

- the list carries ``refresh_kind`` / ``source_slug`` / ``last_run`` /
  ``last_run_error`` / ``available_items`` / ``refreshing`` for the three
  provider shapes (catalogue source, scraper, plain provider) and does so in
  a bounded number of queries regardless of provider count;
- ``POST /api/providers/<slug>/refresh/`` refreshes *exactly one* source:
  catalogue sources only enqueue the existing worker marker (queue-only, no
  network), scraper providers only run their own scraper through the shared
  runner, providers with no collector get 409 instead of a button that could
  never work;
- refreshing never mutates the subscription: activation (the toggle) and
  refresh are independent, and a press must not create, keep or delete a
  ``Subscription`` row.

The queue side (``process_catalogue_refresh`` draining the marker) and the
shared scraper execution path (``/api/scrapers/run/``) are covered by their
own modules; nothing here reaches the network.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from availability.domain.enums import SyncStatus
from availability.models import ProviderContent, Source, SyncRun
from availability.sources import DEFAULT_SOURCE_SLUG
from tracker.models import (
    CatalogueRefreshRequest,
    CatalogueRefreshStatus,
    Movie,
    Provider,
    RefreshRun,
    ScrapedContentMapping,
    Subscription,
)
from tracker.scrapers import ScrapedItem
from tracker.tests.availability_factories import local_offer

NOW = timezone.now()


def _scraped_item(title="Refreshed Title", year=2021):
    return ScrapedItem(
        title=title,
        year=year,
        content_type="movie",
        provider_slug="kisskh",
        source_url="https://kisskh.example/1",
        availability=True,
        audio_languages=["EN"],
        subtitle_languages=[],
        quality="",
        raw_payload={"id": "1"},
    )


def _fake_scraper_class(items=(), error=None):
    """Stand-in for a scraper *class* (``run_provider_scraper`` instantiates it)."""

    class FakeScraper:
        def run(self, query=None):
            if error is not None:
                raise error
            return list(items)

    return FakeScraper


class ProviderListCardFieldTests(TestCase):
    """``GET /api/providers/`` is the single source of truth for the cards."""

    def setUp(self):
        self.client = APIClient()

    @staticmethod
    def _card(data, slug):
        return next(p for p in data if p["slug"] == slug)

    def test_catalogue_source_reports_its_source_and_content_count(self):
        ProviderContent.objects.create(
            provider=Provider.objects.get(slug="bbc_iplayer"),
            provider_content_id="bb-1",
            content_type="movie",
            title="Doctor Who",
            identity="bbc_iplayer:bb-1",
            currently_available=True,
        )
        # A second, deactivated row must not inflate the count.
        ProviderContent.objects.create(
            provider=Provider.objects.get(slug="bbc_iplayer"),
            provider_content_id="bb-2",
            content_type="movie",
            title="Gone",
            identity="bbc_iplayer:bb-2",
            currently_available=False,
        )

        data = self.client.get("/api/providers/").json()
        card = self._card(data, "bbc_iplayer")

        self.assertEqual(card["refresh_kind"], "catalogue")
        self.assertEqual(card["source_slug"], "bbc_iplayer")
        self.assertEqual(card["available_items"], 1)
        self.assertFalse(card["refreshing"])

    def test_catalogue_card_reads_the_run_audit_rows(self):
        now = timezone.now()
        # Older success, newer failure: the card must show both the current
        # failure and the last genuinely successful run.
        RefreshRun.objects.create(
            service="bbc_iplayer",
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=3) + timedelta(minutes=5),
            error="",
        )
        RefreshRun.objects.create(
            service="bbc_iplayer",
            started_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=9),
            error="boom",
        )

        data = self.client.get("/api/providers/").json()
        card = self._card(data, "bbc_iplayer")

        self.assertEqual(card["last_run_error"], "boom")
        self.assertEqual(
            card["last_run"],
            (now - timedelta(hours=3) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        )

    def test_running_run_marks_the_source_refreshing(self):
        RefreshRun.objects.create(service="cbc_gem", started_at=timezone.now())

        data = self.client.get("/api/providers/").json()
        self.assertTrue(self._card(data, "cbc_gem")["refreshing"])

    def test_pending_marker_marks_the_source_refreshing(self):
        CatalogueRefreshRequest.objects.create(sources=["tubi", "pbs"])

        data = self.client.get("/api/providers/").json()
        self.assertTrue(self._card(data, "tubi")["refreshing"])
        self.assertTrue(self._card(data, "pbs")["refreshing"])
        self.assertFalse(self._card(data, "cbc_gem")["refreshing"])

    def test_scraper_card_reports_scraper_target_and_its_own_items(self):
        provider = Provider.objects.get(slug="kisskh")
        provider.last_scraped_at = NOW - timedelta(days=1)
        provider.save()
        ScrapedContentMapping.objects.create(
            provider=provider,
            scraped_title="Cached",
            scraped_year=2020,
            is_available=True,
            raw_payload={},
        )
        ScrapedContentMapping.objects.create(
            provider=provider,
            scraped_title="Unavailable",
            scraped_year=2019,
            is_available=False,
            raw_payload={},
        )

        data = self.client.get("/api/providers/").json()
        card = self._card(data, "kisskh")

        self.assertEqual(card["refresh_kind"], "scraper")
        self.assertIsNone(card["source_slug"])
        self.assertEqual(card["available_items"], 1)
        self.assertFalse(card["refreshing"])
        self.assertEqual(card["last_run"], provider.last_scraped_at.isoformat().replace("+00:00", "Z"))

    def test_plain_provider_reports_never_run_and_active_offer_count(self):
        movie = Movie.objects.create(tmdb_id=11, title="Plained", year=2019)
        local_offer(movie, service="netflix")

        data = self.client.get("/api/providers/").json()
        card = self._card(data, "netflix")

        self.assertIsNone(card["refresh_kind"])
        self.assertIsNone(card["last_run"])
        self.assertEqual(card["available_items"], 1)
        self.assertFalse(card["refreshing"])

    def test_plain_provider_last_run_is_the_default_scope_sync(self):
        provider = Provider.objects.get(slug="disney")
        source, _ = Source.objects.get_or_create(
            slug=DEFAULT_SOURCE_SLUG, defaults={"name": "Streaming Availability API"}
        )
        completed = timezone.now() - timedelta(hours=2)
        SyncRun.objects.create(
            provider=provider,
            source=source,
            status=SyncStatus.SUCCEEDED,
            completed_at=completed,
        )
        # A failed later run must not move the last successful run backwards.
        SyncRun.objects.create(
            provider=provider,
            source=source,
            status=SyncStatus.FAILED,
            completed_at=completed + timedelta(hours=1),
        )

        data = self.client.get("/api/providers/").json()
        self.assertEqual(
            self._card(data, "disney")["last_run"],
            completed.isoformat().replace("+00:00", "Z"),
        )

    def test_list_never_scales_queries_with_provider_count(self):
        """Regression guard for the N+1 the card fields would otherwise cost."""
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        with CaptureQueriesContext(connection) as ctx:
            data = self.client.get("/api/providers/").json()
        self.assertGreaterEqual(len(data), 11)
        # Fixed aggregate pass (status, counts, run rows, subscriptions, plus
        # the serializer's prefetch) — far below one query per provider.
        self.assertLessEqual(len(ctx.captured_queries), 15)


class ProviderRefreshEndpointTests(TestCase):
    """One press, one source, no subscription side effects."""

    def setUp(self):
        self.client = APIClient()

    def test_catalogue_source_enqueues_only_its_own_source(self):
        resp = self.client.post("/api/providers/bbc_iplayer/refresh/")

        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        body = resp.json()
        self.assertTrue(body["queued"])
        self.assertEqual(body["kind"], "catalogue")
        self.assertEqual(body["source"], "bbc_iplayer")

        marker = CatalogueRefreshRequest.objects.get()
        self.assertEqual(marker.status, CatalogueRefreshStatus.PENDING)
        self.assertEqual(marker.sources, ["bbc_iplayer"])

    def test_catalogue_refresh_is_idempotent(self):
        self.client.post("/api/providers/bbc_iplayer/refresh/")
        self.client.post("/api/providers/bbc_iplayer/refresh/")

        self.assertEqual(CatalogueRefreshRequest.objects.filter(status=CatalogueRefreshStatus.PENDING).count(), 1)
        self.assertEqual(
            CatalogueRefreshRequest.objects.get().sources,
            ["bbc_iplayer"],
        )

    def test_catalogue_refresh_never_touches_the_subscription(self):
        provider = Provider.objects.get(slug="cbc_gem")
        self.assertFalse(Subscription.objects.filter(user_id=1, provider=provider).exists())

        self.client.post("/api/providers/cbc_gem/refresh/")
        self.assertFalse(Subscription.objects.filter(user_id=1, provider=provider).exists())

        Subscription.objects.create(user_id=1, provider=provider, name=provider.name)
        self.client.post("/api/providers/cbc_gem/refresh/")
        self.assertTrue(Subscription.objects.filter(user_id=1, provider=provider).exists())

    def test_scraper_refresh_runs_that_scraper_only(self):
        provider = Provider.objects.get(slug="kisskh")
        marker = CatalogueRefreshRequest.objects.filter(status=CatalogueRefreshStatus.PENDING)

        with patch(
            "tracker.services.scraper_selection.resolve_scraper_class",
            return_value=_fake_scraper_class([_scraped_item()]),
        ) as mock_resolve:
            resp = self.client.post("/api/providers/kisskh/refresh/")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        body = resp.json()
        self.assertEqual(body["kind"], "scraper")
        self.assertFalse(body["queued"])
        self.assertEqual(body["count"], 1)
        mock_resolve.assert_called_once_with("kisskh")

        provider.refresh_from_db()
        self.assertIsNotNone(provider.last_scraped_at)
        self.assertTrue(
            ScrapedContentMapping.objects.filter(provider=provider, scraped_title="Refreshed Title").exists()
        )
        # Enqueue-only path untouched: no worker marker was created.
        self.assertFalse(marker.exists())

    def test_scraper_refresh_does_not_change_subscription_state(self):
        provider = Provider.objects.get(slug="animeworld")

        with patch(
            "tracker.services.scraper_selection.resolve_scraper_class",
            return_value=_fake_scraper_class(),
        ):
            self.client.post("/api/providers/animeworld/refresh/")
        self.assertFalse(Subscription.objects.filter(user_id=1, provider=provider).exists())

        Subscription.objects.create(user_id=1, provider=provider, name=provider.name)
        with patch(
            "tracker.services.scraper_selection.resolve_scraper_class",
            return_value=_fake_scraper_class(),
        ):
            self.client.post("/api/providers/animeworld/refresh/")
        self.assertTrue(Subscription.objects.filter(user_id=1, provider=provider).exists())

    def test_scraper_failure_returns_the_error_and_nothing_pretends_success(self):
        provider = Provider.objects.get(slug="kisskh")

        with patch(
            "tracker.services.scraper_selection.resolve_scraper_class",
            return_value=_fake_scraper_class(error=RuntimeError("scrape down")),
        ):
            resp = self.client.post("/api/providers/kisskh/refresh/")

        self.assertEqual(resp.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn("scrape down", resp.json()["error"])
        provider.refresh_from_db()
        self.assertIsNone(provider.last_scraped_at)

    def test_provider_without_a_collector_is_rejected(self):
        resp = self.client.post("/api/providers/netflix/refresh/")

        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("No collector", resp.json()["error"])
        self.assertEqual(CatalogueRefreshRequest.objects.count(), 0)

    def test_unknown_provider_is_404(self):
        resp = self.client.post("/api/providers/does-not-exist/refresh/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_refresh_uses_the_same_target_rules_as_the_list(self):
        """The endpoint and the card agree, or the button presses the wrong thing."""
        catalogue_card = self.client.get("/api/providers/").json()
        target = next(
            (p["refresh_kind"], p["source_slug"])
            for p in catalogue_card
            if p["slug"] == "prime"
        )
        self.assertEqual(target, ("catalogue", "prime_video"))

        resp = self.client.post("/api/providers/prime/refresh/")
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(resp.json()["source"], "prime_video")
        self.assertEqual(CatalogueRefreshRequest.objects.get().sources, ["prime_video"])
