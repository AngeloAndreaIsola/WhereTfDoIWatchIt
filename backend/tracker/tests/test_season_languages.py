"""Season-level language data: parse → persist → read → score (Phase 3).

Regression tests for series language detection. Each season of a series can
carry its own audio/subtitle lists upstream; those lists must survive the
cache round-trip keyed per (movie, region, season), be returned by
``read_offers(movie, season=N)``, and reach the language matcher — without
collapsing into the show-level row or leaking between seasons. Movies keep
the show-level-only behavior they always had.
"""
from unittest.mock import MagicMock, patch

import datetime as dt
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from tracker.models import (
    Availability,
    LanguageEntry,
    LanguageInfo,
    Movie,
    Preference,
    Provider,
    Region,
    Subscription,
)
from tracker.services.cache import AvailabilityCache
from tracker.services.offer_pipeline import OfficialOffersPipeline
from tracker.services.offers import Offer
from tracker.services.rate_limit import ApiCallCounter, RateLimiter
from tracker.services.score import score_offer
from tracker.services.streaming_api import (
    StreamingAvailabilityClient,
    StreamingOffer,
    StreamingResult,
    StreamingSeason,
)
from tracker.tests.availability_factories import local_offer

SEASONS_DATA = [
    {"season_number": 1, "name": "Season 1", "episode_count": 10, "poster_path": "/s1.jpg"},
    {"season_number": 2, "name": "Season 2", "episode_count": 8, "poster_path": "/s2.jpg"},
]


def _offer(country="NL", service_id="prime", audio=("EN",), subs=("EN",)):
    return StreamingOffer(
        country=country,
        service_id=service_id,
        service_name=service_id,
        offer_type="subscription",
        quality="hd",
        audio_languages=list(audio),
        subtitle_languages=list(subs),
    )


class SeasonLanguageParsingTests(SimpleTestCase):
    """Each season of the payload parses its own audio/subtitle lists."""

    def test_each_season_parses_its_own_languages(self):
        payload = {
            "seasons": [
                {
                    "title": "Season 1",
                    "streamingOptions": {
                        "nl": [
                            {
                                "type": "subscription",
                                "quality": "hd",
                                "service": {"id": "prime", "name": "Prime Video"},
                                "audios": [{"language": "eng"}, {"language": "nld"}],
                                "subtitles": [{"locale": {"language": "eng"}}],
                            },
                            # Excluded at parse time, exactly like show-level.
                            {"type": "rent", "service": {"id": "prime", "name": "Prime Video"}},
                        ],
                    },
                },
                {
                    "title": "Season 2",
                    "streamingOptions": {
                        "nl": [
                            {
                                "type": "subscription",
                                "quality": "sd",
                                "service": {"id": "prime", "name": "Prime Video"},
                                "audios": [{"language": "deu"}],
                                "subtitles": [{"locale": {"language": "deu"}}],
                            },
                        ],
                    },
                },
            ]
        }

        seasons = StreamingAvailabilityClient._parse_seasons(payload)

        self.assertEqual(len(seasons), 2)
        first, second = seasons
        self.assertEqual(first.offers[0].audio_languages, ["EN", "NL"])
        self.assertEqual(first.offers[0].subtitle_languages, ["EN"])
        self.assertEqual(second.offers[0].audio_languages, ["DE"])
        self.assertEqual(second.offers[0].subtitle_languages, ["DE"])


class SeasonLanguageCacheTests(TestCase):
    """Season-level languages persist per (movie, region, season) and are read back."""

    @staticmethod
    def _result(with_seasons=True):
        # Show-level: EN audio/subs. Season 1: EN audio, EN+NL subs.
        # Season 2: DE audio/subs — a deliberately different language set.
        show = [_offer(audio=("EN",), subs=("EN",))]
        seasons = []
        if with_seasons:
            seasons = [
                StreamingSeason(
                    title="Season 1",
                    offers=[_offer(audio=("EN",), subs=("EN", "NL"))],
                ),
                StreamingSeason(
                    title="Season 2",
                    offers=[_offer(audio=("DE",), subs=("DE",))],
                ),
            ]
        return StreamingResult(tmdb_id=2316, offers=show, seasons=seasons)

    def setUp(self):
        self.series = Movie.objects.create(
            tmdb_id=2316,
            title="The Office",
            year=2005,
            content_type="series",
            seasons_data=SEASONS_DATA,
        )
        self.upstream = MagicMock()
        self.upstream.get_availability.return_value = self._result()
        self.cache = AvailabilityCache(
            client=self.upstream, limiter=RateLimiter("test_season_langs")
        )
        ApiCallCounter.objects.filter(service="test_season_langs").delete()

    def test_season_level_languages_are_persisted_per_season(self):
        self.cache.get_or_refresh(self.series)

        region = Region.objects.get(provider="prime", country="NL")
        season_one = LanguageInfo.objects.get(
            movie=self.series, region=region, season=1
        )
        season_two = LanguageInfo.objects.get(
            movie=self.series, region=region, season=2
        )
        show_level = LanguageInfo.objects.get(
            movie=self.series, region=region, season=None
        )

        def codes(info, kind):
            return [
                e.language_code
                for e in info.entries.filter(kind=kind).order_by("id")
            ]

        self.assertEqual(codes(season_one, "audio"), ["EN"])
        self.assertEqual(codes(season_one, "subtitle"), ["EN", "NL"])
        self.assertEqual(codes(season_two, "audio"), ["DE"])
        self.assertEqual(codes(season_two, "subtitle"), ["DE"])
        # The show-level row keeps the show-level languages untouched.
        self.assertEqual(codes(show_level, "audio"), ["EN"])
        self.assertEqual(codes(show_level, "subtitle"), ["EN"])

    def test_read_offers_returns_the_selected_seasons_languages(self):
        self.cache.get_or_refresh(self.series)

        season_one = {
            o.service: o for o in self.cache.read_offers(self.series, season=1)
        }["prime"]
        self.assertEqual(season_one.audio_languages, ["EN"])
        self.assertEqual(season_one.subtitle_languages, ["EN", "NL"])

        season_two = {
            o.service: o for o in self.cache.read_offers(self.series, season=2)
        }["prime"]
        self.assertEqual(season_two.audio_languages, ["DE"])
        self.assertEqual(season_two.subtitle_languages, ["DE"])

        # The unfiltered view keeps the show-level languages (pre-fix shape).
        unfiltered = {
            o.service: o for o in self.cache.read_offers(self.series)
        }["prime"]
        self.assertEqual(unfiltered.audio_languages, ["EN"])
        self.assertEqual(unfiltered.subtitle_languages, ["EN"])

    def test_two_seasons_do_not_cross_contaminate(self):
        self.cache.get_or_refresh(self.series)

        season_one = {
            o.service: o for o in self.cache.read_offers(self.series, season=1)
        }["prime"]
        season_two = {
            o.service: o for o in self.cache.read_offers(self.series, season=2)
        }["prime"]

        self.assertNotEqual(season_one.audio_languages, season_two.audio_languages)
        self.assertNotIn("DE", season_one.audio_languages)
        self.assertNotIn("EN", season_two.audio_languages)
        # Season rows exist independently for the same (movie, region).
        region = Region.objects.get(provider="prime", country="NL")
        self.assertEqual(
            set(
                LanguageInfo.objects.filter(
                    movie=self.series, region=region
                ).values_list("season", flat=True)
            ),
            {1, 2, None},
        )

    def test_season_read_falls_back_to_show_level_when_no_season_row(self):
        """Legacy caches (pre-migration) only have show-level rows."""
        self.cache.get_or_refresh(self.series)
        LanguageInfo.objects.filter(
            movie=self.series, season__isnull=False
        ).delete()

        fallback = {
            o.service: o for o in self.cache.read_offers(self.series, season=1)
        }["prime"]

        self.assertEqual(fallback.audio_languages, ["EN"])
        self.assertEqual(fallback.subtitle_languages, ["EN"])

    def test_refresh_without_seasons_drops_stale_season_language_rows(self):
        self.cache.get_or_refresh(self.series)
        self.assertTrue(
            LanguageInfo.objects.filter(
                movie=self.series, season__isnull=False
            ).exists()
        )

        self.upstream.get_availability.return_value = self._result(
            with_seasons=False
        )
        Availability.objects.update(
            movie=self.series,
            last_checked_at=timezone.now() - dt.timedelta(days=2),
        )
        status = self.cache.get_or_refresh(self.series)
        self.assertTrue(status.refreshed)

        self.assertFalse(
            LanguageInfo.objects.filter(
                movie=self.series, season__isnull=False
            ).exists()
        )
        # Show-level rows survive the refresh.
        self.assertTrue(
            LanguageInfo.objects.filter(
                movie=self.series, season=None
            ).exists()
        )


class SeasonLanguageScoringTests(TestCase):
    """The language matcher evaluates the selected season's own languages."""

    @staticmethod
    def _result():
        show = [_offer(audio=("EN",), subs=("EN",))]
        seasons = [
            StreamingSeason(title="Season 1", offers=[_offer(audio=("EN",), subs=("EN",))]),
            StreamingSeason(title="Season 2", offers=[_offer(audio=("DE",), subs=("DE",))]),
        ]
        return StreamingResult(tmdb_id=2316, offers=show, seasons=seasons)

    def setUp(self):
        self.series = Movie.objects.create(
            tmdb_id=2316,
            title="The Office",
            year=2005,
            content_type="series",
            seasons_data=SEASONS_DATA,
        )
        self.upstream = MagicMock()
        self.upstream.get_availability.return_value = self._result()
        self.cache = AvailabilityCache(
            client=self.upstream, limiter=RateLimiter("test_season_score")
        )
        ApiCallCounter.objects.filter(service="test_season_score").delete()
        self.cache.get_or_refresh(self.series)

    @staticmethod
    def _preference(language_rule=None):
        return Preference(
            user_id=1,
            audio_languages=[],
            subtitle_languages=[],
            candidate_countries=[],
            language_rule=language_rule,
        )

    def test_language_rule_matches_the_seasons_own_language(self):
        rule = {"type": "condition", "field": "audio", "languages": ["DE"]}

        season_one = self.cache.read_offers(self.series, season=1)[0]
        season_two = self.cache.read_offers(self.series, season=2)[0]

        self.assertFalse(
            score_offer(season_one, self._preference(rule)).is_match
        )
        self.assertTrue(
            score_offer(season_two, self._preference(rule)).is_match
        )

    def test_preferred_audio_list_ranks_season_languages(self):
        preference = Preference(
            user_id=1,
            audio_languages=["DE"],
            subtitle_languages=["DE"],
            candidate_countries=[],
            language_rule=None,
        )

        season_one = self.cache.read_offers(self.series, season=1)[0]
        season_two = self.cache.read_offers(self.series, season=2)[0]

        self.assertFalse(score_offer(season_one, preference).is_match)
        self.assertTrue(score_offer(season_two, preference).is_match)


class SeasonLanguageEndpointTests(TestCase):
    """``?season=N`` end-to-end: canonical rows → read → rank → JSON."""

    def setUp(self):
        Subscription.objects.create(
            user_id=1, provider=Provider.objects.get(slug="prime"), name="Prime Video"
        )

    @patch("tracker.services.streaming_api.StreamingAvailabilityClient.get_availability")
    @patch("tracker.services.offer_pipeline.enrich_movie_from_tmdb")
    def test_series_endpoint_serves_and_scores_season_languages(
        self, mock_enrich, mock_get
    ):
        series = Movie.objects.create(
            tmdb_id=2316,
            title="The Office",
            year=2005,
            content_type="series",
            seasons_data=SEASONS_DATA,
        )
        local_offer(series, country="US", service="prime", season_number=1,
                    audio_languages=["EN"], subtitle_languages=["EN"])
        local_offer(series, country="US", service="prime", season_number=2,
                    audio_languages=["DE"], subtitle_languages=["DE"])

        season_one = self.client.get("/api/prime/series/2316/?season=1").json()
        season_two = self.client.get("/api/prime/series/2316/?season=2").json()

        offer_one = season_one["offers"][0]
        offer_two = season_two["offers"][0]
        self.assertEqual(offer_one["audio_languages"], ["EN"])
        self.assertEqual(offer_one["subtitle_languages"], ["EN"])
        self.assertEqual(offer_two["audio_languages"], ["DE"])
        self.assertEqual(offer_two["subtitle_languages"], ["DE"])
        # Default preferences prefer EN: the matcher sees each season's own
        # languages, so season 1 matches and season 2 is flagged as a mismatch.
        self.assertTrue(offer_one["is_language_match"])
        self.assertFalse(offer_two["is_language_match"])
        self.assertEqual(season_two["selected_season"], 2)
        self.assertEqual(
            {o["service"] for o in season_two["offers"]}, {"Prime Video"}
        )
        # Reads serve every season view from canonical rows, no upstream call.
        mock_get.assert_not_called()
        self.assertEqual(series.tmdb_id, 2316)


class MovieLanguageRegressionTests(TestCase):
    """Movies keep the show-level-only language behavior (no season rows)."""

    @staticmethod
    def _result():
        return StreamingResult(
            tmdb_id=680,
            offers=[
                _offer(country="US", service_id="prime", audio=("EN",), subs=("EN",)),
                _offer(country="IT", service_id="netflix", audio=("IT",), subs=("IT", "EN")),
            ],
            seasons=[],
        )

    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=680, title="Pulp Fiction", year=1994, content_type="movie"
        )
        self.upstream = MagicMock()
        self.upstream.get_availability.return_value = self._result()
        self.cache = AvailabilityCache(
            client=self.upstream, limiter=RateLimiter("test_movie_langs")
        )
        ApiCallCounter.objects.filter(service="test_movie_langs").delete()

    def test_movie_writes_only_show_level_rows_and_reads_unchanged(self):
        self.cache.get_or_refresh(self.movie)

        infos = LanguageInfo.objects.filter(movie=self.movie)
        self.assertEqual(infos.count(), 2)
        self.assertFalse(infos.exclude(season=None).exists())

        offers = {o.service: o for o in self.cache.read_offers(self.movie)}
        self.assertEqual(offers["prime"].audio_languages, ["EN"])
        self.assertEqual(offers["netflix"].audio_languages, ["IT"])
        self.assertEqual(offers["netflix"].subtitle_languages, ["IT", "EN"])

        # Movie views never pass a season, and season reads stay empty.
        self.assertEqual(self.cache.read_offers(self.movie, season=1), [])


class LanguageInfoSeasonModelTests(TestCase):
    """(movie, region, season) uniqueness: one show-level row + one per season."""

    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=2316, title="The Office", year=2005)
        self.region = Region.objects.get_or_create(provider="prime", country="NL")[0]

    def test_show_level_rows_stay_unique_per_movie_region(self):
        LanguageInfo.objects.create(movie=self.movie, region=self.region)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LanguageInfo.objects.create(movie=self.movie, region=self.region)

    def test_each_season_gets_its_own_row(self):
        LanguageInfo.objects.create(movie=self.movie, region=self.region, season=1)
        LanguageInfo.objects.create(movie=self.movie, region=self.region, season=2)
        self.assertEqual(
            LanguageInfo.objects.filter(movie=self.movie, region=self.region).count(),
            2,
        )

    def test_duplicate_season_row_is_rejected(self):
        LanguageInfo.objects.create(movie=self.movie, region=self.region, season=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LanguageInfo.objects.create(
                    movie=self.movie, region=self.region, season=1
                )


class ManualLanguageEntryEndpointTests(TestCase):
    """The manual language-info endpoint lands entries on the slug-keyed region.

    The UI sends the display name shown on the offer card ("Prime Video"),
    but the entry must land on the slug-keyed Region ("prime") that reads
    use — and round-trip back through the display-name query string.
    """

    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=2316, title="The Office", year=2005, content_type="series"
        )

    def test_manual_entry_with_display_name_lands_on_the_slug_region(self):
        resp = self.client.put(
            "/api/movie/2316/language-info/",
            data={
                "provider": "Prime Video",
                "country": "NL",
                "audio_languages": ["EN"],
                "subtitle_languages": ["EN", "NL"],
            },
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        self.assertFalse(Region.objects.filter(provider="Prime Video").exists())
        region = Region.objects.get(provider="prime", country="NL")
        info = LanguageInfo.objects.get(
            movie=self.movie, region=region, season=None
        )
        self.assertEqual(
            list(
                info.entries.filter(kind="audio").values_list(
                    "language_code", flat=True
                )
            ),
            ["EN"],
        )
        # Round-trip through the display-name string the UI sends.
        got = self.client.get(
            "/api/movie/2316/language-info/?provider=Prime Video&country=NL"
        ).json()
        self.assertEqual(got["audio_languages"], ["EN"])
        self.assertEqual(got["subtitle_languages"], ["EN", "NL"])
