"""Season-aware availability for series (Phase 3).

Three layers:
- upstream payload parsing + season-number resolution (no network, no DB);
- cache write and season-filtered read of ``Availability.seasons_available``;
- the series detail endpoint end-to-end: ``?season=N`` in, filtered offers out.
"""
import datetime as dt
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from tracker.models import (
    Availability,
    Movie,
    Provider,
    ScrapedContentMapping,
    Subscription,
)
from tracker.services.cache import AvailabilityCache
from tracker.services.offers import Offer
from tracker.services.rate_limit import ApiCallCounter, RateLimiter
from tracker.services.streaming_api import (
    StreamingAvailabilityClient,
    StreamingOffer,
    StreamingResult,
    StreamingSeason,
    resolve_season_number,
)
from tracker.tests.availability_factories import local_offer

SEASONS_DATA = [
    {"season_number": 1, "name": "Season 1", "episode_count": 10, "poster_path": "/s1.jpg"},
    {"season_number": 2, "name": "Season 2", "episode_count": 8, "poster_path": "/s2.jpg"},
    {"season_number": 3, "name": "Season 3", "episode_count": 6, "poster_path": "/s3.jpg"},
]


def _option(offer_type="subscription", service_id="prime", service_name="Prime Video"):
    """A v4 season/show streaming option (same shape at both levels)."""
    return {
        "type": offer_type,
        "quality": "hd",
        "link": "https://example.com/watch",
        "service": {"id": service_id, "name": service_name},
        "audios": [{"language": "eng", "region": "USA"}],
        "subtitles": [{"closedCaptions": False, "locale": {"language": "eng", "region": "USA"}}],
    }


def _offer(country="US", service_id="prime", service_name="Prime Video", offer_type="subscription"):
    return StreamingOffer(
        country=country,
        service_id=service_id,
        service_name=service_name,
        offer_type=offer_type,
        quality="hd",
        audio_languages=["EN"],
        subtitle_languages=["EN"],
    )


class SeasonPayloadParsingTests(SimpleTestCase):
    """``seasons[]`` parsing — movie payloads, option shape, malformed input."""

    def test_movie_payload_has_no_seasons(self):
        self.assertEqual(StreamingAvailabilityClient._parse_seasons({}), [])
        self.assertEqual(StreamingAvailabilityClient._parse_seasons({"title": "Film"}), [])
        self.assertEqual(StreamingAvailabilityClient._parse_seasons({"seasons": None}), [])

    def test_series_seasons_keep_titles_and_parsed_options(self):
        payload = {
            "seasons": [
                {
                    "itemType": "season",
                    "title": "Season 1",
                    "streamingOptions": {
                        "us": [
                            _option(),
                            _option(offer_type="rent"),
                            _option(offer_type="buy"),
                        ],
                        "it": [_option(service_id="netflix", service_name="Netflix")],
                    },
                },
                {"title": "Series 2", "streamingOptions": {"us": [_option()]}},
            ]
        }
        seasons = StreamingAvailabilityClient._parse_seasons(payload)

        self.assertEqual([s.title for s in seasons], ["Season 1", "Series 2"])
        # No upstream season number in v4; callers resolve it from the title.
        self.assertEqual([s.number for s in seasons], [None, None])

        first = seasons[0]
        self.assertEqual(
            [(o.country, o.service_id, o.offer_type) for o in first.offers],
            # rent/buy filtered out at parse time, exactly like show-level offers.
            [("US", "prime", "subscription"), ("IT", "netflix", "subscription")],
        )
        self.assertEqual(first.offers[0].audio_languages, ["EN"])
        self.assertEqual(first.offers[0].subtitle_languages, ["EN"])
        self.assertEqual(first.offers[0].link, "https://example.com/watch")

    def test_explicit_season_number_is_taken_from_the_payload(self):
        seasons = StreamingAvailabilityClient._parse_seasons(
            {"seasons": [{"number": 4}, {"seasonNumber": 5}]}
        )
        self.assertEqual([s.number for s in seasons], [4, 5])

    def test_malformed_season_entries_never_raise(self):
        seasons = StreamingAvailabilityClient._parse_seasons(
            {"seasons": [None, 7, [], {"streamingOptions": None}, {"title": 123}]}
        )
        # Non-dicts are dropped; the option-less and numeric-title entries stay
        # harmless instead of raising.
        self.assertEqual(len(seasons), 2)
        self.assertEqual(seasons[0].title, "")
        self.assertEqual(seasons[0].offers, [])
        self.assertEqual(seasons[1].title, "123")


class ResolveSeasonNumberTests(SimpleTestCase):
    """Title → TMDb ``season_number`` mapping."""

    @staticmethod
    def _season(title, number=None):
        return StreamingSeason(title=title, number=number)

    def test_trailing_number_of_the_title(self):
        self.assertEqual(resolve_season_number(self._season("Season 2"), []), 2)
        self.assertEqual(resolve_season_number(self._season("Series 10"), []), 10)
        self.assertEqual(resolve_season_number(self._season("Season 2"), SEASONS_DATA), 2)

    def test_exact_match_against_tmdb_season_names(self):
        data = [{"season_number": 13, "name": "Flux"}]
        self.assertEqual(resolve_season_number(self._season("Flux"), data), 13)
        self.assertEqual(resolve_season_number(self._season("flux"), data), 13)

    def test_specials_title_maps_to_season_zero(self):
        self.assertEqual(resolve_season_number(self._season("Specials"), []), 0)
        # TMDb does not list a specials season here, so it is not a match.
        self.assertIsNone(
            resolve_season_number(self._season("Specials"), SEASONS_DATA)
        )

    def test_explicit_number_wins(self):
        self.assertEqual(resolve_season_number(self._season("Season 9", number=7), SEASONS_DATA), 7)

    def test_title_without_signal_returns_none(self):
        self.assertIsNone(resolve_season_number(self._season("The Beginning"), []))
        self.assertIsNone(resolve_season_number(self._season(""), SEASONS_DATA))

    def test_number_absent_from_tmdb_season_list_is_rejected(self):
        self.assertIsNone(resolve_season_number(self._season("Season 9"), SEASONS_DATA))
        # Without TMDb metadata there is nothing to contradict the title.
        self.assertEqual(resolve_season_number(self._season("Season 9"), []), 9)


class SeasonCacheTests(TestCase):
    """``seasons_available`` write + season-filtered ``read_offers``."""

    @staticmethod
    def _result(with_seasons=True):
        show_offers = [
            _offer(service_id="prime", service_name="Prime Video"),
            _offer(service_id="hulu", service_name="Hulu"),
        ]
        seasons = []
        if with_seasons:
            seasons = [
                StreamingSeason(
                    title="Season 1",
                    offers=[
                        _offer(service_id="prime", service_name="Prime Video"),
                        _offer(service_id="netflix", service_name="Netflix"),
                    ],
                ),
                StreamingSeason(
                    title="Season 2",
                    offers=[_offer(service_id="netflix", service_name="Netflix")],
                ),
                # Not a season TMDb knows about — must never be persisted.
                StreamingSeason(title="Season 9", offers=[_offer(service_id="prime")]),
            ]
        return StreamingResult(tmdb_id=1396, offers=show_offers, seasons=seasons)

    def setUp(self):
        self.series = Movie.objects.create(
            tmdb_id=1396,
            title="Game of Thrones",
            year=2011,
            content_type="series",
            seasons_data=SEASONS_DATA,
        )
        self.upstream = MagicMock()
        self.upstream.get_availability.return_value = self._result()
        self.cache = AvailabilityCache(
            client=self.upstream, limiter=RateLimiter("test_seasons")
        )
        ApiCallCounter.objects.filter(service="test_seasons").delete()

    def test_refresh_persists_seasons_per_region(self):
        status = self.cache.get_or_refresh(self.series)
        self.assertTrue(status.refreshed)

        prime = Availability.objects.get(
            movie=self.series, region__provider="prime", region__country="US"
        )
        # Season 9 was dropped: it is not in Movie.seasons_data.
        self.assertEqual(prime.seasons_available, [1])

        netflix = Availability.objects.get(
            movie=self.series, region__provider="netflix", region__country="US"
        )
        # Season-only region: Netflix has no show-level option but streams both.
        self.assertEqual(netflix.seasons_available, [1, 2])

        hulu = Availability.objects.get(
            movie=self.series, region__provider="hulu", region__country="US"
        )
        self.assertEqual(hulu.seasons_available, [])

    def test_read_offers_filters_by_season(self):
        self.cache.get_or_refresh(self.series)

        self.assertEqual(
            {o.service for o in self.cache.read_offers(self.series, season=1)},
            {"prime", "netflix"},
        )
        self.assertEqual(
            {o.service for o in self.cache.read_offers(self.series, season=2)},
            {"netflix"},
        )
        self.assertEqual(self.cache.read_offers(self.series, season=9), [])

        # Unfiltered read is untouched by season logic.
        self.assertEqual(
            {o.service for o in self.cache.read_offers(self.series)},
            {"prime", "netflix", "hulu"},
        )

    def test_season_view_excludes_scraped_offers(self):
        provider = Provider.objects.create(
            slug="hydra", name="Hydra", category="scraper", is_enabled=True
        )
        ScrapedContentMapping.objects.create(
            provider=provider,
            movie=self.series,
            scraped_title="Game of Thrones",
            scraped_year=2011,
            scraped_url="https://hydra.example/got",
            is_available=True,
        )
        self.cache.get_or_refresh(self.series)

        unfiltered = {o.service for o in self.cache.read_offers(self.series)}
        self.assertIn("hydra", unfiltered)

        for season in (1, 2, 3):
            self.assertNotIn(
                "hydra",
                {o.service for o in self.cache.read_offers(self.series, season=season)},
                f"scraper row leaked into season {season} view",
            )

    def test_refresh_without_seasons_clears_stale_values(self):
        self.cache.get_or_refresh(self.series)
        self.assertTrue(
            Availability.objects.get(
                movie=self.series, region__provider="prime"
            ).seasons_available
        )

        self.upstream.get_availability.return_value = self._result(with_seasons=False)
        Availability.objects.update(
            movie=self.series, last_checked_at=timezone.now() - dt.timedelta(days=2)
        )
        status = self.cache.get_or_refresh(self.series)
        self.assertTrue(status.refreshed)

        prime = Availability.objects.get(
            movie=self.series, region__provider="prime"
        )
        self.assertEqual(prime.seasons_available, [])

    def test_cached_series_answer_is_not_rewritten_when_fresh(self):
        self.cache.get_or_refresh(self.series)
        self.cache.get_or_refresh(self.series)
        self.assertEqual(self.upstream.get_availability.call_count, 1)


class SeasonDetailEndpointTests(TestCase):
    """``/api/prime/series/<id>/?season=N`` payload shape and season plumbing."""

    def setUp(self):
        reads_patcher = patch("tracker.services.offer_pipeline.get_local_offers")
        enrich_patcher = patch("tracker.services.offer_pipeline.enrich_movie_from_tmdb")
        self.addCleanup(reads_patcher.stop)
        self.addCleanup(enrich_patcher.stop)
        mock_reads = reads_patcher.start()
        enrich_patcher.start()
        self.read_offers = mock_reads

        self.series = Movie.objects.create(
            tmdb_id=1396,
            title="Game of Thrones",
            year=2011,
            content_type="series",
            seasons_data=SEASONS_DATA + ["not-a-dict", {"name": "No number"}],
        )
        Subscription.objects.create(
            user_id=1, provider=Provider.objects.get(slug="prime"), name="Prime Video"
        )
        self.offer = Offer(
            country="US",
            service="prime",
            offer_type="subscription",
            quality="hd",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )
        self.read_offers.return_value = [self.offer]

    def test_no_season_param_reads_show_level_offers(self):
        response = self.client.get("/api/prime/series/1396/")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.read_offers.assert_called_once_with(self.series, season=None)
        self.assertEqual(len(data["offers"]), 1)
        self.assertIsNone(data["selected_season"])
        self.assertEqual([row["season"] for row in data["seasons"]], [1, 2, 3])
        self.assertEqual(data["seasons"][0]["name"], "Season 1")
        self.assertEqual(data["seasons"][0]["episode_count"], 10)
        self.assertEqual(data["seasons"][0]["poster_path"], "/s1.jpg")

    def test_season_param_narrows_the_offers_read(self):
        response = self.client.get("/api/prime/series/1396/?season=2")
        self.assertEqual(response.status_code, 200)
        self.read_offers.assert_called_once_with(self.series, season=2)
        self.assertEqual(response.json()["selected_season"], 2)

    def test_invalid_season_value_is_ignored(self):
        self.client.get("/api/prime/series/1396/?season=abc")
        self.client.get("/api/prime/series/1396/?season=")
        recorded = self.read_offers.call_args_list
        self.assertEqual([c.kwargs for c in recorded], [{"season": None}, {"season": None}])
        for one_call in recorded:
            self.assertEqual(one_call.args, (self.series,))

    def test_unknown_season_returns_empty_offers_but_echoes_selection(self):
        self.read_offers.return_value = []
        response = self.client.get("/api/prime/series/1396/?season=99")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.read_offers.assert_called_once_with(self.series, season=99)
        self.assertEqual(data["offers"], [])
        self.assertEqual(data["selected_season"], 99)

    def test_movie_endpoint_ignores_season_param_and_keeps_its_shape(self):
        movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)

        response = self.client.get("/api/prime/movie/550/?season=2")

        self.assertEqual(response.status_code, 200)
        self.read_offers.assert_called_once_with(movie, season=None)
        data = response.json()
        self.assertNotIn("seasons", data)
        self.assertNotIn("selected_season", data)
        self.assertEqual(len(data["offers"]), 1)


class SeasonEndToEndEndpointTests(TestCase):
    """Request → pipeline → canonical offer reads → ranking and season filtering."""

    @patch("tracker.services.streaming_api.StreamingAvailabilityClient.get_availability")
    @patch("tracker.services.offer_pipeline.enrich_movie_from_tmdb")
    def test_season_requests_filter_canonical_offers_without_upstream_calls(
        self, mock_enrich, mock_get
    ):
        series = Movie.objects.create(
            tmdb_id=1396,
            title="Game of Thrones",
            year=2011,
            content_type="series",
            seasons_data=SEASONS_DATA,
        )
        Subscription.objects.create(
            user_id=1, provider=Provider.objects.get(slug="prime"), name="Prime Video"
        )
        Subscription.objects.create(
            user_id=1, provider=Provider.objects.get(slug="netflix"), name="Netflix"
        )
        Subscription.objects.create(
            user_id=1, provider=Provider.objects.get(slug="hulu"), name="Hulu"
        )
        # Canonical rows: Prime streams season 1, Netflix streams seasons 1-2,
        # Hulu streams season 3 only (mirrors the upstream payload shape).
        for service, season in (("prime", 1), ("netflix", 1), ("netflix", 2), ("hulu", 3)):
            local_offer(
                series,
                country="US",
                service=service,
                season_number=season,
                audio_languages=["EN"],
                subtitle_languages=["EN"],
            )

        def services_for(url):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            return {offer["service"] for offer in response.json()["offers"]}

        unfiltered = services_for("/api/prime/series/1396/")
        season_one = services_for("/api/prime/series/1396/?season=1")
        season_two = services_for("/api/prime/series/1396/?season=2")
        season_nine = services_for("/api/prime/series/1396/?season=9")

        # Show-level (unfiltered) reads see every canonical series row.
        self.assertEqual(unfiltered, {"Prime Video", "Netflix", "Hulu"})
        self.assertEqual(season_one, {"Prime Video", "Netflix"})
        # Season 2 is Netflix-only: Prime and Hulu drop out.
        self.assertEqual(season_two, {"Netflix"})
        self.assertEqual(season_nine, set())

        # Reads never fall through to the upstream API.
        mock_get.assert_not_called()

        payload = self.client.get("/api/prime/series/1396/?season=2").json()
        self.assertEqual(payload["selected_season"], 2)
        self.assertEqual([row["season"] for row in payload["seasons"]], [1, 2, 3])
        self.assertEqual(payload["series"]["tmdb_id"], 1396)


class SeriesSubscriptionFilterTests(TestCase):
    """Canonical rows for a series whose services the user does not subscribe to.

    Regression guard for the reported "not available" outcome: an empty
    ``offers`` list must come from the subscription/provider filters, never
    from a series-only drop. The series payload has to stay complete so the
    page can render its empty state, and one subscribed row in the same set
    must still be surfaced.
    """

    def setUp(self):
        reads_patcher = patch("tracker.services.offer_pipeline.get_local_offers")
        enrich_patcher = patch("tracker.services.offer_pipeline.enrich_movie_from_tmdb")
        self.addCleanup(reads_patcher.stop)
        self.addCleanup(enrich_patcher.stop)
        mock_reads = reads_patcher.start()
        enrich_patcher.start()
        self.read_offers = mock_reads

        self.series = Movie.objects.create(
            tmdb_id=90001,
            title="Some Series",
            year=2005,
            content_type="series",
            seasons_data=SEASONS_DATA,
        )
        Subscription.objects.create(
            user_id=1, provider=Provider.objects.get(slug="prime"), name="Prime Video"
        )
        # The shape the cache really holds for a series the user has no
        # subscription for: services outside the subscription list, one of
        # them (skyshowtime) with no Provider row at all.
        self.unsubscribed = [
            Offer(country=country, service=service, offer_type="subscription",
                  quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])
            for country, service in (("NL", "netflix"), ("NL", "disney"), ("NL", "skyshowtime"))
        ]

    def test_unsubscribed_rows_yield_empty_offers_but_full_series_payload(self):
        self.read_offers.return_value = self.unsubscribed

        response = self.client.get("/api/prime/series/90001/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.read_offers.assert_called_once_with(self.series, season=None)
        self.assertEqual(data["offers"], [])
        self.assertIsNone(data["selected_season"])
        self.assertEqual(data["series"]["tmdb_id"], 90001)
        self.assertEqual(data["series"]["title"], "Some Series")
        self.assertEqual([row["season"] for row in data["seasons"]], [1, 2, 3])

    def test_a_subscribed_prime_row_in_the_same_cache_is_still_returned(self):
        self.read_offers.return_value = self.unsubscribed + [
            Offer(country="AT", service="prime", offer_type="subscription",
                  quality="hd", audio_languages=["EN"], subtitle_languages=["EN"]),
        ]

        response = self.client.get("/api/prime/series/90001/")

        self.assertEqual(response.status_code, 200)
        services = {offer["service"] for offer in response.json()["offers"]}
        self.assertEqual(services, {"Prime Video"})
