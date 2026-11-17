"""Tests for the new unofficial scrapers (StreamingCommunity, CineBlog01, AltaDefinizione).

Fixtures mirror the *actual* live markup: image-only anchors whose titles live
in ``data-title``/``title`` attributes, not in anchor text.
"""
from unittest.mock import patch

from django.test import TestCase

from tracker.scrapers import (
    AltaDefinizioneScraper,
    CineBlog01Scraper,
    HydraHDScraper,
    KissKHScraper,
    SCRAPER_REGISTRY,
    StreamingCommunityScraper,
    resolve_scraper_class,
)
from tracker.scrapers.listing import items_from_listing


# --- StreamingCommunity: live tiles use /titles/{id}-{slug} + data-title ---

SC_PAGE = """
<html><body>
<div class="owl-carousel">
<a href="/titles/28205-limperatrice-streaming" class="tile-image"
   data-title="L'imperatrice" data-id="28205" data-year="2022"
   data-kind="series" data-sub="" data-imdbid="tt13720112"><img alt="L'imperatrice"></a>
<a href="/titles/33477-street-fighter-streaming"
   data-title="Street Fighter" data-id="33477" data-year="2026"
   data-kind="movie"><img src="x.jpg"></a>
<a href="/titles/32167-spider-man-brand-new-day-streaming"
   data-title="Spider-Man: Brand New Day" data-id="32167" data-year="2026"
   data-kind="movie"><img src="y.jpg"></a>
</div>
</body></html>
"""


# --- CineBlog01: live tiles use absolute /cb01-streaming/... + title attr ---

CB_PAGE = """
<html><body>
<div class="promo-item">
<a href="https://cineblog001.run/cb01-streaming/35542-mammina-streaming-cb01.html"
   title="Mammina"><img class="promo-item-img" alt="Mammina"></a>
<a href="https://cineblog001.run/cb01-streaming/27123-il-signore-degli-anelli-streaming-cb01.html"
   title="Il Signore degli Anelli - Gli Anelli del Potere (2024)"><img alt="Il Signore"></a>
<a href="https://cineblog001.run/cb01-streaming/35370-dead-end-job-streaming-cb01.html"
   title="Dead-End Job [ITA] [HD] (2024)"><img alt="Dead-End Job"></a>
</div>
</body></html>
"""


# --- AltaDefinizione: live tiles use data-title / data-year / data-kind ---

AD_PAGE = """
<html><body>
<div class="movie-item" data-imdb="6.0" data-year="2024" data-category="Romantico">
<a href="/tv-show/45584-un-appuntamento-sotto-lalbero-streaming.html"
   data-title="Un appuntamento sotto l'albero" data-id="45584"
   data-kind="movie" data-year="2024" data-sub=""><img alt="Un appuntamento"></a>
<a href="/documentario/45583-mi-ameranno-quando-saro-morto-streaming.html"
   data-title="Mi ameranno quando sarò morto [SUB-ITA]" data-id="45583"
   data-kind="movie" data-year="1999" data-sub="ITA"><img alt="Mi ameranno"></a>
</div>
</body></html>
"""


class ListingParserTest(TestCase):
    def test_reads_title_year_type_from_data_attributes(self):
        items = items_from_listing(
            SC_PAGE,
            href_regex=r"/titles/\d+-",
            provider_slug="streamingcommunity",
            base_url="https://streaming-community.army",
        )
        self.assertEqual(len(items), 3)
        by_url = {i.source_url: i for i in items}
        imp = by_url["https://streaming-community.army/titles/28205-limperatrice-streaming"]
        self.assertEqual(imp.title, "L'imperatrice")
        self.assertEqual(imp.year, 2022)
        self.assertEqual(imp.content_type, "series")
        self.assertEqual(imp.raw_payload["title_id"], "28205")

    def test_query_filter(self):
        items = items_from_listing(
            SC_PAGE,
            href_regex=r"/titles/\d+-",
            provider_slug="streamingcommunity",
            base_url="https://streaming-community.army",
            query_filter="street fighter",
        )
        self.assertEqual([i.title for i in items], ["Street Fighter"])

    def test_bracket_languages_normalized(self):
        items = items_from_listing(
            CB_PAGE,
            href_regex=r"/cb01-streaming/\d+-.*-streaming-cb01\.html$",
            provider_slug="cineblog01",
            base_url="https://cineblog001.run",
        )
        self.assertEqual(len(items), 3)
        dead_end = next(i for i in items if i.title == "Dead-End Job")
        self.assertIn("IT", dead_end.audio_languages)
        self.assertEqual(dead_end.year, 2024)

    def test_data_sub_adds_subtitle_language(self):
        items = items_from_listing(
            AD_PAGE,
            href_regex=r"/\d+-.*-streaming\.html$",
            provider_slug="altadefinizione",
            base_url="https://altadefinizionex.ink",
        )
        self.assertEqual(len(items), 2)
        subbed = next(i for i in items if "Mi ameranno" in i.title)
        self.assertIn("IT", subbed.subtitle_languages)

    def test_ignores_ui_buttons_on_title_links(self):
        page = """
        <a href="/titles/999-real-title-streaming" data-title="Real Title"><img alt="Real Title"></a>
        <a href="/titles/999-real-title-streaming" class="info-button favorite"
           aria-label="La mia lista">La mia lista</a>
        <a href="/titles/999-real-title-streaming/watching" class="play2">Riproduci</a>
        """
        items = items_from_listing(
            page,
            href_regex=r"/titles/\d+-",
            provider_slug="streamingcommunity",
            base_url="https://streaming-community.army",
        )
        self.assertEqual([i.title for i in items], ["Real Title"])


class StreamingCommunityScraperTest(TestCase):
    @patch.object(StreamingCommunityScraper, "fetch_url")
    def test_browse_returns_items(self, mock_fetch):
        mock_fetch.return_value = SC_PAGE
        items = StreamingCommunityScraper().run(query=None)
        # 4 listing pages, deduped by numeric title id -> 3 unique titles
        self.assertEqual(len(items), 3)
        self.assertTrue(all(i.provider_slug == "streamingcommunity" for i in items))
        self.assertIn("Street Fighter", [i.title for i in items])

    @patch.object(StreamingCommunityScraper, "fetch_url")
    def test_cloudflare_only_returns_empty(self, mock_fetch):
        mock_fetch.return_value = "<html>Attention Required! | Cloudflare</html>"
        self.assertEqual(StreamingCommunityScraper().run(query="test"), [])


class CineBlog01ScraperTest(TestCase):
    @patch.object(CineBlog01Scraper, "fetch_url")
    def test_browse_uses_pagination_and_title_attributes(self, mock_fetch):
        mock_fetch.return_value = CB_PAGE
        items = CineBlog01Scraper().run(query=None)
        # 8 page fetches, deduped by URL -> 3 unique titles
        self.assertEqual(len(items), 3)
        self.assertEqual(mock_fetch.call_count, 8)
        self.assertTrue(all(i.provider_slug == "cineblog01" for i in items))
        self.assertIn("Mammina", [i.title for i in items])

    def test_detect_languages_from_title(self):
        from tracker.scrapers.cineblog01 import (
            clean_title,
            detect_languages_from_title,
            extract_year_from_title,
        )

        self.assertEqual(detect_languages_from_title("Movie [ITA] [HD] (2024)"), (["it"], []))
        self.assertEqual(detect_languages_from_title("Movie [Sub-ITA] [HD] (2024)"), ([], ["it"]))
        self.assertEqual(
            detect_languages_from_title("Movie [ITA] [Sub-ENG] [HD] (2024)"),
            (["it"], ["en"]),
        )
        self.assertEqual(clean_title("Movie [ITA] [HD] (2024)"), "Movie")
        self.assertEqual(extract_year_from_title("Movie (2024)"), 2024)


class AltaDefinizioneScraperTest(TestCase):
    @patch.object(AltaDefinizioneScraper, "fetch_url")
    def test_browse_returns_items_with_years(self, mock_fetch):
        mock_fetch.return_value = AD_PAGE
        items = AltaDefinizioneScraper().run(query=None)
        # 4 listing pages, deduped by URL -> 2 unique titles
        self.assertEqual(len(items), 2)
        self.assertEqual(mock_fetch.call_count, 4)
        self.assertTrue(all(i.provider_slug == "altadefinizione" for i in items))
        self.assertTrue(all(i.year for i in items))

    def test_detect_languages(self):
        from tracker.scrapers.altadefinizione import detect_languages

        text = "Audio: ITA, ENG. Subtitle: SUB-ITA, SUB-ENG"
        audio = detect_languages(text, {"it": ["ITA"], "en": ["ENG"]})
        self.assertIn("it", audio)
        self.assertIn("en", audio)


class LegacyScraperPaginationTest(TestCase):
    @patch.object(KissKHScraper, "fetch_url")
    def test_kisskh_requests_large_pages_for_every_type(self, mock_fetch):
        rows = [{"id": i, "title": f"Movie {i}"} for i in range(200)]

        def fake_fetch(url, *args, **kwargs):
            return {"data": rows} if "type=2" in url else {"data": []}

        mock_fetch.side_effect = fake_fetch
        items = KissKHScraper().run()

        self.assertEqual(len(items), 200)
        urls = [c.args[0] for c in mock_fetch.call_args_list]
        self.assertTrue(all("pageSize=200" in u for u in urls))
        # Full pages keep going; short pages stop after the first request.
        self.assertEqual(sum("type=2" in u for u in urls), 3)
        self.assertEqual(sum("type=1" in u for u in urls), 1)

    @patch.object(HydraHDScraper, "fetch_url")
    def test_hydrahd_paginates_on_canonical_domain(self, mock_fetch):
        page = (
            '<a class="hthis" href="/movie/1-test"><img></a>'
            '<div class="title detz">Test</div>'
        )
        mock_fetch.return_value = page
        HydraHDScraper().run()

        urls = [c.args[0] for c in mock_fetch.call_args_list]
        self.assertEqual(len(urls), 20)  # 10 pages x 2 categories
        self.assertTrue(all(u.startswith("https://hydrahd.gd/") for u in urls))
        self.assertTrue(any("/movies/date/10" in u for u in urls))


class NewScrapersRegistryTest(TestCase):
    def test_scrapers_registered(self):
        for slug in ["streamingcommunity", "cineblog01", "altadefinizione"]:
            self.assertIn(slug, SCRAPER_REGISTRY)
            cls = resolve_scraper_class(slug)
            self.assertIsNotNone(cls)
            self.assertEqual(cls.provider_slug, slug)
            self.assertIsInstance(cls(), cls)

    def test_live_domains_are_first(self):
        from tracker.scrapers.altadefinizione import VERIFIED_DOMAINS as AD_DOMAINS
        from tracker.scrapers.cineblog01 import VERIFIED_DOMAINS as CB_DOMAINS
        from tracker.scrapers.streamingcommunity import VERIFIED_DOMAINS as SC_DOMAINS

        self.assertEqual(SC_DOMAINS[0], "streaming-community.army")
        self.assertEqual(CB_DOMAINS[0], "cineblog001.run")
        self.assertEqual(AD_DOMAINS[0], "altadefinizionex.ink")
