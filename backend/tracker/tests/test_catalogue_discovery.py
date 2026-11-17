from unittest.mock import patch
from datetime import datetime, timezone

from django.test import TestCase

from availability.models import Offer
from tracker.models import CatalogueDiscoveryState, DiscoveredCatalogueRecord, Movie, Provider, ScrapedContentMapping
from tracker.scrapers.base import ScrapedItem
from tracker.services.catalogue_discovery import CatalogueDiscoveryError, CataloguePageRows, PROVIDERS, _HttpFailure, _persist_unclassified_observation, run_discovery
from tracker.services.scraped_mapping import persist_scraped_item


def fixture_item(slug, page=1, part=""):
    return ScrapedItem(
        title=f"Fixture {slug} {part} {page}", provider_slug=slug, year=2020,
        content_type="series" if (slug == "hydrahd" and part == "tv-shows") or (slug == "kisskh" and part in ("1", "3")) else "movie",
        source_url=f"https://{slug}.example/title/{part}/{page}",
        raw_payload={"id": f"{slug}-{part}-{page}", "unverified_discovery": True},
    )


class CatalogueDiscoveryTests(TestCase):
    def setUp(self):
        self.providers = {
            slug: Provider.objects.get_or_create(slug=slug, defaults={"name": slug, "category": "scraper"})[0]
            for slug in PROVIDERS
        }

    def test_fixture_walks_all_six_provider_strategies_and_marks_discoveries_unverified(self):
        for slug in sorted(PROVIDERS):
            with self.subTest(provider=slug):
                def page(_scraper, provider_slug, part, page, query=None):
                    _scraper.session.get("https://fixture.invalid/page")
                    return [fixture_item(provider_slug, part=part)], True, 1, "fixture_terminal_page"

                with patch("tracker.services.catalogue_discovery.PROVENANCE_BLOCKED", {}), patch("tracker.services.catalogue_discovery._page", side_effect=page), patch("requests.Session.get", return_value=object()):
                    mode = "incremental" if slug == "animeworld" else "historical"
                    result = run_discovery(slug, mode=mode, request_budget=10, delay=0)
                self.assertEqual(result["status"], "PARTIAL" if slug == "animeworld" else "COMPLETED")
                if slug != "animeworld":
                    self.assertEqual(result["pages_scanned"], len(result["checkpoint"]["done"]))
                if slug == "kisskh":
                    mappings = ScrapedContentMapping.objects.filter(provider=self.providers[slug])
                    self.assertEqual(mappings.count(), result["pages_scanned"])
                    self.assertTrue(all(mapping.raw_payload["unverified_discovery"] for mapping in mappings))
                    self.assertFalse(any(mapping.is_confirmed for mapping in mappings))
                else:
                    records = DiscoveredCatalogueRecord.objects.filter(provider=self.providers[slug])
                    self.assertEqual(records.count(), result["pages_scanned"])
                    self.assertTrue(all(record.classification_state == "PENDING" for record in records))
                    self.assertTrue(all(record.classified_content_type is None for record in records))
                    self.assertFalse(ScrapedContentMapping.objects.filter(provider=self.providers[slug]).exists())
                    self.assertFalse(Offer.objects.filter(provider=self.providers[slug]).exists())
                self.assertEqual(
                    CatalogueDiscoveryState.objects.get(provider=self.providers[slug]).status,
                    "PARTIAL" if slug == "animeworld" else "COMPLETED",
                )

    def test_budget_exhaustion_is_partial_and_resume_uses_committed_checkpoint(self):
        slug = "hydrahd"

        def pages(_scraper, provider_slug, part, page, query=None):
            _scraper.session.get("https://fixture.invalid/page")
            return [fixture_item(provider_slug, page)], page == 2, 2, "fixture_terminal_page"

        with patch("tracker.services.catalogue_discovery._parts", return_value=["movies"]), \
             patch("tracker.services.catalogue_discovery._page", side_effect=pages), patch("requests.Session.get", return_value=object()):
            first = run_discovery(slug, request_budget=1, delay=0)
            self.assertEqual(first["status"], "PARTIAL")
            self.assertEqual(first["pages_remaining"], 1)
            second = run_discovery(slug, request_budget=2, delay=0)
        self.assertEqual(second["status"], "COMPLETED")
        self.assertEqual(second["pages_scanned"], 1)
        self.assertEqual(DiscoveredCatalogueRecord.objects.filter(provider=self.providers[slug]).count(), 2)

    def test_hydra_partition_checkpoint_does_not_complete_other_partition(self):
        def one_page(scraper, provider_slug, part, page, query=None):
            scraper.session.get("https://fixture.invalid/page")
            return [fixture_item(provider_slug, part=part)], True, 1, "fixture_terminal_page"

        with patch("tracker.services.catalogue_discovery._page", side_effect=one_page), \
             patch("requests.Session.get", return_value=object()):
            result = run_discovery("hydrahd", partition="movies", request_budget=2, delay=0)
        state = CatalogueDiscoveryState.objects.get(provider=self.providers["hydrahd"])
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(state.cursor["parts"], {"movies": 1})
        self.assertEqual(state.cursor["done"], ["movies"])

    def test_hydra_navigation_window_changes_are_not_reported_as_total_or_completion(self):
        def rolling_pages(scraper, provider_slug, part, page, query=None):
            scraper.session.get("https://fixture.invalid/page")
            item = fixture_item(provider_slug, part=part, page=page)
            return [item], False, None, f"pagination_links_navigation_max={page + 3}"

        with patch("tracker.services.catalogue_discovery._page", side_effect=rolling_pages), \
             patch("requests.Session.get", return_value=object()):
            result = run_discovery("hydrahd", partition="movies", request_budget=3, delay=0)
        state = CatalogueDiscoveryState.objects.get(provider=self.providers["hydrahd"])
        self.assertEqual(result["pages_scanned"], 3)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertIsNone(result["pages_remaining"])
        self.assertEqual(result["navigation_max_observed"], {"movies": 6})
        self.assertEqual(state.cursor["parts"]["movies"], 3)
        self.assertEqual(state.cursor["navigation_max"]["movies"], 6)

    def test_hydra_http_404_does_not_complete_partition_or_advance_checkpoint(self):
        state = CatalogueDiscoveryState.objects.create(
            provider=self.providers["hydrahd"],
            status="PARTIAL",
            mode="full",
            cursor={"done": [], "parts": {"movies": 125}, "navigation_max": {"movies": 128}},
        )
        with patch("tracker.services.catalogue_discovery._parts", return_value=["movies"]), \
             patch("tracker.services.catalogue_discovery._page", side_effect=_HttpFailure(404)), \
             patch("requests.Session.get", return_value=object()):
            result = run_discovery(
                "hydrahd", partition="movies", request_budget=2, delay=0,
            )

        state.refresh_from_db()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["errors"], 1)
        self.assertIn("HTTP 404 is not verified as a terminal page", result["last_error"])
        self.assertEqual(result["checkpoint"]["parts"], {"movies": 125})
        self.assertEqual(state.cursor["parts"], {"movies": 125})
        self.assertEqual(state.cursor["done"], [])
        self.assertEqual(state.status, "PARTIAL")
        self.assertFalse(DiscoveredCatalogueRecord.objects.filter(provider=self.providers["hydrahd"]).exists())

    def test_hydra_empty_listing_is_a_terminal_candidate_not_completion(self):
        state = CatalogueDiscoveryState.objects.create(
            provider=self.providers["hydrahd"],
            status="PARTIAL",
            mode="full",
            cursor={"done": [], "parts": {"movies": 125}, "navigation_max": {"movies": 128}},
        )

        def empty_page(scraper, provider_slug, part, page, query=None):
            scraper.session.get("https://fixture.invalid/page")
            return [], True, None, "empty_page_terminal"

        with patch("tracker.services.catalogue_discovery._parts", return_value=["movies"]), \
             patch("tracker.services.catalogue_discovery._page", side_effect=empty_page), \
             patch("requests.Session.get", return_value=object()):
            result = run_discovery(
                "hydrahd", partition="movies", request_budget=2, delay=0,
            )

        state.refresh_from_db()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["terminal_page_candidate"], {
            "partition": "movies", "page": 126, "evidence": "empty_page_terminal",
        })
        self.assertEqual(result["checkpoint"]["parts"], {"movies": 125})
        self.assertEqual(state.cursor["parts"], {"movies": 125})
        self.assertEqual(state.cursor["done"], [])
        self.assertEqual(state.status, "PARTIAL")
        self.assertEqual(result["pages_scanned"], 1)
        self.assertEqual(result["items_seen"], 0)
        self.assertFalse(DiscoveredCatalogueRecord.objects.filter(provider=self.providers["hydrahd"]).exists())

    def test_incremental_checkpoint_does_not_reset_historical_progress(self):
        state = CatalogueDiscoveryState.objects.create(
            provider=self.providers["hydrahd"], status="PARTIAL", mode="full",
            cursor={"done": [], "parts": {"movies": 128, "tv-shows": 73}, "navigation_max": {"movies": 131, "tv-shows": 76}},
        )

        def page(scraper, provider_slug, part, page, query=None):
            scraper.session.get("https://fixture.invalid/page")
            return [fixture_item(provider_slug, part=part, page=page)], False, None, f"pagination_links_navigation_max={page + 3}"

        with patch("tracker.services.catalogue_discovery._parts", return_value=["movies"]), \
             patch("tracker.services.catalogue_discovery._page", side_effect=page), \
             patch("requests.Session.get", return_value=object()):
            result = run_discovery("hydrahd", mode="incremental", overlap_pages=2, request_budget=2, delay=0)

        state.refresh_from_db()
        self.assertEqual(result["page_ranges"], {"movies": [1, 2]})
        self.assertTrue(result["window_complete"])
        self.assertEqual(state.cursor["modes"]["historical"]["parts"], {"movies": 128, "tv-shows": 73})
        self.assertEqual(state.cursor["modes"]["incremental"]["parts"], {"movies": 2})
        self.assertEqual(state.cursor["parts"], {"movies": 128, "tv-shows": 73})

    def test_incremental_run_resumes_after_budget_interruption(self):
        def page(scraper, provider_slug, part, page, query=None):
            scraper.session.get("https://fixture.invalid/page")
            return [fixture_item(provider_slug, part=part, page=page)], False, None, f"pagination_links_navigation_max={page + 3}"

        with patch("tracker.services.catalogue_discovery._parts", return_value=["movies"]), \
             patch("tracker.services.catalogue_discovery._page", side_effect=page), \
             patch("requests.Session.get", return_value=object()):
            first = run_discovery("hydrahd", mode="incremental", overlap_pages=2, request_budget=1, delay=0)
            second = run_discovery("hydrahd", mode="incremental", overlap_pages=2, request_budget=1, delay=0)

        self.assertEqual(first["page_ranges"], {"movies": [1, 1]})
        self.assertFalse(first.get("window_complete", False))
        self.assertEqual(second["page_ranges"], {"movies": [2, 2]})
        self.assertTrue(second["window_complete"])
        self.assertEqual(DiscoveredCatalogueRecord.objects.filter(provider=self.providers["hydrahd"]).count(), 2)

    def test_incremental_overlap_detects_shifting_pages_and_deduplicates(self):
        page_contents = {1: ["a"], 2: ["b"]}

        def page(scraper, provider_slug, part, page, query=None):
            scraper.session.get("https://fixture.invalid/page")
            key = page_contents[page][0]
            item = fixture_item(provider_slug, part=part, page=page)
            item.title = f"Title {key}"
            item.source_url = f"https://hydrahd.example/{key}"
            item.raw_payload = {"id": key, "listing_page": page}
            return [item], False, None, f"pagination_links_navigation_max={page + 3}"

        with patch("tracker.services.catalogue_discovery._parts", return_value=["movies"]), \
             patch("tracker.services.catalogue_discovery._page", side_effect=page), \
             patch("requests.Session.get", return_value=object()):
            first = run_discovery("hydrahd", mode="incremental", overlap_pages=2, request_budget=2, delay=0)
            page_contents = {1: ["b"], 2: ["a"]}
            second = run_discovery("hydrahd", mode="incremental", overlap_pages=2, request_budget=2, delay=0)

        self.assertEqual(first["items_inserted"], 2)
        self.assertEqual(second["items_inserted"], 0)
        self.assertEqual(second["duplicate_identities"], 2)
        self.assertEqual(second["items_updated"], 2)
        self.assertEqual(second["items_shifted_between_pages"], 2)
        self.assertEqual(DiscoveredCatalogueRecord.objects.filter(provider=self.providers["hydrahd"]).count(), 2)

    def test_animeworld_incremental_rss_replay_stays_partial_and_idempotent(self):
        item = fixture_item("animeworld")

        def rss_page(scraper, provider_slug, part, page, query=None):
            scraper.session.get("https://fixture.invalid/rss")
            scraper.feed_last_build_date = "Tue, 17 Nov 2026 15:20:06 GMT"
            return [item], False, None, "official_rss_snapshot_not_full_archive"

        with patch("tracker.services.catalogue_discovery._page", side_effect=rss_page), \
             patch("requests.Session.get", return_value=object()):
            first = run_discovery("animeworld", mode="incremental", request_budget=2, delay=0)
            second = run_discovery("animeworld", mode="incremental", request_budget=2, delay=0)

        state = CatalogueDiscoveryState.objects.get(provider=self.providers["animeworld"])
        self.assertEqual(first["items_inserted"], 1)
        self.assertEqual(second["items_unchanged"], 1)
        self.assertEqual(second["status"], "PARTIAL")
        self.assertEqual(state.cursor["modes"]["incremental"]["parts"], {"rss-animes": 1})

    def test_provider_lock_rejects_concurrent_mode_without_dispatch_or_state_change(self):
        with patch("tracker.services.catalogue_discovery.try_source_lock", return_value=False), \
             patch("tracker.services.catalogue_discovery._page") as page:
            with self.assertRaisesRegex(RuntimeError, "already active"):
                run_discovery("hydrahd", mode="incremental")
        page.assert_not_called()
        self.assertFalse(CatalogueDiscoveryState.objects.filter(provider=self.providers["hydrahd"]).exists())

    def test_retry_after_becomes_a_persisted_cooldown_instead_of_long_worker_sleep(self):
        class Response:
            def __init__(self, status_code, *, text="", headers=None):
                self.status_code = status_code
                self.text = text
                self.headers = headers or {}

            def json(self):
                raise ValueError("not JSON")

        responses = [
            Response(200, text="User-agent: *\nAllow: /\n"),
            Response(429, headers={"Retry-After": "120"}),
        ]
        with patch("requests.Session.get", side_effect=responses) as get, \
             patch("tracker.services.catalogue_discovery.time.sleep") as sleep:
            with self.assertRaises(CatalogueDiscoveryError) as raised:
                run_discovery("hydrahd", mode="historical", request_budget=3, delay=1, retry_limit=1)

        state = CatalogueDiscoveryState.objects.get(provider=self.providers["hydrahd"])
        self.assertEqual(get.call_count, 2)
        sleep.assert_not_called()
        self.assertEqual(raised.exception.report["failed_pages"], [{"partition": "movies", "page": 1, "status": 429}])
        self.assertEqual(state.cursor["modes"]["historical"]["cooldown_until"], raised.exception.report["cooldown_until"])
        self.assertGreater(datetime.fromisoformat(state.cursor["modes"]["historical"]["cooldown_until"]), datetime.now(timezone.utc))

    def test_replaying_a_page_reports_persisted_items_as_unchanged(self):
        slug = "kisskh"

        def one_page(_scraper, provider_slug, part, page, query=None):
            _scraper.session.get("https://fixture.invalid/page")
            return [fixture_item(provider_slug, part=part)], True, 1, "fixture_terminal_page"

        with patch("tracker.services.catalogue_discovery.PROVENANCE_BLOCKED", {}), \
             patch("tracker.services.catalogue_discovery._page", side_effect=one_page), \
             patch("tracker.services.catalogue_discovery._parts", return_value=["2"]), \
             patch("requests.Session.get", return_value=object()):
            first = run_discovery(slug, request_budget=2, delay=0)
            state = CatalogueDiscoveryState.objects.get(provider=self.providers[slug])
            state.cursor = {}
            state.save(update_fields=["cursor", "updated_at"])
            replay = run_discovery(slug, request_budget=2, delay=0)

        self.assertEqual(first["items_inserted"], 1)
        self.assertEqual(replay["items_unchanged"], 1)
        self.assertEqual(ScrapedContentMapping.objects.filter(provider=self.providers[slug]).count(), 1)

    def test_unclassified_native_records_are_idempotent_and_do_not_project(self):
        slug = "streamingcommunity"
        def one_page(scraper, provider_slug, part, page, query=None):
            scraper.session.get("https://fixture.invalid/page")
            item = ScrapedItem(
                title="Unclassified title", provider_slug=provider_slug,
                content_type="movie", source_url="https://sc.example/titles/7-example",
                raw_payload={"title_id": "7", "data_kind": "unknown"},
            )
            return [item], True, 1, "fixture_terminal_page"

        with patch("tracker.services.catalogue_discovery.PROVENANCE_BLOCKED", {}), \
             patch("tracker.services.catalogue_discovery._page", side_effect=one_page), \
             patch("tracker.services.catalogue_discovery._parts", return_value=["/"]), \
             patch("requests.Session.get", return_value=object()):
            first = run_discovery(slug, request_budget=2, delay=0)
            state = CatalogueDiscoveryState.objects.get(provider=self.providers[slug])
            state.cursor = {}
            state.save(update_fields=["cursor", "updated_at"])
            replay = run_discovery(slug, request_budget=2, delay=0)

        record = DiscoveredCatalogueRecord.objects.get(provider=self.providers[slug])
        self.assertEqual(first["items_inserted"], 1)
        self.assertEqual(replay["items_unchanged"], 1)
        self.assertEqual(record.provider_native_id, "7")
        self.assertEqual(record.classification_state, "PENDING")
        self.assertIsNone(record.classified_content_type)
        self.assertEqual(record.original_metadata["listing_content_type_hint"], "movie")
        self.assertFalse(ScrapedContentMapping.objects.filter(provider=self.providers[slug]).exists())
        self.assertFalse(Offer.objects.filter(provider=self.providers[slug]).exists())

    def test_unclassified_native_id_deduplicates_across_partitions(self):
        slug = "streamingcommunity"
        def two_partitions(scraper, provider_slug, part, page, query=None):
            scraper.session.get("https://fixture.invalid/page")
            item = ScrapedItem(
                title="Same provider title", provider_slug=provider_slug,
                content_type="series" if part == "series" else "movie",
                source_url=f"https://sc.example/titles/7-{part}",
                raw_payload={"title_id": "7", "partition_hint": part},
            )
            return [item], True, 1, "fixture_terminal_page"

        with patch("tracker.services.catalogue_discovery.PROVENANCE_BLOCKED", {}), \
             patch("tracker.services.catalogue_discovery._page", side_effect=two_partitions), \
             patch("tracker.services.catalogue_discovery._parts", return_value=["film", "series"]), \
             patch("requests.Session.get", return_value=object()):
            result = run_discovery(slug, request_budget=3, delay=0)

        records = DiscoveredCatalogueRecord.objects.filter(provider=self.providers[slug])
        self.assertEqual(records.count(), 1)
        self.assertEqual(records.get().provider_native_id, "7")
        self.assertEqual(records.get().source_url, "https://sc.example/titles/7-film")
        self.assertEqual(records.get().source_urls_seen, ["https://sc.example/titles/7-film", "https://sc.example/titles/7-series"])
        self.assertEqual(records.get().classification_state, "PENDING")
        self.assertEqual(result["duplicate_native_ids_across_partitions"], 1)
        self.assertEqual(result["items_inserted"], 1)
        self.assertEqual(result["items_updated"], 1)
        self.assertFalse(ScrapedContentMapping.objects.filter(provider=self.providers[slug]).exists())

    def test_later_native_id_promotes_url_identity_without_erasing_classification(self):
        provider = self.providers["animeworld"]
        first = ScrapedItem(
            title="Title", provider_slug="animeworld", source_url="https://animeworld.example/title",
            raw_payload={"href": "/title", "title": "Title"},
        )
        _persist_unclassified_observation(provider=provider, item=first, partition="az-list")
        record = DiscoveredCatalogueRecord.objects.get(provider=provider)
        record.classification_state = "CLASSIFIED"
        record.classified_content_type = "series"
        record.save(update_fields=["classification_state", "classified_content_type", "updated_at"])

        replay = ScrapedItem(
            title="Title", provider_slug="animeworld", source_url="https://animeworld.example/title",
            raw_payload={"id": "native-1", "href": "/title", "title": "Title"},
        )
        outcome = _persist_unclassified_observation(provider=provider, item=replay, partition="az-list")

        record.refresh_from_db()
        self.assertEqual(outcome, "updated")
        self.assertEqual(DiscoveredCatalogueRecord.objects.filter(provider=provider).count(), 1)
        self.assertEqual(record.provider_native_id, "native-1")
        self.assertEqual(record.classification_state, "CLASSIFIED")
        self.assertEqual(record.classified_content_type, "series")

    def test_duplicate_native_id_across_partitions_is_counted_once_but_types_stay_separate(self):
        slug = "kisskh"
        def pages(scraper, _provider_slug, part, page, query=None):
            scraper.session.get("https://fixture.invalid/page")
            kind = "movie" if part == "1" else "series"
            item = ScrapedItem(
                title="Shared native record", provider_slug=slug, content_type=kind,
                source_url="https://kisskh.do/Drama/shared",
                raw_payload={"id": "shared", "type": "Movie" if kind == "movie" else "TVSeries"},
            )
            return CataloguePageRows([item]), True, 1, "api_total_count"
        with patch("tracker.services.catalogue_discovery._page", side_effect=pages), \
             patch("tracker.services.catalogue_discovery._parts", return_value=["1", "2"]), \
             patch("requests.Session.get", return_value=object()):
            result = run_discovery(slug, request_budget=3, delay=0)
        rows = ScrapedContentMapping.objects.filter(provider=self.providers[slug])
        self.assertEqual(rows.count(), 2)
        self.assertEqual(result["items_unique"], 1)
        self.assertEqual(result["duplicate_native_ids_across_partitions"], 1)

    def test_changed_partition_total_restarts_partition_and_stays_partial(self):
        slug = "kisskh"
        provider = self.providers[slug]
        state = CatalogueDiscoveryState.objects.create(
            provider=provider,
            cursor={"done": [], "parts": {"2": 1}, "reported_totals": {"2": 40}},
        )
        item = ScrapedItem(
            title="Page two", provider_slug=slug, content_type="movie",
            source_url="https://kisskh.do/Drama/2", raw_payload={"id": "2"},
        )
        def pages(scraper, _provider_slug, part, page, query=None):
            scraper.session.get("https://fixture.invalid/page")
            return CataloguePageRows([item], total_count=41, page=2, page_size=20), False, 3, "api_total_count"
        with patch("tracker.services.catalogue_discovery._page", side_effect=pages), \
             patch("tracker.services.catalogue_discovery._parts", return_value=["2"]), \
             patch("requests.Session.get", return_value=object()):
            result = run_discovery(slug, request_budget=2, delay=0)
        state.refresh_from_db()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["pagination_total_changes"], [{"partition": "2", "previous": 40, "current": 41}])
        self.assertEqual(state.cursor["parts"]["2"], 0)
        self.assertEqual(state.cursor["reported_totals"]["2"], 41)
        self.assertEqual(ScrapedContentMapping.objects.filter(provider=provider).count(), 1)

    def test_failed_run_exposes_dispatched_request_count_and_durable_report(self):
        slug = "animeworld"

        def failed_page(scraper, provider_slug, part, page, query=None):
            scraper.session.get("https://fixture.invalid/page")
            raise RuntimeError("fixture page failure")

        with patch("tracker.services.catalogue_discovery._page", side_effect=failed_page), \
             patch("requests.Session.get", return_value=object()):
            with self.assertRaises(CatalogueDiscoveryError) as caught:
                run_discovery(slug, request_budget=3, delay=0)

        self.assertEqual(caught.exception.report["requests_made"], 1)
        state = CatalogueDiscoveryState.objects.get(provider=self.providers[slug])
        self.assertEqual(state.report["requests_dispatched"], 1)
        self.assertEqual(state.status, "FAILED")

    def test_targeted_results_are_never_reported_complete(self):
        slug = "kisskh"
        with patch("tracker.services.catalogue_discovery.resolve_scraper_class") as resolve:
            scraper = resolve.return_value.return_value
            scraper.run.return_value = [fixture_item(slug)]
            result = run_discovery(slug, mode="targeted", targeted_query="fixture", delay=0)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["items_seen"], 1)

    def test_movie_and_series_with_same_title_year_keep_separate_identities(self):
        provider = self.providers["animeworld"]
        for kind in ("movie", "series"):
            persist_scraped_item(
                provider=provider,
                item=ScrapedItem(
                    title="Same Name", year=2020, content_type=kind,
                    provider_slug="animeworld", source_url=f"https://animeworld.example/{kind}",
                    raw_payload={"unverified_discovery": True},
                ),
            )
        rows = ScrapedContentMapping.objects.filter(provider=provider, scraped_title="Same Name", scraped_year=2020)
        self.assertEqual(rows.count(), 2)
        self.assertSetEqual(set(rows.values_list("scraped_content_type", flat=True)), {"movie", "series"})

    def test_native_ids_preserve_distinct_same_title_releases_and_are_idempotent(self):
        provider = self.providers["animeworld"]
        for native_id, url in (("release-a", "https://animeworld.example/a"), ("release-b", "https://animeworld.example/b")):
            persist_scraped_item(
                provider=provider,
                item=ScrapedItem(
                    title="Same Name", year=2020, content_type="movie",
                    provider_slug="animeworld", source_url=url,
                    raw_payload={"id": native_id, "unverified_discovery": True},
                ),
            )
        persist_scraped_item(
            provider=provider,
            item=ScrapedItem(
                title="Same Name", year=2020, content_type="movie",
                provider_slug="animeworld", source_url="https://animeworld.example/a-new-url",
                raw_payload={"id": "release-a", "unverified_discovery": True},
            ),
        )
        rows = ScrapedContentMapping.objects.filter(provider=provider, scraped_title="Same Name", scraped_year=2020)
        self.assertEqual(rows.count(), 2)
        self.assertSetEqual(set(rows.values_list("provider_content_id", flat=True)), {"release-a", "release-b"})
        self.assertEqual(rows.get(provider_content_id="release-a").scraped_url, "https://animeworld.example/a-new-url")

    def test_native_id_promotes_existing_legacy_row_without_losing_manual_match(self):
        provider = self.providers["animeworld"]
        movie = Movie.objects.create(tmdb_id=992020, title="Same Name", year=2020)
        legacy_item = ScrapedItem(
            title="Same Name", year=2020, content_type="movie", provider_slug="animeworld",
            source_url="https://animeworld.example/title", raw_payload={"unverified_discovery": True},
        )
        mapping = persist_scraped_item(provider=provider, item=legacy_item)
        mapping.movie = movie
        mapping.is_confirmed = True
        mapping.save(update_fields=["movie", "is_confirmed"])
        upgraded = persist_scraped_item(
            provider=provider,
            item=ScrapedItem(
                title="Same Name", year=2020, content_type="movie", provider_slug="animeworld",
                source_url=legacy_item.source_url,
                raw_payload={"id": "native-992020", "unverified_discovery": True},
            ),
        )
        self.assertEqual(upgraded.pk, mapping.pk)
        self.assertEqual(upgraded.provider_content_id, "native-992020")
        self.assertEqual(upgraded.movie_id, movie.pk)
        self.assertTrue(upgraded.is_confirmed)

    def test_dry_run_does_not_create_state_or_mappings(self):
        slug = "animeworld"
        def page(_scraper, provider_slug, part, page, query=None):
            return [fixture_item(provider_slug, part=part)], True, 1, "fixture_terminal_page"
        with patch("tracker.services.catalogue_discovery._page", side_effect=page):
            result = run_discovery(slug, request_budget=2, delay=0, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse(CatalogueDiscoveryState.objects.filter(provider=self.providers[slug]).exists())
        self.assertFalse(ScrapedContentMapping.objects.filter(provider=self.providers[slug]).exists())
        self.assertFalse(DiscoveredCatalogueRecord.objects.filter(provider=self.providers[slug]).exists())
