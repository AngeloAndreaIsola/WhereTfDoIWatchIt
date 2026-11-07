"""JustWatch as a first-class *title-level* source (capability-model regression).

The bug these tests pin: JustWatch implements **both** a title-targeted fetch
(``fetch(content=...)`` resolves one film by name/year) and a whole-catalogue
walk (``fetch(content=None)``). The old title-level discriminator was
``not supports_catalogue_sync``, which conflated the two capabilities and so
excluded JustWatch from *every* per-title path. Its only way to answer a title
was the slow, incomplete catalogue partition walk (measured: 4 of 72
partitions), so any film the walk had not reached reported ``unknown`` on the
detail page — the "Availability not known yet." symptom.

The fix makes the two capabilities independent (``supports_title_fetch`` is the
title-level discriminator) and changes runtime behaviour only for JustWatch:
catalogue-only collectors keep ``supports_title_fetch=False`` and stay exactly
as they were.

Coverage, matching the agreed regression list:

1. JustWatch is included in title-level refresh despite catalogue sync.
2. Catalogue-only Prime Video remains excluded.
3. A successful JustWatch title fetch persists offers and makes them visible to
   the report resolver — exercised through the **real coordinator** (queue
   drain + persistence), not by calling ``_fetch_title`` in isolation.
4. A valid empty result is a completed answer, not a failure.
5. Errors and quota exhaustion do not become false unavailability.
6. Country-specific offers do not leak across countries.

No test here makes live HTTP requests to JustWatch: the source is replaced by
an in-process stub at the registry seam, so the coordinator, the persistence
layer and the report resolver all run for real against deterministic input.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase

from availability.models import Offer
from availability.sources import source_registry
from availability.tests.source_stubs import (
    StubSource,
    fetch_result,
    normalized_offer,
)
from tracker.models import (
    CatalogueRefreshRequest,
    Movie,
    RefreshRequest,
    RefreshRequestStatus,
    WatchlistItem,
)
from tracker.services import availability_report as ar
from tracker.services.availability_reads import get_local_offers
from tracker.services.bulk_refresh import title_level_source_slugs
from tracker.services.refresh_requests import (
    process_refresh_queue,
    request_availability_refresh,
    request_title_refresh,
)

JW = "justwatch"
PRIME = "prime_video"

#: The real registry entries, captured before any patching, so a test can
#: delegate every slug except the one it is faking.
_REAL_SOURCES = {slug: source_registry.get(slug) for slug in source_registry.slugs()}
_REAL_JW_CAPS = _REAL_SOURCES[JW].capabilities


class _RecordingStub(StubSource):
    """A JustWatch stub that records the ``country`` hint the drain forwards."""

    def __init__(self, **kwargs):
        kwargs.setdefault("slug", JW)
        kwargs.setdefault("capabilities", _REAL_JW_CAPS)
        super().__init__(**kwargs)
        self.countries: list = []

    def fetch(self, content=None, *, provider=None, country=None, cursor=None):
        self.countries.append(country)
        return super().fetch(
            content, provider=provider, country=country, cursor=cursor
        )


def _registry_with(stub):
    """A ``get`` that returns *stub* for JustWatch and the real source otherwise."""
    def get(slug):
        return stub if slug == JW else _REAL_SOURCES[slug]

    return get


def _movie(tmdb_id=1000, title="Natural Born Killers", year=1994) -> Movie:
    return Movie.objects.create(tmdb_id=tmdb_id, title=title, year=year)


class CapabilityModelTests(TestCase):
    """The discriminator itself: independent capabilities, not a conflation."""

    def test_justwatch_is_title_level_despite_supporting_catalogue_sync(self):
        caps = source_registry.get(JW).capabilities
        self.assertTrue(caps.supports_title_fetch)
        self.assertTrue(caps.supports_catalogue_sync)
        self.assertIn(JW, title_level_source_slugs())

    def test_catalogue_only_prime_video_stays_excluded(self):
        caps = source_registry.get(PRIME).capabilities
        self.assertFalse(caps.supports_title_fetch)
        self.assertTrue(caps.supports_catalogue_sync)
        self.assertNotIn(PRIME, title_level_source_slugs())

    def test_title_level_set_is_exactly_the_title_fetch_capable_sources(self):
        expected = {
            slug
            for slug in source_registry.slugs()
            if source_registry.get(slug).capabilities.supports_title_fetch
        }
        self.assertEqual(set(title_level_source_slugs()), expected)
        self.assertIn(JW, expected)


class SchedulingEligibilityTests(TestCase):
    """JustWatch is *scheduled* for a title once it is title-level.

    Verifying decision 2: the role is not the lever that admits a source to
    per-title work — the capability is. JustWatch stays a ``broad_aggregator``
    (it genuinely resells many providers' catalogues), and a non-metered role
    is never gated, so it runs freely. What used to exclude it was the
    capability discriminator, not its role.
    """

    def test_justwatch_role_is_not_metered(self):
        from tracker.services import source_policy

        self.assertIs(
            source_policy.role_of(JW),
            source_policy.AvailabilityRole.BROAD_AGGREGATOR,
        )
        self.assertFalse(source_policy.is_policy_metered(JW))

    def test_justwatch_is_allowed_to_run_for_an_unknown_title(self):
        from tracker.services import source_policy

        decision = source_policy.decide_sources_for_title(
            {}, applicable_slugs=[JW, PRIME, "streaming_availability"]
        )
        self.assertIn(JW, decision["allowed_sources"])
        self.assertNotIn(JW, decision["skipped_sources"])

    def test_the_watchlist_fan_out_now_includes_justwatch(self):
        """Refresh All queues a per-title JustWatch request."""
        from tracker.services.refresh_requests import request_watchlist_refresh

        movie = _movie(tmdb_id=1001)
        WatchlistItem.objects.create(user_id=1, movie=movie, source="letterboxd")

        result = request_watchlist_refresh(user_id=1)

        self.assertIn(JW, result["sources"])
        self.assertTrue(
            RefreshRequest.objects.filter(movie=movie, source=JW).exists()
        )

    def test_justwatch_is_refreshable_by_the_background_sweep(self):
        """The sweep's candidate set now contains JustWatch."""
        from tracker.services.bulk_refresh import refreshable_source_slugs

        self.assertIn(JW, refreshable_source_slugs())

    def test_the_stale_sweep_selects_justwatch_once_observed(self):
        """A stale JustWatch observation is picked up by the sweep, per title.

        The sweep re-checks scopes that have already observed a title; the
        *first* observation comes from the Refresh-All fan-out. Both are
        country-scoped, so a viewed title resolves without waiting for its
        catalogue partition to be walked.
        """
        from availability.models import Country, Offer, Source
        from availability.domain.enums import OfferType, SourceType
        from django.utils import timezone
        import datetime as dt

        from tracker.models import Provider
        from tracker.services.bulk_refresh import stale_refresh_scopes

        movie = _movie(tmdb_id=1002)
        WatchlistItem.objects.create(user_id=1, movie=movie, source="letterboxd")
        source, _ = Source.objects.get_or_create(
            slug=JW, defaults={"name": "JustWatch", "type": SourceType.AGGREGATOR}
        )
        provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix"}
        )
        country, _ = Country.objects.get_or_create(code="NL", defaults={"name": "NL"})
        row = Offer.objects.create(
            provider=provider,
            country=country,
            source=source,
            content_type=movie.content_type,
            tmdb_id=movie.tmdb_id,
            offer_type=OfferType.SUBSCRIPTION,
            provider_content_id="nbk-nl",
            currently_available=True,
        )
        Offer.objects.filter(pk=row.pk).update(
            last_seen=timezone.now() - dt.timedelta(hours=48)
        )

        self.assertIn(JW, stale_refresh_scopes(movie.tmdb_id))


class TitleFanOutTests(TestCase):
    """Opening one title fans out to every eligible title-level source.

    ``request_title_refresh`` is the single-title counterpart of the watchlist
    fan-out: it discovers sources from the capability model, so catalogue-only
    collectors are structurally excluded and no catalogue crawl can happen.
    """

    def test_fans_out_to_every_title_level_source(self):
        movie = _movie(tmdb_id=1100)

        result = request_title_refresh(movie, if_stale=False)

        queued = set(
            RefreshRequest.objects.filter(movie=movie).values_list(
                "source", flat=True
            )
        )
        self.assertEqual(queued, set(title_level_source_slugs()))
        self.assertIn(JW, queued)
        self.assertEqual(set(result["sources"]), set(title_level_source_slugs()))

    def test_catalogue_only_sources_never_get_a_title_request(self):
        movie = _movie(tmdb_id=1101)

        request_title_refresh(movie, if_stale=False)

        queued = set(
            RefreshRequest.objects.filter(movie=movie).values_list(
                "source", flat=True
            )
        )
        self.assertNotIn(PRIME, queued)
        self.assertTrue(queued.issubset(set(title_level_source_slugs())))

    def test_it_never_triggers_a_catalogue_crawl(self):
        """Only targeted lookups — no CatalogueRefreshRequest is created."""
        movie = _movie(tmdb_id=1102)

        request_title_refresh(movie, if_stale=False)

        self.assertEqual(CatalogueRefreshRequest.objects.count(), 0)

    def test_an_explicit_source_list_restricts_the_fan_out(self):
        movie = _movie(tmdb_id=1103)

        result = request_title_refresh(movie, if_stale=False, sources=[JW])

        self.assertEqual(result["sources"], [JW])
        self.assertEqual(
            set(
                RefreshRequest.objects.filter(movie=movie).values_list(
                    "source", flat=True
                )
            ),
            {JW},
        )

    def test_fresh_title_queues_nothing(self):
        from availability.domain.enums import SourceType
        from availability.models import Country, Offer, Source
        from tracker.models import Provider

        movie = _movie(tmdb_id=1104)
        provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix"}
        )
        country, _ = Country.objects.get_or_create(code="US", defaults={"name": "US"})
        for slug in title_level_source_slugs():
            source, _ = Source.objects.get_or_create(
                slug=slug, defaults={"name": slug, "type": SourceType.API}
            )
            Offer.objects.create(
                provider=provider,
                country=country,
                source=source,
                content_type=movie.content_type,
                tmdb_id=movie.tmdb_id,
                offer_type="subscription",
                provider_content_id=f"{slug}-fresh",
            )

        request_title_refresh(movie, if_stale=True)

        self.assertEqual(RefreshRequest.objects.count(), 0)


class CoordinatorEndToEndTests(TestCase):
    """The real coordinator: enqueue → drain → persist → report resolver."""

    def _drain_justwatch(self, stub, *, country="NL"):
        movie = _movie()
        WatchlistItem.objects.create(user_id=1, movie=movie, source="letterboxd")
        request_availability_refresh(
            movie, source=JW, if_stale=False
        )
        with patch(
            "tracker.services.refresh_requests.source_registry"
        ) as registry, patch(
            "tracker.services.refresh_requests.resolve_source_country",
            return_value=country,
        ):
            registry.get.side_effect = _registry_with(stub)
            summary = process_refresh_queue(budget=99, service=MagicMock())
        return movie, summary

    def test_a_successful_title_fetch_persists_and_resolves_the_report(self):
        """The core fix: a JustWatch title fetch makes a title ``available``."""
        offer = normalized_offer(
            provider_id="netflix",
            provider_content_id="nbk-nl",
            country="NL",
            tmdb_id=1000,
        )
        stub = _RecordingStub(response=fetch_result(offer))

        movie, summary = self._drain_justwatch(stub)

        # 1. Persisted against the movie, attributed to JustWatch.
        self.assertTrue(
            Offer.objects.filter(tmdb_id=movie.tmdb_id, source__slug=JW).exists()
        )
        self.assertEqual(summary["errors"], 0)

        # 2. The country hint travelled with the request (selected market).
        self.assertEqual(stub.countries, ["NL"])

        # 3. The report resolver — the read path the detail page uses — now
        #    says the title is available, resolved by JustWatch.
        entry = ar.title_source_report([movie.pk])[movie.pk]
        self.assertEqual(entry["state"], "available")
        self.assertIn(JW, entry["resolved_sources"])
        self.assertEqual(entry["reason"], "")

    def test_the_report_endpoint_returns_the_available_verdict(self):
        """End-to-end through HTTP, exactly what the detail page fetches."""
        offer = normalized_offer(
            provider_id="netflix",
            provider_content_id="nbk-nl",
            country="NL",
            tmdb_id=1000,
        )
        self._drain_justwatch(_RecordingStub(response=fetch_result(offer)))

        resp = self.client.get(f"/api/availability/report/{1000}/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["state"], "available")
        self.assertIn(JW, resp.data["resolved_sources"])

    def test_a_valid_empty_result_is_a_completed_answer_not_a_failure(self):
        """An empty fetch is success: the request is DONE, never FAILED."""
        stub = _RecordingStub(response=fetch_result())
        movie, _ = self._drain_justwatch(stub)

        request = RefreshRequest.objects.get(movie=movie, source=JW)
        self.assertEqual(request.status, RefreshRequestStatus.DONE)
        self.assertFalse(
            Offer.objects.filter(tmdb_id=movie.tmdb_id, source__slug=JW).exists()
        )
        # JustWatch has spoken ("no offer") — that is a definitive negative for
        # this source, never a transient failure.
        row = next(
            r
            for r in ar.title_source_report([movie.pk])[movie.pk]["sources"]
            if r["source"] == JW
        )
        self.assertEqual(row["reason"], "no_offer")
        self.assertEqual(row["status"], "completed")

    def test_an_error_does_not_become_false_unavailability(self):
        """A transient failure is absence of information, not a 'no'."""
        stub = _RecordingStub(error=RuntimeError("justwatch exploded"))
        movie, _ = self._drain_justwatch(stub)

        self.assertFalse(
            Offer.objects.filter(tmdb_id=movie.tmdb_id, source__slug=JW).exists()
        )
        entry = ar.title_source_report([movie.pk])[movie.pk]
        self.assertNotEqual(entry["state"], "unavailable")
        self.assertEqual(entry["state"], "unknown")

    def test_quota_exhaustion_does_not_become_false_unavailability(self):
        """A budget-blocked source has not said 'no' — it has not spoken."""
        stub = _RecordingStub(response=fetch_result())
        movie = _movie()
        WatchlistItem.objects.create(user_id=1, movie=movie, source="letterboxd")
        request_availability_refresh(movie, source=JW, if_stale=False)

        spent = MagicMock()
        spent.remaining.return_value = 0
        with patch(
            "tracker.services.refresh_requests.source_registry"
        ) as registry, patch(
            "tracker.services.refresh_requests.resolve_source_country",
            return_value="NL",
        ), patch(
            "tracker.services.refresh_requests.rate_limiter_for",
            return_value=spent,
        ):
            registry.get.side_effect = _registry_with(stub)
            process_refresh_queue(budget=99, service=MagicMock())

        self.assertFalse(
            Offer.objects.filter(tmdb_id=movie.tmdb_id, source__slug=JW).exists()
        )
        entry = ar.title_source_report([movie.pk])[movie.pk]
        self.assertNotEqual(entry["state"], "unavailable")

    def test_country_specific_offers_do_not_leak_across_countries(self):
        """A Dutch offer is readable in NL and nowhere else."""
        offer = normalized_offer(
            provider_id="netflix",
            provider_content_id="nbk-nl",
            country="NL",
            tmdb_id=1000,
        )
        movie, _ = self._drain_justwatch(_RecordingStub(response=fetch_result(offer)))

        nl = get_local_offers(movie, country="NL")
        us = get_local_offers(movie, country="US")

        self.assertEqual(len(nl), 1)
        self.assertEqual(nl[0].country, "NL")
        self.assertEqual(us, [])
