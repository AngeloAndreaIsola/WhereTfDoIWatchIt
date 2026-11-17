"""Season-level language data: parse → persist → read → score (Phase 3).

Regression tests for series language detection. Each season of a series can
carry its own audio/subtitle lists upstream; those lists must survive the
legacy ``_write_result`` round-trip keyed per (movie, region, season) —
without collapsing into the show-level row or leaking between seasons.
Season *reads* go through the canonical read path
(``SeasonLanguageEndpointTests`` below and the availability reconciliation
tests), where each season's offers carry their own languages to the matcher.
Movies keep the show-level-only behavior they always had.
"""
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from tracker.models import (
    LanguageInfo,
    Movie,
    Preference,
    Provider,
    Region,
    Subscription,
)
from tracker.services.availability_reads import get_local_offers
from tracker.services.cache import AvailabilityCache
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
    """Season-level languages persist per (movie, region, season) on write."""

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
        self.cache = AvailabilityCache()

    def test_season_level_languages_are_persisted_per_season(self):
        self.cache._write_result(self.series, self._result())

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

    def test_two_seasons_do_not_cross_contaminate(self):
        self.cache._write_result(self.series, self._result())

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
        season_one = LanguageInfo.objects.get(
            movie=self.series, region=region, season=1
        )
        season_two = LanguageInfo.objects.get(
            movie=self.series, region=region, season=2
        )
        one_audio = [e.language_code for e in season_one.entries.filter(kind="audio")]
        two_audio = [e.language_code for e in season_two.entries.filter(kind="audio")]
        self.assertNotEqual(one_audio, two_audio)
        self.assertNotIn("DE", one_audio)
        self.assertNotIn("EN", two_audio)

    def test_write_without_seasons_drops_stale_season_language_rows(self):
        self.cache._write_result(self.series, self._result())
        self.assertTrue(
            LanguageInfo.objects.filter(
                movie=self.series, season__isnull=False
            ).exists()
        )

        self.cache._write_result(self.series, self._result(with_seasons=False))

        self.assertFalse(
            LanguageInfo.objects.filter(
                movie=self.series, season__isnull=False
            ).exists()
        )
        # Show-level rows survive the write.
        self.assertTrue(
            LanguageInfo.objects.filter(
                movie=self.series, season=None
            ).exists()
        )


class SeasonLanguageScoringTests(TestCase):
    """The language matcher evaluates the selected season's own languages."""

    def setUp(self):
        self.series = Movie.objects.create(
            tmdb_id=2316,
            title="The Office",
            year=2005,
            content_type="series",
            seasons_data=SEASONS_DATA,
        )
        local_offer(self.series, country="NL", service="prime", season_number=1,
                    audio_languages=["EN"], subtitle_languages=["EN"])
        local_offer(self.series, country="NL", service="prime", season_number=2,
                    audio_languages=["DE"], subtitle_languages=["DE"])

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

        season_one = get_local_offers(self.series, season=1)[0]
        season_two = get_local_offers(self.series, season=2)[0]

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

        season_one = get_local_offers(self.series, season=1)[0]
        season_two = get_local_offers(self.series, season=2)[0]

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
        self.cache = AvailabilityCache()

    def test_movie_writes_only_show_level_rows(self):
        self.cache._write_result(self.movie, self._result())

        infos = LanguageInfo.objects.filter(movie=self.movie)
        self.assertEqual(infos.count(), 2)
        self.assertFalse(infos.exclude(season=None).exists())

        def codes(info, kind):
            return [
                e.language_code
                for e in info.entries.filter(kind=kind).order_by("id")
            ]

        by_provider = {i.region.provider: i for i in infos}
        self.assertEqual(codes(by_provider["prime"], "audio"), ["EN"])
        self.assertEqual(codes(by_provider["netflix"], "audio"), ["IT"])
        self.assertEqual(codes(by_provider["netflix"], "subtitle"), ["IT", "EN"])


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
