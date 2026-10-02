"""YouTube through the source interface: registration, targeted semantics,
badge filtering, title verification, failure contracts (fixture API, zero network).

Fixture queries (see ``youtube_fixtures.QUERIES``) cover, one behaviour each:
a free match with free distractors ahead of it, a recorded year-suffixed
title, a videoRenderer free listing, a paid (Buy or rent) listing on the
right title, a no-free result set, a year disagreement, a malformed payload
and an upstream API error.
"""
from __future__ import annotations

import datetime as dt

from django.test import SimpleTestCase, TestCase

from availability.domain.enums import ContentType, OfferType, SourceType, SyncMode
from availability.freshness import declared_freshness
from availability.models import Country, Offer, ProviderContent, Source, SyncRun
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import SourceFetchError, source_registry
from availability.sources.base import ContentRequest
from availability.sources.youtube import (
    CLIENT_VERSION,
    FREE_PROGRAM_COUNTRIES,
    YouTubeClient,
    YouTubeSource,
)
from tracker.models import Movie, Provider

from .youtube_fixtures import FakeYouTubeApi

MATILDA_VIDEO_ID = "_F8kU1vb_rk"


def make_source(api) -> YouTubeSource:
    client = YouTubeClient(http_get=api.get, http_post=api.post)
    return YouTubeSource(client=client)


def movie_request(**overrides) -> ContentRequest:
    base = {"tmdb_id": 460, "content_type": "movie", "title": "Matilda", "year": 1996}
    base.update(overrides)
    return ContentRequest(**base)


class RegistrationTests(SimpleTestCase):
    def test_youtube_source_is_registered(self):
        source = source_registry.get("youtube")
        self.assertIsInstance(source, YouTubeSource)
        self.assertEqual(source.slug, "youtube")
        self.assertEqual(source.source_type, SourceType.WEB)

    def test_capabilities_declare_targeted_movie_scoped_source(self):
        caps = YouTubeSource.capabilities
        self.assertEqual(caps.supported_providers, frozenset({"youtube"}))
        self.assertEqual(caps.supported_countries, frozenset(FREE_PROGRAM_COUNTRIES))
        self.assertEqual(caps.supported_content_types, frozenset({ContentType.MOVIE}))
        self.assertFalse(caps.supports_catalogue_sync)
        self.assertFalse(caps.supports_incremental_sync)
        self.assertFalse(caps.supports_authoritative_snapshots)
        self.assertFalse(caps.supports_tmdb_ids)
        self.assertFalse(caps.supports_seasons)
        self.assertFalse(caps.supports_episodes)
        self.assertFalse(caps.supports_languages)

    def test_freshness_policy_is_declared_for_the_registered_slug(self):
        self.assertEqual(declared_freshness("youtube"), dt.timedelta(hours=24))

    def test_catalogue_fetch_is_rejected(self):
        with self.assertRaises(ValueError):
            make_source(FakeYouTubeApi()).fetch(None)

    def test_series_request_returns_empty_without_any_network(self):
        api = FakeYouTubeApi()
        result = make_source(api).fetch(
            ContentRequest(tmdb_id=1, content_type="series", title="Some Show")
        )
        self.assertEqual(result.offers, [])
        self.assertEqual(result.contents, [])
        self.assertEqual(api.requests, [])


class RegionTests(SimpleTestCase):
    def test_region_is_detected_from_the_homepage_and_cached(self):
        api = FakeYouTubeApi()
        source = make_source(api)
        source.fetch(movie_request())
        source.fetch(movie_request(tmdb_id=11))
        homepage_probes = api.requests_matching("GET https://www.youtube.com/")
        self.assertEqual(len(homepage_probes), 1)
        searches = api.requests_matching("POST")
        self.assertEqual(len(searches), 2)

    def test_region_probe_parses_the_ytcfg_gl_marker(self):
        api = FakeYouTubeApi()
        client = YouTubeClient(http_get=api.get, http_post=api.post)
        self.assertEqual(client.region(), "US")

    def test_region_outside_the_program_returns_empty_without_searching(self):
        api = FakeYouTubeApi()
        api.homepage = api.homepage.replace('"GL":"US"', '"GL":"NL"').replace(
            '"countryCode":"US"', '"countryCode":"NL"'
        )
        result = make_source(api).fetch(movie_request())
        self.assertEqual(result.offers, [])
        self.assertEqual(result.contents, [])
        self.assertEqual(api.requests_matching("POST"), [])
        self.assertEqual(len(api.requests_matching("GET")), 1)

    def test_unparsable_homepage_fails_loudly(self):
        api = FakeYouTubeApi(homepage="<html><body>no ytcfg</body></html>")
        with self.assertRaises(SourceFetchError):
            make_source(api).fetch(movie_request())

    def test_explicit_region_skips_the_homepage_probe(self):
        api = FakeYouTubeApi()
        client = YouTubeClient(region="GB", http_get=api.get, http_post=api.post)
        self.assertEqual(YouTubeSource(client=client).fetch(movie_request()).offers[0].country, "GB")
        self.assertEqual(api.requests_matching("GET"), [])


class SearchParsingTests(SimpleTestCase):
    def setUp(self):
        self.api = FakeYouTubeApi()
        self.source = make_source(self.api)

    def test_free_match_becomes_one_movie_offer(self):
        result = self.source.fetch(movie_request())
        self.assertEqual(len(result.offers), 1)
        offer = result.offers[0]
        self.assertEqual(offer.provider_id, "youtube")
        self.assertEqual(offer.provider_content_id, MATILDA_VIDEO_ID)
        self.assertEqual(offer.country, "US")
        self.assertEqual(offer.content_type, ContentType.MOVIE)
        self.assertEqual(offer.tmdb_id, 460)
        self.assertEqual(offer.season_number, None)
        self.assertEqual(offer.episode_number, None)
        self.assertEqual(offer.offer_type, OfferType.FREE_WITH_ADS)
        self.assertEqual(offer.provider_url, f"https://www.youtube.com/watch?v={MATILDA_VIDEO_ID}")
        self.assertEqual(offer.quality, "")
        self.assertIsNone(offer.audio_languages)
        self.assertIsNone(offer.subtitle_languages)
        self.assertIsNone(offer.available_from)
        self.assertIsNone(offer.available_until)
        self.assertEqual(offer.source_record_id, MATILDA_VIDEO_ID)
        self.assertEqual(offer.source_version, CLIENT_VERSION)

    def test_matched_entry_is_carried_as_discovered_content(self):
        result = self.source.fetch(movie_request())
        self.assertEqual(len(result.contents), 1)
        content = result.contents[0]
        self.assertEqual(content.provider_id, "youtube")
        self.assertEqual(content.provider_content_id, MATILDA_VIDEO_ID)
        self.assertEqual(content.content_type, ContentType.MOVIE)
        self.assertEqual(content.title, "Matilda")
        self.assertEqual(content.url, f"https://www.youtube.com/watch?v={MATILDA_VIDEO_ID}")
        self.assertEqual(content.external_ids, {"tmdb": "460"})
        self.assertEqual(content.source_id, "youtube")

    def test_result_never_claims_authoritativeness_or_cursor(self):
        result = self.source.fetch(movie_request())
        self.assertFalse(result.authoritative)
        self.assertFalse(result.complete_snapshot)
        self.assertEqual(result.sync_mode, SyncMode.TARGETED)
        self.assertIsNone(result.cursor)

    def test_free_distractors_do_not_shadow_the_matching_title(self):
        # Fixture order is free Cloudy -> free Grease -> free Matilda.
        result = self.source.fetch(movie_request())
        self.assertEqual(result.offers[0].provider_content_id, MATILDA_VIDEO_ID)

    def test_recorded_year_suffixed_title_matches_after_stripping(self):
        result = self.source.fetch(movie_request(title="The Longest Yard", year=2005))
        self.assertEqual(len(result.offers), 1)
        self.assertEqual(result.offers[0].provider_content_id, "uGJv_zSRiR0")

    def test_free_video_renderer_listing_is_accepted(self):
        result = self.source.fetch(movie_request(title="Mean Girls", year=2004))
        self.assertEqual(len(result.offers), 1)
        self.assertEqual(result.offers[0].provider_content_id, "HPkDFc8hq5c")

    def test_paid_badge_on_the_matching_title_is_never_persisted(self):
        # "Inception" is on YouTube, but as Buy-or-rent: excluded by design.
        result = self.source.fetch(movie_request(title="Inception", year=2010))
        self.assertEqual(result.offers, [])
        self.assertEqual(result.contents, [])

    def test_no_free_badge_for_the_requested_title_is_an_empty_result(self):
        result = self.source.fetch(
            movie_request(title="The Shawshank Redemption", year=1994)
        )
        self.assertEqual(result.offers, [])
        self.assertEqual(result.contents, [])
        # The search still happened: the source observed the marketplace.
        self.assertEqual(len(self.api.requests_matching("POST")), 1)

    def test_candidate_year_must_agree_with_the_request(self):
        # Fixture answers "Grease 1990" with a free "Grease (2023)".
        result = self.source.fetch(movie_request(title="Grease", year=1990))
        self.assertEqual(result.offers, [])

    def test_missing_title_returns_empty_without_network(self):
        result = self.source.fetch(movie_request(title="   "))
        self.assertEqual(result.offers, [])
        self.assertEqual(self.api.requests, [])


class FailureContractTests(SimpleTestCase):
    def setUp(self):
        self.api = FakeYouTubeApi()
        self.source = make_source(self.api)

    def test_homepage_transport_failure_is_not_an_empty_result(self):
        self.api.failures["youtube.com/"] = SourceFetchError("simulated outage")
        with self.assertRaises(SourceFetchError):
            self.source.fetch(movie_request())

    def test_search_transport_failure_is_not_an_empty_result(self):
        self.api.failures["Matilda 1996"] = SourceFetchError("simulated outage")
        with self.assertRaises(SourceFetchError):
            self.source.fetch(movie_request())

    def test_malformed_search_payload_is_rejected(self):
        with self.assertRaises(SourceFetchError):
            self.source.fetch(movie_request(title="Broken Payload", year=2020))

    def test_upstream_api_error_is_rejected(self):
        with self.assertRaises(SourceFetchError) as ctx:
            self.source.fetch(movie_request(title="Upstream Error", year=2021))
        self.assertIn("API error", str(ctx.exception))

    def test_unknown_query_fails_loudly_in_tests(self):
        with self.assertRaises(SourceFetchError):
            self.source.fetch(movie_request(title="Not Provisioned", year=1985))


class RefreshPathTests(TestCase):
    """End-to-end targeted refresh: fixture API -> Offer/ProviderContent rows."""

    def setUp(self):
        self.api = FakeYouTubeApi()
        self.source = make_source(self.api)
        self.service = AvailabilityRefreshService(source=self.source)
        self.provider, _ = Provider.objects.get_or_create(
            slug="youtube", defaults={"name": "YouTube", "is_enabled": True}
        )
        Country.objects.get_or_create(code="US", defaults={"name": "United States"})
        self.movie = Movie.objects.create(
            tmdb_id=460, title="Matilda", year=1996, content_type="movie"
        )

    def test_refresh_content_persists_the_offer_and_a_targeted_sync_run(self):
        result = self.service.refresh_content(self.movie)

        self.assertEqual(result.discovered, 1)
        self.assertEqual(result.processed, 1)
        self.assertEqual(result.created, 1)
        self.assertEqual(result.skipped, 0)
        self.assertTrue(Source.objects.filter(slug="youtube", enabled=True).exists())
        self.assertEqual(Offer.objects.count(), 1)
        offer = Offer.objects.get()
        self.assertEqual(
            offer.identity,
            f"youtube|US|movie|460|||free_with_ads|{MATILDA_VIDEO_ID}",
        )
        self.assertEqual(offer.provider, self.provider)
        self.assertEqual(offer.country.code, "US")
        self.assertEqual(offer.source.slug, "youtube")
        self.assertEqual(offer.offer_type, OfferType.FREE_WITH_ADS)
        self.assertEqual(offer.content_type, ContentType.MOVIE)
        self.assertIsNone(offer.audio_languages)
        self.assertTrue(offer.currently_available)

        run = SyncRun.objects.get()
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(run.provider, self.provider)
        self.assertEqual(run.country.code, "US")
        self.assertEqual(run.source.slug, "youtube")
        self.assertEqual(run.mode, "targeted")
        self.assertFalse(run.authoritative)
        self.assertEqual(run.items_processed, 1)
        self.assertEqual(run.offers_created, 1)

    def test_discovered_content_is_kept_even_for_the_targeted_path(self):
        self.service.refresh_content(self.movie)
        content = ProviderContent.objects.get()
        self.assertEqual(content.provider, self.provider)
        self.assertEqual(content.provider_content_id, MATILDA_VIDEO_ID)
        self.assertEqual(content.content_type, ContentType.MOVIE)
        self.assertEqual(content.title, "Matilda")

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
        self.api.failures["Matilda 1996"] = SourceFetchError("simulated outage")
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
