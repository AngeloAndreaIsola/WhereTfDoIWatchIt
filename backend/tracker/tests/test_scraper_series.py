"""Series support for the scraper layer (Phase 4).

Three concerns:
- classification: each scraper stamps ``ScrapedItem.content_type`` from a real
  signal (href / browse category / API type / typology) and never guesses a
  series from an unknown signal;
- linking: ``ContentMatcher`` searches TMDb in the declared type, keeps a
  series scrape away from a movie row of the same title/year, and never flips
  an existing row's ``content_type``;
- season gating: show-level scraper offers are suppressed when the series
  detail request is narrowed to one season.
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIClient

from availability.services.legacy_ingest import ingest_scraped_mapping
from tracker.models import Movie, Provider, ScrapedContentMapping
from tracker.scrapers import (
    HydraHDScraper,
    KissKHScraper,
    NHKScraper,
    RaiPlayScraper,
    AnimeWorldScraper,
)
from tracker.scrapers.adapters import _content_type_from_url
from tracker.scrapers.base import ContentMatcher, ScrapedItem
from tracker.services.tmdb import TMDbItem


# ---------------------------------------------------------------- fixtures

HYDRA_TV_HTML = """
<html><body>
<a class="hthis" href="/watchseries/breaking-bad-online-free">
  <span> 2008 </span>
</a>
<div class="title detz">Breaking Bad</div>
</body></html>
"""

HYDRA_MOVIE_HTML = """
<html><body>
<a class="hthis" href="/movie/1234-inception-watch-online">
  <span> 2010 </span>
</a>
<div class="title detz">Inception</div>
</body></html>
"""

HYDRA_UNKNOWN_HTML = """
<html><body>
<a class="hthis" href="/something/odd">
  <span> 2020 </span>
</a>
<div class="title detz">Odd Title</div>
</body></html>
"""

ANIMEWORLD_HTML = """
<html><body>
<a href="play/one-piece-1" class="name">One Piece</a>
</body></html>
"""


def raiplay_html(typology: str, title: str = "Title", weblink: str = "/programmi/x") -> str:
    return f"""
<html><body>
<script>window.WashiContext = {{
  "cards": {{
    "0": {{
      "type": "RaiPlay Programma Item",
      "typology": "{typology}",
      "title": "{title}",
      "year": "2021",
      "weblink": "{weblink}"
    }}
  }}
}}</script>
</body></html>
"""


def series_item(title="Dune", year=2021, provider_slug="kisskh") -> ScrapedItem:
    return ScrapedItem(
        title=title,
        provider_slug=provider_slug,
        year=year,
        content_type="series",
        source_url="https://example.com/series/dune",
    )


# ---------------------------------------------------------------- classification


class URLClassificationTests(TestCase):
    def test_watchseries_href_is_a_series(self):
        self.assertEqual(
            _content_type_from_url("https://hydrahd.ws/watchseries/breaking-bad-free"),
            "series",
        )

    def test_movie_href_is_a_movie(self):
        self.assertEqual(
            _content_type_from_url("https://hydrahd.ws/movie/1234-watch-online"),
            "movie",
        )

    def test_unknown_href_keeps_the_default(self):
        # An unknown signal must never become a series.
        self.assertEqual(_content_type_from_url("https://hydrahd.ws/odd/path"), "movie")
        self.assertEqual(
            _content_type_from_url("https://hydrahd.ws/odd/path", default="series"),
            "series",
        )
        self.assertEqual(_content_type_from_url(None), "movie")


class HydraHDClassificationTests(TestCase):
    def setUp(self):
        self.scraper = HydraHDScraper()

    def test_html_href_classifies_show_and_film(self):
        show = self.scraper.parse_html(HYDRA_TV_HTML)
        film = self.scraper.parse_html(HYDRA_MOVIE_HTML)
        self.assertEqual(len(show), 1)
        self.assertEqual(len(film), 1)
        self.assertEqual(show[0].content_type, "series")
        self.assertEqual(film[0].content_type, "movie")

    def test_unrecognized_href_falls_back_to_the_browse_category(self):
        # tv-shows browse page with a href we do not recognize → still a series,
        # because the browse category is a real signal.
        items = self.scraper.parse_html(HYDRA_UNKNOWN_HTML, default_content_type="series")
        self.assertEqual(items[0].content_type, "series")

        items = self.scraper.parse_html(HYDRA_UNKNOWN_HTML, default_content_type="movie")
        self.assertEqual(items[0].content_type, "movie")

    def test_search_json_classifies_from_the_link(self):
        items = self.scraper.parse_items([
            {"title": "Breaking Bad", "year": "2008", "link": "/watchseries/breaking-bad"},
            {"title": "Inception", "year": "2010", "link": "/movie/inception"},
        ])
        self.assertEqual([i.content_type for i in items], ["series", "movie"])


class AnimeWorldClassificationTests(TestCase):
    def setUp(self):
        self.scraper = AnimeWorldScraper()

    def test_animes_browse_is_a_series(self):
        items = self.scraper.parse_html(ANIMEWORLD_HTML, category="animes")
        self.assertEqual(items[0].content_type, "series")

    def test_movies_browse_is_a_movie(self):
        items = self.scraper.parse_html(ANIMEWORLD_HTML, category="movies")
        self.assertEqual(items[0].content_type, "movie")

    def test_unknown_category_is_never_a_series(self):
        items = self.scraper.parse_html(ANIMEWORLD_HTML, category="dorama")
        self.assertEqual(items[0].content_type, "movie")

    def test_search_results_without_a_category_stay_movies(self):
        # The search API carries no category, so no series can be inferred.
        items = self.scraper.parse_html(ANIMEWORLD_HTML)
        self.assertEqual(items[0].content_type, "movie")


class KissKHClassificationTests(TestCase):
    def setUp(self):
        self.scraper = KissKHScraper()

    def test_parse_items_uses_the_declared_type(self):
        rows = [{"id": 1, "title": "Some Show", "year": 2020}]
        self.assertEqual(self.scraper.parse_items(rows, content_type="series"), [])
        rows = [{**rows[0], "type": "TVSeries"}]
        series = self.scraper.parse_items(rows, content_type="series")
        rows = [{**rows[0], "type": "Movie"}]
        movies = self.scraper.parse_items(rows, content_type="movie")
        self.assertEqual(series[0].content_type, "series")
        self.assertEqual(movies[0].content_type, "movie")

    @patch.object(KissKHScraper, "fetch_url")
    def test_browse_requests_each_type_and_classifies(self, mock_fetch):
        def fake_fetch(url, *args, **kwargs):
            if "type=2" in url:
                return {"data": [{"id": 10, "title": "A Film", "year": 2020, "type": "Movie"}]}
            if "type=1" in url:
                return {"data": [{"id": 11, "title": "A Drama", "year": 2021, "type": "TVSeries"}]}
            if "type=3" in url:
                return {"data": [{"id": 12, "title": "An Anime", "year": 2022, "episodesCount": 12}]}
            return None

        mock_fetch.side_effect = fake_fetch
        items = self.scraper._execute_run(query=None)

        urls = [c.args[0] for c in mock_fetch.call_args_list]
        # The mixed `type=0` listing carries no usable signal — never used.
        self.assertTrue(all("type=0" not in u for u in urls))
        self.assertTrue(any("type=2" in u for u in urls))
        self.assertTrue(any("type=1" in u for u in urls))
        self.assertTrue(any("type=3" in u for u in urls))

        by_title = {i.title: i.content_type for i in items}
        self.assertEqual(by_title, {
            "A Film": "movie",
            "A Drama": "series",
        })

    @patch.object(KissKHScraper, "fetch_url")
    def test_search_queries_every_type(self, mock_fetch):
        mock_fetch.return_value = [{"id": 1, "title": "Result", "year": 2020, "type": "Movie"}]
        self.scraper._execute_run(query="dune")

        urls = [c.args[0] for c in mock_fetch.call_args_list]
        self.assertTrue(all("DramaList/Search" in u for u in urls))
        self.assertTrue(any("type=1" in u for u in urls))
        self.assertTrue(any("type=2" in u for u in urls))

    @patch.object(KissKHScraper, "fetch_url")
    def test_same_native_id_in_movie_and_series_partitions_stays_separate(self, mock_fetch):
        def result(url, *args, **kwargs):
            return [{"id": 77, "title": "Shared", "year": 2019, "type": "TVSeries" if "type=1" in url else "Movie"}]
        mock_fetch.side_effect = result
        items = self.scraper._execute_run(query="shared")
        self.assertEqual({item.content_type for item in items}, {"movie", "series"})
        self.assertEqual(len(items), 2)

    @patch.object(KissKHScraper, "fetch_url")
    def test_failed_requests_do_not_break_the_run(self, mock_fetch):
        mock_fetch.side_effect = Exception("blocked")
        self.assertEqual(self.scraper._execute_run(query=None), [])
        self.assertEqual(self.scraper._execute_run(query="x"), [])


class RaiPlayClassificationTests(TestCase):
    def setUp(self):
        self.scraper = RaiPlayScraper()

    def _items(self, html, source_url):
        return self.scraper._parse_washi_context(html, source_url)

    def test_fiction_typology_is_a_series(self):
        items = self._items(
            raiplay_html("Fiction"), "https://www.raiplay.it/serieitaliane"
        )
        self.assertEqual(items[0].content_type, "series")

    def test_serie_tv_typology_is_a_series(self):
        # Verified live: both listings mix typologies, "Serie TV" appears on both.
        items = self._items(raiplay_html("Serie TV"), "https://www.raiplay.it/film/")
        self.assertEqual(items[0].content_type, "series")

    def test_film_typology_is_a_movie(self):
        items = self._items(raiplay_html("Film"), "https://www.raiplay.it/film/")
        self.assertEqual(items[0].content_type, "movie")

    def test_film_listed_on_the_series_page_is_still_a_movie(self):
        # The typology wins over the page: the series listing contains films.
        items = self._items(raiplay_html("Film"), "https://www.raiplay.it/serieitaliane")
        self.assertEqual(items[0].content_type, "movie")

    def test_unknown_typology_falls_back_to_the_listing_page(self):
        items = self._items(raiplay_html("Kids"), "https://www.raiplay.it/serieitaliane")
        self.assertEqual(items[0].content_type, "series")

        items = self._items(raiplay_html("Programmi TV"), "https://www.raiplay.it/film/")
        self.assertEqual(items[0].content_type, "movie")


class PrioritySourceClassificationTests(TestCase):
    def test_bbc_legacy_scraper_retired_in_favor_of_canonical_source(self):
        from availability.sources import source_registry
        from tracker.scrapers import resolve_scraper_class
        from tracker.services.provider_status import catalogue_source_map
        self.assertIsNone(resolve_scraper_class("bbc_iplayer"))
        self.assertEqual(catalogue_source_map().get("bbc_iplayer"), "bbc_iplayer")
        self.assertIn("bbc_iplayer", source_registry.slugs())

    @patch.object(NHKScraper, "fetch_url")
    def test_nhk_has_no_type_signal_so_it_stays_a_movie(self, mock_fetch):
        mock_fetch.return_value = """
        <html><body>
          <div data-testid="program-card">
            <a data-testid="program-title" href="/nhkworld/tv/x">Japan Railway Journal</a>
          </div>
        </body></html>
        """
        items = NHKScraper()._execute_run(query="Japan Railway Journal")
        self.assertEqual(items[0].content_type, "movie")


# ---------------------------------------------------------------- linking


class ContentMatcherContentTypeTests(TestCase):
    """``match_item`` must search the declared type and never flip a row."""

    def setUp(self):
        self.client = APIClient()
        self.provider, _ = Provider.objects.get_or_create(
            slug="kisskh",
            defaults={"name": "KissKH", "category": "scraper", "is_enabled": True},
        )
        if self.provider.category != "scraper":
            self.provider.category = "scraper"
            self.provider.save()

    @patch("tracker.scrapers.base.TMDbClient.search")
    def test_series_scrape_does_not_link_to_a_movie_row(self, mock_search):
        # Same title and year as a tracked movie: the series scrape must not
        # attach to it, it must search the TV endpoint and get its own row.
        movie_row = Movie.objects.create(
            tmdb_id=438631, title="Dune", year=2021, content_type="movie"
        )
        mock_search.return_value = [
            TMDbItem(tmdb_id=90209, title="Dune", year=2024, content_type="series")
        ]

        matcher = ContentMatcher()
        movie, score, candidates, is_confirmed = matcher.match_item(series_item())

        mock_search.assert_called_once_with("Dune", year=2021, content_type="series")
        self.assertIsNotNone(movie)
        self.assertNotEqual(movie.id, movie_row.id)
        self.assertEqual(movie.tmdb_id, 90209)
        self.assertEqual(movie.content_type, "series")

        # The movie row is untouched: no content_type flip, no overwrite.
        movie_row.refresh_from_db()
        self.assertEqual(movie_row.content_type, "movie")
        self.assertEqual(movie_row.tmdb_id, 438631)

    @patch("tracker.scrapers.base.TMDbClient.search")
    def test_series_scrape_links_to_an_existing_series_row(self, mock_search):
        series_row = Movie.objects.create(
            tmdb_id=90209, title="Dune", year=2024, content_type="series"
        )

        matcher = ContentMatcher()
        movie, score, candidates, is_confirmed = matcher.match_item(
            series_item(title="Dune", year=2024)
        )

        mock_search.assert_not_called()
        self.assertEqual(movie.id, series_row.id)
        self.assertEqual(score, 1.0)

    @patch("tracker.scrapers.base.TMDbClient.search")
    def test_movie_scrape_keeps_the_movie_search(self, mock_search):
        mock_search.return_value = [
            TMDbItem(tmdb_id=438631, title="Dune", year=2021, content_type="movie")
        ]
        item = ScrapedItem(
            title="Dune",
            provider_slug="kisskh",
            year=2021,
            content_type="movie",
            source_url="https://example.com/movie/dune",
        )

        matcher = ContentMatcher()
        movie, score, _, _ = matcher.match_item(item)

        mock_search.assert_called_once_with("Dune", year=2021, content_type="movie")
        self.assertEqual(movie.tmdb_id, 438631)
        self.assertEqual(movie.content_type, "movie")

    @patch("tracker.scrapers.base.TMDbClient.search")
    def test_existing_row_content_type_is_never_flipped(self, mock_search):
        # Movie and TV can legitimately share a numeric TMDb ID.
        row = Movie.objects.create(
            tmdb_id=90209, title="Dune", year=2021, content_type="movie"
        )
        mock_search.return_value = [
            TMDbItem(tmdb_id=90209, title="Dune", year=2021, content_type="series")
        ]

        matcher = ContentMatcher()
        movie, _, _, _ = matcher.match_item(series_item())

        row.refresh_from_db()
        self.assertNotEqual(movie.id, row.id)
        self.assertEqual(movie.content_type, "series")
        self.assertEqual(row.content_type, "movie")

    @patch("tracker.scrapers.base.TMDbClient.search")
    def test_unresolved_series_mapping_keeps_its_type_for_review(self, mock_search):
        mock_search.return_value = []

        matcher = ContentMatcher()
        matcher.match_item(series_item(title="Nope", year=1999))

        mapping = ScrapedContentMapping.objects.get(
            provider=self.provider, scraped_title="Nope", scraped_year=1999
        )
        self.assertIsNone(mapping.movie)
        self.assertFalse(mapping.is_confirmed)
        self.assertEqual(mapping.raw_payload.get("content_type"), "series")

    @patch("tracker.scrapers.base.TMDbClient.search")
    def test_unresolved_review_searches_in_the_stored_type(self, mock_search):
        mock_search.return_value = []
        ContentMatcher().match_item(series_item(title="Nope", year=1999))
        mock_search.reset_mock()

        response = self.client.get("/api/scrapers/unresolved/")

        self.assertEqual(response.status_code, 200)
        mock_search.assert_called_once_with("Nope", year=1999, content_type="series")
        self.assertEqual(response.json()[0]["raw_payload"]["content_type"], "series")


# ---------------------------------------------------------------- scrape payload


class ScrapePayloadContentTypeTests(TestCase):
    """Scrape responses echo the classified content type."""

    def setUp(self):
        self.client = APIClient()
        self.provider, _ = Provider.objects.get_or_create(
            slug="kisskh",
            defaults={"name": "KissKH", "category": "scraper", "is_enabled": True},
        )

    @patch("tracker.services.scraper_selection.resolve_scraper_class")
    @patch("tracker.scrapers.base.TMDbClient.search")
    def test_scraper_run_serializes_content_type(self, mock_search, mock_resolve):
        mock_search.return_value = []

        class FakeScraper:
            def run(self, query=None):
                return [series_item(title="Some Show", year=2020)]

        mock_resolve.return_value = FakeScraper

        response = self.client.post(
            "/api/scrapers/run/",
            {"provider_slug": "kisskh"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["results"][0]["scraped_item"]["content_type"], "series")

        mapping = ScrapedContentMapping.objects.get(
            provider=self.provider, scraped_title="Some Show", scraped_year=2020
        )
        self.assertEqual(mapping.raw_payload["content_type"], "series")


class ConfirmMatchContentTypeTests(TestCase):
    """Manual confirm picks the TMDb endpoint from the stated content type."""

    def setUp(self):
        self.client = APIClient()
        self.provider, _ = Provider.objects.get_or_create(
            slug="kisskh",
            defaults={"name": "KissKH", "category": "scraper", "is_enabled": True},
        )
        self.mapping = ScrapedContentMapping.objects.create(
            provider=self.provider,
            scraped_title="Breaking Bad",
            scraped_year=2008,
            scraped_url="https://example.com/bb",
        )

    @patch("tracker.views.TMDbClient")
    def test_confirming_a_series_uses_the_tv_endpoint(self, mock_client_cls):
        mock_client_cls.return_value.details.return_value = TMDbItem(
            tmdb_id=1396, title="Breaking Bad", year=2008, content_type="series"
        )

        response = self.client.post(
            "/api/scrapers/confirm-match/",
            {"mapping_id": self.mapping.id, "tmdb_id": 1396, "content_type": "series"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_client_cls.return_value.details.assert_called_once_with(
            1396, content_type="series"
        )
        movie = Movie.objects.get(tmdb_id=1396)
        self.assertEqual(movie.content_type, "series")

        self.mapping.refresh_from_db()
        self.assertTrue(self.mapping.is_confirmed)
        self.assertEqual(self.mapping.movie_id, movie.id)

    @patch("tracker.views.TMDbClient")
    def test_confirming_without_a_type_defaults_to_the_movie_endpoint(self, mock_client_cls):
        mock_client_cls.return_value.details.return_value = TMDbItem(
            tmdb_id=438631, title="Dune", year=2021, content_type="movie"
        )

        response = self.client.post(
            "/api/scrapers/confirm-match/",
            {"mapping_id": self.mapping.id, "tmdb_id": 438631},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_client_cls.return_value.details.assert_called_once_with(
            438631, content_type="movie"
        )
        self.assertEqual(Movie.objects.get(tmdb_id=438631).content_type, "movie")

    def test_unknown_content_type_is_rejected(self):
        response = self.client.post(
            "/api/scrapers/confirm-match/",
            {"mapping_id": self.mapping.id, "tmdb_id": 1, "content_type": "game"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


# ---------------------------------------------------------------- season gating


class UnofficialOffersSeasonGatingTests(TestCase):
    """Show-level scraper offers are suppressed for a season-filtered request."""

    def setUp(self):
        self.client = APIClient()
        self.series = Movie.objects.create(
            tmdb_id=1396, title="Breaking Bad", year=2008, content_type="series"
        )
        self.movie = Movie.objects.create(
            tmdb_id=438631, title="Dune", year=2021, content_type="movie"
        )
        self.provider, _ = Provider.objects.get_or_create(
            slug="hydrahd",
            defaults={"name": "HydraHD", "category": "scraper", "is_enabled": True},
        )
        if self.provider.category != "scraper":
            self.provider.category = "scraper"
            self.provider.save()
        self.provider.is_enabled = True
        self.provider.save()

        for target in (self.series, self.movie):
            mapping = ScrapedContentMapping.objects.create(
                provider=self.provider,
                movie=target,
                scraped_title=target.title,
                scraped_year=target.year,
                scraped_url="https://hydrahd.ws/watchseries/x",
                is_available=True,
                raw_payload={"audio_languages": ["EN"], "subtitle_languages": ["EN"]},
            )
            # The v2 offers endpoint reads the canonical catalogue, so the
            # fixture must go through the ingestion bridge like production
            # scraper writes do.
            ingest_scraped_mapping(mapping)

    def test_no_season_returns_the_scraper_offers(self):
        response = self.client.get("/api/v2/unofficial/movie/1396/offers/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["offers"]), 1)
        self.assertIsNone(data["selected_season"])
        self.assertEqual(data["movie"]["content_type"], "series")

    def test_season_filter_suppresses_show_level_scraper_offers(self):
        response = self.client.get("/api/v2/unofficial/movie/1396/offers/?season=2")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["offers"], [])
        self.assertEqual(data["selected_season"], 2)

    def test_every_season_answer_is_empty(self):
        for season in (0, 1, 99):
            data = self.client.get(
                f"/api/v2/unofficial/movie/1396/offers/?season={season}"
            ).json()
            self.assertEqual(data["offers"], [], f"season {season}")
            self.assertEqual(data["selected_season"], season)

    def test_invalid_season_value_is_ignored(self):
        data = self.client.get("/api/v2/unofficial/movie/1396/offers/?season=abc").json()
        self.assertEqual(len(data["offers"]), 1)
        self.assertIsNone(data["selected_season"])

    def test_movie_endpoint_keeps_offers_for_any_season_value(self):
        # A movie request never narrows to a season — same as the prime movie endpoint.
        data = self.client.get("/api/v2/unofficial/movie/438631/offers/?season=2").json()
        self.assertEqual(len(data["offers"]), 1)
        self.assertIsNone(data["selected_season"])

    def test_unknown_movie_still_404s(self):
        response = self.client.get("/api/v2/unofficial/movie/999999/offers/?season=2")
        self.assertEqual(response.status_code, 404)
