"""PBS through the source interface: registration, catalogue pagination,
episode-granular parsing, failure contracts (fixture API, zero network).

Fixture data (see ``pbs_fixtures``) covers, one behaviour each: a two-page
show walk whose second page repeats a slug, a batch response delivered in
*reverse* request order, three watchable shows (free, free, all-membership),
two shows with nothing watchable (clips/extras only; expired window), and
per-asset edge cases (CLIP, null ordinals, half-missing ordinals, expired).
"""
from __future__ import annotations

import copy
import datetime as dt

from django.test import SimpleTestCase, TestCase

from availability.domain.enums import ContentType, OfferType, SourceType, SyncMode
from availability.freshness import declared_freshness
from availability.models import (
    ContentMapping,
    Country,
    Offer,
    ProviderContent,
    Source,
    SyncRun,
)
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import SourceFetchError, source_registry
from availability.sources.base import ContentRequest
from availability.sources.pbs import SOURCE_VERSION, PbsClient, PbsSource
from tracker.models import Movie, Provider

from .pbs_fixtures import FakePbsApi, load_fixture

NATURE_TMDB = 45612
NOVA_TMDB = 45613
BATCH_KEY = "nature,nova,frontline,extra-show,expired-show"

TOTAL_CONTENTS = 3   # nature, nova, frontline (the two empty shows emit nothing)
TOTAL_OFFERS = 12
MAPPED_OFFERS = 9    # nature (6) + nova (3)
UNMAPPED_OFFERS = 3  # frontline (membership-only, unseeded in the tracker)


def make_source(api) -> PbsSource:
    return PbsSource(client=PbsClient(http_post=api.post))


class RegistrationTests(SimpleTestCase):
    def test_pbs_source_is_registered(self):
        source = source_registry.get("pbs")
        self.assertIsInstance(source, PbsSource)
        self.assertEqual(source.slug, "pbs")
        self.assertEqual(source.source_type, SourceType.API)

    def test_capabilities_declare_catalogue_episode_scoped_source(self):
        caps = PbsSource.capabilities
        self.assertEqual(caps.supported_providers, frozenset({"pbs"}))
        self.assertEqual(caps.supported_countries, frozenset({"US"}))
        self.assertEqual(
            caps.supported_content_types,
            frozenset({ContentType.SERIES, ContentType.SEASON, ContentType.EPISODE}),
        )
        self.assertTrue(caps.supports_catalogue_sync)
        self.assertFalse(caps.supports_incremental_sync)
        self.assertFalse(caps.supports_authoritative_snapshots)
        self.assertFalse(caps.supports_tmdb_ids)
        self.assertTrue(caps.supports_seasons)
        self.assertTrue(caps.supports_episodes)
        self.assertTrue(caps.supports_languages)

    def test_freshness_policy_is_declared_for_the_registered_slug(self):
        self.assertEqual(declared_freshness("pbs"), dt.timedelta(hours=24))

    def test_targeted_fetch_is_rejected(self):
        with self.assertRaises(ValueError):
            make_source(FakePbsApi()).fetch(
                ContentRequest(tmdb_id=1, content_type="movie", title="Some Movie")
            )


class PaginationTests(SimpleTestCase):
    def test_walk_pages_all_shows_and_passes_the_cursor_forward(self):
        api = FakePbsApi()
        make_source(api).fetch()

        self.assertEqual(len(api.requests_matching("allShows")), 2)
        first, second = api.payloads[0], api.payloads[1]
        self.assertEqual(first["operationName"], "PbsAllShows")
        self.assertEqual(first["variables"], {"after": None})
        self.assertEqual(second["variables"], {"after": "CURSOR1"})
        # ``first`` ships as a literal — the gateway rejects it as a variable.
        self.assertIn("first: 100", first["query"])
        self.assertNotIn("first: $", first["query"])

    def test_show_repeated_across_pages_is_deduped_before_batching(self):
        api = FakePbsApi()
        make_source(api).fetch()

        batches = [p for p in api.payloads if p["operationName"] == "PbsShowsBatch"]
        self.assertEqual(len(batches), 1)
        self.assertEqual(
            batches[0]["variables"]["slugs"],
            ["nature", "nova", "frontline", "extra-show", "expired-show"],
        )

    def test_has_next_page_without_end_cursor_is_a_failure(self):
        api = FakePbsApi()
        api.pages[None] = {
            "data": {
                "allShows": {
                    "pageInfo": {"hasNextPage": True, "endCursor": None},
                    "edges": [],
                }
            }
        }
        with self.assertRaises(SourceFetchError) as ctx:
            make_source(api).fetch()
        self.assertIn("endCursor", str(ctx.exception))

    def test_repeated_cursor_stops_instead_of_looping(self):
        api = FakePbsApi()
        api.pages["CURSOR1"] = copy.deepcopy(api.pages[None])  # page 1 replays
        api.batches["nature,nova,frontline"] = load_fixture("batch_all.json")
        with self.assertLogs("availability.sources.pbs", level="WARNING") as logs:
            result = make_source(api).fetch()
        self.assertIn("pbs_repeated_cursor", "\n".join(logs.output))
        self.assertEqual(len(api.requests_matching("allShows")), 2)
        self.assertIn("POST showsBatch n=3", api.requests)
        self.assertEqual(len(result.offers), TOTAL_OFFERS)

    def test_unknown_cursor_fails_loudly_in_tests(self):
        api = FakePbsApi()
        page = copy.deepcopy(api.pages[None])
        page["data"]["allShows"]["pageInfo"]["endCursor"] = "NOT-RECORDED"
        api.pages[None] = page
        with self.assertRaises(SourceFetchError):
            make_source(api).fetch()

    def test_transport_failure_is_not_an_empty_catalogue(self):
        api = FakePbsApi()
        api.failures["allShows"] = SourceFetchError("simulated outage")
        with self.assertRaises(SourceFetchError):
            make_source(api).fetch()


class CatalogueParsingTests(SimpleTestCase):
    def setUp(self):
        self.api = FakePbsApi()
        self.result = make_source(self.api).fetch()

    def _offers(self, content_type, show):
        return [
            o for o in self.result.offers
            if o.content_type == content_type and o.provider_content_id == show
        ]

    def test_result_never_claims_authoritativeness_or_cursor(self):
        self.assertFalse(self.result.authoritative)
        self.assertFalse(self.result.complete_snapshot)
        self.assertEqual(self.result.sync_mode, SyncMode.FULL)
        self.assertIsNone(self.result.cursor)

    def test_totals_count_every_watchable_group(self):
        self.assertEqual(len(self.result.contents), TOTAL_CONTENTS)
        self.assertEqual(len(self.result.offers), TOTAL_OFFERS)
        self.assertEqual(
            len(self.api.requests_matching("allShows")), 2
        )  # two pages, one batch

    def test_only_watchable_shows_become_content_rows(self):
        self.assertEqual(
            {c.provider_content_id for c in self.result.contents},
            {"nature", "nova", "frontline"},
        )

    def test_series_content_row_carries_the_show_metadata(self):
        [content] = [
            c for c in self.result.contents if c.provider_content_id == "nature"
        ]
        self.assertEqual(content.provider_id, "pbs")
        self.assertEqual(content.content_type, ContentType.SERIES)
        self.assertEqual(content.title, "Nature")
        self.assertEqual(content.url, "https://www.pbs.org/show/nature")
        self.assertEqual(
            content.poster, "https://toch.pub/pbs/nature-poster.jpg"
        )
        self.assertTrue(content.description.startswith("Nature on PBS."))
        self.assertEqual(content.external_ids, {})
        self.assertEqual(content.source_id, "pbs")

    def test_series_offer_rolls_up_free_embers_and_latest_expiry(self):
        [offer] = self._offers(ContentType.SERIES, "nature")
        self.assertEqual(offer.provider_id, "pbs")
        self.assertEqual(offer.country, "US")
        self.assertIsNone(offer.tmdb_id)
        self.assertIsNone(offer.season_number)
        self.assertIsNone(offer.episode_number)
        self.assertEqual(offer.offer_type, OfferType.FREE_WITH_ADS)
        self.assertEqual(offer.provider_url, "https://www.pbs.org/show/nature")
        self.assertEqual(offer.quality, "")
        self.assertEqual(offer.audio_languages, ["EN"])
        self.assertIsNone(offer.subtitle_languages)
        self.assertIsNone(offer.available_from)
        # Watchable assets expire 2026-10-29, 2026-10-22 and 2026-12-31.
        self.assertEqual(
            offer.available_until,
            dt.datetime(2026, 12, 31, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(offer.source_record_id, "nature")
        self.assertEqual(offer.source_version, SOURCE_VERSION)

    def test_season_offers_are_derived_from_ordinal_groups(self):
        seasons = self._offers(ContentType.SEASON, "nature")
        self.assertEqual([o.season_number for o in seasons], [52, 53])
        free = next(o for o in seasons if o.season_number == 53)
        membership = next(o for o in seasons if o.season_number == 52)
        self.assertEqual(free.offer_type, OfferType.FREE_WITH_ADS)
        self.assertEqual(
            free.available_until,
            dt.datetime(2026, 10, 29, tzinfo=dt.timezone.utc),
        )
        # Season 52's only watchable asset is membership-only.
        self.assertEqual(membership.offer_type, OfferType.SUBSCRIPTION)
        self.assertEqual(membership.source_record_id, "nature:s52")
        self.assertIsNone(membership.episode_number)
        self.assertEqual(membership.provider_url, "https://www.pbs.org/show/nature")

    def test_episode_offers_carry_windows_urls_and_languages(self):
        episodes = self._offers(ContentType.EPISODE, "nature")
        self.assertEqual(len(episodes), 3)  # clip, extra, expired, half skipped
        newest = next(o for o in episodes if o.episode_number == 11)
        self.assertEqual(newest.season_number, 53)
        self.assertEqual(newest.offer_type, OfferType.FREE_WITH_ADS)
        self.assertEqual(newest.country, "US")
        self.assertEqual(
            newest.provider_url,
            "https://www.pbs.org/video/nature-s53e11",
        )
        self.assertEqual(newest.source_record_id, "nature-s53e11")
        self.assertEqual(newest.audio_languages, ["EN"])
        self.assertEqual(
            newest.available_from,
            dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(
            newest.available_until,
            dt.datetime(2026, 10, 29, tzinfo=dt.timezone.utc),
        )

    def test_membership_episode_maps_to_subscription(self):
        [mvod] = [
            o for o in self._offers(ContentType.EPISODE, "nature")
            if o.season_number == 52
        ]
        self.assertEqual(mvod.offer_type, OfferType.SUBSCRIPTION)
        self.assertEqual(
            mvod.available_until,
            dt.datetime(2026, 12, 31, tzinfo=dt.timezone.utc),
        )

    def test_all_membership_show_is_subscription_end_to_end(self):
        [series] = self._offers(ContentType.SERIES, "frontline")
        [season] = self._offers(ContentType.SEASON, "frontline")
        [episode] = self._offers(ContentType.EPISODE, "frontline")
        self.assertEqual(series.offer_type, OfferType.SUBSCRIPTION)
        self.assertIsNone(series.available_until)  # no expiry on the asset
        self.assertEqual(season.offer_type, OfferType.SUBSCRIPTION)
        self.assertEqual(season.season_number, 53)
        self.assertEqual(episode.offer_type, OfferType.SUBSCRIPTION)
        self.assertEqual(
            episode.available_from,
            dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc),
        )
        self.assertIsNone(episode.available_until)

    def test_reverse_ordered_batch_response_is_mapped_by_slug(self):
        # The fixture returns expired-show ... nature (reverse of request);
        # nature's offers are all present and correct.
        self.assertEqual(len(self._offers(ContentType.EPISODE, "nature")), 3)
        self.assertEqual(len(self._offers(ContentType.EPISODE, "nova")), 1)

    def test_watchable_episode_of_a_free_show_is_in_its_window(self):
        nova = self._offers(ContentType.EPISODE, "nova")[0]
        self.assertEqual(nova.season_number, 50)
        self.assertEqual(nova.episode_number, 1)
        self.assertEqual(nova.offer_type, OfferType.FREE_WITH_ADS)
        self.assertEqual(
            nova.available_until,
            dt.datetime(2026, 10, 27, tzinfo=dt.timezone.utc),
        )

    def test_half_missing_ordinal_is_logged_and_skipped(self):
        with self.assertLogs("availability.sources.pbs", level="WARNING") as logs:
            make_source(FakePbsApi()).fetch()
        self.assertIn("pbs_asset_skipped", "\n".join(logs.output))
        self.assertIn("half_missing_ordinal", "\n".join(logs.output))

    def test_batch_missing_a_show_warns_and_contributes_nothing(self):
        api = FakePbsApi()
        shortened = copy.deepcopy(api.batches[BATCH_KEY])
        shortened["data"]["showsBatch"] = [
            entry for entry in shortened["data"]["showsBatch"]
            if entry["show"]["slug"] != "nature"
        ]
        api.batches[BATCH_KEY] = shortened
        with self.assertLogs("availability.sources.pbs", level="INFO") as logs:
            result = api and make_source(api).fetch()
        output = "\n".join(logs.output)
        self.assertIn("pbs_batch_short", output)
        self.assertIn("pbs_show_missing_from_batch", output)
        self.assertEqual(
            {c.provider_content_id for c in result.contents},
            {"nova", "frontline"},
        )
        self.assertEqual(len(result.offers), 6)


class FailureContractTests(SimpleTestCase):
    def setUp(self):
        self.api = FakePbsApi()
        self.source = make_source(self.api)

    def test_graphql_errors_array_is_rejected(self):
        self.api.pages[None] = {"errors": [{"message": "boom"}], "data": None}
        with self.assertRaises(SourceFetchError) as ctx:
            self.source.fetch()
        self.assertIn("GraphQL errors", str(ctx.exception))
        self.assertIn("boom", str(ctx.exception))

    def test_allshows_payload_without_the_shell_is_rejected(self):
        self.api.pages[None] = {"data": {}}
        with self.assertRaises(SourceFetchError) as ctx:
            self.source.fetch()
        self.assertIn("allShows payload shape", str(ctx.exception))

    def test_non_dict_allshows_payload_is_rejected(self):
        self.api.pages[None] = ["not", "a", "dict"]
        with self.assertRaises(SourceFetchError) as ctx:
            self.source.fetch()
        self.assertIn("response type", str(ctx.exception))

    def test_batch_payload_without_the_shell_is_rejected(self):
        self.api.batches[BATCH_KEY] = {"data": {}}
        with self.assertRaises(SourceFetchError) as ctx:
            self.source.fetch()
        self.assertIn("showsBatch payload shape", str(ctx.exception))

    def test_batch_payload_that_is_not_a_list_is_rejected(self):
        self.api.batches[BATCH_KEY] = {"data": {"showsBatch": {"not": "a list"}}}
        with self.assertRaises(SourceFetchError) as ctx:
            self.source.fetch()
        self.assertIn("showsBatch type", str(ctx.exception))

    def test_unknown_batch_key_fails_loudly_in_tests(self):
        self.api.batches = {}
        with self.assertRaises(SourceFetchError):
            self.source.fetch()

    def test_batch_transport_failure_is_not_an_empty_observation(self):
        self.api.failures["showsBatch"] = SourceFetchError("simulated outage")
        with self.assertRaises(SourceFetchError):
            self.source.fetch()


class RefreshPathTests(TestCase):
    """End-to-end catalogue refresh: fixture API -> Offer/ProviderContent rows."""

    def setUp(self):
        self.api = FakePbsApi()
        self.source = make_source(self.api)
        self.service = AvailabilityRefreshService(source=self.source)
        self.provider, _ = Provider.objects.get_or_create(
            slug="pbs", defaults={"name": "PBS", "is_enabled": True}
        )
        Country.objects.get_or_create(code="US", defaults={"name": "United States"})
        self.nature = Movie.objects.create(
            tmdb_id=NATURE_TMDB, title="Nature", year=2000, content_type="series"
        )
        self.nova = Movie.objects.create(
            tmdb_id=NOVA_TMDB, title="NOVA", year=2000, content_type="series"
        )

    def test_refresh_catalogue_persists_resolved_offers_and_skips_unresolved(self):
        result = self.service.refresh_catalogue()

        self.assertEqual(result.discovered, TOTAL_OFFERS)
        self.assertEqual(result.processed, MAPPED_OFFERS)
        self.assertEqual(result.created, MAPPED_OFFERS)
        self.assertEqual(result.skipped, UNMAPPED_OFFERS)
        self.assertEqual(result.unresolved, UNMAPPED_OFFERS)
        self.assertEqual(result.deactivated, 0)
        self.assertEqual(Offer.objects.count(), MAPPED_OFFERS)
        self.assertEqual(
            set(Offer.objects.values_list("tmdb_id", flat=True)),
            {NATURE_TMDB, NOVA_TMDB},
        )
        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)
        self.assertEqual(ContentMapping.objects.count(), 2)

    def test_offer_granularity_survives_persistence(self):
        self.service.refresh_catalogue()
        breakdown = {}
        for offer in Offer.objects.all():
            breakdown[offer.content_type] = breakdown.get(offer.content_type, 0) + 1
        # frontline's three offers are unresolved and never persisted.
        self.assertEqual(
            breakdown, {ContentType.SERIES: 2, ContentType.SEASON: 3, ContentType.EPISODE: 4}
        )

    def test_identity_and_key_fields_are_canonical(self):
        self.service.refresh_catalogue()
        episode = Offer.objects.get(
            content_type=ContentType.EPISODE,
            provider_content_id="nature",
            season_number=53,
            episode_number=11,
        )
        self.assertEqual(
            episode.identity,
            f"pbs|US|episode|{NATURE_TMDB}|53|11|free_with_ads|nature",
        )
        self.assertEqual(episode.provider, self.provider)
        self.assertEqual(episode.country.code, "US")
        self.assertEqual(episode.source.slug, "pbs")
        self.assertEqual(episode.offer_type, OfferType.FREE_WITH_ADS)
        self.assertEqual(episode.audio_languages, ["EN"])
        self.assertTrue(episode.currently_available)

        series = Offer.objects.get(
            content_type=ContentType.SERIES, provider_content_id="nature"
        )
        self.assertEqual(
            series.identity,
            f"pbs|US|series|{NATURE_TMDB}|||free_with_ads|nature",
        )

    def test_sync_run_records_a_full_non_authoritative_catalogue_run(self):
        self.service.refresh_catalogue()

        run = SyncRun.objects.get()
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.provider, self.provider)
        self.assertEqual(run.country.code, "US")
        self.assertEqual(run.source.slug, "pbs")
        self.assertEqual(run.mode, "full")
        self.assertFalse(run.authoritative)
        self.assertEqual(run.items_processed, MAPPED_OFFERS)
        self.assertEqual(run.offers_created, MAPPED_OFFERS)

    def test_rerun_is_idempotent(self):
        self.service.refresh_catalogue()
        second = self.service.refresh_catalogue()

        self.assertEqual(second.deactivated, 0)
        self.assertEqual(Offer.objects.count(), MAPPED_OFFERS)
        self.assertEqual(ContentMapping.objects.count(), 2)
        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)

    def test_fetch_failure_leaves_no_rows_and_no_sync_run(self):
        self.api.failures["allShows"] = SourceFetchError("simulated outage")
        with self.assertRaises(SourceFetchError):
            self.service.refresh_catalogue()
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertFalse(Source.objects.filter(slug="pbs").exists())
