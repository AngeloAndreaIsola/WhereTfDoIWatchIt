"""P1 scraper reliability: result association and query encoding.

Every fixture here is synthetic HTML/JSON; no test touches the network. The
regressions these tests pin are structural:

- AnimeWorld used to collect titles and years into separate lists and pair
  them by index, so one card without an ``Anno:`` label shifted every later
  year onto an earlier title.
- HydraHD used one ``[\\s\\S]*?`` regex from an anchor to the next title div,
  so a malformed card could publish the *next* card's title under its own URL.
- Search terms were interpolated into URLs raw, letting ``&``/``+``/spaces
  rewrite the query string.
"""
from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from tracker.scrapers import AnimeWorldScraper, HydraHDScraper, KissKHScraper, NHKScraper
from tracker.scrapers.rai_play import RaiPlayScraper


def anime_card(title, href, year=None):
    year_html = f'<span class="year">Anno: {year}</span>' if year is not None else ""
    return f"""
    <div class="item">
      <a href="{href}" class="name">{title}</a>
      {year_html}
    </div>
    """


def hydra_card(title_html, href, year=None):
    year_html = f"<span> {year} </span>" if year is not None else ""
    return f"""
    <div class="item">
      <a class="hthis" href="{href}">{year_html}</a>
      {title_html}
    </div>
    """


class AnimeWorldPairingTests(SimpleTestCase):
    """A missing or malformed year never shifts onto a neighbouring title."""

    def setUp(self):
        self.scraper = AnimeWorldScraper()

    def test_consecutive_years_belong_to_their_own_titles(self):
        html = (
            anime_card("First", "play/first", 2007)
            + anime_card("Second", "play/second", 2011)
            + anime_card("Third", "play/third", 2020)
        )
        items = self.scraper.parse_html(html)
        self.assertEqual(
            [(item.title, item.year) for item in items],
            [("First", 2007), ("Second", 2011), ("Third", 2020)],
        )

    def test_missing_year_between_two_valid_years_does_not_shift(self):
        html = (
            anime_card("Before", "play/before", 2001)
            + anime_card("No Year", "play/no-year")
            + anime_card("After", "play/after", 2022)
        )
        items = self.scraper.parse_html(html)
        self.assertEqual(
            [(item.title, item.year) for item in items],
            [("Before", 2001), ("No Year", None), ("After", 2022)],
        )

    def test_malformed_year_is_unknown_not_truncated(self):
        html = (
            anime_card("Bad", "play/bad", "20x4")
            + anime_card("Long", "play/long", "20077")
            + anime_card("Good", "play/good", 2019)
        )
        items = self.scraper.parse_html(html)
        self.assertEqual(
            [(item.title, item.year) for item in items],
            [("Bad", None), ("Long", None), ("Good", 2019)],
        )

    def test_missing_title_or_link_is_skipped_without_shifting(self):
        html = """
        <div class="item"><a href="" class="name">No Link</a></div>
        <div class="item"><a href="play/empty" class="name"></a></div>
        <div class="item">
          <a href="play/kept" class="name">Kept</a>
          <span>Anno: 2018</span>
        </div>
        """
        items = self.scraper.parse_html(html)
        self.assertEqual([(i.title, i.year, i.source_url) for i in items], [
            ("Kept", 2018, "https://www.animeworld.ac/play/kept"),
        ])

    def test_duplicate_titles_keep_their_own_years(self):
        html = (
            anime_card("Same Name", "play/old", 1999)
            + anime_card("Same Name", "play/new", 2024)
        )
        items = self.scraper.parse_html(html)
        self.assertEqual(
            [(i.title, i.year, i.source_url) for i in items],
            [
                ("Same Name", 1999, "https://www.animeworld.ac/play/old"),
                ("Same Name", 2024, "https://www.animeworld.ac/play/new"),
            ],
        )

    def test_non_ascii_titles_and_absolute_links_are_preserved(self):
        html = (
            anime_card("Città Incantata", "https://cdn.example/play/x", 2001)
            + anime_card("葬送のフリーレン", "/play/frieren", 2023)
        )
        items = self.scraper.parse_html(html, category="animes")
        self.assertEqual([i.title for i in items], ["Città Incantata", "葬送のフリーレン"])
        self.assertEqual(items[0].source_url, "https://cdn.example/play/x")
        self.assertEqual(items[1].source_url, "https://www.animeworld.ac/play/frieren")
        self.assertEqual([i.content_type for i in items], ["series", "series"])


class HydraHDPairingTests(SimpleTestCase):
    """Each title is paired with the destination URL from its own card."""

    def setUp(self):
        self.scraper = HydraHDScraper()

    def test_adjacent_cards_keep_their_own_title_and_link(self):
        html = (
            hydra_card('<div class="title detz">First Film</div>', "/movie/one", 2010)
            + hydra_card('<div class="title detz">Second Show</div>',
                         "/watchseries/two", 2015)
        )
        items = self.scraper.parse_html(html)
        self.assertEqual(
            [(i.title, i.source_url, i.year, i.content_type) for i in items],
            [
                ("First Film", "https://hydrahd.ws/movie/one", 2010, "movie"),
                ("Second Show", "https://hydrahd.ws/watchseries/two", 2015, "series"),
            ],
        )

    def test_card_missing_a_title_does_not_steal_the_next_cards_title(self):
        html = (
            hydra_card("", "/movie/broken", 2012)
            + hydra_card('<div class="title detz">Good Film</div>', "/movie/good", 2021)
        )
        items = self.scraper.parse_html(html)
        self.assertEqual(
            [(i.title, i.source_url) for i in items],
            [("Good Film", "https://hydrahd.ws/movie/good")],
        )

    def test_flat_markup_stops_the_title_search_at_the_next_result(self):
        html = """
        <a class="hthis" href="/movie/flat"><span>2019</span></a>
        <div class="title detz">Flat Movie</div>
        <a class="hthis" href="/movie/next"><span>2020</span></a>
        <div class="title detz">Next Movie</div>
        """
        items = self.scraper.parse_html(html)
        self.assertEqual(
            [(i.title, i.source_url) for i in items],
            [
                ("Flat Movie", "https://hydrahd.ws/movie/flat"),
                ("Next Movie", "https://hydrahd.ws/movie/next"),
            ],
        )

    def test_nested_anchor_is_not_a_second_result(self):
        html = """
        <a class="hthis" href="/movie/outer"><span>2018</span>
          <div class="title detz">Outer</div>
          <a class="hthis" href="/inner">Inner</a>
        </a>
        """
        items = self.scraper.parse_html(html)
        self.assertEqual([(i.title, i.source_url) for i in items],
                         [("Outer", "https://hydrahd.ws/movie/outer")])

    def test_missing_href_and_malformed_year_are_skipped_or_unknown(self):
        html = """
        <div class="item">
          <a class="hthis"><span>2010</span></a>
          <div class="title detz">No Link</div>
        </div>
        <div class="item">
          <a class="hthis" href="/movie/odd"><span>not-a-year</span></a>
          <div class="title detz">Odd Year</div>
        </div>
        """
        items = self.scraper.parse_html(html)
        self.assertEqual([(i.title, i.year) for i in items], [("Odd Year", None)])

    def test_absolute_destination_is_not_rewritten(self):
        html = """
        <div class="item">
          <a class="hthis" href="https://cdn.example/watch/9"><span>2017</span></a>
          <div class="title detz">Absolute</div>
        </div>
        """
        items = self.scraper.parse_html(html)
        self.assertEqual(items[0].source_url, "https://cdn.example/watch/9")


class QueryEncodingTests(SimpleTestCase):
    """Reserved characters in a search term cannot alter the query structure."""

    QUERY = "spazio & più + l'apostrofo = 100%"

    def _params(self, url):
        from urllib.parse import parse_qs, urlsplit
        return parse_qs(urlsplit(url).query, keep_blank_values=True)

    @patch.object(AnimeWorldScraper, "fetch_url")
    def test_animeworld_search_encodes_the_keyword(self, fetch):
        fetch.return_value = {"html": ""}
        AnimeWorldScraper()._execute_run(query=self.QUERY)
        url = fetch.call_args.args[0]
        self.assertEqual(self._params(url)["keyword"], [self.QUERY])

    @patch.object(KissKHScraper, "fetch_url")
    def test_kisskh_search_encodes_the_query_and_keeps_type(self, fetch):
        fetch.return_value = []
        KissKHScraper()._execute_run(query=self.QUERY)
        urls = [call.args[0] for call in fetch.call_args_list]
        self.assertTrue(urls)
        for url in urls:
            params = self._params(url)
            self.assertEqual(params["q"], [self.QUERY])
            self.assertEqual(len(params["type"]), 1)

    @patch.object(NHKScraper, "fetch_url")
    def test_nhk_search_encodes_the_query(self, fetch):
        fetch.return_value = "<html></html>"
        NHKScraper()._execute_run(query=self.QUERY)
        url = fetch.call_args.args[0]
        self.assertEqual(self._params(url)["q"], [self.QUERY])

    @patch.object(HydraHDScraper, "fetch_url")
    def test_hydrahd_search_encodes_the_query_on_both_domains(self, fetch):
        fetch.side_effect = [None, "<html></html>"]
        HydraHDScraper()._execute_run(query=self.QUERY)
        urls = [call.args[0] for call in fetch.call_args_list]
        self.assertEqual(len(urls), 2)
        for url in urls:
            params = self._params(url)
            self.assertEqual(params["query"], [self.QUERY])
            self.assertEqual(params["menu"], ["search"])

    @patch.object(RaiPlayScraper, "fetch_url")
    def test_raiplay_search_encodes_the_query(self, fetch):
        fetch.return_value = "<html></html>"
        RaiPlayScraper()._execute_run(query=self.QUERY)
        url = fetch.call_args.args[0]
        self.assertEqual(self._params(url)["search"], [self.QUERY])


class AnimeWorldBrowseRequestTests(SimpleTestCase):
    """Browse must use the shared request path, not a bare ``session.get``."""

    @patch.object(AnimeWorldScraper, "fetch_url")
    def test_browse_uses_fetch_url_with_the_browser_header(self, fetch):
        fetch.return_value = anime_card("Only", "play/only", 2020)
        scraper = AnimeWorldScraper()
        with patch.object(scraper, "session") as session:
            items = scraper._execute_run(query=None)

        self.assertEqual(len(items), 4, "two categories x two pages")
        self.assertFalse(session.get.called, "browse must not bypass fetch_url")
        urls = [call.args[0] for call in fetch.call_args_list]
        self.assertEqual(urls, [
            "https://www.animeworld.ac/movies?page=1",
            "https://www.animeworld.ac/movies?page=2",
            "https://www.animeworld.ac/animes?page=1",
            "https://www.animeworld.ac/animes?page=2",
        ])
        for call in fetch.call_args_list:
            self.assertEqual(call.kwargs["headers"]["User-Agent"], "Mozilla/5.0")

    @patch.object(AnimeWorldScraper, "fetch_url")
    def test_browse_failure_is_empty_not_a_crash(self, fetch):
        fetch.return_value = None
        self.assertEqual(AnimeWorldScraper()._execute_run(query=None), [])

    def test_browse_timeout_is_absorbed_by_the_shared_helper(self):
        scraper = AnimeWorldScraper()
        with patch.object(scraper.session, "get",
                          side_effect=requests.exceptions.Timeout("slow")) as get:
            self.assertEqual(scraper._execute_run(query=None), [])
        self.assertTrue(get.called)
        # fetch_url retries each URL max_retries times, then gives up.
        self.assertEqual(get.call_count, 4 * (1 + scraper.max_retries))


class DocumentedRealMarkupTests(SimpleTestCase):
    """The parsers against the one real markup shape the repo actually recorded.

    No genuine HTML capture survives in the tree. ``docs/SCRAPER_FIX_REPORT.md``
    preserves the single real snippet we have — AnimeWorld's search API answers
    ``{"html": "<div class='item'>...<a class='name' href='...'>Naruto</a>"}``
    with *single-quoted* attributes — so this reconstructs exactly that shape
    (envelope + single quotes) instead of only the double-quoted synthetic
    helpers above. It pins that the structural parser is not quietly relying on
    the quoting style of our own fixtures.
    """

    # Mirrors the report's recorded response shape: one ``div.item`` per
    # result, single-quoted attributes, the title anchor and the ``Anno:`` span
    # inside the same card.
    REAL_SHAPE_HTML = (
        "<div class='item'>"
        "<a class='name' href='/play/naruto-1'>Naruto</a>"
        "<span class='year'>Anno: 2002</span>"
        "</div>"
        "<div class='item'>"
        "<a class='name' href='/play/naruto-shippuden'>Naruto Shippuden</a>"
        "<span class='year'>Anno: 2007</span>"
        "</div>"
    )

    def test_animeworld_parses_the_recorded_single_quoted_html(self):
        items = AnimeWorldScraper().parse_html(self.REAL_SHAPE_HTML)
        self.assertEqual(
            [(i.title, i.year, i.source_url) for i in items],
            [
                ("Naruto", 2002, "https://www.animeworld.ac/play/naruto-1"),
                ("Naruto Shippuden", 2007, "https://www.animeworld.ac/play/naruto-shippuden"),
            ],
        )

    @patch.object(AnimeWorldScraper, "fetch_url")
    def test_animeworld_search_unwraps_the_html_envelope(self, fetch):
        fetch.return_value = {"html": self.REAL_SHAPE_HTML}
        items = AnimeWorldScraper()._execute_run(query="naruto")
        self.assertEqual(
            [(i.title, i.year) for i in items],
            [("Naruto", 2002), ("Naruto Shippuden", 2007)],
        )
