"""Mediaset Infinity through the source interface: registration, targeted
semantics, rights mapping, title verification, failure contracts (fixture
API, zero network).

Fixture queries (see ``mediaset_fixtures.SEARCHES``) cover, one behaviour
each: a verified AVOD movie whose search page carries decoy cards ahead of it,
an SVOD (subscription) movie, a TVOD (rent/buy) movie that must never be
persisted, an AVOD movie with a real expiry window, and a no-match result.
"""
from __future__ import annotations

import datetime as dt
import copy

from django.test import SimpleTestCase, TestCase

from availability.domain.enums import ContentType, OfferType, SourceType, SyncMode
from availability.freshness import declared_freshness
from availability.domain.enums import MatchMethod
from availability.models import ContentMapping, Country, Offer, ProviderContent, Source, SyncRun
from availability.services.refresh import AvailabilityRefreshService
from availability.services.reconciliation import AvailabilityReconciliationService
from availability.sources import SourceFetchError, source_registry
from availability.sources.base import ContentRequest
from availability.sources.mediaset_infinity import (
    APP_VERSION,
    COUNTRY,
    MediasetInfinityClient,
    MediasetInfinitySource,
)
from tracker.models import Movie, Provider

from .mediaset_fixtures import FakeMediasetApi, load_fixture

KAROL_GUID = "F011844503000101"
KAROL_TITLE = "Karol un uomo diventato Papa"
KAROL_URL = (
    "https://mediasetinfinity.mediaset.it/movie/karolunuomodiventatopapa/"
    "karol-un-uomo-diventato-papa_F011844503000101"
)
TROPPO_GUID = "F000567001000101"
MATRIX_GUID = "F010285001000102"
LUPIN_GUID = "F311059101000102"


def make_source(api) -> MediasetInfinitySource:
    client = MediasetInfinityClient(http_post=api.post)
    return MediasetInfinitySource(client=client)


def movie_request(**overrides) -> ContentRequest:
    base = {"tmdb_id": 64997, "content_type": "movie", "title": KAROL_TITLE, "year": 2005}
    base.update(overrides)
    return ContentRequest(**base)


class RegistrationTests(SimpleTestCase):
    def test_mediaset_source_is_registered(self):
        source = source_registry.get("mediaset_infinity")
        self.assertIsInstance(source, MediasetInfinitySource)
        self.assertEqual(source.slug, "mediaset_infinity")
        self.assertEqual(source.source_type, SourceType.API)

    def test_capabilities_declare_targeted_movie_scoped_source(self):
        caps = MediasetInfinitySource.capabilities
        self.assertEqual(caps.supported_providers, frozenset({"mediaset_infinity"}))
        self.assertEqual(caps.supported_countries, frozenset({COUNTRY}))
        self.assertEqual(caps.supported_content_types, frozenset({ContentType.MOVIE}))
        self.assertFalse(caps.supports_catalogue_sync)
        self.assertFalse(caps.supports_incremental_sync)
        self.assertFalse(caps.supports_authoritative_snapshots)
        self.assertFalse(caps.supports_tmdb_ids)
        self.assertFalse(caps.supports_automatic_title_mapping)
        self.assertFalse(caps.supports_seasons)
        self.assertFalse(caps.supports_episodes)
        # The detail carries audio/subtitle language codes.
        self.assertTrue(caps.supports_languages)

    def test_freshness_policy_is_declared_for_the_registered_slug(self):
        self.assertEqual(declared_freshness("mediaset_infinity"), dt.timedelta(hours=24))

    def test_catalogue_fetch_is_rejected(self):
        with self.assertRaises(ValueError):
            make_source(FakeMediasetApi()).fetch(None)

    def test_series_request_returns_empty_without_any_network(self):
        api = FakeMediasetApi()
        result = make_source(api).fetch(
            ContentRequest(tmdb_id=1, content_type="series", title="Some Show")
        )
        self.assertEqual(result.offers, [])
        self.assertEqual(result.contents, [])
        self.assertEqual(api.requests, [])


class RequestContractTests(SimpleTestCase):
    """The reduced documents ship exactly the variables the gateway needs."""

    def setUp(self):
        self.api = FakeMediasetApi()
        self.source = make_source(self.api)

    def test_search_and_detail_operations_carry_the_reduced_documents(self):
        self.source.fetch(movie_request())
        operations = {p["operationName"]: p for p in self.api.payloads}
        self.assertEqual(set(operations), {"MediasetSearch", "MediasetMovie"})

        search = operations["MediasetSearch"]
        self.assertEqual(
            search["variables"],
            {
                "property": "search",
                "query": KAROL_TITLE,
                "uxReference": "filteredSearch",
                "variant": "movie",
                "first": 12,
                "after": None,
            },
        )
        self.assertIn("getSearchPage", search["query"])
        self.assertIn("areaContainersConnection", search["query"])
        # No year is folded into the query: Mediaset exposes no year field.
        self.assertNotIn("2005", search["variables"]["query"])

        movie = operations["MediasetMovie"]
        self.assertEqual(movie["variables"], {"id": KAROL_GUID})
        self.assertIn("getMoviePage", movie["query"])
        self.assertIn("contentRight", movie["query"])


class ScopeTests(SimpleTestCase):
    def setUp(self):
        self.api = FakeMediasetApi()
        self.source = make_source(self.api)

    def test_missing_title_returns_empty_without_network(self):
        result = self.source.fetch(movie_request(title="   "))
        self.assertEqual(result.offers, [])
        self.assertEqual(result.contents, [])
        self.assertEqual(self.api.requests, [])

    def test_result_never_claims_authoritativeness_or_cursor(self):
        result = self.source.fetch(movie_request())
        self.assertFalse(result.authoritative)
        self.assertFalse(result.complete_snapshot)
        self.assertEqual(result.sync_mode, SyncMode.TARGETED)
        self.assertIsNone(result.cursor)


class SearchParsingTests(SimpleTestCase):
    def setUp(self):
        self.api = FakeMediasetApi()
        self.source = make_source(self.api)

    def test_mediaset_does_not_claim_the_requested_tmdb_id_as_provider_evidence(self):
        result = self.source.fetch(movie_request())
        self.assertIsNone(result.offers[0].tmdb_id)
        self.assertEqual(result.contents[0].external_ids, {})

    def test_avod_match_becomes_one_movie_offer(self):
        result = self.source.fetch(movie_request())
        self.assertEqual(len(result.offers), 1)
        offer = result.offers[0]
        self.assertEqual(offer.provider_id, "mediaset_infinity")
        self.assertEqual(offer.provider_content_id, KAROL_GUID)
        self.assertEqual(offer.country, "IT")
        self.assertEqual(offer.content_type, ContentType.MOVIE)
        self.assertIsNone(offer.tmdb_id)
        self.assertIsNone(offer.season_number)
        self.assertIsNone(offer.episode_number)
        self.assertEqual(offer.offer_type, OfferType.FREE_WITH_ADS)
        self.assertEqual(offer.provider_url, KAROL_URL)
        self.assertEqual(offer.quality, "")
        self.assertEqual(offer.audio_languages, ["IT"])
        self.assertEqual(offer.subtitle_languages, ["IT"])
        self.assertIsNone(offer.available_from)
        self.assertIsNone(offer.available_until)
        self.assertEqual(offer.source_record_id, KAROL_GUID)
        self.assertEqual(offer.source_version, APP_VERSION)

    def test_matched_entry_is_carried_as_discovered_content(self):
        result = self.source.fetch(movie_request())
        self.assertEqual(len(result.contents), 1)
        content = result.contents[0]
        self.assertEqual(content.provider_id, "mediaset_infinity")
        self.assertEqual(content.provider_content_id, KAROL_GUID)
        self.assertEqual(content.content_type, ContentType.MOVIE)
        self.assertEqual(content.title, KAROL_TITLE)
        self.assertEqual(content.url, KAROL_URL)
        self.assertTrue(content.description.startswith("ITALIA, 2004"))
        self.assertEqual(content.external_ids, {})
        self.assertTrue(content.replace_external_ids)
        self.assertEqual(content.identity_evidence["status"], "unresolved")
        self.assertEqual(content.source_id, "mediaset_infinity")

    def test_decoy_cards_ahead_of_the_match_do_not_shadow_it(self):
        # Fixture order is Chopin (decoy) -> Karol -> La moglie giovane.
        result = self.source.fetch(movie_request())
        self.assertEqual(result.offers[0].provider_content_id, KAROL_GUID)

    def test_case_and_punctuation_differences_still_match(self):
        result = self.source.fetch(movie_request(title="KAROL: un uomo diventato papa"))
        self.assertEqual(len(result.offers), 1)
        self.assertEqual(result.offers[0].provider_content_id, KAROL_GUID)

    def test_svod_detail_maps_to_a_subscription_offer(self):
        result = self.source.fetch(
            movie_request(tmdb_id=1111, title="Troppo rischio per un uomo solo", year=1973)
        )
        self.assertEqual(len(result.offers), 1)
        offer = result.offers[0]
        self.assertEqual(offer.offer_type, OfferType.SUBSCRIPTION)
        self.assertEqual(offer.provider_content_id, TROPPO_GUID)
        self.assertEqual(offer.country, "IT")
        self.assertEqual(offer.audio_languages, ["IT"])
        # The SVOD detail ships no subtitle languages: None stays None.
        self.assertIsNone(offer.subtitle_languages)

    def test_tvod_detail_is_observed_but_never_persisted(self):
        with self.assertLogs(
            "availability.sources.mediaset_infinity", level="INFO"
        ) as logs:
            result = self.source.fetch(
                movie_request(tmdb_id=2222, title="Matrix", year=1999)
            )
        self.assertEqual(result.offers, [])
        self.assertEqual(result.contents, [])
        self.assertIn("mediaset_infinity_offer_excluded", "\n".join(logs.output))
        # The rights arrive on the detail: both requests were spent.
        self.assertEqual(len(self.api.requests_matching("POST search Matrix")), 1)
        self.assertEqual(
            len(self.api.requests_matching(f"POST movie {MATRIX_GUID}")), 1
        )

    def test_expiring_offer_carries_until_and_normalized_languages(self):
        result = self.source.fetch(
            movie_request(tmdb_id=3333, title="Lupin III - La partita italiana", year=2016)
        )
        self.assertEqual(len(result.offers), 1)
        offer = result.offers[0]
        self.assertEqual(offer.provider_content_id, LUPIN_GUID)
        self.assertEqual(
            offer.available_until,
            dt.datetime(2026, 10, 27, 22, 59, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(offer.audio_languages, ["IT", "JA"])
        self.assertIsNone(offer.subtitle_languages)

    def test_no_verified_movie_card_is_an_empty_result(self):
        result = self.source.fetch(
            movie_request(tmdb_id=4444, title="Un Titolo Che Non Esiste", year=1990)
        )
        self.assertEqual(result.offers, [])
        self.assertEqual(result.contents, [])
        # The search still happened: the source observed the storefront.
        self.assertEqual(len(self.api.requests_matching("POST search")), 1)
        self.assertEqual(self.api.requests_matching("POST movie"), [])


class FailureContractTests(SimpleTestCase):
    def setUp(self):
        self.api = FakeMediasetApi()
        self.source = make_source(self.api)

    def test_search_transport_failure_is_not_an_empty_result(self):
        self.api.failures[KAROL_TITLE] = SourceFetchError("simulated outage")
        with self.assertRaises(SourceFetchError):
            self.source.fetch(movie_request())

    def test_detail_transport_failure_is_not_an_empty_result(self):
        self.api.failures["MediasetMovie"] = SourceFetchError("simulated outage")
        with self.assertRaises(SourceFetchError):
            self.source.fetch(movie_request())

    def test_detail_for_an_unprovisioned_guid_fails_loudly_in_tests(self):
        self.api.movies.pop(KAROL_GUID)
        with self.assertRaises(SourceFetchError):
            self.source.fetch(movie_request())

    def test_unknown_search_query_fails_loudly_in_tests(self):
        with self.assertRaises(SourceFetchError):
            self.source.fetch(movie_request(title="Not Provisioned", year=1985))

    def test_graphql_errors_array_is_rejected(self):
        self.api.searches[KAROL_TITLE] = {
            "errors": [{"message": "boom"}],
            "data": None,
        }
        with self.assertRaises(SourceFetchError) as ctx:
            self.source.fetch(movie_request())
        self.assertIn("GraphQL errors", str(ctx.exception))
        self.assertIn("boom", str(ctx.exception))

    def test_search_payload_without_the_shell_is_rejected(self):
        self.api.searches[KAROL_TITLE] = {"data": {}}
        with self.assertRaises(SourceFetchError) as ctx:
            self.source.fetch(movie_request())
        self.assertIn("payload shape", str(ctx.exception))

    def test_non_dict_search_payload_is_rejected(self):
        self.api.searches[KAROL_TITLE] = ["not", "a", "dict"]
        with self.assertRaises(SourceFetchError) as ctx:
            self.source.fetch(movie_request())
        self.assertIn("response type", str(ctx.exception))

    def test_movie_page_without_a_data_source_is_rejected(self):
        self.api.movies[KAROL_GUID] = {"data": {"getMoviePage": {"title": "x"}}}
        with self.assertRaises(SourceFetchError) as ctx:
            self.source.fetch(movie_request())
        self.assertIn("dataSource", str(ctx.exception))

    def test_null_movie_page_is_rejected(self):
        self.api.movies[KAROL_GUID] = {"data": {"getMoviePage": None}}
        with self.assertRaises(SourceFetchError) as ctx:
            self.source.fetch(movie_request())
        self.assertIn("movie page type", str(ctx.exception))

    def test_detail_without_content_right_is_rejected(self):
        self.api.movies[KAROL_GUID] = {
            "data": {
                "getMoviePage": {
                    "title": KAROL_TITLE,
                    "url": KAROL_URL,
                    "dataSource": {"__typename": "VideoItem", "guid": KAROL_GUID},
                }
            }
        }
        with self.assertRaises(SourceFetchError) as ctx:
            self.source.fetch(movie_request())
        self.assertIn("contentRight", str(ctx.exception))

    def test_unknown_content_right_yields_empty_not_a_crash(self):
        self.api.movies[KAROL_GUID] = {
            "data": {
                "getMoviePage": {
                    "title": KAROL_TITLE,
                    "url": KAROL_URL,
                    "dataSource": {
                        "__typename": "VideoItem",
                        "guid": KAROL_GUID,
                        "title": KAROL_TITLE,
                        "contentRight": "SOMEWHERE_NEW",
                    },
                }
            }
        }
        with self.assertLogs(
            "availability.sources.mediaset_infinity", level="WARNING"
        ) as logs:
            result = self.source.fetch(movie_request())
        self.assertEqual(result.offers, [])
        self.assertEqual(result.contents, [])
        self.assertIn("mediaset_infinity_unknown_content_right", "\n".join(logs.output))


class RefreshPathTests(TestCase):
    """End-to-end targeted refresh: fixture API -> Offer/ProviderContent rows."""

    def setUp(self):
        self.api = FakeMediasetApi()
        self.source = make_source(self.api)
        self.service = AvailabilityRefreshService(source=self.source)
        self.provider, _ = Provider.objects.get_or_create(
            slug="mediaset_infinity", defaults={"name": "Mediaset Infinity", "is_enabled": True}
        )
        Country.objects.get_or_create(code="IT", defaults={"name": "Italy"})
        self.movie = Movie.objects.create(
            tmdb_id=64997, title=KAROL_TITLE, year=2005, content_type="movie"
        )
        source_row, _ = Source.objects.get_or_create(
            slug="mediaset_infinity", defaults={"name": "Mediaset Infinity"}
        )
        ContentMapping.objects.create(
            provider=self.provider,
            source=source_row,
            provider_content_id=KAROL_GUID,
            content_type="movie",
            tmdb_id=64997,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )

    def test_refresh_content_persists_the_offer_and_a_targeted_sync_run(self):
        result = self.service.refresh_content(self.movie)

        self.assertEqual(result.discovered, 1)
        self.assertEqual(result.processed, 1)
        self.assertEqual(result.created, 1)
        self.assertEqual(result.skipped, 0)
        self.assertTrue(Source.objects.filter(slug="mediaset_infinity", enabled=True).exists())
        self.assertEqual(Offer.objects.count(), 1)
        offer = Offer.objects.get()
        self.assertEqual(
            offer.identity,
            f"mediaset_infinity|IT|movie|64997|||free_with_ads|{KAROL_GUID}",
        )
        self.assertEqual(offer.provider, self.provider)
        self.assertEqual(offer.country.code, "IT")
        self.assertEqual(offer.source.slug, "mediaset_infinity")
        self.assertEqual(offer.offer_type, OfferType.FREE_WITH_ADS)
        self.assertEqual(offer.content_type, ContentType.MOVIE)
        self.assertEqual(offer.audio_languages, ["IT"])
        self.assertEqual(offer.subtitle_languages, ["IT"])
        self.assertTrue(offer.currently_available)

        run = SyncRun.objects.get()
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.provider, self.provider)
        self.assertEqual(run.country.code, "IT")
        self.assertEqual(run.source.slug, "mediaset_infinity")
        self.assertEqual(run.mode, "targeted")
        self.assertFalse(run.authoritative)
        self.assertEqual(run.items_processed, 1)
        self.assertEqual(run.offers_created, 1)

    def test_discovered_content_is_kept_even_for_the_targeted_path(self):
        self.service.refresh_content(self.movie)
        content = ProviderContent.objects.get()
        self.assertEqual(content.provider, self.provider)
        self.assertEqual(content.provider_content_id, KAROL_GUID)
        self.assertEqual(content.content_type, ContentType.MOVIE)
        self.assertEqual(content.title, KAROL_TITLE)

    def test_series_refresh_is_a_successful_noop_without_network(self):
        series = Movie.objects.create(
            tmdb_id=700, title="Some Show", year=2020, content_type="series"
        )
        result = self.service.refresh_content(series)
        self.assertEqual(result.discovered, 0)
        self.assertEqual(result.created, 0)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        self.assertEqual(self.api.requests, [])

    def test_fetch_failure_leaves_no_rows_and_no_sync_run(self):
        self.api.failures[KAROL_TITLE] = SourceFetchError("simulated outage")
        with self.assertRaises(SourceFetchError):
            self.service.refresh_content(self.movie)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        self.assertEqual(ProviderContent.objects.count(), 0)

    def test_missing_country_row_skips_the_offer_but_succeeds(self):
        Country.objects.all().delete()
        result = self.service.refresh_content(self.movie)
        self.assertEqual(result.created, 0)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(Offer.objects.count(), 0)

    def test_tvod_refresh_persists_nothing(self):
        movie = Movie.objects.create(
            tmdb_id=2222, title="Matrix", year=1999, content_type="movie"
        )
        result = self.service.refresh_content(movie)
        self.assertEqual(result.discovered, 0)
        self.assertEqual(result.created, 0)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)

    @staticmethod
    def dune_api(*, guids=("DUNE-1984",), description="USA, 1984 | Dune"):
        search = load_fixture("search_karol.json")
        items = search["data"]["getSearchPage"]["areaContainersConnection"]["areaContainers"][0]["areas"][0]["sections"][0]["collections"][0]["itemsConnection"]["items"]
        items[:] = [
            {
                "__typename": "VideoItem",
                "guid": guid,
                "cardTitle": "Dune",
                "editorialType": "Movie",
                "url": f"https://mediasetinfinity.mediaset.it/movie/dune/{guid}",
                "cardLink": {"value": f"https://mediasetinfinity.mediaset.it/movie/dune/{guid}"},
            }
            for guid in guids
        ]
        detail = load_fixture("movie_avod.json")
        page = detail["data"]["getMoviePage"]
        page["title"] = "Dune"
        page["url"] = f"https://mediasetinfinity.mediaset.it/movie/dune/{guids[0]}"
        page["dataSource"].update({
            "guid": guids[0], "title": "Dune", "url": page["url"],
            "description": description,
        })
        return FakeMediasetApi(searches={"Dune": search}, movies={guids[0]: detail})

    def test_dune_1984_title_match_does_not_create_2021_mapping_or_offer(self):
        dune_2021 = Movie.objects.create(
            tmdb_id=438631, title="Dune", year=2021, content_type="movie"
        )
        Movie.objects.create(
            tmdb_id=841, title="Dune", year=1984, content_type="movie"
        )
        ProviderContent.objects.create(
            provider=self.provider,
            source=Source.objects.get(slug="mediaset_infinity"),
            provider_content_id="DUNE-1984",
            content_type="movie",
            title="Dune",
            metadata={"external_ids": {"tmdb": "438631"}},
            identity="mediaset_infinity|DUNE-1984|movie||",
        )
        api = self.dune_api()
        service = AvailabilityRefreshService(source=make_source(api))

        result = service.refresh_content(dune_2021)

        provider_content = ProviderContent.objects.get(provider_content_id="DUNE-1984")
        self.assertIsNone(provider_content.release_date)
        self.assertEqual(provider_content.metadata["external_ids"], {})
        self.assertFalse(ContentMapping.objects.filter(provider_content_id="DUNE-1984").exists())
        self.assertFalse(Offer.objects.filter(provider_content_id="DUNE-1984").exists())
        self.assertEqual(provider_content.metadata["identity_evidence"]["status"], "unresolved")
        self.assertEqual(result.unresolved, 1)

    def test_unknown_year_does_not_resolve_even_when_local_title_is_unique(self):
        dune_2021 = Movie.objects.create(
            tmdb_id=438631, title="Dune", year=2021, content_type="movie"
        )
        api = self.dune_api(description="Dune")

        result = AvailabilityRefreshService(source=make_source(api)).refresh_content(dune_2021)

        self.assertTrue(ProviderContent.objects.filter(provider_content_id="DUNE-1984").exists())
        self.assertFalse(ContentMapping.objects.filter(provider_content_id="DUNE-1984").exists())
        self.assertFalse(Offer.objects.filter(provider_content_id="DUNE-1984").exists())
        self.assertEqual(result.unresolved, 1)

    def test_ambiguous_same_title_provider_candidates_remain_unresolved(self):
        dune_2021 = Movie.objects.create(
            tmdb_id=438631, title="Dune", year=2021, content_type="movie"
        )
        api = self.dune_api(guids=("DUNE-1984", "DUNE-2021"))
        service = AvailabilityRefreshService(source=make_source(api))

        result = service.refresh_content(dune_2021)

        self.assertEqual(ProviderContent.objects.count(), 2)
        self.assertFalse(
            ContentMapping.objects.filter(
                provider=self.provider,
                provider_content_id__startswith="DUNE-",
            ).exists()
        )
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(result.discovered, 0)
        self.assertEqual(api.requests_matching("POST movie"), [])

    def test_trusted_manual_mapping_is_reused_but_corrupt_external_mapping_is_not(self):
        dune_2021 = Movie.objects.create(
            tmdb_id=438631, title="Dune", year=2021, content_type="movie"
        )
        manual_api = self.dune_api(guids=("DUNE-MANUAL",), description="Dune")
        ContentMapping.objects.create(
            provider=self.provider,
            source=Source.objects.create(slug="operator_review", name="Operator review"),
            provider_content_id="DUNE-MANUAL",
            content_type="movie", tmdb_id=438631, confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )
        AvailabilityRefreshService(source=make_source(manual_api)).refresh_content(dune_2021)
        manual_offer = Offer.objects.get(provider_content_id="DUNE-MANUAL")
        self.assertEqual(manual_offer.tmdb_id, 438631)
        self.assertEqual(
            ContentMapping.objects.get(provider_content_id="DUNE-MANUAL").match_method,
            MatchMethod.MANUAL,
        )

        corrupt_api = self.dune_api(guids=("DUNE-CORRUPT",), description="USA, 1984 | Dune")
        ContentMapping.objects.create(
            provider=self.provider,
            source=Source.objects.get(slug="mediaset_infinity"),
            provider_content_id="DUNE-CORRUPT",
            content_type="movie", tmdb_id=438631, confidence=0.95,
            match_method=MatchMethod.EXTERNAL_ID,
        )
        AvailabilityRefreshService(source=make_source(corrupt_api)).refresh_content(dune_2021)
        self.assertFalse(Offer.objects.filter(provider_content_id="DUNE-CORRUPT").exists())

    def test_historical_external_id_mapping_cannot_make_an_offer_watchable(self):
        dune_2021 = Movie.objects.create(
            tmdb_id=438631, title="Dune", year=2021, content_type="movie"
        )
        source_row, _ = Source.objects.get_or_create(
            slug="mediaset_infinity", defaults={"name": "Mediaset Infinity"}
        )
        country = Country.objects.get(code="IT")
        ContentMapping.objects.create(
            provider=self.provider, source=source_row,
            provider_content_id="DUNE-CORRUPT", content_type="movie",
            tmdb_id=dune_2021.tmdb_id, confidence=0.95,
            match_method=MatchMethod.EXTERNAL_ID,
        )
        Offer.objects.create(
            provider=self.provider, country=country, source=source_row,
            content_type="movie", tmdb_id=dune_2021.tmdb_id,
            offer_type=OfferType.SUBSCRIPTION,
            provider_content_id="DUNE-CORRUPT",
            identity="mediaset_infinity|IT|movie|438631|||subscription|DUNE-CORRUPT",
        )
        Offer.objects.create(
            provider=self.provider, country=country, source=source_row,
            content_type="movie", tmdb_id=dune_2021.tmdb_id,
            offer_type=OfferType.SUBSCRIPTION,
            provider_content_id="DUNE-UNMAPPED",
            identity="mediaset_infinity|IT|movie|438631|||subscription|DUNE-UNMAPPED",
        )
        justwatch_source, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JustWatch"}
        )
        Offer.objects.create(
            provider=self.provider, country=country, source=justwatch_source,
            content_type="movie", tmdb_id=dune_2021.tmdb_id,
            offer_type=OfferType.SUBSCRIPTION,
            provider_content_id="JUSTWATCH-DUNE-2021",
            identity="mediaset_infinity|IT|movie|438631|||subscription|JUSTWATCH-DUNE-2021",
        )

        reconciled = AvailabilityReconciliationService().reconcile_content(
            content_type="movie", tmdb_id=dune_2021.tmdb_id, country="IT"
        )

        observations = reconciled.offers[0].observations
        self.assertEqual(len(observations), 3)
        by_content_id = {
            observation.provider_content_id: observation for observation in observations
        }
        self.assertFalse(by_content_id["DUNE-CORRUPT"].available_now)
        self.assertEqual(
            by_content_id["DUNE-CORRUPT"].mapping_doubt,
            "provider-content mapping provenance is unverified",
        )
        self.assertFalse(by_content_id["DUNE-UNMAPPED"].available_now)
        self.assertEqual(
            by_content_id["DUNE-UNMAPPED"].mapping_doubt,
            "provider-content mapping is unresolved",
        )
        self.assertTrue(by_content_id["JUSTWATCH-DUNE-2021"].available_now)
        self.assertEqual(by_content_id["JUSTWATCH-DUNE-2021"].mapping_doubt, "")
        doubts = {observation.mapping_doubt for observation in observations}
        self.assertIn("provider-content mapping provenance is unverified", doubts)
        self.assertIn("provider-content mapping is unresolved", doubts)

    def test_non_authoritative_missing_result_preserves_existing_offer(self):
        existing = Offer.objects.create(
            provider=self.provider,
            country=Country.objects.get(code="IT"),
            source=Source.objects.get(slug="mediaset_infinity"),
            content_type="movie", tmdb_id=64997,
            offer_type=OfferType.FREE_WITH_ADS,
            provider_content_id=KAROL_GUID,
            identity=f"mediaset_infinity|IT|movie|64997|||free_with_ads|{KAROL_GUID}",
        )
        api = FakeMediasetApi(
            searches={KAROL_TITLE: load_fixture("search_nomatch.json")},
            movies={},
        )

        AvailabilityRefreshService(source=make_source(api)).refresh_content(self.movie)

        existing.refresh_from_db()
        self.assertTrue(existing.currently_available)
