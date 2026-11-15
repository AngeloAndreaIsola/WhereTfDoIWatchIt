"""CBC Gem source: catalogue parsing, pagination, failure semantics.

Fixture-based (``cbc_fixtures.FakeCbcApi``): no test performs network I/O and
no test touches the database (``SimpleTestCase`` doubles as a guard — the
source must stay persistence-free).

Fixture catalogue (recorded payloads):

    shows       p1 [north-of-north, coronation-street]   totalPages 2
                p2 [murdoch-mysteries]
    all-films   p1 [beirut, float]                       totalPages 1
    kids-all    p1 [north-of-north]  (cross-category duplicate)

    north-of-north  series, 2 seasons x (1 trailer + 8 episodes)   -> 19 offers
    coronation-street series, NoSeason, 1 lineup x 21 episodes     -> 23 offers
    murdoch-mysteries series, s01 (2 eps), s12 (1 trailer + 18 eps),
                      s20 (trailer only)                          -> 23 offers
    beirut          movie, Member tier, productionYear 2018       ->  1 offer
    float           movie, hasOnlyATrailer                        ->  0 offers
"""
import datetime as dt

import requests
from django.test import SimpleTestCase

from availability.domain.enums import ContentType, OfferType, SourceType, SyncMode
from availability.sources import (
    CBCGemSource,
    ContentRequest,
    SourceFetchError,
    source_registry,
)
from availability.sources.cbc_gem import CBCGemClient, CBCGemSource as DirectSource

from .cbc_fixtures import FakeCbcApi

TOTAL_OFFERS = 66


def build_source(api=None):
    api = api or FakeCbcApi()
    return CBCGemSource(client=CBCGemClient(requester=api)), api


class RegistrationTests(SimpleTestCase):
    def test_cbc_gem_source_is_registered(self):
        source = source_registry.get("cbc_gem")
        self.assertIsInstance(source, CBCGemSource)
        self.assertEqual(source.slug, "cbc_gem")
        self.assertEqual(source.source_type, SourceType.API)

    def test_capabilities_declare_catalogue_non_authoritative_scoped_source(self):
        capabilities = source_registry.get("cbc_gem").capabilities
        self.assertEqual(capabilities.supported_providers, frozenset({"cbc_gem"}))
        self.assertEqual(capabilities.supported_countries, frozenset({"CA"}))
        self.assertEqual(
            capabilities.supported_content_types,
            frozenset({ContentType.MOVIE, ContentType.SERIES, ContentType.SEASON,
                       ContentType.EPISODE}),
        )
        self.assertTrue(capabilities.supports_catalogue_sync)
        # Category indices cannot be proven complete and CBC exposes no
        # removal signal: absence must never deactivate offers.
        self.assertFalse(capabilities.supports_authoritative_snapshots)
        self.assertFalse(capabilities.supports_incremental_sync)
        # CBC publishes no TMDb ids; mapping happens in the refresh service.
        self.assertFalse(capabilities.supports_tmdb_ids)
        self.assertTrue(capabilities.supports_seasons)
        self.assertTrue(capabilities.supports_episodes)
        self.assertTrue(capabilities.supports_languages)

    def test_targeted_content_request_is_rejected(self):
        source, _api = build_source()
        with self.assertRaisesMessage(ValueError, "catalogue-driven"):
            source.fetch(ContentRequest(tmdb_id=550, title="Fight Club"))

    def test_fetch_without_content_walks_the_catalogue(self):
        source, _api = build_source()
        result = source.fetch()
        self.assertEqual(len(result.contents), 5)
        self.assertEqual(len(result.offers), TOTAL_OFFERS)


class SnapshotSemanticsTests(SimpleTestCase):
    def test_result_never_claims_authoritativeness_and_is_resumable(self):
        source, _api = build_source()
        result = source.fetch()
        self.assertFalse(result.authoritative)
        self.assertFalse(result.complete_snapshot)
        self.assertEqual(result.sync_mode, SyncMode.FULL)
        # CBC's traversal is checkpointed, but the checkpoint is *progress*, not
        # a completeness claim: an exhausted walk (this fixture fits in one
        # batch) emits none, and authoritativeness stays False either way.
        self.assertIsNone(result.cursor)


class CatalogueParsingTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source, cls.api = build_source()
        cls.result = cls.source.fetch()

    def _offers(self, slug, content_type=None):
        offers = [o for o in self.result.offers if o.provider_content_id == slug]
        if content_type is not None:
            offers = [o for o in offers if o.content_type == content_type]
        return offers

    def test_every_catalogue_item_becomes_a_content_record(self):
        self.assertEqual(
            {c.provider_content_id for c in self.result.contents},
            {"north-of-north", "coronation-street", "murdoch-mysteries",
             "beirut", "float"},
        )

    def test_offer_granularity_counts(self):
        counts = {}
        for offer in self.result.offers:
            counts[offer.content_type] = counts.get(offer.content_type, 0) + 1
        self.assertEqual(counts, {
            ContentType.SERIES: 3,   # north, coronation, murdoch
            ContentType.MOVIE: 1,    # beirut (float is trailer-only)
            ContentType.SEASON: 5,   # north 1-2, coronation 1, murdoch 1+12
            ContentType.EPISODE: 57, # 16 + 21 + 20
        })

    def test_movie_offer_fields(self):
        offer = self._offers("beirut", ContentType.MOVIE)[0]
        self.assertEqual(offer.provider_id, "cbc_gem")
        self.assertEqual(offer.country, "CA")
        self.assertIsNone(offer.tmdb_id)          # no external ids at the source
        self.assertIsNone(offer.season_number)
        self.assertIsNone(offer.episode_number)
        self.assertEqual(offer.offer_type, OfferType.FREE)  # Member tier -> free
        self.assertEqual(offer.provider_url, "https://gem.cbc.ca/beirut")
        self.assertEqual(offer.quality, "")
        self.assertEqual(offer.audio_languages, ["EN"])
        self.assertIsNone(offer.subtitle_languages)  # only closedCaption exists
        self.assertEqual(
            offer.available_from, dt.datetime(2026, 9, 25, tzinfo=dt.timezone.utc)
        )
        self.assertIsNone(offer.available_until)  # CBC never exposes expiry
        self.assertEqual(offer.source_record_id, "beirut")
        self.assertEqual(offer.source_version, "ott-catalog-v2")

    def test_movie_release_date_is_production_year_with_year_precision(self):
        beirut = next(c for c in self.result.contents
                      if c.provider_content_id == "beirut")
        self.assertEqual(beirut.release_date, dt.date(2018, 1, 1))

    def test_series_release_date_is_none(self):
        # CBC series dates are catalog/re-add dates, not first-air years.
        for content in self.result.contents:
            if content.content_type == ContentType.SERIES:
                self.assertIsNone(content.release_date, content.provider_content_id)

    def test_series_season_and_episode_rows_share_the_catalogue_slug(self):
        show, season, episode = (
            self._offers("murdoch-mysteries", ContentType.SERIES)[0],
            self._offers("murdoch-mysteries", ContentType.SEASON),
            self._offers("murdoch-mysteries", ContentType.EPISODE),
        )
        self.assertIsNone(show.season_number)
        self.assertEqual({s.season_number for s in season}, {1, 12})
        self.assertEqual(show.provider_url, "https://gem.cbc.ca/murdoch-mysteries")
        first_episode = next(e for e in episode
                             if e.season_number == 1 and e.episode_number == 1)
        self.assertEqual(
            first_episode.provider_url,
            "https://gem.cbc.ca/murdoch-mysteries/s01e01",
        )
        self.assertEqual(first_episode.source_record_id, "murdoch-mysteries/s01e01")

    def test_trailer_items_never_become_offers_even_on_episode_number_collisions(self):
        # s12b01 (Trailer) and s12e01 both carry episodeNumber 1: filtering
        # trailers keeps exactly one (12, 1) offer.
        episodes = self._offers("murdoch-mysteries", ContentType.EPISODE)
        collisions = [e for e in episodes
                      if e.season_number == 12 and e.episode_number == 1]
        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0].source_record_id, "murdoch-mysteries/s12e01")

    def test_trailer_only_season_yields_no_season_or_episode_rows(self):
        # Season 20 contains only a trailer: no rows, but the show row survives.
        murdoch = self._offers("murdoch-mysteries")
        self.assertNotIn(20, {o.season_number for o in murdoch})
        self.assertTrue(any(o.content_type == ContentType.SERIES for o in murdoch))

    def test_trailer_only_movie_yields_content_but_no_offers(self):
        float_content = next(c for c in self.result.contents
                             if c.provider_content_id == "float")
        self.assertEqual(float_content.content_type, ContentType.MOVIE)
        self.assertEqual(self._offers("float"), [])

    def test_no_season_series_still_uses_the_lineup_season_number(self):
        seasons = self._offers("coronation-street", ContentType.SEASON)
        episodes = self._offers("coronation-street", ContentType.EPISODE)
        self.assertEqual([s.season_number for s in seasons], [1])
        self.assertEqual(len(episodes), 21)
        self.assertEqual(episodes[0].season_number, 1)

    def test_series_show_offer_has_no_availability_start(self):
        show = self._offers("north-of-north", ContentType.SERIES)[0]
        self.assertIsNone(show.available_from)
        season = self._offers("north-of-north", ContentType.SEASON)[0]
        self.assertIsNone(season.available_from)
        episode = self._offers("north-of-north", ContentType.EPISODE)[0]
        self.assertIsNotNone(episode.available_from)

    def test_episode_languages_inherit_the_series_language(self):
        for offer in self.result.offers:
            self.assertEqual(offer.audio_languages, ["EN"], offer.source_record_id)

    def test_all_fixture_tiers_map_to_free(self):
        # Fixtures contain Standard and Member only; both are free-to-watch.
        self.assertEqual(
            {o.offer_type for o in self.result.offers}, {OfferType.FREE}
        )


class TierMappingTests(SimpleTestCase):
    def _movie_offer_tier(self, *, tier=None, permission_tier=None):
        api = FakeCbcApi()
        item = api.details["beirut"]["content"][0]["lineups"][0]["items"][0]
        item["tier"] = tier
        if permission_tier is not None:
            item["permission"]["tier"] = permission_tier
        source, _api = build_source(api)
        offers = [o for o in source.fetch().offers
                  if o.provider_content_id == "beirut"]
        return offers[0].offer_type

    def test_standard_and_member_are_free(self):
        # Standard = no account needed; Member = free registered account
        # ("only available to our signed in members"), not a paid plan.
        self.assertEqual(self._movie_offer_tier(tier="Standard"), OfferType.FREE)
        self.assertEqual(self._movie_offer_tier(tier="Member"), OfferType.FREE)

    def test_premium_is_subscription(self):
        self.assertEqual(
            self._movie_offer_tier(tier="Premium"), OfferType.SUBSCRIPTION
        )

    def test_tier_read_from_permission_when_tier_field_is_absent(self):
        self.assertEqual(
            self._movie_offer_tier(tier=None, permission_tier="Premium"),
            OfferType.SUBSCRIPTION,
        )

    def test_unknown_tier_falls_through_as_unknown(self):
        self.assertEqual(
            self._movie_offer_tier(tier="Gold"), OfferType.UNKNOWN
        )


class PaginationTests(SimpleTestCase):
    def test_multi_page_category_is_walked_to_total_pages(self):
        source, api = build_source()
        source.fetch()
        requested = [
            int(url.split("pageNumber=")[1].split("&")[0])
            for url in api.urls_containing("/category/shows")
        ]
        self.assertEqual(requested, [1, 2])
        self.assertEqual(len(api.urls_containing("/show/murdoch-mysteries")), 1)

    def test_client_never_probes_past_total_pages(self):
        # The real API rejects page numbers beyond totalPages with HTTP 400;
        # the fake raises SourceFetchError for such requests, so completing
        # proves termination came from the response's own totalPages.
        source, api = build_source()
        source.fetch()
        for url in api.requests:
            if "/category/" not in url:
                continue
            page = int(url.split("pageNumber=")[1].split("&")[0])
            if "/category/shows" in url:
                self.assertLessEqual(page, 2)
            else:
                self.assertEqual(page, 1)

    def test_repeated_page_signature_terminates_instead_of_looping(self):
        api = FakeCbcApi()
        page_one = api.categories["shows"]["1"]
        api.categories["shows"]["3"] = dict(page_one, pageNumber=3)
        api.categories["shows"]["1"] = dict(page_one, totalPages=3)
        api.categories["shows"]["2"] = dict(page_one, pageNumber=2)
        source, _api = build_source(api)

        result = source.fetch()

        self.assertEqual(len(api.urls_containing("/category/shows")), 2)
        self.assertEqual(
            {c.provider_content_id for c in result.contents},
            {"north-of-north", "coronation-street", "beirut", "float"},
        )

    def test_missing_category_fails_loudly_instead_of_looking_empty(self):
        api = FakeCbcApi()
        del api.categories["kids-all"]
        source, _api = build_source(api)
        with self.assertRaises(SourceFetchError):
            source.fetch()

    def test_transport_failure_mid_pagination_is_not_an_empty_result(self):
        api = FakeCbcApi()
        api.failures["pageNumber=2"] = requests.ConnectionError("reset")
        source, _api = build_source(api)
        with self.assertRaises(SourceFetchError):
            source.fetch()

    def test_detail_failure_mid_run_is_not_an_empty_result(self):
        api = FakeCbcApi()
        api.failures["/show/murdoch-mysteries"] = SourceFetchError("upstream 500")
        source, _api = build_source(api)
        with self.assertRaises(SourceFetchError):
            source.fetch()

    def test_empty_category_page_is_a_normal_outcome(self):
        api = FakeCbcApi()
        api.categories["kids-all"]["1"]["results"] = []
        source, _api = build_source(api)
        result = source.fetch()
        # shows/all-films still deliver every item; kids-all adds no duplicates.
        self.assertEqual(len(result.contents), 5)


class CrossCategoryDeduplicationTests(SimpleTestCase):
    def test_item_in_two_categories_is_fetched_once(self):
        source, api = build_source()
        result = source.fetch()
        self.assertEqual(len(api.urls_containing("/show/north-of-north")), 1)
        self.assertEqual(
            [c.provider_content_id for c in result.contents].count("north-of-north"),
            1,
        )

    def test_editorial_cards_with_query_strings_are_skipped(self):
        api = FakeCbcApi()
        api.categories["shows"]["1"]["results"].append(
            {"url": "live-event/special?type=live", "title": "Live Special"}
        )
        source, _api = build_source(api)
        result = source.fetch()
        self.assertFalse(
            any(c.provider_content_id.startswith("live-event")
                for c in result.contents)
        )
        self.assertEqual(api.urls_containing("/show/live-event"), [])


class UnsupportedPayloadTests(SimpleTestCase):
    def test_non_catalogue_structural_type_is_skipped(self):
        api = FakeCbcApi()
        api.details["beirut"]["structuredMetadata"]["@type"] = "RadioBroadcast"
        source, _api = build_source(api)
        result = source.fetch()
        self.assertNotIn("beirut", {c.provider_content_id for c in result.contents})
        self.assertNotIn("beirut", {o.provider_content_id for o in result.offers})

    def test_french_locale_normalizes_to_fr(self):
        api = FakeCbcApi()
        api.details["beirut"]["structuredMetadata"]["inLanguage"] = "fr-CA"
        source, _api = build_source(api)
        offers = [o for o in source.fetch().offers
                  if o.provider_content_id == "beirut"]
        self.assertEqual(offers[0].audio_languages, ["FR"])

    def test_missing_language_metadata_stays_none(self):
        api = FakeCbcApi()
        del api.details["beirut"]["structuredMetadata"]["inLanguage"]
        source, _api = build_source(api)
        offers = [o for o in source.fetch().offers
                  if o.provider_content_id == "beirut"]
        self.assertIsNone(offers[0].audio_languages)

    def test_transport_error_becomes_source_fetch_error(self):
        api = FakeCbcApi()
        api.failures["/category/"] = requests.Timeout("too slow")
        source, _api = build_source(api)
        with self.assertRaises(SourceFetchError):
            source.fetch()

    def test_non_dict_response_is_rejected(self):
        source = DirectSource(client=CBCGemClient(requester=lambda url: ["nope"]))
        with self.assertRaises(SourceFetchError):
            source.fetch()

    def test_malformed_category_payload_is_rejected(self):
        source = DirectSource(client=CBCGemClient(requester=lambda url: {}))
        with self.assertRaises(SourceFetchError):
            source.fetch()


class ComingSoonItemTests(SimpleTestCase):
    """A listed item with nothing playable yet must not fail the refresh.

    CBC lists upcoming titles ("COMING SOON") in category indices while their
    detail payload still carries ``content: null`` — no lineups, no episodes
    exist yet. That is a valid detail parsed as a catalogue entry with zero
    offers, not a malformed payload: raising on it would turn the daily
    scheduled refresh permanently red because of one un-aired programme.
    """

    def test_null_content_is_parsed_as_an_offerless_catalogue_entry(self):
        api = FakeCbcApi()
        api.details["coronation-street"]["content"] = None
        source, _api = build_source(api)
        result = source.fetch()

        self.assertIn(
            "coronation-street", {c.provider_content_id for c in result.contents}
        )
        self.assertNotIn(
            "coronation-street", {o.provider_content_id for o in result.offers}
        )
        # Only that title's 23 offers disappear; the rest still refreshes.
        self.assertEqual(len(result.offers), TOTAL_OFFERS - 23)

    def test_null_content_movie_yields_no_offer_but_still_parses(self):
        api = FakeCbcApi()
        api.details["beirut"]["content"] = None
        source, _api = build_source(api)
        result = source.fetch()

        self.assertIn("beirut", {c.provider_content_id for c in result.contents})
        self.assertNotIn(
            "beirut", {o.provider_content_id for o in result.offers}
        )

    def test_detail_without_structural_metadata_is_still_rejected(self):
        api = FakeCbcApi()
        del api.details["coronation-street"]["structuredMetadata"]
        source, _api = build_source(api)
        with self.assertRaises(SourceFetchError):
            source.fetch()
