"""ABC iview through the source interface: registration, catalogue walk,
title-derived episode numbering, failure contracts (fixture API, zero
network), and the politeness delay knob.

Fixture data (see ``abc_iview_fixtures``) covers, one behaviour each: a junk
index row, an index row without ``latestEpisode``, a duplicated series, a
numbered multi-season series (plus unnumbered trailer and live stream), an
all-unnumbered news show, a show with no video id, an empty detail, a
404'd series, and Sydney-DST ``pubDate`` conversions.
"""
from __future__ import annotations

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
from availability.sources.abc_iview import (
    DEFAULT_DELAY,
    SERIES_LIST_URL,
    AbcIviewClient,
    AbcIviewSource,
)
from availability.sources.base import ContentRequest
from tracker.models import Movie, Provider

from .abc_iview_fixtures import FakeAbcIviewApi

CULTURE_TMDB = 99101

TOTAL_CONTENTS = 2   # culture-by-design, 7-30 (the rest observe nothing)
TOTAL_OFFERS = 7     # culture: series + 2 seasons + 3 episodes; 7-30: series
MAPPED_OFFERS = 6    # culture (7-30 is never seeded -> unresolved)
DETAIL_REQUESTS = 6  # junk + ghost + duplicate index rows fetch no detail


def make_source(api, *, delay: float = 0.0, sleeper=None) -> AbcIviewSource:
    return AbcIviewSource(
        client=AbcIviewClient(requester=api, delay=delay, sleeper=sleeper)
    )


class RegistrationTests(SimpleTestCase):
    def test_abc_iview_source_is_registered(self):
        source = source_registry.get("abc_iview")
        self.assertIsInstance(source, AbcIviewSource)
        self.assertEqual(source.slug, "abc_iview")
        self.assertEqual(source.name, "ABC iview")
        self.assertEqual(source.source_type, SourceType.API)

    def test_capabilities_declare_catalogue_episode_scoped_source(self):
        caps = AbcIviewSource.capabilities
        self.assertEqual(caps.supported_providers, frozenset({"abc_iview"}))
        self.assertEqual(caps.supported_countries, frozenset({"AU"}))
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
        self.assertEqual(declared_freshness("abc_iview"), dt.timedelta(hours=24))

    def test_targeted_fetch_is_rejected(self):
        with self.assertRaises(ValueError):
            make_source(FakeAbcIviewApi()).fetch(
                ContentRequest(tmdb_id=1, content_type="movie", title="Some Movie")
            )


class CatalogueParsingTests(SimpleTestCase):
    def setUp(self):
        self.api = FakeAbcIviewApi()
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

    def test_walk_is_one_index_request_plus_one_detail_per_unique_series(self):
        index_requests = [u for u in self.api.requests if u == SERIES_LIST_URL]
        # 9 index rows: junk string, ghost (no latestEpisode) and the
        # duplicated culture entry never reach the detail endpoint.
        self.assertEqual(len(index_requests), 1)
        self.assertEqual(len(self.api.requests) - 1, DETAIL_REQUESTS)

    def test_totals_count_every_watchable_group(self):
        self.assertEqual(len(self.result.contents), TOTAL_CONTENTS)
        self.assertEqual(len(self.result.offers), TOTAL_OFFERS)

    def test_only_series_with_a_playable_episode_become_content_rows(self):
        self.assertEqual(
            {c.provider_content_id for c in self.result.contents},
            {"culture-by-design", "7-30"},
        )

    def test_series_content_row_carries_the_show_metadata(self):
        [content] = [
            c for c in self.result.contents
            if c.provider_content_id == "culture-by-design"
        ]
        self.assertEqual(content.provider_id, "abc_iview")
        self.assertEqual(content.content_type, ContentType.SERIES)
        self.assertEqual(content.title, "Culture By Design")
        self.assertEqual(
            content.url, "https://iview.abc.net.au/shows/culture-by-design"
        )
        self.assertEqual(
            content.poster,
            "https://cdn.iview.abc.net.au/thumbs/i/cb/culture_1280.jpg",
        )
        self.assertEqual(
            content.description, "Designers unpack the ideas shaping everyday life."
        )
        self.assertEqual(content.external_ids, {})
        self.assertEqual(content.source_id, "abc_iview")

    def test_series_offer_is_free_avod_without_windows(self):
        [offer] = self._offers(ContentType.SERIES, "culture-by-design")
        self.assertEqual(offer.provider_id, "abc_iview")
        self.assertEqual(offer.country, "AU")
        self.assertIsNone(offer.tmdb_id)
        self.assertIsNone(offer.season_number)
        self.assertIsNone(offer.episode_number)
        self.assertEqual(offer.offer_type, OfferType.FREE_WITH_ADS)
        self.assertEqual(
            offer.provider_url,
            "https://iview.abc.net.au/shows/culture-by-design",
        )
        self.assertEqual(offer.quality, "")
        self.assertEqual(offer.audio_languages, ["EN"])
        self.assertIsNone(offer.subtitle_languages)
        self.assertIsNone(offer.available_from)
        self.assertIsNone(offer.available_until)
        self.assertEqual(offer.source_record_id, "culture-by-design")

    def test_season_offers_are_derived_from_title_ordinals(self):
        seasons = self._offers(ContentType.SEASON, "culture-by-design")
        self.assertEqual([o.season_number for o in seasons], [1, 2])
        for offer in seasons:
            self.assertEqual(offer.offer_type, OfferType.FREE_WITH_ADS)
            self.assertIsNone(offer.episode_number)
            self.assertIsNone(offer.available_from)
            self.assertIsNone(offer.available_until)
        self.assertEqual(
            {o.source_record_id for o in seasons},
            {"culture-by-design:s1", "culture-by-design:s2"},
        )

    def test_episode_offers_carry_numbering_sydney_dates_and_urls(self):
        episodes = self._offers(ContentType.EPISODE, "culture-by-design")
        self.assertEqual(
            [(o.season_number, o.episode_number) for o in episodes],
            [(1, 1), (1, 2), (2, 1)],
        )
        first = episodes[0]
        self.assertEqual(first.country, "AU")
        self.assertEqual(first.offer_type, OfferType.FREE_WITH_ADS)
        self.assertEqual(
            first.provider_url,
            "https://iview.abc.net.au/video/CB2501H001S00",
        )
        self.assertEqual(first.source_record_id, "CB2501H001S00")
        self.assertEqual(first.audio_languages, ["EN"])
        self.assertIsNone(first.subtitle_languages)
        self.assertIsNone(first.available_until)

    def test_pub_dates_convert_sydney_wall_time_through_both_dst_states(self):
        episodes = self._offers(ContentType.EPISODE, "culture-by-design")
        by_number = {(o.season_number, o.episode_number): o for o in episodes}
        # AEST (UTC+10): late September, before the October changeover.
        self.assertEqual(
            by_number[(1, 1)].available_from,
            dt.datetime(2026, 9, 30, 8, 30, tzinfo=dt.timezone.utc),
        )
        # AEDT (UTC+11): mid-January.
        self.assertEqual(
            by_number[(1, 2)].available_from,
            dt.datetime(2026, 1, 15, 9, 0, tzinfo=dt.timezone.utc),
        )
        # AEDT (UTC+11): after the first Sunday in October.
        self.assertEqual(
            by_number[(2, 1)].available_from,
            dt.datetime(2026, 10, 10, 8, 0, tzinfo=dt.timezone.utc),
        )

    def test_unnumbered_and_live_entries_are_skipped_but_logged(self):
        with self.assertLogs("availability.sources.abc_iview", level="INFO") as logs:
            result = make_source(FakeAbcIviewApi()).fetch()
        output = "\n".join(logs.output)
        self.assertIn("reason=unnumbered_title", output)
        self.assertIn("reason=livestream", output)
        # The trailer still made the series watchable: same totals.
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)
        self.assertEqual(len(result.offers), TOTAL_OFFERS)

    def test_unnumbered_news_show_gets_a_series_offer_only(self):
        show_offers = [o for o in self.result.offers if o.provider_content_id == "7-30"]
        self.assertEqual(len(show_offers), 1)
        self.assertEqual(show_offers[0].content_type, ContentType.SERIES)
        self.assertEqual(show_offers[0].offer_type, OfferType.FREE_WITH_ADS)

    def test_series_without_a_video_id_nowhere_is_observed_as_nothing(self):
        with self.assertLogs("availability.sources.abc_iview", level="INFO") as logs:
            result = make_source(FakeAbcIviewApi()).fetch()
        self.assertIn("reason=missing_video_id", "\n".join(logs.output))
        self.assertNotIn(
            "9-11-stories", {c.provider_content_id for c in result.contents}
        )

    def test_series_gone_between_index_and_detail_is_logged_and_skipped(self):
        with self.assertLogs("availability.sources.abc_iview", level="INFO") as logs:
            result = make_source(FakeAbcIviewApi()).fetch()
        output = "\n".join(logs.output)
        self.assertIn("abc_iview_show_gone", output)
        self.assertIn("gone-show", output)
        self.assertNotIn(
            "gone-show", {c.provider_content_id for c in result.contents}
        )

    def test_empty_and_missing_episode_lists_are_observations_of_nothing(self):
        contents = {c.provider_content_id for c in self.result.contents}
        self.assertNotIn("empty-show", contents)
        self.assertNotIn("no-eps-show", contents)


class DelayTests(SimpleTestCase):
    def test_detail_requests_are_spaced_but_the_index_request_is_not(self):
        sleeps: list[float] = []
        api = FakeAbcIviewApi()
        make_source(api, delay=0.123, sleeper=sleeps.append).fetch()
        self.assertEqual(len(sleeps), DETAIL_REQUESTS)
        self.assertTrue(all(s == 0.123 for s in sleeps))

    def test_default_delay_keeps_a_full_walk_polite(self):
        # ~1613 detail requests per run; the walk must be paced, not bursty.
        self.assertGreaterEqual(DEFAULT_DELAY, 0.1)
        self.assertLessEqual(DEFAULT_DELAY, 0.5)


class FailureContractTests(SimpleTestCase):
    def test_index_that_is_not_a_list_is_rejected(self):
        api = FakeAbcIviewApi(list_payload={"not": "a list"})
        with self.assertRaises(SourceFetchError) as ctx:
            make_source(api).fetch()
        self.assertIn("series list payload type", str(ctx.exception))

    def test_index_with_a_non_json_like_scalar_is_rejected(self):
        api = FakeAbcIviewApi(list_payload="garbage")
        with self.assertRaises(SourceFetchError) as ctx:
            make_source(api).fetch()
        self.assertIn("response type", str(ctx.exception))

    def test_detail_that_is_not_a_mapping_is_rejected(self):
        api = FakeAbcIviewApi(
            list_payload=[
                {"seriesTitle": "Weird", "latestEpisode": "programs/weird/W1S00"}
            ],
            details={"weird": ["not", "a", "dict"]},
        )
        with self.assertRaises(SourceFetchError) as ctx:
            make_source(api).fetch()
        self.assertIn("detail payload type", str(ctx.exception))

    def test_episodes_value_that_is_neither_null_nor_a_list_is_rejected(self):
        api = FakeAbcIviewApi(
            list_payload=[
                {"seriesTitle": "Bad", "latestEpisode": "programs/bad-eps/B1S00"}
            ],
            details={"bad-eps": {"seriesTitle": "Bad", "episodes": {"x": 1}}},
        )
        with self.assertRaises(SourceFetchError) as ctx:
            make_source(api).fetch()
        self.assertIn("episodes payload", str(ctx.exception))

    def test_unknown_series_in_the_index_fails_loudly_in_tests(self):
        api = FakeAbcIviewApi(
            list_payload=[
                {"seriesTitle": "Mystery", "latestEpisode": "programs/mystery/M1S00"}
            ]
        )
        with self.assertRaises(SourceFetchError):
            make_source(api).fetch()

    def test_transport_failure_on_the_index_is_not_an_empty_catalogue(self):
        api = FakeAbcIviewApi(failures={"/api/series": SourceFetchError("outage")})
        with self.assertRaises(SourceFetchError):
            make_source(api).fetch()

    def test_transport_failure_on_a_detail_is_not_an_empty_catalogue(self):
        api = FakeAbcIviewApi(
            failures={"culture-by-design": SourceFetchError("outage")}
        )
        with self.assertRaises(SourceFetchError):
            make_source(api).fetch()


class RefreshPathTests(TestCase):
    """End-to-end catalogue refresh: fixture API -> Offer/ProviderContent rows."""

    def setUp(self):
        self.api = FakeAbcIviewApi()
        self.source = make_source(self.api)
        self.service = AvailabilityRefreshService(source=self.source)
        self.provider, _ = Provider.objects.get_or_create(
            slug="abc_iview", defaults={"name": "ABC iview", "is_enabled": True}
        )
        Country.objects.get_or_create(code="AU", defaults={"name": "Australia"})
        self.culture = Movie.objects.create(
            tmdb_id=CULTURE_TMDB, title="Culture By Design", year=2026,
            content_type="series",
        )

    def test_refresh_catalogue_persists_resolved_offers_and_skips_unresolved(self):
        result = self.service.refresh_catalogue()

        self.assertEqual(result.discovered, TOTAL_OFFERS)
        self.assertEqual(result.processed, MAPPED_OFFERS)
        self.assertEqual(result.created, MAPPED_OFFERS)
        self.assertEqual(result.skipped, TOTAL_OFFERS - MAPPED_OFFERS)
        self.assertEqual(result.unresolved, TOTAL_OFFERS - MAPPED_OFFERS)
        self.assertEqual(result.deactivated, 0)
        self.assertEqual(Offer.objects.count(), MAPPED_OFFERS)
        self.assertEqual(
            set(Offer.objects.values_list("tmdb_id", flat=True)),
            {CULTURE_TMDB},
        )
        # Both series get a ProviderContent row; only the mapped one gets
        # offers persisted and a ContentMapping.
        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)
        self.assertEqual(ContentMapping.objects.count(), 1)

    def test_offer_granularity_survives_persistence(self):
        self.service.refresh_catalogue()
        breakdown: dict = {}
        for offer in Offer.objects.all():
            breakdown[offer.content_type] = breakdown.get(offer.content_type, 0) + 1
        self.assertEqual(
            breakdown,
            {ContentType.SERIES: 1, ContentType.SEASON: 2, ContentType.EPISODE: 3},
        )

    def test_identity_and_key_fields_are_canonical(self):
        self.service.refresh_catalogue()
        episode = Offer.objects.get(
            content_type=ContentType.EPISODE,
            provider_content_id="culture-by-design",
            season_number=1,
            episode_number=1,
        )
        self.assertEqual(
            episode.identity,
            f"abc_iview|AU|episode|{CULTURE_TMDB}|1|1|free_with_ads|culture-by-design",
        )
        self.assertEqual(episode.provider, self.provider)
        self.assertEqual(episode.country.code, "AU")
        self.assertEqual(episode.source.slug, "abc_iview")
        self.assertEqual(episode.offer_type, OfferType.FREE_WITH_ADS)
        self.assertEqual(episode.audio_languages, ["EN"])
        self.assertEqual(
            episode.available_from,
            dt.datetime(2026, 9, 30, 8, 30, tzinfo=dt.timezone.utc),
        )
        self.assertTrue(episode.currently_available)

        series = Offer.objects.get(
            content_type=ContentType.SERIES, provider_content_id="culture-by-design"
        )
        self.assertEqual(
            series.identity,
            f"abc_iview|AU|series|{CULTURE_TMDB}|||free_with_ads|culture-by-design",
        )

    def test_sync_run_records_a_full_non_authoritative_catalogue_run(self):
        self.service.refresh_catalogue()

        run = SyncRun.objects.get()
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.provider, self.provider)
        self.assertEqual(run.country.code, "AU")
        self.assertEqual(run.source.slug, "abc_iview")
        self.assertEqual(run.mode, "full")
        self.assertFalse(run.authoritative)
        self.assertEqual(run.items_processed, MAPPED_OFFERS)
        self.assertEqual(run.offers_created, MAPPED_OFFERS)

    def test_rerun_is_idempotent(self):
        self.service.refresh_catalogue()
        second = self.service.refresh_catalogue()

        self.assertEqual(second.deactivated, 0)
        self.assertEqual(Offer.objects.count(), MAPPED_OFFERS)
        self.assertEqual(ContentMapping.objects.count(), 1)
        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)

    def test_fetch_failure_leaves_no_rows_and_no_sync_run(self):
        self.api.failures["/api/series"] = SourceFetchError("simulated outage")
        with self.assertRaises(SourceFetchError):
            self.service.refresh_catalogue()
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertFalse(Source.objects.filter(slug="abc_iview").exists())
