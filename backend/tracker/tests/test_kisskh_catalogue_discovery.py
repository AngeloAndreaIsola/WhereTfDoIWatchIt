from tracker.tests.discovery_fixtures import enable_discovery_fixtures, FixtureResponse
from unittest.mock import patch

from django.test import TestCase

from tracker.models import CatalogueDiscoveryState, KissKHPendingClassification, Provider, ScrapedContentMapping
from tracker.scrapers.adapters import KissKHScraper
from tracker.scrapers.base import ScrapedItem
from tracker.services.catalogue_discovery import CatalogueDiscoveryError, CataloguePageRows, _page, resolve_kisskh_pending_classification, run_discovery


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
        enable_discovery_fixtures(self)
        self.scraper = KissKHScraper()

    def test_listing_schema_keeps_unclassified_rows_pending(self):
        self.scraper._fetch_api = lambda _path: observed_page()
        items, terminal, pages, evidence = _page(
            self.scraper, "kisskh", "1", 1
        )
        self.assertEqual(len(items), 0)
        self.assertEqual(len(items.pending), 1)
        self.assertEqual(items.pending[0]["id"], 10973)
        self.assertEqual(items.pending[0]["title"], "Sample Drama")
        self.assertEqual((terminal, pages, evidence), (False, 3, "api_total_count"))

    def test_explicit_detail_types_keep_same_native_id_separate(self):
        row = {"id": 91, "title": "Same title", "type": "Movie"}
        movie = self.scraper.parse_items([row], content_type="series")
        row = {"id": 91, "title": "Same title", "type": "TVSeries"}
        series = self.scraper.parse_items([row], content_type="movie")
        movies = movie
        self.assertEqual(movies[0].content_type, "movie")
        self.assertEqual(series[0].content_type, "series")
        self.assertEqual(len(self.scraper._dedupe(movies + series)), 2)

    def test_repeated_native_id_within_partition_is_deduplicated(self):
        rows = [{"id": 91, "title": "Same title", "type": "Movie"}] * 2
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
        metadata = self.scraper.extract_detail_metadata(detail, native_id="9987")
        self.assertEqual(metadata["source_media_type"], "TVSeries")
        self.assertEqual(metadata["release_year"], 2025)
        self.assertEqual(metadata["episode_count"], 19)
        self.assertEqual(metadata["episode_ids"], ["181765"])
        self.assertNotIn("audio_languages", metadata)
        self.assertNotIn("subtitle_languages", metadata)

    def test_episode_count_never_overrides_explicit_detail_type(self):
        movie = self.scraper.parse_items([
            {"id": 5, "title": "Grouped film", "type": "Movie", "episodesCount": 6}
        ])[0]
        one_episode_series = self.scraper.parse_items([
            {"id": 6, "title": "Single episode show", "type": "TVSeries", "episodesCount": 1}
        ])[0]
        self.assertEqual(movie.content_type, "movie")
        self.assertEqual(one_episode_series.content_type, "series")

    def test_episode_count_and_anime_type_never_guess_movie_or_series(self):
        for count in (1, 12):
            items = self.scraper.parse_items(
                [{"id": count, "title": "Unknown", "type": "Anime", "episodesCount": count}],
                content_type="series",
            )
            self.assertEqual(items, [])

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

    def test_boundary_pages_and_clamped_beyond_last_page(self):
        last_rows = [{"id": n, "title": f"Title {n}"} for n in range(19)]
        self.scraper._fetch_api = lambda _path: {
            "page": 345, "pageSize": 20, "totalCount": 6899, "data": last_rows
        }
        _, terminal, pages, _ = _page(self.scraper, "kisskh", "1", 345)
        self.assertTrue(terminal)
        self.assertEqual(pages, 345)
        _, terminal, pages, evidence = _page(self.scraper, "kisskh", "1", 346)
        self.assertTrue(terminal)
        self.assertEqual(pages, 345)
        self.assertEqual(evidence, "api_clamped_terminal_page")

    def test_all_partitions_use_page_size_and_partition_parameters(self):
        paths = []
        self.scraper._fetch_api = lambda path: (paths.append(path) or observed_page())
        for part in ("1", "2", "3"):
            _page(self.scraper, "kisskh", part, 1)
        self.assertTrue(all("pageSize=20" in path for path in paths))
        self.assertEqual([f"type={part}" in path for path, part in zip(paths, ("1", "2", "3"))], [True, True, True])

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

    def test_anime_partition_with_unclassified_single_episode_is_pending(self):
        self.scraper._fetch_api = lambda _path: observed_page(
            total=41,
            title="Single episode anime",
        ) | {"data": [{"id": 5972, "title": "Anime Film", "episodesCount": 1}]}
        result = _page(self.scraper, "kisskh", "3", 1)
        self.assertEqual(len(result[0]), 0)
        self.assertEqual(len(result[0].pending), 1)
        self.assertEqual(result[3], "api_total_count")


class KissKHCatalogueRequestTests(TestCase):
    def setUp(self):
        enable_discovery_fixtures(self)
        self.provider = Provider.objects.get(slug="kisskh")

    @patch("tracker.services.discovery_http.DiscoveryHTTP._send")
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

    @patch("tracker.services.discovery_http.DiscoveryHTTP._send")
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

    @patch("tracker.services.discovery_http.DiscoveryHTTP._send")
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


class KissKHPendingClassificationTests(TestCase):
    def setUp(self):
        enable_discovery_fixtures(self)
        self.provider = Provider.objects.get_or_create(
            slug="kisskh", defaults={"name": "KissKH", "category": "scraper"}
        )[0]

    def test_partial_page_persists_typed_rows_and_pending_rows_before_checkpoint(self):
        typed = ScrapedItem(
            title="Known movie", provider_slug="kisskh", content_type="movie",
            source_url="https://kisskh.do/Drama/1", raw_payload={"id": "1"},
        )
        pending = {"id": 2, "title": "Unknown title", "episodesCount": 12}
        def page(_scraper, _slug, _part, _page, query=None):
            _scraper.session.get("https://fixture.invalid/page")
            return CataloguePageRows([typed], [pending], total_count=2), True, 1, "api_total_count"
        with patch("tracker.services.catalogue_discovery._page", side_effect=page), \
             patch("tracker.services.catalogue_discovery._parts", return_value=["2"]), \
             patch("tracker.services.discovery_http.DiscoveryHTTP._send", return_value=FixtureResponse()):
            result = run_discovery("kisskh", request_budget=2, delay=0)
        self.assertEqual(result["items_inserted"], 2)
        self.assertEqual(result["items_pending_classification"], 1)
        self.assertEqual(ScrapedContentMapping.objects.filter(provider=self.provider).count(), 1)
        unresolved = KissKHPendingClassification.objects.get(provider=self.provider)
        self.assertEqual(unresolved.native_id, "2")
        state = CatalogueDiscoveryState.objects.get(provider=self.provider)
        self.assertEqual(state.cursor["parts"]["2"], 1)
        self.assertEqual(state.status, "PARTIAL")
        state.cursor = {}
        state.save(update_fields=["cursor", "updated_at"])
        with patch("tracker.services.catalogue_discovery._page", side_effect=page), \
             patch("tracker.services.catalogue_discovery._parts", return_value=["2"]), \
             patch("tracker.services.discovery_http.DiscoveryHTTP._send", return_value=FixtureResponse()):
            replay = run_discovery("kisskh", request_budget=2, delay=0)
        self.assertEqual(replay["pending_unchanged"], 1)
        self.assertEqual(KissKHPendingClassification.objects.filter(provider=self.provider).count(), 1)

    def test_detail_retry_keeps_ambiguity_then_promotes_from_explicit_type(self):
        pending = KissKHPendingClassification.objects.create(
            provider=self.provider, native_id="55", title="Film or show", partition="3",
            detail_url="https://kisskh.do/Drama/55", listing_observation={"id": 55, "title": "Film or show"},
        )
        self.assertFalse(ScrapedContentMapping.objects.filter(provider=self.provider).exists())
        self.assertFalse(resolve_kisskh_pending_classification(
            pending, {"id": 55, "title": "Film or show", "type": "Anime", "episodesCount": 1}
        ))
        pending.refresh_from_db()
        self.assertEqual(pending.status, "PENDING")
        self.assertEqual(pending.detail_attempts, 1)
        self.assertTrue(resolve_kisskh_pending_classification(
            pending, {"id": 55, "title": "Film or show", "type": "TVSeries", "episodesCount": 1}
        ))
        pending.refresh_from_db()
        self.assertEqual(pending.status, "RESOLVED")
        self.assertEqual(pending.resolved_content_type, "series")
        mapping = ScrapedContentMapping.objects.get(provider=self.provider)
        self.assertEqual(mapping.scraped_content_type, "series")
        self.assertIsNone(mapping.movie_id)
        self.assertFalse(mapping.is_confirmed)
        self.assertEqual(mapping.raw_payload["listing_observation"]["id"], 55)
        self.assertEqual(mapping.raw_payload["detail_enrichment"]["source_media_type"], "TVSeries")

    def test_page_one_records_survive_failure_on_page_two(self):
        pending_row = {"id": 88, "title": "Not yet classified"}
        calls = {"count": 0}
        def page(scraper, _slug, _part, number, query=None):
            scraper.session.get("https://fixture.invalid/page")
            calls["count"] += 1
            if number == 2:
                raise RuntimeError("fixture later page failure")
            return CataloguePageRows([], [pending_row], total_count=40), False, 2, "api_total_count"
        with patch("tracker.services.catalogue_discovery._page", side_effect=page), \
             patch("tracker.services.catalogue_discovery._parts", return_value=["2"]), \
             patch("tracker.services.discovery_http.DiscoveryHTTP._send", return_value=FixtureResponse()):
            with self.assertRaises(CatalogueDiscoveryError):
                run_discovery("kisskh", request_budget=3, delay=0)
        self.assertTrue(KissKHPendingClassification.objects.filter(provider=self.provider, native_id="88").exists())
        state = CatalogueDiscoveryState.objects.get(provider=self.provider)
        self.assertEqual(state.cursor["parts"]["2"], 1)
