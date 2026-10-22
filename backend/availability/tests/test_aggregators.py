"""JustWatch + Watchmode aggregators: transport, mapping, source and lifecycle tests.

Zero network. Every external call goes through the injected ``http_post`` /
``http_get`` seam and returns a recorded fixture, so CI never depends on either
API being online.

What is asserted, and why each matters:

- **Transport**: request construction, the typed-fragment requirement, the
  measured 100-title ceiling, ``page too large`` handling, retryable vs
  non-retryable statuses, malformed bodies, and the single-request
  multi-country operation.
- **Provider mapping**: Prime Video resolves to ONE canonical provider from two
  different country ids (``prv`` in NL, ``amp`` in US/GB), and an unknown
  JustWatch package is skipped rather than inventing a Provider.
- **Source**: movie/season mapping, TMDb resolution, monetization, quality,
  deeplinks, and client-side package filtering (the measured trap: the
  ``packages`` filter selects titles, not offers).
- **Lifecycle**: JustWatch/Watchmode absence must NOT deactivate a first-party
  collector's offer, and a failed run must not look like a successful empty one.
"""
from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from availability.domain.enums import (
    ContentType,
    OfferType,
    SourceType,
    SyncMode,
)
from availability.freshness import declared_freshness
from availability.models import (
    ContentMapping,
    Country,
    Offer,
    ProviderContent,
    Source,
    SyncCursor,
    SyncRun,
)
from availability.persistence.repositories import (
    InactiveSyncError,
    OfferRepository,
    SyncRunRepository,
)
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import SourceFetchError, source_registry
from availability.sources.base import ContentRequest, SourceQuotaExceeded
from availability.sources.justwatch import JustWatchSource
from availability.sources.justwatch_client import (
    MAX_TITLES_PER_REQUEST,
    JustWatchClient,
    JustWatchGraphQLError,
    JustWatchResponseError,
    JustWatchTransportError,
    offers_for_countries_query,
)
from availability.sources.justwatch_providers import (
    PACKAGE_SHORT_NAMES,
    PACKAGE_TO_PROVIDER_SLUG,
    country_package_map,
    package_short_name,
    provider_slug_for_package,
)
from availability.sources.watchmode import WatchmodeSource
from availability.sources.watchmode_client import (
    WatchmodeClient,
    WatchmodeQuotaExceeded,
    WatchmodeRegionUnsupported,
    WatchmodeResponseError,
)
from tracker.models import ExternalProviderMapping, Movie, Provider
from tracker.services.justwatch_sync import (
    DEFAULT_MAX_PARTITIONS,
    available_partitions,
    refresh_justwatch_partitions,
)

from . import aggregator_fixtures as fx


def sleeper(_seconds):
    """Never actually sleep in tests."""


# =============================================================================
# JustWatch transport
# =============================================================================
class JustWatchTransportTests(SimpleTestCase):
    def build(self, *responses):
        """Client whose POST returns *responses* in order; records payloads."""
        queue = list(responses)
        seen = []

        def http_post(url, json=None, headers=None, timeout=None):
            seen.append(json)
            item = queue.pop(0) if queue else fx.FakeResponse()
            if isinstance(item, Exception):
                raise item
            return item

        client = JustWatchClient(http_post=http_post, sleeper=sleeper)
        return client, seen

    def test_catalogue_request_construction(self):
        client, seen = self.build(
            fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX))
        page = client.catalogue_titles(country="NL", count=50, offset=100,
                                       packages=["nfx"], object_types=["MOVIE"],
                                       min_year=2025, max_year=2026)
        self.assertEqual(len(page), 2)
        payload = seen[0]
        self.assertEqual(payload["operationName"], "GetPopularTitles")
        variables = payload["variables"]
        self.assertEqual(variables["country"], "NL")
        self.assertEqual(variables["first"], 50)
        self.assertEqual(variables["offset"], 100)
        filter_vars = variables["popularTitlesFilter"]
        self.assertEqual(filter_vars["packages"], ["nfx"])
        self.assertEqual(filter_vars["objectTypes"], ["MOVIE"])
        self.assertEqual(filter_vars["releaseYear"], {"min": 2025, "max": 2026})

    def test_typed_fragment_required_fields_present(self):
        """``fullPath``/``externalIds`` need the typed fragment, else HTTP 422."""
        client, seen = self.build(
            fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX))
        client.catalogue_titles(country="NL", count=10)
        query = seen[0]["query"]
        self.assertIn("fragment FullContentDetails on MovieOrShowOrSeasonContent",
                      query)
        self.assertIn("...FullContentDetails", query)
        self.assertIn("externalIds", query)
        # The offers fragment must exist because TitleDetails spreads it.
        self.assertIn("fragment TitleOffer on Offer", query)
        self.assertIn("fragment PackageDetails on Package", query)

    def test_page_size_ceiling_is_enforced_before_the_network(self):
        client, seen = self.build()
        with self.assertRaises(ValueError) as ctx:
            client.catalogue_titles(country="NL", count=500)
        self.assertIn(str(MAX_TITLES_PER_REQUEST), str(ctx.exception))
        self.assertEqual(seen, [], "no request may be sent for an oversized page")

    def test_max_page_size_is_one_hundred(self):
        self.assertEqual(MAX_TITLES_PER_REQUEST, 100)

    def test_page_too_large_is_a_graphql_error_not_empty(self):
        client, _ = self.build(
            fx.FakeResponse(payload=fx.JW_ERRORS_PAGE_TOO_LARGE))
        with self.assertRaises(JustWatchGraphQLError) as ctx:
            client.catalogue_titles(country="NL", count=100)
        self.assertTrue(ctx.exception.page_too_large)

    def test_complexity_limit_is_recognised_as_page_too_large(self):
        client, _ = self.build(
            fx.FakeResponse(payload=fx.JW_ERRORS_COMPLEXITY))
        with self.assertRaises(JustWatchGraphQLError) as ctx:
            client.catalogue_titles(country="NL", count=100)
        self.assertTrue(ctx.exception.page_too_large)

    def test_other_graphql_error_is_not_treated_as_page_size(self):
        client, _ = self.build(fx.FakeResponse(payload=fx.JW_ERRORS_INVALID))
        with self.assertRaises(JustWatchGraphQLError) as ctx:
            client.catalogue_titles(country="NL", count=10)
        self.assertFalse(ctx.exception.page_too_large)

    def test_retryable_status_is_retried_then_raised(self):
        client, seen = self.build(
            fx.FakeResponse(status_code=503),
            fx.FakeResponse(status_code=503),
            fx.FakeResponse(status_code=503),
        )
        with self.assertRaises(JustWatchTransportError):
            client.catalogue_titles(country="NL", count=10)
        self.assertEqual(len(seen), 3, "a retryable status must be retried")

    def test_retryable_status_then_success(self):
        client, seen = self.build(
            fx.FakeResponse(status_code=429),
            fx.FakeResponse(payload=fx.JW_CATALOGUE_EMPTY),
        )
        page = client.catalogue_titles(country="NL", count=10)
        self.assertEqual(len(page), 0)
        self.assertEqual(len(seen), 2)

    def test_non_retryable_4xx_is_not_retried(self):
        client, seen = self.build(fx.FakeResponse(status_code=400, payload={}))
        with self.assertRaises(JustWatchTransportError):
            client.catalogue_titles(country="NL", count=10)
        self.assertEqual(len(seen), 1, "a 400 will stay a 400; do not burn budget")

    def test_malformed_non_json_response(self):
        client, _ = self.build(fx.FakeResponse(
            content_type="text/html", text="<html>We are not available</html>"))
        with self.assertRaises(JustWatchResponseError) as ctx:
            client.catalogue_titles(country="NL", count=10)
        self.assertIn("non-JSON", str(ctx.exception))

    def test_undecodable_json_response(self):
        client, _ = self.build(fx.FakeResponse(content_type="application/json",
                                              payload=None))
        with self.assertRaises(JustWatchResponseError):
            client.catalogue_titles(country="NL", count=10)

    def test_empty_catalogue_is_success_not_failure(self):
        client, _ = self.build(fx.FakeResponse(payload=fx.JW_CATALOGUE_EMPTY))
        page = client.catalogue_titles(country="NL", count=10)
        self.assertEqual(len(page), 0, "an empty page is a valid, successful result")

    def test_tmdb_and_imdb_ids_are_parsed(self):
        client, _ = self.build(
            fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX))
        title = client.catalogue_titles(country="NL", count=10).titles[0]
        self.assertEqual(title.tmdb_id, "1061474")
        self.assertEqual(title.imdb_id, "tt5950044")
        self.assertEqual(title.tmdb_id_int, 1061474)
        self.assertEqual(title.poster, "https://images.justwatch.com/poster.jpg")
        self.assertEqual(title.url, "https://www.justwatch.com/nl/movie/x")

    def test_blank_tmdb_id_does_not_resolve_to_movie_zero(self):
        client, _ = self.build(fx.FakeResponse(payload=envelope_with_blank_tmdb()))
        title = client.catalogue_titles(country="NL", count=10).titles[0]
        self.assertIsNone(title.tmdb_id_int)

    def test_providers_are_parsed(self):
        client, _ = self.build(fx.FakeResponse(payload=fx.JW_PROVIDERS_NL))
        packages = client.providers("NL")
        by_tech = {p.technical_name: p for p in packages}
        self.assertEqual(by_tech["netflix"].short_name, "nfx")
        self.assertEqual(by_tech["raiplay"].clear_name, "Rai Play")
        self.assertIn("nosuchprovider", by_tech)

    def test_offers_for_multiple_countries_in_one_request(self):
        client, seen = self.build(
            fx.FakeResponse(payload=fx.envelope(fx.JW_OFFERS_MULTI_COUNTRY)))
        result = client.offers_for_countries("tm1", {"NL", "US", "DE"})
        self.assertEqual(sorted(result), ["DE", "NL", "US"])
        self.assertEqual(len(result["NL"]), 1)
        self.assertEqual(len(result["US"]), 1)
        self.assertEqual(result["DE"], [], "an empty country is still present")
        query = seen[0]["query"]
        self.assertIn("NL: offers(country: NL", query)
        self.assertIn("US: offers(country: US", query)
        self.assertIn("DE: offers(country: DE", query)

    def test_offers_query_requires_a_country(self):
        with self.assertRaises(ValueError):
            offers_for_countries_query(set())

    def test_seasons_are_nested_under_node(self):
        """Regression: ``seasons`` lives under ``data["node"]``.

        Reading ``data["seasons"]`` returns nothing for a show that reports
        ``total_season_count > 0`` — a silent empty result, not an error. The
        fixture mirrors the real envelope so this cannot regress unnoticed.
        """
        client, seen = self.build(
            fx.FakeResponse(payload=fx.envelope(fx.JW_SEASONS)))
        seasons = client.seasons("ts258721", country="NL")
        self.assertEqual(len(seasons), 2)
        self.assertEqual([s.season_number for s in seasons], [1, 2])
        self.assertIn("seasons(sortDirection: ASC)", seen[0]["query"])

    def test_episodes_are_nested_under_node(self):
        client, _ = self.build(
            fx.FakeResponse(payload=fx.envelope(fx.JW_EPISODES)))
        episodes = client.episodes("tss351167", country="NL")
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].episode_number, 1)

    def test_empty_seasons_payload_is_not_an_error(self):
        client, _ = self.build(fx.FakeResponse(payload=fx.envelope({"node": {}})))
        self.assertEqual(client.seasons("ts1", country="NL"), [])

    def test_seasons_and_episodes_are_parsed(self):
        client, _ = self.build(
            fx.FakeResponse(payload=fx.envelope(fx.JW_SEASONS)))
        seasons = client.seasons("ts258721", country="NL")
        self.assertEqual([s.season_number for s in seasons], [1, 2])
        self.assertEqual(seasons[0].total_episode_count, 8)

        client2, _ = self.build(
            fx.FakeResponse(payload=fx.envelope(fx.JW_EPISODES)))
        episodes = client2.episodes("tss351167", country="NL")
        self.assertEqual(episodes[0].episode_number, 1)
        self.assertEqual(episodes[0].object_type, "SHOW_EPISODE")


def envelope_with_blank_tmdb():
    node = fx.title("tm1", "Blank", tmdb_id="")
    return fx.envelope({"popularTitles": {"edges": [{"node": node}]}})


# =============================================================================
# Provider mapping
# =============================================================================
class JustWatchProviderMappingTests(SimpleTestCase):
    def test_prime_video_has_two_technical_names_one_canonical_slug(self):
        self.assertEqual(provider_slug_for_package("amazonprimevideo"), "prime")
        self.assertEqual(provider_slug_for_package("amazonprime"), "prime")

    def test_country_specific_short_names(self):
        self.assertEqual(package_short_name("prime", "NL"), "prv")
        self.assertEqual(package_short_name("prime", "US"), "amp")
        self.assertEqual(package_short_name("prime", "GB"), "amp")
        self.assertEqual(package_short_name("netflix", "NL"), "nfx")

    def test_unknown_package_is_unmapped_not_guessed(self):
        self.assertEqual(provider_slug_for_package("wug"), "")
        self.assertEqual(provider_slug_for_package("nosuchprovider"), "")

    def test_similar_names_do_not_map(self):
        """``amazonhbomax``/``amazonprimevideowithads`` are different services."""
        for technical in ("amazonhbomax", "amazonprimevideowithads",
                          "amazonimdbtv", "amazonmgm", "appletvparamountplus"):
            self.assertNotEqual(provider_slug_for_package(technical), "max",
                                f"{technical} must not resolve to max")
            self.assertNotEqual(provider_slug_for_package(technical), "prime",
                                f"{technical} must not resolve to prime")

    def test_crave_is_not_mapped_to_cbc_gem(self):
        """Both are Canadian, but they are different services.

        Mapping them together would attach Crave's catalogue to the canonical
        cbc_gem provider and write confidently wrong offers.
        """
        for technical in ("crave",):
            self.assertNotEqual(provider_slug_for_package(technical), "cbc_gem")
            self.assertEqual(provider_slug_for_package(technical), "")
        self.assertNotIn("cbc_gem", PACKAGE_SHORT_NAMES)
        # CBC Gem simply has no JustWatch package in our measured set.
        self.assertEqual(package_short_name("cbc_gem", "CA"), "")

    def test_unmapped_market_yields_no_short_name(self):
        self.assertEqual(package_short_name("tubi", "NL"), "")
        self.assertEqual(package_short_name("peacock", "NL"), "")

    def test_country_package_map_dedupes(self):
        self.assertEqual(country_package_map(["netflix", "netflix"], "NL"), ["nfx"])

    def test_country_package_map_empty_when_nothing_mapped(self):
        self.assertEqual(country_package_map(["peacock"], "NL"), [])


# =============================================================================
# JustWatch source
# =============================================================================
class JustWatchSourceRegistrationTests(SimpleTestCase):
    def test_source_is_registered_as_aggregator(self):
        source = source_registry.get("justwatch")
        self.assertIsInstance(source, JustWatchSource)
        self.assertEqual(source.source_type, SourceType.AGGREGATOR)

    def test_never_authoritative_so_it_cannot_deactivate(self):
        caps = JustWatchSource.capabilities
        self.assertFalse(caps.supports_authoritative_snapshots)
        self.assertTrue(caps.supports_catalogue_sync)
        self.assertTrue(caps.supports_tmdb_ids)
        self.assertTrue(caps.supports_episodes)
        self.assertTrue(caps.supports_seasons)

    def test_country_scope_is_unrestricted(self):
        """A fixed country set would cap coverage and imply a market."""
        self.assertIsNone(JustWatchSource.capabilities.supported_countries)

    def test_freshness_is_declared_per_source(self):
        self.assertIsNotNone(declared_freshness("justwatch"))


class JustWatchSourceCountryTests(SimpleTestCase):
    def test_missing_country_is_rejected_not_inferred(self):
        source = JustWatchSource(client=object())
        with self.assertRaises(SourceFetchError) as ctx:
            source.fetch(ContentRequest(tmdb_id=550, title="Fight Club"))
        self.assertIn("explicit country", str(ctx.exception))

    def test_malformed_country_is_rejected(self):
        source = JustWatchSource(client=object())
        with self.assertRaises(ValueError):
            source.fetch(None, country="NLD")


class JustWatchSourceFetchTests(SimpleTestCase):
    def movie_source(self, *responses):
        queue = list(responses)
        seen = []

        def http_post(url, json=None, headers=None, timeout=None):
            seen.append(json)
            return queue.pop(0) if queue else fx.FakeResponse()

        return JustWatchSource(client=JustWatchClient(http_post=http_post,
                                                       sleeper=sleeper)), seen

    def test_movie_fetch_returns_content_and_offers(self):
        source, _ = self.movie_source(
            fx.FakeResponse(payload=fx.JW_SEARCH_MOVIE))
        result = source.fetch(ContentRequest(tmdb_id=1061474, title="Superman",
                                             year=2025),
                              country="NL")
        self.assertEqual(len(result.contents), 1)
        self.assertTrue(result.offers)
        content = result.contents[0]
        self.assertEqual(content.provider_content_id, "tm1303572")
        self.assertEqual(content.content_type, ContentType.MOVIE)
        self.assertEqual(content.external_ids["tmdb"], "1061474")
        self.assertEqual(content.external_ids["imdb"], "tt5950044")

    def test_show_fetch_walks_seasons(self):
        source, _ = self.movie_source(
            fx.FakeResponse(payload=fx.JW_SEARCH_SHOW),
            fx.FakeResponse(payload=fx.envelope(fx.JW_SEASONS)),
        )
        result = source.fetch(
            ContentRequest(tmdb_id=236235, content_type="series",
                           title="The Gentlemen"), country="NL")
        types = {c.content_type for c in result.contents}
        self.assertIn(ContentType.SERIES, types)
        self.assertIn(ContentType.SEASON, types)
        season_contents = [c for c in result.contents
                           if c.content_type == ContentType.SEASON]
        numbers = [c.season_number for c in season_contents
                   if c.season_number is not None]
        self.assertEqual(sorted(numbers), [1, 2])

    def test_season_failure_does_not_discard_series_offers(self):
        """A partial enrichment must not turn into a failed fetch."""
        source, _ = self.movie_source(
            fx.FakeResponse(payload=fx.JW_SEARCH_SHOW),
            fx.FakeResponse(status_code=500),
            fx.FakeResponse(status_code=500),
            fx.FakeResponse(status_code=500),
        )
        result = source.fetch(
            ContentRequest(tmdb_id=236235, content_type="series",
                           title="The Gentlemen"), country="NL")
        self.assertTrue(result.offers, "series-level offers survive a season failure")

    def test_unmapped_provider_is_skipped_not_fatal(self):
        source, _ = self.movie_source(
            fx.FakeResponse(payload=fx.JW_CATALOGUE_UNMAPPED_ONLY))
        result = source.fetch(None, provider=None, country="NL")
        self.assertEqual(result.offers, [])

    def test_transport_failure_raises_rather_than_returning_empty(self):
        source, _ = self.movie_source(
            fx.FakeResponse(status_code=500),
            fx.FakeResponse(status_code=500),
            fx.FakeResponse(status_code=500),
        )
        with self.assertRaises(SourceFetchError):
            source.fetch(ContentRequest(tmdb_id=1, title="X"), country="NL")

    def test_graphql_failure_raises_rather_than_returning_empty(self):
        source, _ = self.movie_source(
            fx.FakeResponse(payload=fx.JW_ERRORS_INVALID))
        with self.assertRaises(SourceFetchError):
            source.fetch(ContentRequest(tmdb_id=1, title="X"), country="NL")

    def test_no_match_is_a_successful_empty_result(self):
        source, _ = self.movie_source(
            fx.FakeResponse(payload=fx.JW_CATALOGUE_EMPTY))
        result = source.fetch(ContentRequest(tmdb_id=1, title="Nope"), country="NL")
        self.assertEqual(result.offers, [])
        self.assertEqual(result.contents, [])


class JustWatchCatalogueTests(SimpleTestCase):
    def build(self, *responses):
        queue = list(responses)
        seen = []

        def http_post(url, json=None, headers=None, timeout=None):
            seen.append(json)
            return queue.pop(0) if queue else fx.FakeResponse()

        return JustWatchSource(client=JustWatchClient(http_post=http_post,
                                                       sleeper=sleeper)), seen

    def test_catalogue_filters_offers_client_side(self):
        """The measured trap: ``packages`` selects titles, not offers."""
        source, _ = self.build(fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX))
        result = source.fetch(None, provider="netflix", country="NL")
        self.assertTrue(result.contents, "titles are still returned")
        providers = {offer.provider_id for offer in result.offers}
        self.assertEqual(providers, {"netflix"},
                         "only Netflix offers may survive a Netflix partition")

    def test_catalogue_pagination_advances_cursor(self):
        source, _ = self.build(fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX))
        result = source.fetch(None, provider="netflix", country="NL",
                              cursor={"content_type": ContentType.MOVIE})
        self.assertEqual(result.cursor, str(source.page_size))

    def test_catalogue_boundary_stops_without_a_cursor(self):
        source, _ = self.build(fx.FakeResponse(payload=fx.JW_CATALOGUE_EMPTY))
        result = source.fetch(None, provider="netflix", country="NL")
        self.assertIsNone(result.cursor, "an empty page ends the walk")

    def test_catalogue_offset_is_bounded(self):
        source, _ = self.build()
        result = source.fetch(None, provider="netflix", country="NL",
                              cursor="99999")
        self.assertEqual(result.offers, [])
        self.assertEqual(result.contents, [])

    def test_unmapped_provider_partition_is_empty_not_an_error(self):
        source, seen = self.build()
        result = source.fetch(None, provider="peacock", country="NL")
        self.assertEqual(result.contents, [])
        self.assertEqual(seen, [], "no point asking for an unwalkable partition")

    def test_catalogue_is_incremental_never_authoritative(self):
        source, _ = self.build(fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX))
        result = source.fetch(None, provider="netflix", country="NL")
        self.assertEqual(result.sync_mode, SyncMode.INCREMENTAL)
        self.assertFalse(result.authoritative)
        self.assertFalse(result.complete_snapshot)

    def test_page_too_large_halves_the_page_and_retries(self):
        source, seen = self.build(
            fx.FakeResponse(payload=fx.JW_ERRORS_PAGE_TOO_LARGE),
            fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX),
        )
        source.page_size = 100
        result = source.fetch(None, provider="netflix", country="NL")
        self.assertEqual(source.page_size, 50)
        self.assertEqual(len(seen), 2)
        self.assertTrue(result.contents)


# =============================================================================
# Watchmode
# =============================================================================
class WatchmodeTransportTests(SimpleTestCase):
    def build(self, *responses):
        queue = list(responses)
        seen = []

        def http_get(url, headers=None, timeout=None):
            seen.append(url)
            item = queue.pop(0) if queue else fx.FakeResponse()
            if isinstance(item, Exception):
                raise item
            return item

        return WatchmodeClient(http_get=http_get, api_key="test-key",
                               sleeper=sleeper, budget=lambda: None), seen

    def test_search_response_is_a_dict_with_title_results(self):
        client, seen = self.build(fx.FakeResponse(payload=fx.WM_SEARCH_MOVIE))
        titles = client.find_title(tmdb_id=550, tmdb_type="movie")
        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].tmdb_id, 550)
        self.assertIn("search_field=tmdb_movie_id", seen[0])
        self.assertIn("search_value=550", seen[0])

    def test_empty_search_is_a_successful_empty_list(self):
        client, _ = self.build(fx.FakeResponse(payload=fx.WM_SEARCH_EMPTY))
        self.assertEqual(client.find_title(tmdb_id=1), [])

    def test_multi_region_sources_in_one_request(self):
        client, seen = self.build(
            fx.FakeResponse(payload=fx.WM_SOURCES_MULTI))
        rows = client.title_sources(1132806, ["NL", "CA", "JP"])
        self.assertEqual(len(rows), len(fx.WM_SOURCES_MULTI))
        self.assertEqual(
            {r.region for r in rows}, {"NL", "CA", "JP"},
            "one request must return every requested region")
        self.assertIn("regions=CA,JP,NL", seen[0])

    def test_unsupported_region_is_explicit_not_empty(self):
        client, _ = self.build(fx.FakeResponse(status_code=400,
                                                payload=fx.WM_ERROR_REGION))
        with self.assertRaises(WatchmodeRegionUnsupported) as ctx:
            client.title_sources(1, ["US"])
        self.assertIn("US", str(ctx.exception))

    def test_quota_response_raises_quota_error(self):
        client, _ = self.build(fx.FakeResponse(status_code=429,
                                                payload=fx.WM_ERROR_QUOTA))
        with self.assertRaises(WatchmodeQuotaExceeded):
            client.title_sources(1, ["NL"])

    def test_live_plan_quota_429_is_terminal_and_keeps_the_evidence(self):
        """The measured exhausted-plan answer must not read as a rate window.

        Live 2026-10-21: HTTP 429, body
        ``{"errorMessage": "Over plan quota on this API Key."}``,
        ``Retry-After: 60`` and ``x-ratelimit-remaining: 0``. The ``Retry-After``
        is misleading (a retry 70s later returned the same answer), so the body
        has to decide the classification — and the ``errorMessage`` must survive
        into the exception instead of collapsing to "429".
        """
        client, _ = self.build(fx.FakeResponse(
            status_code=429,
            payload=fx.WM_ERROR_PLAN_QUOTA,
            headers=fx.WM_PLAN_QUOTA_HEADERS,
        ))
        with self.assertRaises(WatchmodeQuotaExceeded) as ctx:
            client.title_sources(1, ["NL"])
        exc = ctx.exception
        self.assertEqual(exc.kind, "plan_quota")
        self.assertTrue(exc.terminal)
        self.assertEqual(exc.status_code, 429)
        self.assertEqual(exc.retry_after, 60)
        self.assertIn("Over plan quota on this API Key.", exc.detail)
        self.assertEqual(exc.quota["x-ratelimit-limit"], "120")
        self.assertEqual(exc.quota["x-ratelimit-remaining"], "0")
        self.assertEqual(exc.quota["retry-after"], "60")

    def test_rate_limit_429_stays_retryable(self):
        """A real short window keeps ``terminal`` false so work is not failed."""
        client, _ = self.build(fx.FakeResponse(
            status_code=429,
            payload=fx.WM_ERROR_RATE_LIMIT,
            headers=fx.WM_RATE_LIMIT_HEADERS,
        ))
        with self.assertRaises(WatchmodeQuotaExceeded) as ctx:
            client.title_sources(1, ["NL"])
        exc = ctx.exception
        self.assertEqual(exc.kind, "rate_limit")
        self.assertFalse(exc.terminal)
        self.assertEqual(exc.retry_after, 30)
        self.assertIn("Rate limit exceeded", exc.detail)

    def test_exhausted_account_headers_classify_as_plan_quota(self):
        """Account headers win over a body that carries no marker."""
        client, _ = self.build(fx.FakeResponse(
            status_code=429,
            payload={"success": False},
            headers=fx.WM_ACCOUNT_EXHAUSTED_HEADERS,
        ))
        with self.assertRaises(WatchmodeQuotaExceeded) as ctx:
            client.title_sources(1, ["NL"])
        self.assertEqual(ctx.exception.kind, "plan_quota")
        self.assertTrue(ctx.exception.terminal)

    def test_unclassified_429_is_not_assumed_terminal(self):
        """Without evidence of a spent plan the refusal stays retryable."""
        client, _ = self.build(fx.FakeResponse(
            status_code=429,
            payload={"success": False},
            headers={"retry-after": "10"},
        ))
        with self.assertRaises(WatchmodeQuotaExceeded) as ctx:
            client.title_sources(1, ["NL"])
        self.assertEqual(ctx.exception.kind, "unknown")
        self.assertFalse(ctx.exception.terminal)

    def test_429_is_not_retried_inside_the_client(self):
        """One refusal is enough: retrying a spent budget only hides the fact."""
        client, seen = self.build(fx.FakeResponse(
            status_code=429,
            payload=fx.WM_ERROR_PLAN_QUOTA,
            headers=fx.WM_PLAN_QUOTA_HEADERS,
        ))
        with self.assertRaises(WatchmodeQuotaExceeded):
            client.title_sources(1, ["NL"])
        self.assertEqual(len(seen), 1)

    def test_401_is_an_auth_error_not_retried(self):
        client, seen = self.build(fx.FakeResponse(status_code=401, payload={}))
        from availability.sources.watchmode_client import WatchmodeAuthError
        with self.assertRaises(WatchmodeAuthError):
            client.title_sources(1, ["NL"])
        self.assertEqual(len(seen), 1)

    def test_5xx_is_retried(self):
        client, seen = self.build(
            fx.FakeResponse(status_code=503),
            fx.FakeResponse(status_code=503),
            fx.FakeResponse(status_code=503),
        )
        with self.assertRaises(WatchmodeTransportError := __import__(
                "availability.sources.watchmode_client", fromlist=["x"]
        ).WatchmodeTransportError):
            client.title_sources(1, ["NL"])
        self.assertEqual(len(seen), 3)

    def test_malformed_response(self):
        client, _ = self.build(fx.FakeResponse(content_type="text/html",
                                                text="<html>maintenance</html>"))
        with self.assertRaises(WatchmodeResponseError):
            client.title_sources(1, ["NL"])

    def test_quota_headers_are_recorded(self):
        client, _ = self.build(fx.FakeResponse(payload=fx.WM_SOURCES_MULTI,
                                                headers=fx.WM_QUOTA_HEADERS))
        client.title_sources(1, ["NL"])
        self.assertEqual(client.last_quota["x-account-quota"], "2500")
        self.assertEqual(client.last_quota["x-ratelimit-remaining"], "986")

    def test_missing_api_key_is_an_auth_error(self):
        client = WatchmodeClient(
            http_get=lambda *a, **k: fx.FakeResponse(),
            api_key="", sleeper=sleeper, budget=lambda: None)
        client.api_key = ""
        from availability.sources.watchmode_client import WatchmodeAuthError
        with self.assertRaises(WatchmodeAuthError):
            client.title_sources(1, ["NL"])


class WatchmodeSourceTests(SimpleTestCase):
    def build(self, *responses):
        queue = list(responses)
        seen = []

        def http_get(url, headers=None, timeout=None):
            seen.append(url)
            return queue.pop(0) if queue else fx.FakeResponse()

        client = WatchmodeClient(
            http_get=http_get, api_key="k", sleeper=sleeper,
            budget=lambda: None)
        return WatchmodeSource(client=client), seen

    def test_registered_as_aggregator_not_catalogue(self):
        source = source_registry.get("watchmode")
        self.assertIsInstance(source, WatchmodeSource)
        self.assertEqual(source.source_type, SourceType.AGGREGATOR)
        self.assertFalse(source.capabilities.supports_catalogue_sync)
        self.assertFalse(source.capabilities.supports_authoritative_snapshots)

    def test_supported_regions_are_plan_scoped(self):
        caps = source_registry.get("watchmode").capabilities
        self.assertEqual(caps.supported_countries, frozenset({"NL", "CA", "JP"}))

    def test_requires_a_content_request(self):
        source, _ = self.build()
        with self.assertRaises(ValueError):
            source.fetch(None, country="NL")

    def test_offer_types_are_mapped_from_source_type(self):
        source, _ = self.build(
            fx.FakeResponse(payload=fx.WM_SEARCH_MOVIE),
            fx.FakeResponse(payload=fx.WM_SOURCES_MULTI),
        )
        result = source.fetch(ContentRequest(tmdb_id=550, title="Fight Club"),
                              country="NL")
        by_type = {o.offer_type for o in result.offers}
        self.assertIn(OfferType.SUBSCRIPTION, by_type)
        self.assertIn(OfferType.BUY, by_type)
        self.assertIn(OfferType.FREE, by_type)

    def test_unknown_provider_is_skipped(self):
        source, _ = self.build(
            fx.FakeResponse(payload=fx.WM_SEARCH_MOVIE),
            fx.FakeResponse(payload=fx.WM_SOURCES_NL),
        )
        result = source.fetch(ContentRequest(tmdb_id=550), country="NL")
        self.assertNotIn("mystery stream",
                         {o.provider_id for o in result.offers})
        self.assertTrue(result.offers, "known rows still persist")

    def test_unsupported_country_raises(self):
        source, _ = self.build()
        with self.assertRaises(WatchmodeRegionUnsupported):
            source.fetch(ContentRequest(tmdb_id=550), country="US")

    def test_multi_region_offer(self):
        source, _ = self.build(
            fx.FakeResponse(payload=fx.WM_SEARCH_MOVIE),
            fx.FakeResponse(payload=fx.WM_SOURCES_MULTI),
        )
        result = source.fetch(ContentRequest(tmdb_id=550))
        countries = {o.country for o in result.offers}
        self.assertEqual(countries, {"NL", "CA", "JP"})

    def test_api_failure_raises_not_empty(self):
        source, _ = self.build(
            fx.FakeResponse(status_code=500),
            fx.FakeResponse(status_code=500),
            fx.FakeResponse(status_code=500),
        )
        with self.assertRaises(SourceFetchError):
            source.fetch(ContentRequest(tmdb_id=550), country="NL")

    def test_plan_quota_becomes_a_typed_source_quota_error(self):
        """The adapter must preserve kind/retry_after/quota, not flatten them.

        Wrapping the transport refusal in a generic ``SourceFetchError`` is what
        previously made the scheduler retry a spent monthly plan three times and
        then record it as a failed source.
        """
        source, _ = self.build(fx.FakeResponse(
            status_code=429,
            payload=fx.WM_ERROR_PLAN_QUOTA,
            headers=fx.WM_PLAN_QUOTA_HEADERS,
        ))
        with self.assertRaises(SourceQuotaExceeded) as ctx:
            source.fetch(ContentRequest(tmdb_id=550), country="NL")
        exc = ctx.exception
        self.assertEqual(exc.kind, "plan_quota")
        self.assertTrue(exc.terminal)
        self.assertEqual(exc.status_code, 429)
        self.assertEqual(exc.retry_after, 60)
        self.assertEqual(exc.quota["x-ratelimit-remaining"], "0")
        self.assertIn("Over plan quota", exc.detail)

    def test_plan_quota_on_the_sources_call_is_also_typed(self):
        """The second upstream call must classify exactly like the first."""
        source, _ = self.build(
            fx.FakeResponse(payload=fx.WM_SEARCH_MOVIE),
            fx.FakeResponse(
                status_code=429,
                payload=fx.WM_ERROR_PLAN_QUOTA,
                headers=fx.WM_PLAN_QUOTA_HEADERS,
            ),
        )
        with self.assertRaises(SourceQuotaExceeded) as ctx:
            source.fetch(ContentRequest(tmdb_id=550), country="NL")
        self.assertEqual(ctx.exception.kind, "plan_quota")
        self.assertTrue(ctx.exception.terminal)

    def test_rate_limit_becomes_a_retryable_source_quota_error(self):
        source, _ = self.build(fx.FakeResponse(
            status_code=429,
            payload=fx.WM_ERROR_RATE_LIMIT,
            headers=fx.WM_RATE_LIMIT_HEADERS,
        ))
        with self.assertRaises(SourceQuotaExceeded) as ctx:
            source.fetch(ContentRequest(tmdb_id=550), country="NL")
        self.assertEqual(ctx.exception.kind, "rate_limit")
        self.assertFalse(ctx.exception.terminal)
        self.assertEqual(ctx.exception.retry_after, 30)

    def test_diagnostic_is_stable_and_credential_free(self):
        source, _ = self.build(fx.FakeResponse(
            status_code=429,
            payload=fx.WM_ERROR_PLAN_QUOTA,
            headers=fx.WM_PLAN_QUOTA_HEADERS,
        ))
        with self.assertRaises(SourceQuotaExceeded) as ctx:
            source.fetch(ContentRequest(tmdb_id=550), country="NL")
        diagnostic = ctx.exception.diagnostic()
        self.assertIn("kind=plan_quota", diagnostic)
        self.assertIn("status=429", diagnostic)
        self.assertIn("retry_after=60", diagnostic)
        self.assertIn("Over plan quota", diagnostic)
        self.assertNotIn("test-key", diagnostic)


# =============================================================================
# Persistence + lifecycle
# =============================================================================
class AggregatorPipelineTests(TestCase):
    """End-to-end: source -> ProviderContent -> ContentMapping -> Offer."""

    @classmethod
    def setUpTestData(cls):
        # These providers are seeded by migrations on a fresh test database,
        # so create-or-fetch rather than create: the assertions here are about
        # mapping behaviour, not about owning the provider rows.
        cls.prime, _ = Provider.objects.get_or_create(
            slug="prime", defaults={"name": "Prime Video"})
        cls.netflix, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix"})
        cls.nl, _ = Country.objects.get_or_create(
            code="NL", defaults={"name": "Netherlands"})
        cls.us, _ = Country.objects.get_or_create(
            code="US", defaults={"name": "United States"})

    def source(self, *responses):
        queue = list(responses)

        def http_post(url, json=None, headers=None, timeout=None):
            return queue.pop(0) if queue else fx.FakeResponse()

        client = JustWatchClient(http_post=http_post, sleeper=sleeper)
        return JustWatchSource(client=client)

    def test_country_specific_mapping_resolves_one_provider(self):
        mapping(self.prime, "justwatch", "amazonprimevideo", "NL")
        mapping(self.prime, "justwatch", "amazonprime", "US")

        def resolve(country, external_id):
            service = AvailabilityRefreshService(source=self.source())
            return service._provider(external_id, country=country)

        self.assertEqual(resolve("NL", "amazonprimevideo").slug, "prime")
        self.assertEqual(resolve("US", "amazonprime").slug, "prime")
        # Same provider row both times - no duplicate Provider is created.
        self.assertEqual(Provider.objects.filter(slug="prime").count(), 1)

    def test_alias_spelling_resolves_when_the_market_slot_holds_its_sibling(self):
        """One row per (provider, integration, country) must not lose a name.

        ``netflix``/``netflixkids`` and ``viaplay``/``viaplaysports`` share one
        market slot. Whichever name the sync wrote last, *both* spellings must
        resolve to the canonical provider — even while that provider is
        disabled, because mapped-but-inactive offers are kept and annotated
        rather than dropped.
        """
        viaplay, _ = Provider.objects.get_or_create(
            slug="viaplay", defaults={"name": "Viaplay"})
        viaplay.is_enabled = False
        viaplay.save(update_fields=["is_enabled"])
        mapping(viaplay, "justwatch", "viaplaysports", "NL")

        service = AvailabilityRefreshService(source=self.source())
        self.assertEqual(service._provider("viaplaysports", country="NL").slug, "viaplay")
        self.assertEqual(service._provider("viaplay", country="NL").slug, "viaplay")
        # An unmapped id is still refused rather than guessed.
        self.assertIsNone(service._provider("amazonhbomax", country="NL"))

    def test_unscoped_mapping_still_resolves_for_existing_integrations(self):
        """A collector's country='' row must keep working after the change."""
        mapping(self.prime, "streaming_availability", "prime")
        service = AvailabilityRefreshService(
            source=source_registry.get("streaming_availability"))
        self.assertEqual(service._provider("prime", country="NL").slug, "prime")
        self.assertEqual(service._provider("prime", country="US").slug, "prime")

    def test_successful_catalogue_persists_content_and_offers(self):
        mapping(self.netflix, "justwatch", "netflix", "NL")
        source = self.source(fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX))
        service = AvailabilityRefreshService(source=source)
        result = service.refresh_catalogue(provider="netflix", country="NL")

        self.assertGreater(result.created, 0)
        self.assertTrue(
            ProviderContent.objects.filter(
                provider=self.netflix, provider_content_id="tm1303572").exists())
        # An exact external-id mapping is persisted too, so the provenance
        # chain ProviderContent -> ContentMapping -> Offer holds for sources
        # that already know the TMDb id (the consistency goal).
        recorded = ContentMapping.objects.get(
            provider=self.netflix, provider_content_id="tm1303572")
        self.assertEqual(recorded.tmdb_id, 1061474)
        self.assertEqual(recorded.match_method, "external_id")
        self.assertEqual(recorded.source.slug, "justwatch")
        self.assertEqual(recorded.content.provider_content_id, "tm1303572")

        offer = Offer.objects.get(provider=self.netflix, tmdb_id=1061474,
                                  country=self.nl)
        self.assertEqual(offer.source.slug, "justwatch")
        self.assertEqual(offer.offer_type, OfferType.SUBSCRIPTION)
        self.assertEqual(offer.provider_content_id, "tm1303572")

    def test_catalogue_content_survives_without_a_mapping_row(self):
        """Provider-native discovery is kept even when nothing needs mapping."""
        mapping(self.netflix, "justwatch", "netflix", "NL")
        source = self.source(fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX))
        result = AvailabilityRefreshService(source=source).refresh_catalogue(
            provider="netflix", country="NL")
        self.assertEqual(result.unresolved, 0)
        row = ProviderContent.objects.get(provider=self.netflix,
                                           provider_content_id="tm2873958")
        self.assertEqual(row.content_type, ContentType.MOVIE)
        self.assertEqual(row.title, "The Whisper Man")
        self.assertEqual(row.metadata["external_ids"]["tmdb"], "860508")

    def test_quality_and_languages_are_normalized(self):
        mapping(self.prime, "justwatch", "amazonprimevideo", "NL")
        source = self.source(fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX))
        AvailabilityRefreshService(source=source).refresh_catalogue(
            provider=None, country="NL")
        offer = Offer.objects.get(provider=self.prime, tmdb_id=1061474,
                                  offer_type=OfferType.SUBSCRIPTION)
        self.assertEqual(offer.quality, "4k", "_4K must map to the shared vocabulary")
        self.assertEqual(offer.audio_languages, ["EN", "NL"])
        self.assertEqual(offer.subtitle_languages, ["NL", "EN"])
        self.assertEqual(offer.provider_url,
                         "https://example.test/o2", "deeplink is preserved")

    def test_justwatch_empty_does_not_deactivate_a_collector_offer(self):
        collector_offer = Offer.objects.create(
            provider=self.prime, country=self.nl,
            source=Source.objects.create(slug="prime_video", name="Prime",
                                         type="web"),
            content_type=ContentType.MOVIE, tmdb_id=550,
            offer_type=OfferType.SUBSCRIPTION)
        source = self.source(fx.FakeResponse(payload=fx.JW_CATALOGUE_EMPTY))
        AvailabilityRefreshService(source=source).refresh_catalogue(
            provider="netflix", country="NL")

        collector_offer.refresh_from_db()
        self.assertTrue(collector_offer.currently_available,
                        "JustWatch reporting nothing must NOT remove a "
                        "first-party collector's offer")

    def test_watchmode_empty_does_not_deactivate_a_collector_offer(self):
        collector_offer = Offer.objects.create(
            provider=self.prime, country=self.nl,
            source=Source.objects.create(slug="prime_video", name="Prime",
                                         type="web"),
            content_type=ContentType.MOVIE, tmdb_id=550,
            offer_type=OfferType.SUBSCRIPTION)

        def http_get(url, headers=None, timeout=None):
            if "/search/" in url:
                return fx.FakeResponse(payload=fx.WM_SEARCH_EMPTY)
            return fx.FakeResponse(payload=[])

        source = WatchmodeSource(client=WatchmodeClient(
            http_get=http_get, api_key="k", sleeper=sleeper, budget=lambda: None))
        AvailabilityRefreshService(source=source).refresh_content(
            _movie(550))

        collector_offer.refresh_from_db()
        self.assertTrue(collector_offer.currently_available,
                        "Watchmode reporting nothing must NOT remove a "
                        "first-party collector's offer")

    def test_failed_run_creates_no_offer_and_no_deactivation(self):
        collector_offer = Offer.objects.create(
            provider=self.prime, country=self.nl,
            source=Source.objects.create(slug="prime_video", name="Prime",
                                         type="web"),
            content_type=ContentType.MOVIE, tmdb_id=550,
            offer_type=OfferType.SUBSCRIPTION)
        source = self.source(
            fx.FakeResponse(status_code=500), fx.FakeResponse(status_code=500),
            fx.FakeResponse(status_code=500))
        with self.assertRaises(SourceFetchError):
            AvailabilityRefreshService(source=source).refresh_catalogue(
                provider="netflix", country="NL")

        collector_offer.refresh_from_db()
        self.assertTrue(collector_offer.currently_available)
        self.assertFalse(
            SyncRun.objects.filter(source__slug="justwatch",
                                   status="succeeded").exists(),
            "a failed fetch must not be recorded as a successful run")

    def test_partial_catalogue_run_is_not_authoritative(self):
        source = self.source(fx.FakeResponse(payload=fx.JW_CATALOGUE_NL_NETFLIX))
        AvailabilityRefreshService(source=source).refresh_catalogue(
            provider="netflix", country="NL")
        run = SyncRun.objects.filter(source__slug="justwatch").first()
        self.assertIsNotNone(run)
        self.assertFalse(run.authoritative,
                         "an aggregator run may never deactivate unseen offers")

    def test_source_rows_are_created_as_aggregators(self):
        self.source(fx.FakeResponse(payload=fx.JW_CATALOGUE_EMPTY))
        AvailabilityRefreshService(source=self.source(
            fx.FakeResponse(payload=fx.JW_CATALOGUE_EMPTY))).refresh_catalogue(
            provider="netflix", country="NL")
        row = Source.objects.get(slug="justwatch")
        self.assertEqual(row.type, SourceType.AGGREGATOR)

    def test_watchmode_persists_and_is_readable(self):
        mapping(self.netflix, "watchmode", "wm:203:movie:tmdb-550")

        def http_get(url, headers=None, timeout=None):
            if "/search/" in url:
                return fx.FakeResponse(payload=fx.WM_SEARCH_MOVIE)
            return fx.FakeResponse(payload=fx.WM_SOURCES_NL)

        source = WatchmodeSource(client=WatchmodeClient(
            http_get=http_get, api_key="k", sleeper=sleeper, budget=lambda: None))
        AvailabilityRefreshService(source=source).refresh_content(_movie(550))

        offer = Offer.objects.get(provider=self.netflix, tmdb_id=550,
                                  country=self.nl)
        self.assertEqual(offer.source.slug, "watchmode")
        self.assertEqual(offer.offer_type, OfferType.SUBSCRIPTION)


class JustWatchCommandFilterTests(SimpleTestCase):
    """Regression: the command must filter the plan BEFORE capping it.

    ``available_partitions`` is country-first alphabetical, so
    ``--max-partitions 1 --country NL --provider netflix`` used to truncate the
    plan to ``AU/abc_iview`` and then report "no partitions match" for a request
    it should have run.
    """

    def test_cap_does_not_hide_the_requested_partition(self):
        from tracker.management.commands.refresh_justwatch_catalogue import (
            Command as JWCommand,
        )
        from unittest.mock import patch as mock_patch

        # Force the global plan to be dominated by other markets.
        plan = [("AU", "abc_iview"), ("AU", "apple"), ("NL", "netflix")]
        captured = {}

        def fake_summary(**kwargs):
            captured.update(kwargs)
            return {"status": "succeeded", "attempted": 1, "refreshed": 1,
                    "errors": 0, "partial": 0, "skipped": 0,
                    "succeeded_partitions": ["NL/netflix"],
                    "failed_partitions": [], "skipped_partitions": [],
                    "results": [], "plan": [["NL", "netflix"]]}

        cmd = JWCommand()
        with mock_patch(
            "tracker.management.commands.refresh_justwatch_catalogue."
            "available_partitions", return_value=plan
        ), mock_patch(
            "tracker.management.commands.refresh_justwatch_catalogue."
            "refresh_justwatch_partitions", side_effect=fake_summary
        ):
            cmd.handle(country="NL", provider="netflix", max_partitions=1,
                       list_only=False)

        self.assertEqual(captured["partitions"], [("NL", "netflix")],
                         "the filtered plan must reach the coordinator intact")
        self.assertIsNone(captured["max_partitions"],
                          "an already-capped list must not be capped again")

    def test_no_filters_runs_the_global_plan(self):
        from tracker.management.commands.refresh_justwatch_catalogue import (
            Command as JWCommand,
        )
        from unittest.mock import patch as mock_patch

        plan = [("NL", "netflix"), ("NL", "prime")]
        captured = {}

        cmd = JWCommand()
        with mock_patch(
            "tracker.management.commands.refresh_justwatch_catalogue."
            "available_partitions", return_value=plan
        ), mock_patch(
            "tracker.management.commands.refresh_justwatch_catalogue."
            "refresh_justwatch_partitions",
            side_effect=lambda **kw: (captured.update(kw) or
                                      {"status": "succeeded", "attempted": 2,
                                       "refreshed": 2, "errors": 0,
                                       "partial": 0, "skipped": 0,
                                       "succeeded_partitions": [], "failed_partitions": [],
                                       "skipped_partitions": [], "results": [],
                                       "plan": []})
        ):
            cmd.handle(country=None, provider=None, max_partitions=None,
                       list_only=False)
        self.assertEqual(captured["partitions"], plan)

    def test_list_only_matches_execution_plan(self):
        from tracker.management.commands.refresh_justwatch_catalogue import (
            Command as JWCommand,
        )
        from unittest.mock import patch as mock_patch

        plan = [("AU", "abc_iview"), ("NL", "netflix"), ("NL", "prime")]
        shown = {}

        cmd = JWCommand()
        with mock_patch(
            "tracker.management.commands.refresh_justwatch_catalogue."
            "available_partitions", return_value=plan
        ):
            with mock_patch.object(cmd, "stdout") as out:
                cmd.handle(country="NL", provider=None, max_partitions=1,
                           list_only=True)
                shown["text"] = out.write.call_args[0][0]

        text = shown["text"]
        self.assertIn('"netflix"', text)
        self.assertNotIn('"abc_iview"', text,
                         "the country filter is applied before anything is shown")
        self.assertNotIn('"prime"', text,
                         "--max-partitions caps the shared plan, so the preview "
                         "shows exactly the partitions that will run")
        self.assertIn("1 partitions", text)


class SyncStatusSemanticsTests(TestCase):
    """SUCCESS / PARTIAL / FAILURE must stay distinguishable."""

    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="partial_probe", defaults={"name": "Partial Probe"})
        self.country, _ = Country.objects.get_or_create(
            code="ZZ", defaults={"name": "Testland"})
        self.source, _ = Source.objects.get_or_create(
            slug="partial_src", defaults={"name": "Partial", "type": "api"})
        self.repository = SyncRunRepository()

    def _run(self):
        return self.repository.start(provider=self.provider, source=self.source,
                                     country=self.country, mode=SyncMode.FULL)

    def test_partial_is_its_own_status(self):
        from availability.domain.enums import SyncStatus

        self.assertNotEqual(SyncStatus.PARTIAL, SyncStatus.SUCCEEDED)
        self.assertNotEqual(SyncStatus.PARTIAL, SyncStatus.FAILED)
        run = self.repository.mark_partial(self._run(), items_discovered=3)
        run.refresh_from_db()
        self.assertEqual(run.status, SyncStatus.PARTIAL)
        self.assertEqual(run.items_discovered, 3)
        self.assertIsNotNone(run.completed_at)

    def test_partial_run_cannot_deactivate(self):
        from availability.domain.enums import SyncStatus

        offer = Offer.objects.create(
            provider=self.provider, country=self.country, source=self.source,
            content_type=ContentType.MOVIE, tmdb_id=999,
            offer_type=OfferType.SUBSCRIPTION)
        run = self.repository.mark_partial(self._run())
        # The repository refuses outright rather than silently deactivating:
        # a partial run is not a complete observation, full stop.
        with self.assertRaises(InactiveSyncError):
            OfferRepository().deactivate_unseen(run, set())
        offer.refresh_from_db()
        self.assertTrue(offer.currently_available,
                        "a partial observation must never erase evidence")

    def test_successful_authoritative_run_still_deactivates(self):
        from availability.domain.enums import SyncStatus

        offer = Offer.objects.create(
            provider=self.provider, country=self.country, source=self.source,
            content_type=ContentType.MOVIE, tmdb_id=998,
            offer_type=OfferType.SUBSCRIPTION)
        run = self.repository.start(provider=self.provider, source=self.source,
                                     country=self.country, mode=SyncMode.FULL,
                                     authoritative=True)
        run = self.repository.mark_succeeded(run)
        OfferRepository().deactivate_unseen(run, set())
        offer.refresh_from_db()
        self.assertFalse(offer.currently_available)
        self.assertEqual(run.status, SyncStatus.SUCCEEDED)


class JustWatchPartitionTests(TestCase):
    """The explicit country x provider partition coordinator."""

    @staticmethod
    def _seed_planner_inputs(*provider_slugs, country="NL"):
        Country.objects.get_or_create(
            code=country, defaults={"name": "Netherlands", "enabled": True})
        for slug in provider_slugs:
            Provider.objects.get_or_create(
                slug=slug, defaults={"name": slug, "is_enabled": True})

    def test_plan_only_includes_mapped_combinations(self):
        self._seed_planner_inputs("netflix", "prime", "cbc_gem")
        # Cap generously: the planner is country-first alphabetical, and the
        # test database already carries seeded countries, so NL is not near the
        # head of the list.
        plan = available_partitions(max_partitions=500)
        self.assertIn(("NL", "netflix"), plan)
        self.assertIn(("NL", "prime"), plan)
        # cbc_gem has no JustWatch package, so no partition may exist for it.
        self.assertNotIn(("NL", "cbc_gem"), plan)
        # cbc_gem has no JustWatch package, so no partition may exist for it.
        self.assertNotIn(("NL", "cbc_gem"), plan)
        # Every planned pair must be genuinely mapped in that market.
        for country_code, provider_slug in plan:
            self.assertIn(country_code, PACKAGE_SHORT_NAMES[provider_slug])

    def test_plan_is_deterministic_and_capped(self):
        self._seed_planner_inputs("netflix", "prime", "disney", "apple")
        first = available_partitions(max_partitions=3)
        second = available_partitions(max_partitions=3)
        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 3)

    def test_disabled_country_is_not_planned(self):
        Country.objects.get_or_create(code="QQ", defaults={"name": "Off",
                                                           "enabled": False})
        Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "netflix", "is_enabled": True})
        self.assertNotIn(("QQ", "netflix"),
                         available_partitions(max_partitions=99))

    # -- explicit partition lists (regression) ----------------------------
    #
    # The bug: the command computed a filtered plan and then called the
    # coordinator with no plan at all, so the coordinator rebuilt the *global*
    # country-first ordering. `--provider netflix` then vanished whenever
    # Netflix was not inside the first N global partitions.

    @staticmethod
    def _fake_service(outcomes):
        """A refresh service stub whose partitions succeed/fail on demand.

        ``outcomes`` maps ``(country, provider)`` -> ``"ok"`` / ``"fail"``.
        Records every call so a test can assert *which* partitions ran.
        """
        from availability.services.refresh import RefreshResult

        calls = []

        class _Service:
            def refresh_catalogue(self, *, provider=None, country=None, cursor=None):
                calls.append((country, provider, cursor))
                if outcomes.get((country, provider)) == "fail":
                    raise SourceFetchError("stub partition failure")
                return RefreshResult(discovered=1, processed=1, created=1)

        return _Service(), calls

    def _stub_source(self):
        from availability.sources.base import SourceCapabilities

        class _S:
            slug = "justwatch"
            name = "JustWatch"
            source_type = SourceType.AGGREGATOR
            capabilities = SourceCapabilities()
        return _S()

    def test_explicit_partition_list_is_executed(self):
        Country.objects.get_or_create(code="NL", defaults={"name": "NL",
                                                           "enabled": True})
        Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "netflix", "is_enabled": True})
        service, calls = self._fake_service({})
        summary = refresh_justwatch_partitions(
            partitions=[("NL", "netflix")], refresh_service=service,
            source=self._stub_source(),
        )
        self.assertEqual([(c, p) for c, p, _ in calls], [("NL", "netflix")])
        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["status"], "succeeded")

    def test_explicit_provider_survives_global_plan_ordering(self):
        """Netflix is late in the country-first ordering; asking for it by name
        must still run Netflix."""
        self._seed_planner_inputs("netflix", "prime", "disney", "apple",
                                  country="AU")
        service, calls = self._fake_service({})
        # A global plan capped at 1 would only contain AU/apple.
        refresh_justwatch_partitions(max_partitions=1, refresh_service=service,
                                     source=self._stub_source())
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0][1], "netflix")

        calls.clear()
        refresh_justwatch_partitions(partitions=[("AU", "netflix")],
                                     refresh_service=service,
                                     source=self._stub_source())
        self.assertEqual([(c, p) for c, p, _ in calls], [("AU", "netflix")],
                         "an explicit partition must run even when the capped "
                         "global plan would not contain it")

    def test_country_and_provider_filter_executes_exactly_one(self):
        self._seed_planner_inputs("netflix", "prime", "disney")
        service, calls = self._fake_service({})
        summary = refresh_justwatch_partitions(
            partitions=[("NL", "netflix")], max_partitions=1,
            refresh_service=service, source=self._stub_source(),
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "NL")
        self.assertEqual(calls[0][1], "netflix")
        self.assertEqual(summary["succeeded_partitions"], ["NL/netflix"])

    def test_max_partitions_truncates_supplied_plan(self):
        self._seed_planner_inputs("netflix", "prime", "disney")
        service, calls = self._fake_service({})
        refresh_justwatch_partitions(
            partitions=[("NL", "netflix"), ("NL", "prime"), ("NL", "disney")],
            max_partitions=2, refresh_service=service, source=self._stub_source(),
        )
        self.assertEqual([p for _, p, _ in calls], ["netflix", "prime"],
                         "max_partitions truncates the supplied list, it does "
                         "not rebuild the global plan")

    def test_coordinator_max_partitions_none_does_not_recap(self):
        """A caller that already capped its own list must not be capped again."""
        self._seed_planner_inputs("netflix", "prime", "disney")
        service, calls = self._fake_service({})
        supplied = [("NL", "netflix")]
        refresh_justwatch_partitions(partitions=supplied, max_partitions=None,
                                     refresh_service=service,
                                     source=self._stub_source())
        self.assertEqual([(c, p) for c, p, _ in calls], supplied)

    def test_partitions_none_keeps_global_behavior(self):
        self._seed_planner_inputs("netflix", "prime")
        service, calls = self._fake_service({})
        summary = refresh_justwatch_partitions(
            partitions=None, refresh_service=service, source=self._stub_source())
        self.assertTrue(calls)
        self.assertEqual(summary["plan"],
                         [list(p) for p in available_partitions(
                             max_partitions=DEFAULT_MAX_PARTITIONS)])

    def test_disabled_partition_is_skipped_not_failed(self):
        Country.objects.get_or_create(code="NL", defaults={"name": "NL",
                                                           "enabled": True})
        provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "netflix"})
        # Explicitly disable: get_or_create defaults do not apply to a row that
        # a migration already seeded as enabled.
        Provider.objects.filter(pk=provider.pk).update(is_enabled=False)
        service, calls = self._fake_service({})
        summary = refresh_justwatch_partitions(
            partitions=[("NL", "netflix")], refresh_service=service,
            source=self._stub_source(),
        )
        self.assertEqual(calls, [], "a disabled provider must not be fetched")
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(summary["status"], "succeeded")

    # -- aggregate status -------------------------------------------------
    def test_all_partitions_succeed_aggregate_succeeded(self):
        self._seed_planner_inputs("netflix", "prime")
        service, _ = self._fake_service({})
        summary = refresh_justwatch_partitions(
            partitions=[("NL", "netflix"), ("NL", "prime")],
            refresh_service=service, source=self._stub_source())
        self.assertEqual(summary["status"], "succeeded")
        self.assertEqual(summary["refreshed"], 2)
        self.assertEqual(summary["errors"], 0)

    def test_one_failure_is_partial_not_succeeded(self):
        self._seed_planner_inputs("netflix", "prime")
        service, _ = self._fake_service({("NL", "prime"): "fail"})
        summary = refresh_justwatch_partitions(
            partitions=[("NL", "netflix"), ("NL", "prime")],
            refresh_service=service, source=self._stub_source())
        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["failed_partitions"], ["NL/prime"])

    def test_all_fail_aggregate_failed(self):
        self._seed_planner_inputs("netflix", "prime")
        service, _ = self._fake_service({("NL", "netflix"): "fail",
                                         ("NL", "prime"): "fail"})
        summary = refresh_justwatch_partitions(
            partitions=[("NL", "netflix"), ("NL", "prime")],
            refresh_service=service, source=self._stub_source())
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["refreshed"], 0)
        self.assertEqual(summary["errors"], 2)

    def test_failed_partition_does_not_stop_others(self):
        """Failure isolation: a broken partition must not cost a good one."""
        self._seed_planner_inputs("netflix", "prime")
        service, calls = self._fake_service({("NL", "netflix"): "fail"})
        summary = refresh_justwatch_partitions(
            partitions=[("NL", "netflix"), ("NL", "prime")],
            refresh_service=service, source=self._stub_source())
        self.assertEqual(len(calls), 2, "both partitions are attempted")
        self.assertEqual(summary["succeeded_partitions"], ["NL/prime"])

    def test_coordinator_run_deactivates_nothing(self):
        """The safety invariant, end to end through the coordinator."""
        self._seed_planner_inputs("netflix")
        existing = Offer.objects.create(
            provider=Provider.objects.get(slug="netflix"),
            country=Country.objects.get(code="NL"),
            source=Source.objects.create(slug="collector_src", name="Collector",
                                         type="web"),
            content_type=ContentType.MOVIE, tmdb_id=4242,
            offer_type=OfferType.SUBSCRIPTION)
        service, _ = self._fake_service({})
        refresh_justwatch_partitions(partitions=[("NL", "netflix")],
                                     refresh_service=service,
                                     source=self._stub_source())
        existing.refresh_from_db()
        self.assertTrue(existing.currently_available,
                        "a JustWatch partition must never deactivate a "
                        "collector's offer")
        self.assertFalse(
            SyncRun.objects.filter(source__slug="justwatch",
                                   authoritative=True).exists(),
            "an aggregator run is never authoritative")

    def test_failed_partition_leaves_cursor_untouched(self):
        self._seed_planner_inputs("netflix")
        service, _ = self._fake_service({("NL", "netflix"): "fail"})
        refresh_justwatch_partitions(partitions=[("NL", "netflix")],
                                     refresh_service=service,
                                     source=self._stub_source())
        self.assertEqual(SyncCursor.objects.count(), 0,
                         "a failed partition must stay retryable")

    def test_summary_shape_with_no_partitions(self):
        # No enabled country/provider pair exists here, so the coordinator must
        # return an empty, well-formed summary instead of raising.
        summary = refresh_justwatch_partitions(max_partitions=1)
        self.assertIn("results", summary)
        self.assertIn("errors", summary)


def mapping(provider, integration, external_id, country=""):
    """Create a country-aware mapping row, tolerating seeded equivalents.

    Migration 0007 already seeds ``streaming_availability`` rows and 0006 seeds
    ``justwatch`` rows, so a bare ``create`` in a test would collide with them.
    """
    _row, _created = ExternalProviderMapping.objects.get_or_create(
        provider=provider, integration_name=integration, country=country,
        defaults={"external_id": external_id},
    )
    return _row


def _movie(tmdb_id):
    return Movie.objects.create(tmdb_id=tmdb_id, title=f"Title {tmdb_id}",
                                content_type=ContentType.MOVIE)