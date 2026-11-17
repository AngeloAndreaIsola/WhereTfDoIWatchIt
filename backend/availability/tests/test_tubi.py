"""Tubi source: sitemap discovery, SSR state parsing, failure semantics.

Fixture-based (``tubi_fixtures.FakeTubiApi``): no test performs network I/O and
no test touches the database (``SimpleTestCase`` doubles as a guard — the
source must stay persistence-free).

Fixture catalogue (recorded pages):

    sitemap index -> movies-1, movies-2, tv-shows-1 (+ editorial children)

    movies-1    i-robot, clearcut (SPA shell), permission, about (non-title)
    movies-2    i-robot (duplicate entry, same URL as movies-1)
    tv-shows-1  blue-water-high, 1-800-missing, ancient-aliens

    i-robot             movie, 2004, 720P, window 2026-09-15 .. 2035-10-15
    permission          movie, 2018, 720P, open-ended window
    blue-water-high     series 02185, season 1 with 4 episodes (576P)
    1-800-missing       series 01076, no episode observable -> content only
    ancient-aliens      series 04258, 3 season wrappers (seasons 2 and 3 are
                        id-only stubs), 3 episodes -> season 1 offers only
    clearcut            title URL whose answer is the 200 SPA shell

    Walk order after interleaving the three sitemaps and de-duplicating:
    i-robot, blue-water-high, clearcut, 1-800-missing, permission,
    ancient-aliens, about (the last is never requested).
"""
import datetime as dt
import json

import requests
from django.test import SimpleTestCase

from availability.domain.enums import ContentType, OfferType, SourceType, SyncMode
from availability.sources import (
    AvailabilitySource,
    ContentRequest,
    SourceFetchError,
    TubiSource,
    source_registry,
)
from availability.sources.tubi import (
    TubiClient,
    TubiPageGone,
    _priority_urls,
    _state_from_html,
    _xml_locs,
)

from .tubi_fixtures import (
    ABOUT,
    ANCIENT_ALIENS,
    BLUE_WATER_HIGH,
    CLEARCUT,
    I_ROBOT,
    MOVIES_1,
    MOVIES_2,
    ONE_EIGHT_HUNDRED,
    PERMISSION,
    SITEMAP_INDEX_URL,
    TV_SHOWS_1,
    FakeTubiApi,
    url_entry,
)

TOTAL_CONTENTS = 5
TOTAL_OFFERS = 13


def build_source(api=None, **source_kwargs):
    api = api or FakeTubiApi()
    return TubiSource(client=TubiClient(requester=api), **source_kwargs), api


class RegistrationTests(SimpleTestCase):
    def test_tubi_source_is_registered(self):
        source = source_registry.get("tubi")
        self.assertIsInstance(source, TubiSource)
        self.assertEqual(source.slug, "tubi")
        self.assertEqual(source.name, "Tubi")
        self.assertEqual(source.source_type, SourceType.WEB)

    def test_source_conforms_to_the_interface(self):
        self.assertIsInstance(source_registry.get("tubi"), AvailabilitySource)

    def test_capabilities_declare_catalogue_non_authoritative_us_scoped_source(self):
        capabilities = source_registry.get("tubi").capabilities
        self.assertEqual(capabilities.supported_providers, frozenset({"tubi"}))
        self.assertEqual(capabilities.supported_countries, frozenset({"US"}))
        self.assertEqual(
            capabilities.supported_content_types,
            frozenset({ContentType.MOVIE, ContentType.SERIES, ContentType.SEASON,
                       ContentType.EPISODE}),
        )
        self.assertTrue(capabilities.supports_catalogue_sync)
        # Sitemap entries go stale and shell pages hide titles: absence can
        # never be evidence of unavailability.
        self.assertFalse(capabilities.supports_authoritative_snapshots)
        self.assertFalse(capabilities.supports_incremental_sync)
        # Tubi publishes no TMDb ids (imdb_id is recorded as metadata only).
        self.assertFalse(capabilities.supports_tmdb_ids)
        self.assertTrue(capabilities.supports_seasons)
        self.assertTrue(capabilities.supports_episodes)
        self.assertTrue(capabilities.supports_languages)

    def test_targeted_content_request_is_rejected(self):
        source, _api = build_source()
        with self.assertRaisesMessage(ValueError, "catalogue-driven"):
            source.fetch(ContentRequest(tmdb_id=550, title="Fight Club"))

    def test_fetch_without_content_walks_the_catalogue(self):
        source, _api = build_source()
        result = source.fetch()
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)
        self.assertEqual(len(result.offers), TOTAL_OFFERS)

    def test_scope_hints_are_accepted_but_never_change_the_reported_scope(self):
        source, _api = build_source()
        result = source.fetch(provider="tubi", country="US")
        self.assertEqual(len(result.offers), TOTAL_OFFERS)
        self.assertEqual({offer.country for offer in result.offers}, {"US"})
        self.assertEqual({offer.provider_id for offer in result.offers}, {"tubi"})


class SnapshotSemanticsTests(SimpleTestCase):
    def test_result_never_claims_authoritativeness_but_returns_a_checkpoint(self):
        source, _api = build_source()
        result = source.fetch()
        self.assertFalse(result.authoritative)
        self.assertFalse(result.complete_snapshot)
        # One fetch reports the whole configured window.
        self.assertEqual(result.sync_mode, SyncMode.FULL)
        # Discovery is checkpointed: the cursor is traversal progress, never a
        # completeness or availability claim.
        checkpoint = json.loads(result.cursor)
        self.assertEqual(checkpoint["v"], 1)
        self.assertIn("queues", checkpoint)


class SitemapDiscoveryTests(SimpleTestCase):
    def test_only_title_sitemaps_are_requested(self):
        source, api = build_source()
        source.fetch()
        self.assertIn(SITEMAP_INDEX_URL, api.requests)
        self.assertIn(MOVIES_1, api.requests)
        self.assertIn(TV_SHOWS_1, api.requests)
        requested = set(api.requests)
        self.assertNotIn("https://tubitv.com/sitemaps/category-pages.xml", requested)
        self.assertNotIn("https://tubitv.com/static-pages.xml", requested)

    def test_interleaved_entries_are_walked_in_the_recorded_order(self):
        source, api = build_source()
        source.fetch()
        self.assertEqual(
            api.detail_requests,
            [I_ROBOT, BLUE_WATER_HIGH, CLEARCUT, ONE_EIGHT_HUNDRED,
             PERMISSION, ANCIENT_ALIENS],
        )

    def test_a_duplicate_sitemap_entry_is_fetched_once(self):
        source, api = build_source()
        source.fetch()
        self.assertEqual(api.detail_requests.count(I_ROBOT), 1)

    def test_a_non_title_url_is_never_requested(self):
        source, api = build_source()
        source.fetch()
        self.assertNotIn(ABOUT, api.requests)

    def test_index_without_title_sitemaps_is_a_failure_not_an_empty_catalogue(self):
        source, api = build_source()
        api.index = api.index.replace("movies-1.xml", "category-1.xml") \
                             .replace("movies-2.xml", "category-2.xml") \
                             .replace("tv-shows-1.xml", "linear-1.xml")
        with self.assertRaisesMessage(SourceFetchError, "no movie/series sitemaps"):
            source.fetch()
        self.assertNotIn(MOVIES_1, api.requests)

    def test_malformed_sitemap_is_a_failure(self):
        source, api = build_source()
        api.sitemaps[MOVIES_1] = "<urlset><loc>not closed"
        with self.assertRaises(SourceFetchError):
            source.fetch()

    def test_non_xml_sitemap_answer_is_a_failure(self):
        source, api = build_source()
        api.sitemaps[MOVIES_1] = "<html><body>consent</body></html>"
        with self.assertRaisesMessage(SourceFetchError, "sitemap root"):
            source.fetch()

    def test_image_locations_are_never_title_pages(self):
        """The real sitemap nests <image:loc> under every <url><loc>.

        Only the direct <loc> child is a page URL. Before this guard, half of
        every batch was poster assets (250 detail pages from a 500 cap).
        """
        source, api = build_source(max_contents=None)
        queues, stats = source._title_queues()

        title_urls = [url for _sitemap, urls in queues for url in urls]
        self.assertNotIn("https://canvas-lb.tubitv.com/opts/poster-i-robot", title_urls)
        self.assertTrue(all("/movies/" in url or "/series/" in url for url in title_urls))
        self.assertEqual(stats["title_urls"], 6)
        # Four movie <url> entries (one is /about), one duplicate, three series.
        self.assertEqual(stats["sitemap_locs"], 8)
        self.assertEqual(stats["non_title_filtered"], 1)
        self.assertEqual(stats["duplicates_removed"], 1)

        source.fetch()
        self.assertNotIn(
            "https://canvas-lb.tubitv.com/opts/poster-i-robot", api.requests
        )

    def test_the_cap_counts_title_pages_not_asset_locations(self):
        source, api = build_source(max_contents=2)
        source.fetch()
        # The fixture carries three <image:loc> assets; they consume no slot.
        self.assertEqual(len(api.detail_requests), 2)
        self.assertEqual(api.detail_requests, [I_ROBOT, BLUE_WATER_HIGH])

    def test_a_title_sitemap_with_no_title_urls_is_a_failure(self):
        source, api = build_source()
        empty = (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://tubitv.com/about</loc></url></urlset>"
        )
        for sitemap_url in (MOVIES_1, MOVIES_2, TV_SHOWS_1):
            api.sitemaps[sitemap_url] = empty
        with self.assertRaisesMessage(SourceFetchError, "no title URLs"):
            source.fetch()

    def test_direct_loc_child_only(self):
        document = (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">'
            "<url><loc>https://tubitv.com/movies/1/one</loc>"
            "<image:image><image:loc>https://canvas/one</image:loc>"
            "</image:image></url></urlset>"
        )
        self.assertEqual(
            _xml_locs(document, expect="urlset", url="synthetic"),
            ["https://tubitv.com/movies/1/one"],
        )


class SitemapRetryTests(SimpleTestCase):
    class FlakyRequester:
        def __init__(self, api, fragment, failures):
            self.api = api
            self.fragment = fragment
            self.failures = failures
            self.calls = 0

        def __call__(self, url):
            if self.fragment in url:
                self.calls += 1
                if self.calls <= self.failures:
                    raise requests.ConnectionError("transient flap")
            return self.api(url)

    def test_a_transient_sitemap_failure_is_retried(self):
        api = FakeTubiApi()
        flaky = self.FlakyRequester(api, MOVIES_1, failures=2)
        source = TubiSource(client=TubiClient(requester=flaky, retries=2))

        result = source.fetch()

        self.assertEqual(flaky.calls, 3)
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)

    def test_a_permanent_sitemap_failure_stops_after_bounded_retries(self):
        api = FakeTubiApi()
        flaky = self.FlakyRequester(api, MOVIES_1, failures=99)
        source = TubiSource(client=TubiClient(requester=flaky, retries=2))

        with self.assertRaisesMessage(SourceFetchError, "transient flap"):
            source.fetch()
        self.assertEqual(flaky.calls, 3)

    def test_a_malformed_sitemap_is_retried_then_fails_loudly(self):
        api = FakeTubiApi()
        api.sitemaps[MOVIES_1] = "<urlset><loc>not closed"
        source = TubiSource(client=TubiClient(requester=api, retries=2))

        with self.assertRaisesMessage(SourceFetchError, "malformed sitemap"):
            source.fetch()
        # One initial attempt plus two retries, and never an empty success.
        self.assertEqual(api.requests.count(MOVIES_1), 3)


class CatalogueCheckpointTests(SimpleTestCase):
    def _walk(self, source, api, cursor=None, priority=None):
        before = len(api.detail_requests)
        result = source.fetch(cursor=cursor, priority=priority)
        return result, api.detail_requests[before:]

    def test_repeated_capped_runs_advance_and_cover_every_title_once(self):
        source, api = build_source(max_contents=2)
        cursor = None
        batches = []
        for _ in range(3):
            result, batch = self._walk(source, api, cursor)
            batches.append(batch)
            cursor = result.cursor

        self.assertEqual(
            batches,
            [
                [I_ROBOT, BLUE_WATER_HIGH],
                [CLEARCUT, ONE_EIGHT_HUNDRED],
                [PERMISSION, ANCIENT_ALIENS],
            ],
        )
        self.assertEqual(len([url for batch in batches for url in batch]), 6)
        self.assertEqual(len(set(url for batch in batches for url in batch)), 6)

        # The fourth run starts the next cycle at the head instead of
        # re-reading the same newest pages forever.
        _result, fourth = self._walk(source, api, cursor)
        self.assertEqual(fourth, [I_ROBOT, BLUE_WATER_HIGH])

    def test_a_batch_that_observes_nothing_still_advances_the_checkpoint(self):
        source, api = build_source(max_contents=2)
        shell = api.pages[CLEARCUT]
        api.pages[I_ROBOT] = shell
        api.pages[BLUE_WATER_HIGH] = shell

        first, first_batch = self._walk(source, api)
        self.assertEqual(first.contents, [])
        self.assertEqual(first_batch, [I_ROBOT, BLUE_WATER_HIGH])
        self.assertIsNotNone(first.cursor)

        second, second_batch = self._walk(source, api, first.cursor)
        self.assertEqual(second_batch, [CLEARCUT, ONE_EIGHT_HUNDRED])
        self.assertNotEqual(first.cursor, second.cursor)

    def test_a_head_insertion_does_not_shift_the_checkpoint(self):
        source, api = build_source(max_contents=2)
        _first, first_batch = self._walk(source, api)
        cursor = _first.cursor
        self.assertEqual(first_batch, [I_ROBOT, BLUE_WATER_HIGH])

        new_url = "https://tubitv.com/movies/700000/brand-new"
        api.sitemaps[MOVIES_1] = api.sitemaps[MOVIES_1].replace(
            "<url>",
            url_entry(new_url) + "<url>",
            1,
        )
        api.pages[new_url] = api.pages[PERMISSION]

        _second, second_batch = self._walk(source, api, cursor)
        # Resume is keyed on the last-seen URL, not an offset: the new head
        # entry waits for the next cycle and does not push older titles back.
        self.assertEqual(second_batch, [CLEARCUT, ONE_EIGHT_HUNDRED])

        # It is not starved permanently: the next cycle starts at the head and
        # includes the newly inserted entry.
        third, _third_batch = self._walk(source, api, _second.cursor)
        fourth, fourth_batch = self._walk(source, api, third.cursor)
        self.assertEqual(fourth_batch, [new_url, BLUE_WATER_HIGH])

    def test_a_removed_checkpoint_url_restarts_only_that_sitemap(self):
        source, api = build_source(max_contents=2)
        first, _batch = self._walk(source, api)
        self.assertEqual(first.contents[0].provider_content_id, "578912")

        api.sitemaps[TV_SHOWS_1] = api.sitemaps[TV_SHOWS_1].replace(
            "<url><loc>https://tubitv.com/series/02185/blue-water-high</loc>"
            "<lastmod>2026-09-12</lastmod></url>",
            "",
        )
        second, batch = self._walk(source, api, first.cursor)

        # movies-1 resumed after I, Robot; tv-shows-1 (whose checkpoint URL was
        # removed) restarted at its first remaining entry. No deadlock.
        self.assertEqual(batch, [CLEARCUT, ONE_EIGHT_HUNDRED])
        self.assertEqual({c.provider_content_id for c in second.contents}, {"1076"})

    def test_an_unreadable_checkpoint_starts_a_new_cycle_loudly(self):
        source, api = build_source(max_contents=2)
        with self.assertLogs("availability.sources.tubi", level="WARNING") as logs:
            result, batch = self._walk(source, api, cursor="{not-json")
        self.assertIn("tubi_cursor_invalid", "\n".join(logs.output))
        self.assertEqual(batch, [I_ROBOT, BLUE_WATER_HIGH])
        self.assertIsNotNone(result.cursor)

    def test_a_cursor_from_another_version_restarts_the_cycle(self):
        source, api = build_source(max_contents=2)
        stale = json.dumps({"v": 999, "cycle": 4, "queues": {}})
        with self.assertLogs("availability.sources.tubi", level="WARNING") as logs:
            _result, batch = self._walk(source, api, cursor=stale)
        self.assertIn("tubi_cursor_reset", "\n".join(logs.output))
        self.assertEqual(batch, [I_ROBOT, BLUE_WATER_HIGH])

    def test_priority_titles_are_fetched_every_run_without_consuming_the_batch(self):
        source, api = build_source(max_contents=1)
        hint = [("Ancient Aliens", None, "series")]

        first, first_batch = self._walk(source, api, priority=hint)
        self.assertEqual(first_batch, [ANCIENT_ALIENS, I_ROBOT])
        second, second_batch = self._walk(source, api, first.cursor, priority=hint)
        # The traversal batch advanced (I, Robot -> Clearcut) and the priority
        # page was fetched again; slug matching never decided identity.
        self.assertEqual(second_batch, [ANCIENT_ALIENS, CLEARCUT])

    def test_priority_slug_matching_respects_content_type(self):
        entries = [
            "https://tubitv.com/movies/1/permission",
            "https://tubitv.com/series/2/permission",
        ]
        self.assertEqual(
            _priority_urls(entries, [("Permission", 2018, "series")], 10),
            ["https://tubitv.com/series/2/permission"],
        )
        self.assertEqual(
            _priority_urls(entries, [("Permission", 2018, "movie")], 10),
            ["https://tubitv.com/movies/1/permission"],
        )

    def test_a_failed_priority_page_does_not_block_the_traversal_batch(self):
        source, api = build_source(max_contents=1)
        del api.pages[ANCIENT_ALIENS]
        hint = [("Ancient Aliens", None, "series")]

        with self.assertLogs("availability.sources.tubi", level="WARNING") as logs:
            result, batch = self._walk(source, api, priority=hint)

        self.assertIn("tubi_priority_page_failed", "\n".join(logs.output))
        self.assertEqual(batch, [ANCIENT_ALIENS, I_ROBOT])
        self.assertEqual(
            {c.provider_content_id for c in result.contents}, {"578912"}
        )
        self.assertIsNotNone(result.cursor)

    def test_a_priority_page_that_is_also_in_the_batch_still_aborts(self):
        """Priority tolerance must never hide a traversal-page failure."""
        source, api = build_source(max_contents=1)
        del api.pages[I_ROBOT]

        with self.assertRaises(SourceFetchError):
            source.fetch(priority=[("I, Robot", 2004, "movie")])


class CatalogueParsingTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source, cls.api = build_source()
        cls.result = cls.source.fetch()

    def _contents(self, provider_content_id):
        return [c for c in self.result.contents
                if c.provider_content_id == provider_content_id]

    def _offers(self, provider_content_id, content_type=None):
        offers = [o for o in self.result.offers
                  if o.provider_content_id == provider_content_id]
        if content_type is not None:
            offers = [o for o in offers if o.content_type == content_type]
        return offers

    def test_every_observable_title_becomes_a_content_record(self):
        self.assertEqual(
            {c.provider_content_id for c in self.result.contents},
            {"578912", "582004", "2185", "1076", "4258"},
        )
        self.assertEqual(
            {c.content_type for c in self.result.contents},
            {ContentType.MOVIE, ContentType.SERIES},
        )
        self.assertTrue(all(c.provider_id == "tubi" for c in self.result.contents))
        self.assertTrue(all(c.source_id == "tubi" for c in self.result.contents))

    def test_provider_content_id_is_the_normalized_tubi_id(self):
        # The URL says /series/02185/, the state says "02185": both normalize
        # to the same identity every content row, offer and mapping shares.
        self.assertEqual(self._contents("2185")[0].title, "Blue Water High")
        self.assertEqual(self._contents("1076")[0].title, "1-800-Missing")
        self.assertEqual(self._contents("4258")[0].title, "Ancient Aliens")

    def test_movie_fields_come_from_the_state(self):
        movie = self._contents("578912")[0]
        self.assertEqual(movie.content_type, ContentType.MOVIE)
        self.assertEqual(movie.title, "I, Robot")
        # Year precision: Jan 1 of the production year.
        self.assertEqual(movie.release_date, dt.date(2004, 1, 1))
        self.assertEqual(movie.url, I_ROBOT)
        self.assertEqual(movie.duration, 6882)  # Tubi publishes seconds
        self.assertEqual(movie.external_ids, {"imdb_id": "tt0343818"})
        self.assertTrue(movie.poster.startswith("https://canvas-lb.tubitv.com/"))
        self.assertIn("mistrusts technology", movie.description)

    def test_series_carry_no_release_date(self):
        # Tubi's series year is not a dependable first-air year (Ancient Aliens
        # carries 2020 against a 2010 premiere), so series stay unmatched by
        # year, like CBC's series.
        series = self._contents("4258")[0]
        self.assertEqual(series.content_type, ContentType.SERIES)
        self.assertIsNone(series.release_date)
        self.assertEqual(series.duration, None)

    def test_movie_offer_is_free_with_ads_in_the_us_with_full_metadata(self):
        offers = self._offers("578912", ContentType.MOVIE)
        self.assertEqual(len(offers), 1)
        offer = offers[0]
        self.assertEqual(offer.offer_type, OfferType.FREE_WITH_ADS)
        self.assertEqual(offer.country, "US")
        self.assertEqual(offer.provider_url, I_ROBOT)
        self.assertEqual(offer.source_record_id, "578912")
        self.assertEqual(offer.source_version, "tubi-ssr-v2")
        self.assertIsNone(offer.tmdb_id)
        self.assertIsNone(offer.season_number)
        self.assertIsNone(offer.episode_number)
        # 720P maps into the shared ranking vocabulary.
        self.assertEqual(offer.quality, "HD")
        self.assertEqual(offer.audio_languages, ["EN"])
        self.assertEqual(offer.subtitle_languages, ["EN"])
        self.assertEqual(
            offer.available_from, dt.datetime(2026, 9, 15, 7, 0, tzinfo=dt.timezone.utc)
        )
        self.assertEqual(
            offer.available_until, dt.datetime(2035, 10, 15, 6, 59, tzinfo=dt.timezone.utc)
        )

    def test_series_offers_cover_series_season_and_its_episodes(self):
        offers = self._offers("2185")
        self.assertEqual(len(offers), 6)
        by_type = {}
        for offer in offers:
            by_type.setdefault(offer.content_type, []).append(offer)
        self.assertEqual(
            sorted(by_type), [ContentType.EPISODE, ContentType.SEASON, ContentType.SERIES]
        )
        self.assertEqual(len(by_type[ContentType.SERIES]), 1)
        self.assertEqual(len(by_type[ContentType.SEASON]), 1)
        self.assertEqual(
            sorted(o.episode_number for o in by_type[ContentType.EPISODE]),
            [2, 4, 6, 8],
        )
        # The default season *is* the page; every other season URL would be
        # the page plus /season-N (Tubi only publishes one season in the SSR
        # state, so no other season offer exists here).
        self.assertEqual(by_type[ContentType.SERIES][0].provider_url, BLUE_WATER_HIGH)
        self.assertEqual(by_type[ContentType.SEASON][0].season_number, 1)
        self.assertEqual(by_type[ContentType.SEASON][0].provider_url, BLUE_WATER_HIGH)
        for offer in by_type[ContentType.EPISODE]:
            self.assertEqual(offer.season_number, 1)
            self.assertEqual(offer.audio_languages, ["EN"])
            # An explicit empty subtitle list stays [], never None.
            self.assertEqual(offer.subtitle_languages, [])
            self.assertEqual(offer.quality, "SD")  # 576P
            self.assertEqual(offer.season_number, 1)
        # Episode URLs come from the page's own links, not from a guess.
        self.assertEqual(
            {o.provider_url for o in by_type[ContentType.EPISODE]},
            {
                "https://tubitv.com/tv-shows/384207/s01-e02-winners-and-losers",
                "https://tubitv.com/tv-shows/384209/s01-e04-fly-takes-a-dive",
                "https://tubitv.com/tv-shows/384211/s01-e06-edge-wipes-out",
                "https://tubitv.com/tv-shows/384213/s01-e08-brothers-and-sisters",
            },
        )
        self.assertEqual(
            sorted(o.source_record_id for o in by_type[ContentType.EPISODE]),
            ["384207", "384209", "384211", "384213"],
        )

    def test_only_seasons_with_full_episode_records_are_offered(self):
        # The fixture carries three season wrappers; seasons 2 and 3 hold
        # id-only stubs, so only season 1 can produce offers.
        offers = self._offers("4258")
        self.assertEqual(len(offers), 5)
        self.assertEqual({o.season_number for o in offers if o.content_type
                          == ContentType.SEASON}, {1})
        self.assertEqual(
            {o.episode_number for o in offers if o.content_type == ContentType.EPISODE},
            {2, 4, 5},
        )
        series = [o for o in offers if o.content_type == ContentType.SERIES][0]
        self.assertEqual(series.available_until,
                         dt.datetime(2027, 8, 1, 3, 59, 59, tzinfo=dt.timezone.utc))
        self.assertEqual(series.subtitle_languages, ["EN"])

    def test_a_series_with_no_observable_episode_yields_a_content_row_only(self):
        # Listed on the catalogue, nothing watchable observable on the page.
        self.assertEqual(len(self._contents("1076")), 1)
        self.assertEqual(self._offers("1076"), [])

    def test_every_offer_is_free_with_ads_from_the_us(self):
        for offer in self.result.offers:
            self.assertEqual(offer.offer_type, OfferType.FREE_WITH_ADS)
            self.assertEqual(offer.country, "US")
            self.assertIsNone(offer.tmdb_id)


class FailureVsEmptyTests(SimpleTestCase):
    def _walk(self, source, api, cursor=None):
        before = len(api.detail_requests)
        result = source.fetch(cursor=cursor)
        return result, api.detail_requests[before:]

    def test_a_shell_page_is_skipped_without_failing_the_walk(self):
        source, api = build_source()
        with self.assertLogs("availability.sources.tubi", level="INFO") as logs:
            result = source.fetch()
        self.assertIn("tubi_content_missing", "\n".join(logs.output))
        self.assertIn(CLEARCUT, api.detail_requests)
        # The stale/shelled title is simply not observable: the rest still is.
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)
        self.assertNotIn("654137", {c.provider_content_id for c in result.contents})

    def test_a_page_without_the_state_marker_is_a_failure(self):
        source, api = build_source()
        api.pages[I_ROBOT] = "<!DOCTYPE html><html><body>consent</body></html>"
        with self.assertRaisesMessage(SourceFetchError, "no window.__data state"):
            source.fetch()

    def test_an_undecodable_state_is_a_failure(self):
        source, api = build_source()
        api.pages[I_ROBOT] = api.pages[I_ROBOT].split("window.__data=", 1)[0] + \
            'window.__data={"video":'
        with self.assertRaisesMessage(SourceFetchError, "Unterminated"):
            source.fetch()

    def test_a_requester_failure_is_a_failure(self):
        source, api = build_source()
        api.failures["i-robot"] = requests.ConnectionError("boom")
        with self.assertRaisesMessage(SourceFetchError, "boom"):
            source.fetch()

    def test_a_source_fetch_error_from_the_requester_propagates_unchanged(self):
        source, api = build_source()
        api.failures["i-robot"] = SourceFetchError("upstream 500")
        with self.assertRaisesMessage(SourceFetchError, "upstream 500"):
            source.fetch()

    def test_an_unknown_url_is_a_failure_rather_than_an_empty_page(self):
        source, _api = build_source()
        with self.assertRaisesMessage(SourceFetchError, "fixture missing"):
            source.client.detail_page("https://tubitv.com/movies/1/nope")

    def test_a_permanently_gone_traversal_page_is_skipped_and_the_batch_advances(self):
        """The 2026-10-21 deadlock: a stale 404 must not block the checkpoint."""
        source, api = build_source(max_contents=2)
        api.failures["i-robot"] = TubiPageGone(
            "Tubi returned HTTP 404 for i-robot", status_code=404
        )

        with self.assertLogs("availability.sources.tubi", level="WARNING") as logs:
            first, first_batch = self._walk(source, api)

        self.assertIn("tubi_page_gone", "\n".join(logs.output))
        self.assertEqual(first_batch, [I_ROBOT, BLUE_WATER_HIGH])
        self.assertEqual(
            {c.provider_content_id for c in first.contents}, {"2185"},
            "the rest of the batch is still collected",
        )
        self.assertIsNotNone(first.cursor, "the checkpoint must still advance")

        second, second_batch = self._walk(source, api, first.cursor)
        self.assertEqual(second_batch, [CLEARCUT, ONE_EIGHT_HUNDRED])
        self.assertNotEqual(first.cursor, second.cursor)
        self.assertTrue(second.contents, "later pages are still collected")

    def test_a_410_page_is_also_treated_as_permanently_gone(self):
        source, api = build_source(max_contents=1)
        api.failures["i-robot"] = TubiPageGone(
            "Tubi returned HTTP 410 for i-robot", status_code=410
        )
        with self.assertLogs("availability.sources.tubi", level="WARNING") as logs:
            result, batch = self._walk(source, api)
        self.assertIn("tubi_page_gone", "\n".join(logs.output))
        self.assertEqual(batch, [I_ROBOT])
        self.assertIsNotNone(result.cursor)

    def test_a_404_is_classified_as_gone_but_a_5xx_is_not(self):
        class Response:
            headers = {"Content-Type": "text/html"}
            text = "<html>gone</html>"
            content = b"<html>gone</html>"

            def __init__(self, status_code):
                self.status_code = status_code

        from unittest.mock import patch

        client = TubiClient(delay=0, retries=0)
        with patch("availability.sources.tubi.requests.get",
                   return_value=Response(404)):
            with self.assertRaises(TubiPageGone) as ctx:
                client.detail_page(I_ROBOT)
        self.assertEqual(ctx.exception.status_code, 404)

        with patch("availability.sources.tubi.requests.get",
                   return_value=Response(503)):
            with self.assertRaises(SourceFetchError) as ctx:
                client.detail_page(I_ROBOT)
        self.assertNotIsInstance(ctx.exception, TubiPageGone)

    def test_a_title_missing_from_the_state_is_skipped(self):
        source, api = build_source()
        gone = "https://tubitv.com/movies/999999/gone-with-the-entries"
        api.sitemaps[MOVIES_1] = api.sitemaps[MOVIES_1].replace(
            "</urlset>", url_entry(gone) + "</urlset>"
        )
        api.pages[gone] = api.pages[I_ROBOT]
        with self.assertLogs("availability.sources.tubi", level="WARNING") as logs:
            result = source.fetch()
        self.assertIn("entry_not_in_state", "\n".join(logs.output))
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)
        self.assertIn(gone, api.detail_requests)

    def test_a_type_mismatch_between_url_and_state_is_skipped(self):
        source, api = build_source()
        api.pages[PERMISSION] = api.pages[PERMISSION].replace(
            '"detailed_type":"movie"', '"detailed_type":"series"'
        )
        with self.assertLogs("availability.sources.tubi", level="WARNING") as logs:
            result = source.fetch()
        self.assertIn("type_mismatch", "\n".join(logs.output))
        self.assertNotIn("582004", {c.provider_content_id for c in result.contents})
        self.assertEqual(len(result.offers), TOTAL_OFFERS - 1)


class OfferWindowTests(SimpleTestCase):
    def test_an_offer_whose_window_has_closed_is_skipped(self):
        source, api = build_source()
        api.pages[I_ROBOT] = api.pages[I_ROBOT].replace(
            "2035-10-15T06:59:00.000Z", "2020-01-01T00:00:00.000Z"
        )
        with self.assertLogs("availability.sources.tubi", level="INFO") as logs:
            result = source.fetch()
        self.assertIn("tubi_offer_expired", "\n".join(logs.output))
        # The catalogue row still stands; only the offer goes away.
        self.assertIn("578912", {c.provider_content_id for c in result.contents})
        self.assertEqual(
            [o for o in result.offers if o.provider_content_id == "578912"], []
        )
        self.assertEqual(len(result.offers), TOTAL_OFFERS - 1)

    def test_an_offer_that_has_not_opened_yet_is_skipped(self):
        source, api = build_source()
        api.pages[I_ROBOT] = api.pages[I_ROBOT].replace(
            "2026-09-15T07:00:00.000Z", "2030-01-01T00:00:00.000Z"
        )
        with self.assertLogs("availability.sources.tubi", level="INFO") as logs:
            result = source.fetch()
        self.assertIn("tubi_offer_not_yet_available", "\n".join(logs.output))
        self.assertEqual(
            [o for o in result.offers if o.provider_content_id == "578912"], []
        )


class CatalogueBoundTests(SimpleTestCase):
    def test_a_capped_walk_mixes_movies_and_series_and_logs_the_truncation(self):
        source, api = build_source(max_contents=2)
        with self.assertLogs("availability.sources.tubi", level="WARNING") as logs:
            result = source.fetch()
        self.assertIn("tubi_catalogue_truncated", "\n".join(logs.output))
        # Round-robin across sitemaps, so a small cap is not all movies.
        self.assertEqual(api.detail_requests, [I_ROBOT, BLUE_WATER_HIGH])
        self.assertEqual(
            {c.content_type for c in result.contents},
            {ContentType.MOVIE, ContentType.SERIES},
        )

    def test_no_bound_walks_every_unique_title_entry(self):
        source, api = build_source(max_contents=None)
        source.fetch()
        self.assertEqual(len(api.detail_requests), len(set(api.detail_requests)))
        self.assertEqual(
            set(api.detail_requests),
            {I_ROBOT, BLUE_WATER_HIGH, CLEARCUT, ONE_EIGHT_HUNDRED,
             PERMISSION, ANCIENT_ALIENS},
        )

    def test_everything_shelled_is_logged_loudly_but_still_not_a_failure(self):
        source, api = build_source()
        shell = api.pages[CLEARCUT]
        for url in (I_ROBOT, BLUE_WATER_HIGH, ONE_EIGHT_HUNDRED, PERMISSION,
                    ANCIENT_ALIENS):
            api.pages[url] = shell
        with self.assertLogs("availability.sources.tubi", level="WARNING") as logs:
            result = source.fetch()
        self.assertIn("tubi_catalogue_nothing_observed", "\n".join(logs.output))
        self.assertEqual(result.contents, [])
        self.assertEqual(result.offers, [])
        self.assertFalse(result.authoritative)


class StateDecodingTests(SimpleTestCase):
    def test_the_recorded_shell_reports_nothing_observable(self):
        shell = FakeTubiApi().pages[CLEARCUT]
        self.assertIsNone(_state_from_html(shell, url=CLEARCUT))

    def test_js_literal_values_are_decoded(self):
        html = (
            '<script>window.__data={"video":{"byId":{"02185":{"id":"02185",'
            '"detailed_type":"series","year":2005,"missing":undefined,'
            '"at":new Date("2025-08-07T23:05:18.636Z"),"score":-Infinity}}},'
            '"ui":{"sponsorship":undefined,}};</script>'
        )
        state = _state_from_html(html, url="synthetic")
        entry = state["video"]["byId"]["02185"]
        self.assertIsNone(entry["missing"])
        self.assertEqual(entry["at"], "2025-08-07T23:05:18.636Z")
        self.assertIsNone(entry["score"])
        self.assertIsNone(state["ui"]["sponsorship"])

    def test_a_bare_identifier_we_cannot_vouch_for_is_a_failure(self):
        html = '<script>window.__data={"video":{"byId":{"a":{}},"x":oops}};</script>'
        with self.assertRaisesMessage(SourceFetchError, "Unsupported value"):
            _state_from_html(html, url="synthetic")

    def test_a_state_without_by_id_is_a_failure(self):
        with self.assertRaisesMessage(SourceFetchError, "video.byId"):
            _state_from_html('<script>window.__data={"video":{}};</script>', url="x")

    def test_a_missing_marker_is_a_failure_not_an_empty_state(self):
        with self.assertRaisesMessage(SourceFetchError, "no window.__data state"):
            _state_from_html("<!DOCTYPE html><html></html>", url="x")
