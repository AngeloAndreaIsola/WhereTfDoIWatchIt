from unittest.mock import patch

from django.test import TestCase

from tracker.models import CatalogueDiscoveryState, Provider
from tracker.scrapers.adapters import KissKHScraper
from tracker.services.catalogue_discovery import CatalogueDiscoveryError, _page, run_discovery


def observed_page(*, page=1, page_size=20, total=41, row_id=10973, title="Sample Drama"):
    # Sanitized schema based on the live KissKH List API response. The live
    # rows expose id/title/episodesCount/etc.; they do not expose year or type.
    return {
        "page": page,
        "pageSize": page_size,
        "totalCount": total,
        "data": [
            {
                "id": row_id,
                "title": title,
                "episodesCount": 6,
                "label": "",
                "thumbnail": "https://images.example/thumbnail.jpg",
                "favoriteID": 0,
            }
        ],
    }


class KissKHCatalogueParserTests(TestCase):
    def setUp(self):
        self.scraper = KissKHScraper()

    def test_live_schema_extracts_native_id_and_uses_requested_partition_type(self):
        self.scraper._fetch_api = lambda _path: observed_page()
        items, terminal, pages, evidence = _page(
            self.scraper, "kisskh", "1", 1
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Sample Drama")
        self.assertEqual(items[0].content_type, "series")
        self.assertEqual(items[0].year, None)
        self.assertEqual(items[0].raw_payload["id"], 10973)
        self.assertEqual(items[0].source_url, "https://kisskh.do/Drama/10973")
        self.assertEqual((terminal, pages, evidence), (False, 3, "api_total_count"))

    def test_movie_partition_remains_distinct_from_series_partition(self):
        row = {"id": 91, "title": "Same title", "episodesCount": 1}
        movies = self.scraper.parse_items([row], content_type="movie")
        series = self.scraper.parse_items([row], content_type="series")
        self.assertEqual(movies[0].content_type, "movie")
        self.assertEqual(series[0].content_type, "series")
        self.assertEqual(len(self.scraper._dedupe(movies + series)), 2)

    def test_repeated_native_id_within_partition_is_deduplicated(self):
        rows = [
            {"id": 91, "title": "Same title", "episodesCount": 1},
            {"id": 91, "title": "Same title", "episodesCount": 1},
        ]
        parsed = self.scraper.parse_items(rows, content_type="movie")
        self.assertEqual(len(self.scraper._dedupe(parsed)), 1)

    def test_detail_metadata_type_and_release_date_are_parsed_without_language_inference(self):
        detail = {
            "id": 9987,
            "title": "A Drama",
            "type": "TVSeries",
            "releaseDate": "2025-01-03T19:40:27",
            "country": "Philippines",
            "episodesCount": 19,
            "episodes": [{"id": 181765, "number": 16.6, "sub": 5}],
        }
        item = self.scraper.parse_items([detail], content_type="movie")[0]
        self.assertEqual(item.content_type, "series")
        self.assertEqual(item.year, 2025)
        self.assertEqual(item.audio_languages, [])
        self.assertEqual(item.subtitle_languages, [])
        self.assertEqual(item.raw_payload["episodesCount"], 19)

    def test_unknown_provider_detail_type_keeps_partition_signal(self):
        item = self.scraper.parse_items(
            [{"id": 1, "title": "Unknown type", "type": "Other"}],
            content_type="series",
        )[0]
        self.assertEqual(item.content_type, "series")

    def test_single_episode_anime_is_skipped_without_movie_series_evidence(self):
        items = self.scraper.parse_items(
            [{"id": 5972, "title": "Anime Film", "episodesCount": 1}],
            content_type="anime",
        )
        self.assertEqual(items, [])

    def test_page_two_and_reported_terminal_page_use_api_totals(self):
        self.scraper._fetch_api = lambda _path: observed_page(
            page=3, page_size=20, total=41
        )
        _, terminal, pages, _ = _page(self.scraper, "kisskh", "2", 3)
        self.assertTrue(terminal)
        self.assertEqual(pages, 3)

    def test_zero_total_empty_page_is_a_valid_terminal_catalogue(self):
        self.scraper._fetch_api = lambda _path: {
            "page": 1, "pageSize": 20, "totalCount": 0, "data": []
        }
        items, terminal, pages, _ = _page(self.scraper, "kisskh", "2", 1)
        self.assertEqual(items, [])
        self.assertTrue(terminal)
        self.assertEqual(pages, 0)

    def test_api_error_payload_is_not_treated_as_an_empty_catalogue(self):
        self.scraper._fetch_api = lambda _path: {"error": "upstream error", "data": []}
        result = _page(self.scraper, "kisskh", "2", 1)
        self.assertEqual(result[0], None)
        self.assertEqual(result[3], "api_error_response")

    def test_malformed_rows_and_pagination_metadata_fail_closed(self):
        self.scraper._fetch_api = lambda _path: {
            **observed_page(), "data": [{"title": "Missing native ID"}]
        }
        self.assertEqual(_page(self.scraper, "kisskh", "2", 1)[3], "malformed_listing_rows")
        self.scraper._fetch_api = lambda _path: observed_page(page=2)
        self.assertEqual(_page(self.scraper, "kisskh", "2", 1)[3], "inconsistent_pagination_metadata")

    def test_empty_page_before_reported_terminal_is_an_error(self):
        self.scraper._fetch_api = lambda _path: {
            "page": 2, "pageSize": 20, "totalCount": 41, "data": []
        }
        self.assertEqual(_page(self.scraper, "kisskh", "2", 2)[3], "empty_page_before_reported_terminal_page")

    def test_anime_partition_with_unclassified_single_episode_blocks_page(self):
        self.scraper._fetch_api = lambda _path: observed_page(
            total=41,
            title="Single episode anime",
        ) | {"data": [{"id": 5972, "title": "Anime Film", "episodesCount": 1}]}
        result = _page(self.scraper, "kisskh", "3", 1)
        self.assertIsNone(result[0])
        self.assertEqual(result[3], "unclassified_or_malformed_anime_rows")


class KissKHCatalogueRequestTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.get(slug="kisskh")

    @patch("tracker.services.catalogue_discovery.requests.Session.get")
    def test_discovery_decodes_json_and_scans_one_bounded_live_schema_page(self, get):
        class Response:
            def __init__(self, *, body="", payload=None, content_type="text/plain"):
                self.status_code = 200
                self.headers = {"Content-Type": content_type}
                self.text = body
                self._payload = payload

            def json(self):
                if self._payload is None:
                    raise ValueError("not JSON")
                return self._payload

        get.side_effect = [
            Response(body="User-agent: *\nAllow: /"),
            Response(payload=observed_page(total=41), content_type="application/json"),
        ]
        result = run_discovery(
            "kisskh", request_budget=2, delay=0, retry_limit=0, dry_run=True
        )
        self.assertEqual(result["pages_scanned"], 1)
        self.assertEqual(result["items_unique"], 1)
        self.assertEqual(result["requests_made"], 2)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertFalse(CatalogueDiscoveryState.objects.filter(provider=self.provider).exists())

    @patch("tracker.services.catalogue_discovery.requests.Session.get")
    def test_json_shaped_html_challenge_is_not_parsed_as_api_data(self, get):
        class Response:
            status_code = 200
            headers = {"Content-Type": "text/html"}
            text = "<html>access page</html>"

            def json(self):
                return observed_page()

        class Robots:
            status_code = 200
            headers = {"Content-Type": "text/plain"}
            text = "User-agent: *\nAllow: /"

            def json(self):
                raise ValueError("not JSON")

        get.side_effect = [Robots(), Response()]
        with self.assertRaises(CatalogueDiscoveryError) as caught:
            run_discovery("kisskh", request_budget=2, delay=0, retry_limit=0)
        report = caught.exception.report
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["last_error"], "missing_or_unexpected_api_response")
        self.assertEqual(report["requests_made"], 2)

    @patch("tracker.services.catalogue_discovery.requests.Session.get")
    def test_robots_disallow_prevents_api_request(self, get):
        class Robots:
            status_code = 200
            headers = {"Content-Type": "text/plain"}
            text = "User-agent: *\nDisallow: /api/"

            def json(self):
                raise ValueError("not JSON")

        get.return_value = Robots()
        with self.assertRaises(CatalogueDiscoveryError) as caught:
            run_discovery("kisskh", request_budget=3, delay=0, retry_limit=0)
        report = caught.exception.report
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["last_error"], "HTTP 403")
        self.assertEqual(report["requests_made"], 1)
        self.assertEqual(get.call_count, 1)

