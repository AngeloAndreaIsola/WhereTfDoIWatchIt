"""Tests for independent provider scrapers."""
from unittest.mock import Mock, patch
from django.test import TestCase

from tracker.scrapers import KissKHScraper, HydraHDScraper, AnimeWorldScraper
from tracker.scrapers.base import ScrapedItem


class KissKHScraperTest(TestCase):
    """Test KissKH scraper implementation."""

    def setUp(self):
        self.scraper = KissKHScraper()

    def test_no_query_returns_empty(self):
        """Scraper returns empty list when no query provided."""
        result = self.scraper.run(query=None)
        self.assertEqual(result, [])
        result = self.scraper.run(query="")
        self.assertEqual(result, [])

    @patch.object(KissKHScraper, 'fetch_url')
    def test_parse_valid_response(self, mock_fetch):
        """Scraper correctly parses valid KissKH API response."""
        mock_fetch.return_value = [
            {
                "id": 12139,
                "title": "Inception",
                "year": 2010,
                "rating": 8.8,
            }
        ]

        items = self.scraper.run(query="Inception")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Inception")
        self.assertEqual(items[0].year, 2010)
        self.assertEqual(items[0].provider_slug, "kisskh")
        self.assertIn("kisskh.co", items[0].source_url)

    @patch.object(KissKHScraper, 'fetch_url')
    def test_handles_no_response(self, mock_fetch):
        """Scraper handles None response gracefully."""
        mock_fetch.return_value = None
        items = self.scraper.run(query="Test")
        self.assertEqual(items, [])

    @patch.object(KissKHScraper, 'fetch_url')
    def test_handles_wrong_response_type(self, mock_fetch):
        """Scraper handles unexpected response type gracefully."""
        mock_fetch.return_value = {"error": "Not found"}
        items = self.scraper.run(query="Test")
        self.assertEqual(items, [])

    @patch.object(KissKHScraper, 'fetch_url')
    def test_missing_fields_handled(self, mock_fetch):
        """Scraper handles missing fields in response."""
        mock_fetch.return_value = [
            {"id": 123},  # Missing title
            {"title": "Valid Title", "id": 456},  # Valid
        ]

        items = self.scraper.run(query="Test")

        # Should skip invalid entry but parse valid one
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Valid Title")


class AnimeWorldScraperTest(TestCase):
    """Test AnimeWorld scraper implementation."""

    def setUp(self):
        self.scraper = AnimeWorldScraper()

    def test_no_query_returns_empty(self):
        """Scraper returns empty list when no query provided."""
        result = self.scraper.run(query=None)
        self.assertEqual(result, [])

    @patch.object(AnimeWorldScraper, 'fetch_url')
    def test_parse_html_response(self, mock_fetch):
        """Scraper correctly parses HTML wrapped in JSON."""
        # Realistic AnimeWorld API response structure
        html_content = '''
            <div class="item">
                <a href="play/naruto.abc123"><img class="thumb" src="img.png"></a>
                <div class="info">
                    <a class="name" href="play/naruto.abc123">Naruto</a>
                    <p>Genere: <a href="../../genre/action">Azione</a><br>
                    <p>Tipo: <a href="animes">Anime</a> - Anno: 2002 - Stato: Finito</p>
                </div>
            </div>
            <div class="item">
                <a class="name" href="play/naruto-shippuden.def456">Naruto Shippuden</a>
                <p>Tipo: <a href="animes">Anime</a> - Anno: 2007 - Stato: Finito</p>
            </div>
        '''
        mock_fetch.return_value = {"html": html_content}

        items = self.scraper.run(query="Naruto")

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "Naruto")
        self.assertEqual(items[0].year, 2002)
        self.assertEqual(items[0].provider_slug, "animeworld")
        self.assertIn("animeworld.ac", items[0].source_url)

        self.assertEqual(items[1].title, "Naruto Shippuden")
        self.assertEqual(items[1].year, 2007)

    @patch.object(AnimeWorldScraper, 'fetch_url')
    def test_handles_no_html_key(self, mock_fetch):
        """Scraper handles dict response without 'html' key."""
        mock_fetch.return_value = {"error": "Not found"}
        items = self.scraper.run(query="Test")
        self.assertEqual(items, [])

    @patch.object(AnimeWorldScraper, 'fetch_url')
    def test_handles_empty_html(self, mock_fetch):
        """Scraper handles empty HTML content."""
        mock_fetch.return_value = {"html": ""}
        items = self.scraper.run(query="Test")
        self.assertEqual(items, [])

    @patch.object(AnimeWorldScraper, 'fetch_url')
    def test_handles_malformed_html(self, mock_fetch):
        """Scraper handles HTML with no matching patterns."""
        mock_fetch.return_value = {"html": "<div>No anime here</div>"}
        items = self.scraper.run(query="Test")
        self.assertEqual(items, [])

    @patch.object(AnimeWorldScraper, 'fetch_url')
    def test_relative_urls_resolved(self, mock_fetch):
        """Scraper resolves relative URLs to absolute."""
        html_content = '<a class="name" href="play/test.xyz">Test Anime</a>'
        mock_fetch.return_value = {"html": html_content}

        items = self.scraper.run(query="Test")

        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].source_url.startswith("https://"))
        self.assertIn("animeworld.ac", items[0].source_url)

    @patch.object(AnimeWorldScraper, 'fetch_url')
    def test_year_extraction_fallback(self, mock_fetch):
        """Scraper handles missing year gracefully."""
        html_content = '<a class="name" href="play/test.xyz">Test Anime</a>'
        mock_fetch.return_value = {"html": html_content}

        items = self.scraper.run(query="Test")

        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0].year)


class HydraHDScraperTest(TestCase):
    """Test HydraHD scraper implementation."""

    def setUp(self):
        self.scraper = HydraHDScraper()

    def test_no_query_returns_empty(self):
        """Scraper returns empty list when no query provided."""
        result = self.scraper.run(query=None)
        self.assertEqual(result, [])

    @patch.object(HydraHDScraper, 'fetch_url_with_headers')
    def test_parse_valid_response(self, mock_fetch):
        """Scraper correctly parses valid HydraHD API response."""
        mock_fetch.return_value = {
            "results": [
                {
                    "id": "abc123",
                    "title": "Inception",
                    "year": 2010,
                    "audio": ["EN", "RU"],
                    "subtitles": ["EN"],
                    "res": "2160p",
                }
            ]
        }

        items = self.scraper.run(query="Inception")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Inception")
        self.assertEqual(items[0].year, 2010)
        self.assertEqual(items[0].provider_slug, "hydrahd")
        self.assertIn("EN", items[0].audio_languages)

    @patch.object(HydraHDScraper, 'fetch_url_with_headers')
    def test_tries_multiple_domains(self, mock_fetch):
        """Scraper tries fallback domains on failure."""
        mock_fetch.side_effect = [None, {"results": [{"title": "Test", "id": "123"}]}]

        items = self.scraper.run(query="Test")

        # Should have tried both URLs
        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(len(items), 1)

    @patch.object(HydraHDScraper, 'fetch_url_with_headers')
    def test_handles_all_domains_failing(self, mock_fetch):
        """Scraper returns empty when all domains fail."""
        mock_fetch.return_value = None

        items = self.scraper.run(query="Test")

        self.assertEqual(items, [])
        # Should have tried both URLs
        self.assertEqual(mock_fetch.call_count, 2)

    @patch.object(HydraHDScraper, 'fetch_url_with_headers')
    def test_handles_empty_results(self, mock_fetch):
        """Scraper handles empty results array."""
        mock_fetch.return_value = {"results": []}

        items = self.scraper.run(query="Test")

        self.assertEqual(items, [])


class ScraperDiagnosticsTest(TestCase):
    """Test scraper diagnostic logging and error handling."""

    @patch('tracker.scrapers.adapters.logger')
    @patch.object(KissKHScraper, 'fetch_url')
    def test_logs_empty_query(self, mock_fetch, mock_logger):
        """Scraper logs when query is empty."""
        scraper = KissKHScraper()
        scraper.run(query=None)
        mock_logger.info.assert_called()

    @patch('tracker.scrapers.adapters.logger')
    @patch.object(AnimeWorldScraper, 'fetch_url')
    def test_logs_parsing_details(self, mock_fetch, mock_logger):
        """Scraper logs parsing progress."""
        html_content = '<a class="name" href="test">Test</a>'
        mock_fetch.return_value = {"html": html_content}

        scraper = AnimeWorldScraper()
        scraper.run(query="Test")

        # Should log HTML size, items found, items parsed
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        self.assertTrue(any("parsing HTML" in str(call) for call in info_calls))
        self.assertTrue(any("parsed" in str(call) for call in info_calls))

    @patch.object(KissKHScraper, 'fetch_url')
    def test_exception_handling(self, mock_fetch):
        """Scraper handles exceptions gracefully and returns empty list."""
        mock_fetch.side_effect = Exception("Network error")

        scraper = KissKHScraper()
        items = scraper.run(query="Test")

        # Should not raise exception, should return empty list
        self.assertEqual(items, [])
