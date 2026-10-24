"""Pluto TV source: catalogue parsing, pagination, failure semantics.

Fixture-based (``pluto_fixtures.FakePlutoTvApi``): no test performs network
I/O and no test touches the database (``SimpleTestCase`` doubles as a guard —
the source must stay persistence-free). ``RefreshPathTests`` then puts the
same fixture fetch through ``AvailabilityRefreshService`` against SQLite.

Fixture catalogue (trimmed recordings of the hubs GraphQL BFF):

    movies-all-movies  p1 [Friday, "M.R. James" Number 13]  hasMore true
                       p2 [Alice Upside Down]               hasMore false
    shows-all-shows    p1 [#Washed, Arrow]                  hasMore false

    region "US" -> path region "us" -> URLs https://pluto.tv/us/...

Local catalog (RefreshPathTests): Friday (movie, 1995) and Arrow (series,
2012) resolve; the three other titles are deliberately absent so the
unmapped-content contract is observable.
"""
import copy
import datetime as dt
import urllib.parse

import requests
from django.test import SimpleTestCase, TestCase

from availability.domain.enums import (
    ContentType,
    MatchMethod,
    OfferType,
    SourceType,
    SyncMode,
    SyncStatus,
)
from availability.models import (
    ContentMapping,
    Country,
    Offer,
    ProviderContent,
    Source,
    SyncCursor,
    SyncRun,
)
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import (
    ContentRequest,
    PlutoTvSource,
    SourceFetchError,
    source_registry,
)
from availability.sources.pluto_tv import (
    MAX_PAGES,
    MOVIES_HUB,
    PATH_BY_COUNTRY,
    SHOWS_HUB,
    SOURCE_VERSION,
    PlutoTvClient,
)
from tracker.models import Movie, Provider

from .pluto_fixtures import (
    FakePlutoTvApi,
    collection_body,
    grid_page_payload,
    grid_result,
    grid_token,
)

MOVIES_RAW_TOKEN = grid_token(MOVIES_HUB)
SHOWS_RAW_TOKEN = grid_token(SHOWS_HUB)

FRIDAY_ID = "6924978453126d02e28c5a16"
MRJ_ID = "6a4bfb38d8618839a6a6c775"
ALICE_ID = "64b9c64bfdd862001369a13f"

TOTAL_CONTENTS = 5
TOTAL_OFFERS = 5
MAPPED_OFFERS = 2    # Friday (movie, 1995) + Arrow (series, 2012)
UNMAPPED_OFFERS = 3  # M.R. James, Alice Upside Down, #Washed


def build_source(api=None):
    api = api or FakePlutoTvApi()
    return PlutoTvSource(client=PlutoTvClient(requester=api)), api


class RegistrationTests(SimpleTestCase):
    def test_pluto_tv_source_is_registered(self):
        source = source_registry.get("pluto_tv")
        self.assertIsInstance(source, PlutoTvSource)
        self.assertEqual(source.slug, "pluto_tv")
        self.assertEqual(source.name, "Pluto TV")
        self.assertEqual(source.source_type, SourceType.API)

    def test_capabilities_declare_catalogue_non_authoritative_scoped_source(self):
        capabilities = source_registry.get("pluto_tv").capabilities
        self.assertEqual(capabilities.supported_providers, frozenset({"pluto_tv"}))
        # The full seoHreflang footprint: one entry per Pluto market.
        self.assertEqual(capabilities.supported_countries, frozenset(PATH_BY_COUNTRY))
        self.assertEqual(len(capabilities.supported_countries), 32)
        self.assertEqual(
            capabilities.supported_content_types,
            frozenset({ContentType.MOVIE, ContentType.SERIES}),
        )
        self.assertTrue(capabilities.supports_catalogue_sync)
        self.assertFalse(capabilities.supports_incremental_sync)
        # Declared totals drift and Pluto has no removal signal.
        self.assertFalse(capabilities.supports_authoritative_snapshots)
        self.assertFalse(capabilities.supports_tmdb_ids)
        self.assertFalse(capabilities.supports_seasons)
        self.assertFalse(capabilities.supports_episodes)
        self.assertFalse(capabilities.supports_languages)

    def test_targeted_content_request_is_rejected(self):
        source, _api = build_source()
        with self.assertRaisesMessage(ValueError, "catalogue-driven"):
            source.fetch(ContentRequest(tmdb_id=550, title="Fight Club"))

    def test_fetch_without_content_walks_both_catalogue_grids(self):
        source, api = build_source()
        result = source.fetch()
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)
        self.assertEqual(len(result.offers), TOTAL_OFFERS)
        # One collection lookup per hub, two movie pages, one shows page.
        self.assertEqual(len(api.requests_matching("collection")), 2)
        self.assertEqual(len(api.requests_matching("carouselData")), 3)

    def test_scope_hints_are_accepted_and_not_acted_on(self):
        # The country comes from the observed egress region, not the hint.
        source, _api = build_source()
        result = source.fetch(provider="pluto_tv", country="NL")
        self.assertEqual(len(result.offers), TOTAL_OFFERS)


class SnapshotSemanticsTests(SimpleTestCase):
    def test_result_never_claims_authoritativeness_and_is_resumable(self):
        source, _api = build_source()
        result = source.fetch()
        self.assertFalse(result.authoritative)
        self.assertFalse(result.complete_snapshot)
        self.assertEqual(result.sync_mode, SyncMode.FULL)
        # This fixture's grids fit in one batch, so the walk is exhausted and
        # correctly emits no checkpoint. Resumability is proven by
        # CatalogueCheckpointTests below.
        self.assertIsNone(result.cursor)


class CatalogueParsingTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source, cls.api = build_source()
        cls.result = cls.source.fetch()

    def _offers(self, provider_content_id, content_type=None):
        offers = [
            o for o in self.result.offers
            if o.provider_content_id == provider_content_id
        ]
        if content_type is not None:
            offers = [o for o in offers if o.content_type == content_type]
        return offers

    def test_every_catalogue_item_becomes_a_content_record(self):
        self.assertEqual(
            {c.provider_content_id for c in self.result.contents},
            {FRIDAY_ID, MRJ_ID, ALICE_ID, "washed", "arrow-ptv3"},
        )

    def test_offer_granularity_counts(self):
        counts = {}
        for offer in self.result.offers:
            counts[offer.content_type] = counts.get(offer.content_type, 0) + 1
        self.assertEqual(counts, {ContentType.MOVIE: 3, ContentType.SERIES: 2})

    def test_movie_offer_fields(self):
        offer = self._offers(FRIDAY_ID, ContentType.MOVIE)[0]
        self.assertEqual(offer.provider_id, "pluto_tv")
        self.assertEqual(offer.country, "US")
        self.assertIsNone(offer.tmdb_id)  # no external ids at the source
        self.assertIsNone(offer.season_number)
        self.assertIsNone(offer.episode_number)
        self.assertEqual(offer.offer_type, OfferType.FREE_WITH_ADS)  # pure AVOD
        self.assertEqual(
            offer.provider_url, f"https://pluto.tv/us/movies/{FRIDAY_ID}/"
        )
        self.assertEqual(offer.quality, "")
        self.assertIsNone(offer.audio_languages)  # no per-title language data
        self.assertIsNone(offer.subtitle_languages)
        # airDate is the content's date, not an availability window.
        self.assertIsNone(offer.available_from)
        self.assertIsNone(offer.available_until)
        self.assertEqual(offer.source_record_id, FRIDAY_ID)
        self.assertEqual(offer.source_version, SOURCE_VERSION)

    def test_show_offer_uses_the_url_slug_as_identity(self):
        offer = self._offers("arrow-ptv3", ContentType.SERIES)[0]
        self.assertEqual(offer.provider_id, "pluto_tv")
        self.assertEqual(offer.content_type, ContentType.SERIES)
        self.assertIsNone(offer.season_number)
        self.assertIsNone(offer.episode_number)
        self.assertEqual(offer.offer_type, OfferType.FREE_WITH_ADS)
        self.assertEqual(offer.provider_url, "https://pluto.tv/us/shows/arrow-ptv3/")
        self.assertEqual(offer.source_record_id, "arrow-ptv3")

    def test_movie_release_dates_come_from_air_date(self):
        dates = {
            c.provider_content_id: c.release_date
            for c in self.result.contents
            if c.content_type == ContentType.MOVIE
        }
        self.assertEqual(
            dates,
            {
                FRIDAY_ID: dt.date(1995, 2, 1),
                MRJ_ID: dt.date(2006, 2, 1),
                ALICE_ID: dt.date(2007, 2, 1),
            },
        )

    def test_show_release_dates_come_from_premiere_date(self):
        dates = {
            c.provider_content_id: c.release_date
            for c in self.result.contents
            if c.content_type == ContentType.SERIES
        }
        # Empty premiereDate stays None rather than inventing a date.
        self.assertEqual(
            dates, {"washed": None, "arrow-ptv3": dt.date(2012, 10, 10)}
        )

    def test_content_carries_metadata_but_no_external_ids(self):
        friday = next(
            c for c in self.result.contents if c.provider_content_id == FRIDAY_ID
        )
        self.assertEqual(friday.title, "Friday")
        self.assertTrue(friday.description)
        self.assertTrue(friday.poster.startswith("https://"))
        self.assertEqual(friday.url, f"https://pluto.tv/us/movies/{FRIDAY_ID}/")
        self.assertEqual(friday.external_ids, {})
        self.assertEqual(friday.source_id, "pluto_tv")

    def test_non_catalogue_content_type_is_skipped(self):
        source, api = build_source()
        items = grid_result(api.grids[MOVIES_RAW_TOKEN]["0"])["data"]
        items[1]["contentType"] = "liveChannel"
        with self.assertLogs("availability.sources.pluto_tv", level="INFO") as logs:
            result = source.fetch()
        self.assertEqual(len(result.contents), TOTAL_CONTENTS - 1)
        self.assertTrue(any("pluto_tv_unsupported_item" in l for l in logs.output))

    def test_movie_with_wrong_href_shape_is_dropped(self):
        source, api = build_source()
        items = grid_result(api.grids[MOVIES_RAW_TOKEN]["0"])["data"]
        items[0]["href"] = f"/collection/{FRIDAY_ID}/"
        with self.assertLogs("availability.sources.pluto_tv", level="WARNING") as logs:
            result = source.fetch()
        self.assertEqual(len(result.contents), TOTAL_CONTENTS - 1)
        self.assertTrue(any("pluto_tv_unstable_item" in l for l in logs.output))

    def test_show_without_slug_shape_is_dropped(self):
        source, api = build_source()
        items = grid_result(api.grids[SHOWS_RAW_TOKEN]["0"])["data"]
        items[1]["href"] = "/shows/nested/slug/"
        with self.assertLogs("availability.sources.pluto_tv", level="WARNING") as logs:
            result = source.fetch()
        self.assertEqual(len(result.contents), TOTAL_CONTENTS - 1)
        self.assertTrue(any("pluto_tv_unstable_item" in l for l in logs.output))


class RegionTests(SimpleTestCase):
    def test_unmapped_region_is_an_empty_observation_not_a_failure(self):
        source, api = build_source()
        collection_body(api.collections[MOVIES_HUB])["region"] = "XX"
        with self.assertLogs("availability.sources.pluto_tv", level="WARNING") as logs:
            result = source.fetch()
        self.assertEqual(result.contents, [])
        self.assertEqual(result.offers, [])
        self.assertFalse(result.authoritative)
        self.assertTrue(any("pluto_tv_unsupported_region" in l for l in logs.output))
        # Bail out before walking either grid: one request, nothing fetched.
        self.assertEqual(len(api.requests), 1)

    def test_lowercase_region_resolves(self):
        source, api = build_source()
        collection_body(api.collections[MOVIES_HUB])["region"] = "us"
        result = source.fetch()
        self.assertEqual(len(result.offers), TOTAL_OFFERS)
        urls = {o.provider_url for o in result.offers}
        self.assertIn(f"https://pluto.tv/us/movies/{FRIDAY_ID}/", urls)

    def test_missing_region_is_a_failure(self):
        source, api = build_source()
        collection_body(api.collections[MOVIES_HUB])["region"] = ""
        with self.assertRaisesMessage(SourceFetchError, "no region"):
            source.fetch()

    def test_region_mismatch_between_hubs_warns_but_proceeds(self):
        source, api = build_source()
        collection_body(api.collections[SHOWS_HUB])["region"] = "CA"
        with self.assertLogs("availability.sources.pluto_tv", level="WARNING") as logs:
            result = source.fetch()
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)
        # URLs keep the region that scoped the walk we trusted.
        self.assertTrue(all("/us/" in o.provider_url for o in result.offers))
        self.assertTrue(any("pluto_tv_region_mismatch" in l for l in logs.output))


class PaginationTests(SimpleTestCase):
    def test_both_grids_are_walked_with_offset_paging(self):
        source, api = build_source()
        source.fetch()
        self.assertEqual(api.grid_starts(MOVIES_RAW_TOKEN), ["0", "24"])
        self.assertEqual(api.grid_starts(SHOWS_RAW_TOKEN), ["0"])

    def test_grid_token_is_percent_encoded_before_send(self):
        source, api = build_source()
        source.fetch()
        encoded = urllib.parse.quote(MOVIES_RAW_TOKEN, safe="")
        self.assertNotEqual(encoded, MOVIES_RAW_TOKEN)  # fixture token encodes
        sent = [v["token"] for v in api.requests_matching("carouselData")]
        self.assertIn(encoded, sent)
        self.assertNotIn(MOVIES_RAW_TOKEN, sent)

    def test_grid_params_reproduce_the_sites_variables(self):
        source, api = build_source()
        source.fetch()
        params = api.requests_matching("carouselData")[0]["carouselParams"]
        self.assertEqual(params["start"], "0")
        self.assertEqual(params["rows"], "24")
        self.assertEqual(params["model"], "universalContent")
        self.assertEqual(params["carouselType"], "grid")

    def test_empty_first_page_is_a_valid_empty_catalogue(self):
        source, api = build_source()
        grid_result(api.grids[MOVIES_RAW_TOKEN]["0"])["data"] = []
        grid_result(api.grids[MOVIES_RAW_TOKEN]["0"])["hasMore"] = False
        result = source.fetch()
        movie_contents = [
            c for c in result.contents if c.content_type == ContentType.MOVIE
        ]
        self.assertEqual(movie_contents, [])
        self.assertEqual(len(result.contents), 2)  # shows grid unaffected

    def test_empty_page_terminates_even_when_has_more(self):
        source, api = build_source()
        grid_result(api.grids[MOVIES_RAW_TOKEN]["24"])["data"] = []
        grid_result(api.grids[MOVIES_RAW_TOKEN]["24"])["hasMore"] = True
        result = source.fetch()
        movies = [c for c in result.contents if c.content_type == ContentType.MOVIE]
        self.assertEqual(len(movies), 2)  # page 1 only

    def test_repeated_page_signature_stops_instead_of_looping(self):
        source, api = build_source()
        api.grids[MOVIES_RAW_TOKEN]["24"] = copy.deepcopy(
            api.grids[MOVIES_RAW_TOKEN]["0"]
        )
        with self.assertLogs("availability.sources.pluto_tv", level="WARNING") as logs:
            result = source.fetch()
        self.assertTrue(any("pluto_tv_repeated_page" in l for l in logs.output))
        movies = [c for c in result.contents if c.content_type == ContentType.MOVIE]
        self.assertEqual(len(movies), 2)

    def test_overlapping_pages_are_deduped(self):
        source, api = build_source()
        friday = grid_result(api.grids[MOVIES_RAW_TOKEN]["0"])["data"][0]
        grid_result(api.grids[MOVIES_RAW_TOKEN]["24"])["data"].insert(
            0, copy.deepcopy(friday)
        )
        with self.assertLogs("availability.sources.pluto_tv", level="WARNING") as logs:
            result = source.fetch()
        self.assertTrue(any("pluto_tv_overlapping_items" in l for l in logs.output))
        movie_ids = {
            c.provider_content_id
            for c in result.contents
            if c.content_type == ContentType.MOVIE
        }
        self.assertEqual(movie_ids, {FRIDAY_ID, MRJ_ID, ALICE_ID})

    def test_page_limit_caps_a_runaway_walk(self):
        pages = {}
        for index in range(MAX_PAGES + 2):
            pages[str(index * 24)] = {
                "data": {
                    "carouselData": {
                        "success": True,
                        "result": {
                            "title": "All Movies",
                            "total": MAX_PAGES + 2,
                            "rows": 24,
                            "skipped": 0,
                            "hasMore": True,
                            "data": [
                                {
                                    "contentType": "movie",
                                    "href": f"/movies/gen{index}/",
                                    "id": f"gen{index}",
                                    "thumb": "",
                                    "tileType": "movie",
                                    "orientation": "portrait",
                                    "title": f"Generated {index}",
                                    "airDate": "",
                                    "movieDuration": 60,
                                }
                            ],
                        },
                    }
                }
            }
        api = FakePlutoTvApi(
            grids={
                MOVIES_RAW_TOKEN: pages,
                SHOWS_RAW_TOKEN: {"0": grid_page_payload(SHOWS_HUB, 1)},
            }
        )
        source, _ = build_source(api)
        with self.assertLogs("availability.sources.pluto_tv", level="WARNING") as logs:
            result = source.fetch()
        self.assertTrue(any("pluto_tv_page_limit" in l for l in logs.output))
        self.assertEqual(len(api.grid_starts(MOVIES_RAW_TOKEN)), MAX_PAGES)
        movies = [c for c in result.contents if c.content_type == ContentType.MOVIE]
        self.assertEqual(len(movies), MAX_PAGES)

    def test_missing_grid_token_is_a_failure(self):
        source, api = build_source()
        collection_body(api.collections[MOVIES_HUB])["hubCarousels"] = []
        with self.assertRaisesMessage(SourceFetchError, "grid token"):
            source.fetch()

    def test_missing_grid_page_fails_instead_of_looking_empty(self):
        source, api = build_source()
        del api.grids[MOVIES_RAW_TOKEN]["24"]  # p1 still claims hasMore
        with self.assertRaisesMessage(SourceFetchError, "Simulated upstream"):
            source.fetch()


class FailureContractTests(SimpleTestCase):
    def test_null_collection_payload_is_rejected(self):
        source, api = build_source()
        api.collections[MOVIES_HUB] = {"data": {"collection": None}}
        with self.assertRaisesMessage(SourceFetchError, "collection payload"):
            source.fetch()

    def test_graphql_error_payload_is_a_failure(self):
        client = PlutoTvClient(
            requester=lambda q, v: {"errors": [{"message": "boom"}], "data": None}
        )
        with self.assertRaisesMessage(SourceFetchError, "GraphQL errors"):
            client.collection(MOVIES_HUB)

    def test_missing_data_payload_is_a_failure(self):
        client = PlutoTvClient(requester=lambda q, v: {"data": None})
        with self.assertRaisesMessage(SourceFetchError, "no data payload"):
            client.collection(MOVIES_HUB)

    def test_non_dict_response_is_a_failure(self):
        client = PlutoTvClient(requester=lambda q, v: [])
        with self.assertRaisesMessage(SourceFetchError, "response type"):
            client.collection(MOVIES_HUB)

    def test_transport_error_is_wrapped(self):
        def boom(query, variables):
            raise requests.ConnectionError("dns down")

        client = PlutoTvClient(requester=boom)
        with self.assertRaisesMessage(SourceFetchError, "request failed"):
            client.collection(MOVIES_HUB)

    def test_grid_payload_without_items_is_rejected(self):
        source, api = build_source()
        api.grids[MOVIES_RAW_TOKEN]["0"]["data"]["carouselData"]["result"] = {
            "hasMore": False
        }
        with self.assertRaisesMessage(SourceFetchError, "grid items"):
            source.fetch()

    def test_mid_run_hub_failure_is_not_an_empty_result(self):
        source, api = build_source()
        api.failures[SHOWS_HUB] = SourceFetchError("network down")
        with self.assertRaisesMessage(SourceFetchError, "network down"):
            source.fetch()
        # The failure surfaced; nothing is ever reported as an empty catalogue.
        self.assertEqual(api.requests_matching("collection")[0]["slug"], MOVIES_HUB)


class RefreshPathTests(TestCase):
    """The fixture fetch through AvailabilityRefreshService, end to end."""

    def setUp(self):
        self.api = FakePlutoTvApi()
        self.source = PlutoTvSource(client=PlutoTvClient(requester=self.api))
        self.service = AvailabilityRefreshService(source=self.source)
        self.provider, _ = Provider.objects.get_or_create(
            slug="pluto_tv", defaults={"name": "Pluto TV"}
        )
        Country.objects.get_or_create(code="US", defaults={"name": "United States"})
        self.friday = Movie.objects.create(
            tmdb_id=941, title="Friday", year=1995, content_type="movie"
        )
        self.arrow = Movie.objects.create(
            tmdb_id=1403, title="Arrow", year=2012, content_type="series"
        )

    def _mapping(self, provider_content_id):
        return ContentMapping.objects.get(
            provider=self.provider, provider_content_id=provider_content_id
        )

    # ------------------------------------------------------------- outcomes

    def test_resolved_offers_are_persisted_and_unresolved_are_skipped(self):
        result = self.service.refresh_catalogue()

        self.assertEqual(result.discovered, TOTAL_OFFERS)
        self.assertEqual(result.processed, MAPPED_OFFERS)
        self.assertEqual(result.created, MAPPED_OFFERS)
        self.assertEqual(result.skipped, UNMAPPED_OFFERS)
        self.assertEqual(result.unresolved, UNMAPPED_OFFERS)
        self.assertEqual(result.deactivated, 0)
        self.assertEqual(Offer.objects.count(), MAPPED_OFFERS)
        self.assertEqual(
            set(Offer.objects.values_list("tmdb_id", flat=True)), {941, 1403}
        )
        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)

    def test_offer_granularity_survives_persistence(self):
        self.service.refresh_catalogue()
        breakdown = {}
        for offer in Offer.objects.all():
            breakdown[offer.content_type] = breakdown.get(offer.content_type, 0) + 1
        self.assertEqual(breakdown, {ContentType.MOVIE: 1, ContentType.SERIES: 1})

    def test_identity_and_key_fields_are_canonical(self):
        self.service.refresh_catalogue()
        movie_offer = Offer.objects.get(
            content_type=ContentType.MOVIE, provider_content_id=FRIDAY_ID
        )
        self.assertEqual(
            movie_offer.identity,
            f"pluto_tv|US|movie|941|||free_with_ads|{FRIDAY_ID}",
        )
        self.assertEqual(movie_offer.provider, self.provider)
        self.assertEqual(movie_offer.country.code, "US")
        self.assertEqual(movie_offer.source.slug, "pluto_tv")
        self.assertEqual(movie_offer.offer_type, OfferType.FREE_WITH_ADS)
        self.assertIsNone(movie_offer.audio_languages)
        self.assertIsNone(movie_offer.subtitle_languages)
        self.assertIsNone(movie_offer.available_from)
        self.assertIsNone(movie_offer.available_until)
        self.assertTrue(movie_offer.currently_available)

        series_offer = Offer.objects.get(
            content_type=ContentType.SERIES, provider_content_id="arrow-ptv3"
        )
        self.assertEqual(
            series_offer.identity, "pluto_tv|US|series|1403|||free_with_ads|arrow-ptv3"
        )

    def test_unmapped_content_persists_as_provider_content(self):
        self.service.refresh_catalogue()
        washed = ProviderContent.objects.get(provider_content_id="washed")
        self.assertEqual(washed.provider, self.provider)
        self.assertEqual(washed.content_type, ContentType.SERIES)
        self.assertEqual(washed.title, "#Washed")
        self.assertEqual(washed.provider_url, "https://pluto.tv/us/shows/washed/")
        self.assertFalse(
            ContentMapping.objects.filter(provider_content_id="washed").exists()
        )
        self.assertFalse(Offer.objects.filter(provider_content_id="washed").exists())

    def test_sync_run_lifecycle_and_counters(self):
        result = self.service.refresh_catalogue()

        self.assertEqual(len(result.sync_runs), 1)
        run = result.sync_runs[0]
        run.refresh_from_db()
        self.assertEqual(run.status, SyncStatus.SUCCEEDED)
        self.assertEqual(run.provider, self.provider)
        self.assertEqual(run.country.code, "US")
        self.assertEqual(run.source.slug, "pluto_tv")
        self.assertEqual(run.mode, "full")
        self.assertFalse(run.authoritative)
        self.assertEqual(run.items_discovered, MAPPED_OFFERS)
        self.assertEqual(run.items_processed, MAPPED_OFFERS)
        self.assertEqual(run.offers_created, MAPPED_OFFERS)
        self.assertEqual(run.offers_updated, 0)
        self.assertEqual(run.offers_deactivated, 0)
        self.assertEqual(SyncRun.objects.count(), 1)

    def test_source_row_is_created_on_first_refresh(self):
        self.service.refresh_catalogue()
        source_row = Source.objects.get(slug="pluto_tv")
        self.assertEqual(source_row.name, "Pluto TV")
        self.assertTrue(source_row.enabled)

    def test_no_cursor_is_invented_for_offset_paging(self):
        self.service.refresh_catalogue()
        self.assertEqual(SyncCursor.objects.count(), 0)

    # -------------------------------------------------------------- mapping

    def test_mappings_are_created_with_title_year_confidence(self):
        self.service.refresh_catalogue()

        self.assertEqual(ContentMapping.objects.count(), MAPPED_OFFERS)
        friday = self._mapping(FRIDAY_ID)
        self.assertEqual(friday.tmdb_id, 941)
        self.assertEqual(friday.confidence, 0.9)
        self.assertEqual(friday.match_method, MatchMethod.TITLE_YEAR)
        self.assertEqual(friday.content_type, ContentType.MOVIE)
        self.assertIsNotNone(friday.last_verified)

        arrow = self._mapping("arrow-ptv3")
        self.assertEqual(arrow.tmdb_id, 1403)
        self.assertEqual(arrow.confidence, 0.9)
        self.assertEqual(arrow.content_type, ContentType.SERIES)

    def test_unmapped_content_logs_and_never_maps(self):
        with self.assertLogs("availability.services.refresh", level="WARNING") as logs:
            result = self.service.refresh_catalogue()

        self.assertEqual(result.skipped, UNMAPPED_OFFERS)
        self.assertFalse(
            ContentMapping.objects.filter(provider_content_id="washed").exists()
        )
        self.assertTrue(
            any(
                "availability_mapping_unresolved" in line and "washed" in line
                for line in logs.output
            )
        )

    def test_preexisting_mapping_is_reused_without_local_catalog_entry(self):
        Source.objects.get_or_create(slug="pluto_tv", defaults={"name": "Pluto TV"})
        ContentMapping.objects.create(
            provider=self.provider,
            source=Source.objects.get(slug="pluto_tv"),
            provider_content_id="washed",
            content_type=ContentType.SERIES,
            tmdb_id=777,
            confidence=0.8,
            match_method=MatchMethod.PROVIDER_MAPPING,
        )

        result = self.service.refresh_catalogue()

        self.assertEqual(result.processed, MAPPED_OFFERS + 1)
        self.assertEqual(Offer.objects.count(), MAPPED_OFFERS + 1)
        washed_offer = Offer.objects.get(provider_content_id="washed")
        self.assertEqual(washed_offer.tmdb_id, 777)
        mapping = self._mapping("washed")
        self.assertEqual(mapping.confidence, 0.8)  # manual tier untouched
        self.assertIsNotNone(mapping.last_verified)

    def test_second_run_reuses_mappings_and_is_idempotent(self):
        first = self.service.refresh_catalogue()
        second = self.service.refresh_catalogue()

        self.assertEqual(first.created, MAPPED_OFFERS)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, MAPPED_OFFERS)
        self.assertEqual(second.skipped, UNMAPPED_OFFERS)
        self.assertEqual(Offer.objects.count(), MAPPED_OFFERS)
        self.assertEqual(ContentMapping.objects.count(), MAPPED_OFFERS)
        self.assertEqual(SyncRun.objects.count(), 2)
        self.assertEqual(SyncRun.objects.filter(status=SyncStatus.SUCCEEDED).count(), 2)

    # ------------------------------------------------------- non-authoritative

    def test_item_disappearing_from_the_catalogue_is_never_deactivated(self):
        self.service.refresh_catalogue()

        # Second fetch: Friday is no longer in the movies grid.
        items = grid_result(self.api.grids[MOVIES_RAW_TOKEN]["0"])["data"]
        items[:] = [item for item in items if item["id"] != FRIDAY_ID]
        result = self.service.refresh_catalogue()

        self.assertEqual(result.deactivated, 0)
        self.assertEqual(Offer.objects.count(), MAPPED_OFFERS)
        friday_offer = Offer.objects.get(provider_content_id=FRIDAY_ID)
        self.assertTrue(friday_offer.currently_available)

    # -------------------------------------------------------------- failures

    def test_fetch_failure_writes_nothing_at_all(self):
        self.api.failures[MOVIES_HUB] = SourceFetchError("network down")

        with self.assertRaises(SourceFetchError):
            self.service.refresh_catalogue()

        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(ContentMapping.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        self.assertFalse(Source.objects.filter(slug="pluto_tv").exists())

    def test_targeted_refresh_against_a_catalogue_source_fails_loudly(self):
        with self.assertRaises(ValueError):
            self.service.refresh_content(self.friday)

    def test_unknown_scope_hint_is_an_error_not_a_silent_noop(self):
        with self.assertRaisesMessage(ValueError, "provider"):
            self.service.refresh_catalogue(provider="netflix")
        with self.assertRaisesMessage(ValueError, "country"):
            self.service.refresh_catalogue(country="ZZ")

    def test_known_country_scope_hint_is_accepted(self):
        result = self.service.refresh_catalogue(country="US")
        self.assertEqual(result.created, MAPPED_OFFERS)

    def test_catalogue_refresh_requires_the_capability(self):
        from availability.sources.streaming_availability import (
            StreamingAvailabilitySource,
        )

        targeted = AvailabilityRefreshService(source=StreamingAvailabilitySource())
        with self.assertRaisesMessage(ValueError, "catalogue sync"):
            targeted.refresh_catalogue()

    # ------------------------------------------------------------ scope rules

    def test_disabled_country_skips_every_offer_but_content_persists(self):
        Country.objects.filter(code="US").update(enabled=False)

        result = self.service.refresh_catalogue()

        self.assertEqual(result.created, 0)
        self.assertEqual(result.skipped, TOTAL_OFFERS)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        # Mapping is country-independent: identity is still recorded.
        self.assertEqual(ContentMapping.objects.count(), MAPPED_OFFERS)
        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)

    def test_unsupported_region_refreshes_to_an_empty_successful_result(self):
        collection_body(self.api.collections[MOVIES_HUB])["region"] = "XX"

        with self.assertLogs(
            "availability.sources.pluto_tv", level="WARNING"
        ) as logs:
            result = self.service.refresh_catalogue()

        self.assertTrue(
            any("pluto_tv_unsupported_region" in l for l in logs.output)
        )
        self.assertEqual(result.discovered, 0)
        self.assertEqual(result.created, 0)
        self.assertEqual(result.sync_runs, [])
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
