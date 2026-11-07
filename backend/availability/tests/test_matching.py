"""Regression coverage for local title/year matching.

The matcher must never turn a known, conflicting provider year into a
title-only match. The 1997 and 2011 Tubi pages are the concrete cases: the
former must not attach to TMDb 389 (1957), the latter must not attach to
TMDb 550 (1999). Exact-year and unknown-year behavior must remain unchanged.
"""
from datetime import date
from types import SimpleNamespace

from django.test import TestCase

from availability.models import ContentMapping, Country, Offer, ProviderContent
from availability.services.matching import match_local_catalog
from availability.services.refresh import AvailabilityRefreshService
from availability.sources.tubi import TubiClient, TubiSource
from tracker.models import Movie, Provider

MOVIES_1 = "https://tubitv.com/sitemaps/movies-1.xml"
TUBI_1997 = "https://tubitv.com/movies/667137/12-angry-men"


def movie(title, year):
    return SimpleNamespace(
        title=title,
        release_date=date(year, 1, 1) if year is not None else None,
        content_type="movie",
    )


class MatchLocalCatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.classic = Movie.objects.create(
            tmdb_id=389, title="12 Angry Men", year=1957, content_type="movie"
        )
        cls.fight_club = Movie.objects.create(
            tmdb_id=550, title="Fight Club", year=1999, content_type="movie"
        )
        cls.series = Movie.objects.create(
            tmdb_id=7000, title="Known Show", year=2020, content_type="series"
        )

    def test_a_short_title_is_not_a_substring_match(self):
        """`Her` must never map to `Hereditary`: title matching is exact."""
        Movie.objects.create(
            tmdb_id=530385, title="Hereditary", year=2018, content_type="movie"
        )
        self.assertIsNone(match_local_catalog(movie("Her", None)))
        self.assertIsNone(match_local_catalog(movie("Her", 2018)))

    def test_1997_12_angry_men_does_not_fall_back_to_the_1957_film(self):
        self.assertIsNone(match_local_catalog(movie("12 Angry Men", 1997)))

    def test_1957_12_angry_men_still_matches_exactly(self):
        match = match_local_catalog(movie("12 Angry Men", 1957))
        self.assertIsNotNone(match)
        self.assertEqual(match.tmdb_id, 389)
        self.assertEqual(match.confidence, 0.9)

    def test_2011_fight_club_does_not_fall_back_to_the_1999_film(self):
        self.assertIsNone(match_local_catalog(movie("Fight Club", 2011)))

    def test_1999_fight_club_still_matches_exactly(self):
        match = match_local_catalog(movie("Fight Club", 1999))
        self.assertIsNotNone(match)
        self.assertEqual(match.tmdb_id, 550)
        self.assertEqual(match.confidence, 0.9)

    def test_unknown_year_movie_keeps_title_only_fallback(self):
        match = match_local_catalog(movie("12 Angry Men", None))
        self.assertIsNotNone(match)
        self.assertEqual(match.tmdb_id, 389)
        self.assertEqual(match.confidence, 0.7)

    def test_series_keeps_title_only_fallback_for_a_conflicting_year(self):
        content = SimpleNamespace(
            title="Known Show",
            release_date=date(2024, 1, 1),
            content_type="series",
        )
        match = match_local_catalog(content)
        self.assertIsNotNone(match)
        self.assertEqual(match.tmdb_id, 7000)
        self.assertEqual(match.confidence, 0.7)

    def test_two_local_rows_with_the_same_title_and_year_are_ambiguous(self):
        """Equal title+year evidence cannot pick a winner; leave it unmapped."""
        Movie.objects.create(
            tmdb_id=990001, title="Fight Club", year=1999, content_type="movie"
        )
        self.assertIsNone(match_local_catalog(movie("Fight Club", 1999)))

    def test_a_conflicting_year_still_picks_the_matching_remake(self):
        Movie.objects.create(
            tmdb_id=1091, title="The Thing", year=1982, content_type="movie"
        )
        Movie.objects.create(
            tmdb_id=60935, title="The Thing", year=2011, content_type="movie"
        )
        match = match_local_catalog(movie("The Thing", 1982))
        self.assertIsNotNone(match)
        self.assertEqual(match.tmdb_id, 1091)
        self.assertEqual(match.confidence, 0.9)


class Tubi1997PersistenceTests(TestCase):
    """The real Tubi 1997 page must be discoverable but produce no offer."""

    def setUp(self):
        Provider.objects.get_or_create(slug="tubi", defaults={"name": "Tubi"})
        Country.objects.get_or_create(code="US", defaults={"name": "United States"})
        Movie.objects.create(
            tmdb_id=389, title="12 Angry Men", year=1957, content_type="movie"
        )

    @staticmethod
    def _respond(url):
        if url == "https://tubitv.com/sitemap.xml":
            return (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<sitemap><loc>{MOVIES_1}</loc></sitemap>"
                "</sitemapindex>"
            )
        if url == MOVIES_1:
            return (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<url><loc>{TUBI_1997}</loc></url>"
                "</urlset>"
            )
        if url == TUBI_1997:
            return (
                "<html><body><script>window.__data="
                '{"video":{"byId":{"667137":{"title":"12 Angry Men",'
                '"year":1997,"detailed_type":"movie","imdb_id":"tt0118528"}}}};'
                "</script></body></html>"
            )
        raise AssertionError(f"unexpected URL {url}")

    def test_page_is_discovered_without_an_offer_or_mapping(self):
        source = TubiSource(
            client=TubiClient(requester=self._respond, retries=0),
            max_contents=1,
            max_priority=0,
        )
        result = AvailabilityRefreshService(source=source).refresh_catalogue(
            provider="tubi", country="US"
        )

        content = ProviderContent.objects.get(
            provider__slug="tubi", provider_content_id="667137"
        )
        self.assertEqual(content.title, "12 Angry Men")
        self.assertEqual(content.release_date, date(1997, 1, 1))
        self.assertTrue(content.currently_available)

        self.assertFalse(
            ContentMapping.objects.filter(
                provider__slug="tubi", provider_content_id="667137"
            ).exists()
        )
        self.assertFalse(
            Offer.objects.filter(
                provider__slug="tubi", provider_content_id="667137"
            ).exists()
        )
        self.assertFalse(Offer.objects.filter(tmdb_id=389).exists())
        self.assertEqual(result.unresolved, 1)
