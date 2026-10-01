"""Prime Video as a catalogue source (Phase 11).

Every test runs offline: :class:`FakePrimeApi` routes each URL to a recorded
fixture, so nothing here opens a socket. The fixture window is eight titles
(TOTAL_CONTENTS / TOTAL_OFFERS below), which is why the walk runs with
``max_contents=8`` — the recorded storefront links exactly those detail pages
and stops paginating there, so a larger budget walks off the recording.

Covered here:

- discovery: seed order, round-robin interleave, relative pagination, card
  de-duplication, budget truncation, and failure-not-empty for anything the
  walk cannot read;
- parsing: movie/series rows, season+episode offers, the cue -> offer-type
  matrix, quality and language mapping, record ids;
- provenance end to end through ``AvailabilityRefreshService`` (Phase 4 §7):
  prime_video offers persist under their own ``Source`` while sharing the
  canonical ``prime`` Provider with Streaming Availability.
"""
import datetime as dt
import json
import re

import requests
from django.test import SimpleTestCase, TestCase

from availability.domain.enums import (
    ContentType,
    MatchMethod,
    OfferType,
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
    AvailabilitySource,
    ContentRequest,
    SourceFetchError,
    source_registry,
)
from availability.sources.prime_video import (
    BASE_URL,
    HYDRATION_MARKER,
    STORE_MARKER,
    PrimeVideoClient,
    PrimeVideoSource,
)
from tracker.models import Movie, Provider

from .prime_video_fixtures import (
    CATEGORIES_URL,
    COMEDY_URL,
    EUPHORIA,
    GOOD_OMENS,
    HANDMAIDS_TALE,
    IN_THE_GREY,
    LOVE_HYPOTHESIS,
    MAXTON_HALL,
    MINIONS,
    MOVIE_URL,
    STERLING_POINT,
    STORE_URL,
    SPIDER_MAN,
    THE_HANGOVER,
    TV_URL,
    FakePrimeApi,
    detail_url,
    load,
)
from .source_stubs import StubSource, fetch_result, normalized_offer

# Recorded window: eight index-linked titles -> eight content records.
TOTAL_CONTENTS = 8
TOTAL_OFFERS = 50
FIXTURE_WINDOW = 8

# Season pages resolve to the canonical *first* season's compactGTI.
# ``HANDMAIDS_TALE`` (observed: season 6) -> ``HANDMAIDS_SHOW``.
HANDMAIDS_SHOW = "0RC07XHU1VXK97YXW7NNKIGV2U"
GOOD_OMENS_SHOW = "0PB7ON6NSFXCOHEOXZQNOEFSZN"
EUPHORIA_SHOW = "0SZVDKRFOUWD9URJ7OF2BP9GC6"
MAXTON_SHOW = "0PNRI6RQY7CIO04HACV89WK5I3"


def build_source(api=None, **source_kwargs):
    api = api or FakePrimeApi()
    source = PrimeVideoSource(client=PrimeVideoClient(requester=api), **source_kwargs)
    return source, api


# ---------------------------------------------------------------- fixture edits


def with_payload(html, marker, mutate):
    """Fixture HTML whose ``<script id=marker>`` JSON object was mutated."""
    pattern = re.compile(
        r'(<script[^>]*\bid="%s"[^>]*>)(.*?)(</script>)' % re.escape(marker),
        re.S,
    )
    match = pattern.search(html)
    assert match is not None, f"fixture has no {marker} payload"
    payload = json.loads(match.group(2))
    mutate(payload)
    return (
        html[: match.start(2)]
        + json.dumps(payload, ensure_ascii=False)
        + html[match.end(2):]
    )


def without_script(html, marker):
    return re.sub(
        r'<script[^>]*\bid="%s"[^>]*>.*?</script>' % re.escape(marker),
        "",
        html,
        count=1,
        flags=re.S,
    )


def with_territory(html, code):
    def mutate(payload):
        payload["RequestContext"]["currentTerritory"] = code

    return with_payload(html, STORE_MARKER, mutate)


def empty_index(html):
    """An index page that publishes no cards and no next page."""

    def mutate(payload):
        body = payload["init"]["preparations"]["body"]
        body["containers"] = []
        body.pop("pagination", None)

    return with_payload(html, HYDRATION_MARKER, mutate)


def index_with(cards, *, cue=""):
    """A storefront page whose only cards link ``cards`` (no pagination)."""

    def mutate(payload):
        body = payload["init"]["preparations"]["body"]
        body["containers"] = [
            {
                "containerType": "PrimeSerpsContainer",
                "entities": [
                    {
                        "entityType": entity_type,
                        "link": {"url": f"/detail/{gti}"},
                        "entitlementCues": {
                            "focusMessage": {"message": cue},
                        },
                    }
                    for gti, entity_type in cards.items()
                ],
            }
        ]
        body.pop("pagination", None)

    return with_payload(load("store.html"), HYDRATION_MARKER, mutate)


def detail_without_message(html):
    """A detail page that publishes no commercial message (card cue must win)."""

    def mutate(payload):
        state = payload["init"]["preparations"]["body"]["atf"]["state"]
        state.pop("action", None)

    return with_payload(html, HYDRATION_MARKER, mutate)


def detail_with_message(html, message):
    def mutate(payload):
        state = payload["init"]["preparations"]["body"]["atf"]["state"]
        action = state["action"]["atf"][state["pageTitleId"]]["messages"]
        action["focusMessage"]["dvMessage"]["string"] = message

    return with_payload(html, HYDRATION_MARKER, mutate)


def _empty_seeds():
    """Every seed except ``/store`` answers with an empty index page."""
    return {
        CATEGORIES_URL: load("categories.html"),
        MOVIE_URL: empty_index(load("movie.html")),
        TV_URL: empty_index(load("tv.html")),
        COMEDY_URL: empty_index(load("comedy.html")),
    }


def single_title_source(gti, *, cue="", detail_html=None, **source_kwargs):
    """A source whose storefront links only ``gti`` (every other seed empty)."""
    pages = _empty_seeds()
    pages[STORE_URL] = index_with({gti: "TV Show"}, cue=cue)
    if detail_html is not None:
        pages[f"/detail/{gti}"] = detail_html
    api = FakePrimeApi(pages=pages)
    source, api = build_source(api, **source_kwargs)
    return source, api


# ------------------------------------------------------------------ registration


class RegistrationTests(SimpleTestCase):
    def test_prime_video_source_is_registered(self):
        source = source_registry.get("prime_video")
        self.assertIsInstance(source, PrimeVideoSource)
        self.assertIsInstance(source, AvailabilitySource)
        self.assertEqual(source.slug, "prime_video")
        self.assertEqual(source.name, "Prime Video")

    def test_capabilities_declare_a_bounded_nl_prime_catalogue_source(self):
        capabilities = source_registry.get("prime_video").capabilities
        # The provider row is shared with Streaming Availability ("prime"),
        # the country is the single territory the source was researched on.
        self.assertEqual(capabilities.supported_providers, frozenset({"prime"}))
        self.assertEqual(capabilities.supported_countries, frozenset({"NL"}))
        self.assertEqual(
            capabilities.supported_content_types,
            frozenset({ContentType.MOVIE, ContentType.SERIES, ContentType.SEASON,
                       ContentType.EPISODE}),
        )
        self.assertTrue(capabilities.supports_catalogue_sync)
        # Editorial ordering, non-chaining genre pages and a bounded walk mean
        # absence can never be evidence of unavailability.
        self.assertFalse(capabilities.supports_authoritative_snapshots)
        self.assertFalse(capabilities.supports_incremental_sync)
        # Prime publishes gtis only; mapping happens in the refresh service.
        self.assertFalse(capabilities.supports_tmdb_ids)
        self.assertTrue(capabilities.supports_seasons)
        self.assertTrue(capabilities.supports_episodes)
        self.assertTrue(capabilities.supports_languages)

    def test_targeted_content_request_is_rejected(self):
        source, _api = build_source()
        with self.assertRaisesMessage(ValueError, "catalogue-driven"):
            source.fetch(ContentRequest(tmdb_id=550))

    def test_fetch_without_content_walks_the_catalogue(self):
        source, _api = build_source(max_contents=FIXTURE_WINDOW)
        result = source.fetch()
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)
        self.assertEqual(len(result.offers), TOTAL_OFFERS)


class ResultSemanticsTests(SimpleTestCase):
    def test_result_never_claims_authoritativeness_or_a_cursor(self):
        source, _api = build_source(max_contents=FIXTURE_WINDOW)
        result = source.fetch()
        self.assertFalse(result.authoritative)
        self.assertFalse(result.complete_snapshot)
        # One fetch reports the whole configured window.
        self.assertEqual(result.sync_mode, SyncMode.FULL)
        # The storefront exposes no resumable server-side cursor.
        self.assertIsNone(result.cursor)

    def test_scope_hints_are_accepted_but_never_change_the_reported_scope(self):
        source, _api = build_source(max_contents=FIXTURE_WINDOW)
        result = source.fetch(provider="prime", country="NL")
        self.assertEqual(len(result.offers), TOTAL_OFFERS)
        self.assertEqual({offer.country for offer in result.offers}, {"NL"})
        self.assertEqual({offer.provider_id for offer in result.offers}, {"prime"})


# -------------------------------------------------------------------- discovery


class DiscoveryTests(SimpleTestCase):
    def test_index_pages_are_walked_in_seed_order_then_pagination(self):
        source, api = build_source(max_contents=FIXTURE_WINDOW)
        source.fetch()
        index = api.index_requests
        self.assertEqual(
            index[:5],
            [CATEGORIES_URL, STORE_URL, MOVIE_URL, TV_URL, COMEDY_URL],
        )
        # ``pagination.url`` is path-absolute and relative to the site origin.
        self.assertTrue(index[5].startswith(f"{STORE_URL}?"))
        self.assertEqual(len(index), 6)

    def test_detail_pages_follow_the_order_the_cards_were_discovered(self):
        source, api = build_source(max_contents=FIXTURE_WINDOW)
        source.fetch()
        self.assertEqual(
            api.detail_requests,
            [
                detail_url(MINIONS),          # /store
                detail_url(LOVE_HYPOTHESIS),  # /movie
                detail_url(STERLING_POINT),   # /tv
                detail_url(HANDMAIDS_TALE),   # /tv
                detail_url(IN_THE_GREY),      # /genre/comedy
                detail_url(GOOD_OMENS),       # /genre/comedy
                detail_url(THE_HANGOVER),     # /store page 2
                detail_url(EUPHORIA),         # /store page 2
            ],
        )

    def test_a_card_seen_twice_is_fetched_once(self):
        source, api = build_source(max_contents=FIXTURE_WINDOW)
        source.fetch()
        # Minions is carded on /store and /movie; The Love Hypothesis on
        # /movie and /genre/comedy.
        self.assertEqual(api.detail_requests.count(detail_url(MINIONS)), 1)
        self.assertEqual(api.detail_requests.count(detail_url(LOVE_HYPOTHESIS)), 1)

    def test_only_the_recorded_genre_index_is_requested(self):
        source, api = build_source(max_contents=FIXTURE_WINDOW)
        source.fetch()
        genres = [url for url in api.index_requests if "/genre/" in url]
        self.assertEqual(genres, [COMEDY_URL])

    def test_the_walk_stops_at_the_configured_budget_and_logs_the_truncation(self):
        source, api = build_source(max_contents=2)
        with self.assertLogs("availability.sources.prime_video", level="WARNING") as logs:
            result = source.fetch()
        self.assertEqual(len(result.contents), 2)
        self.assertEqual(
            api.detail_requests,
            [detail_url(MINIONS), detail_url(LOVE_HYPOTHESIS)],
        )
        self.assertTrue(
            any("prime_video_catalogue_truncated" in line for line in logs.output)
        )

    def test_categories_failure_degrades_to_the_core_storefront(self):
        source, api = build_source(max_contents=6)
        api.failures[CATEGORIES_URL] = SourceFetchError("network down")
        with self.assertLogs("availability.sources.prime_video", level="WARNING") as logs:
            result = source.fetch()
        # No genre seed, so /store + /movie + /tv (+ /store page 2) only.
        self.assertEqual(len(result.contents), 6)
        self.assertNotIn(COMEDY_URL, api.requests)
        self.assertTrue(
            any("prime_video_categories_unavailable" in line
                for line in logs.output)
        )

    def test_walking_beyond_the_recorded_pages_fails_loudly(self):
        # Fixture router contract: a page the recording does not cover answers
        # like an upstream 404 instead of pretending the catalogue ended.
        source, _api = build_source()  # default budget, unbounded by fixtures
        with self.assertRaisesMessage(SourceFetchError, "fixture missing"):
            source.fetch()


# ------------------------------------------------------------- failure vs empty


class FailureVsEmptyTests(SimpleTestCase):
    def test_foreign_territory_on_an_index_page_fails_the_walk(self):
        source, api = build_source(max_contents=FIXTURE_WINDOW)
        api.pages[STORE_URL] = with_territory(load("store.html"), "US")
        with self.assertRaisesMessage(SourceFetchError, "region mismatch"):
            source.fetch()

    def test_foreign_territory_on_a_detail_page_fails_the_fetch(self):
        source, api = build_source(max_contents=FIXTURE_WINDOW)
        api.pages[detail_url(MINIONS)] = with_territory(
            load("detail_0Q3VAOUAIPMBYOV70KCJTSNZ6O.html"), "GB"
        )
        with self.assertRaisesMessage(SourceFetchError, "region mismatch"):
            source.fetch()

    def test_a_territory_mismatch_on_categories_only_drops_the_genre_seeds(self):
        # ``/categories`` is opportunistic: it is logged, not fatal, so the
        # core storefront still reports what it can.
        source, api = build_source(max_contents=6)
        api.pages[CATEGORIES_URL] = with_territory(load("categories.html"), "US")
        with self.assertLogs("availability.sources.prime_video", level="WARNING") as logs:
            result = source.fetch()
        self.assertEqual(len(result.contents), 6)
        self.assertNotIn(COMEDY_URL, api.requests)
        self.assertTrue(
            any("prime_video_categories_unavailable" in line
                for line in logs.output)
        )

    def test_missing_hydration_payload_is_a_failure_not_an_empty_page(self):
        source, api = build_source(max_contents=FIXTURE_WINDOW)
        api.pages[STORE_URL] = without_script(load("store.html"), HYDRATION_MARKER)
        with self.assertRaisesMessage(
            SourceFetchError, f"carries no {HYDRATION_MARKER}"
        ):
            source.fetch()

    def test_malformed_hydration_json_is_a_failure_not_an_empty_page(self):
        source, api = build_source(max_contents=FIXTURE_WINDOW)
        api.pages[STORE_URL] = re.sub(
            r'(<script[^>]*\bid="%s"[^>]*>).*?(</script>)' % HYDRATION_MARKER,
            r'\1{"init": \2',
            load("store.html"),
            count=1,
            flags=re.S,
        )
        with self.assertRaisesMessage(SourceFetchError, "not valid JSON"):
            source.fetch()

    def test_a_detail_url_answering_with_an_index_page_is_skipped(self):
        source, api = build_source(max_contents=FIXTURE_WINDOW)
        api.pages[detail_url(MINIONS)] = load("store.html")
        with self.assertLogs("availability.sources.prime_video", level="INFO") as logs:
            result = source.fetch()
        self.assertEqual(len(result.contents), TOTAL_CONTENTS - 1)
        self.assertNotIn(
            MINIONS, {offer.provider_content_id for offer in result.offers}
        )
        self.assertTrue(
            any("prime_video_content_skipped" in line for line in logs.output)
        )

    def test_when_every_detail_page_is_an_index_the_fetch_reports_nothing(self):
        source, api = build_source(max_contents=FIXTURE_WINDOW)
        api.pages["/detail/"] = load("store.html")
        with self.assertLogs("availability.sources.prime_video", level="WARNING") as logs:
            result = source.fetch()
        self.assertEqual(result.contents, [])
        self.assertEqual(result.offers, [])
        self.assertTrue(
            any("prime_video_catalogue_nothing_observed" in line
                for line in logs.output)
        )

    def test_transport_failure_is_reported_as_a_fetch_failure(self):
        source, api = build_source(max_contents=FIXTURE_WINDOW)
        api.failures["/detail/"] = requests.ConnectionError("connection refused")
        with self.assertRaisesMessage(SourceFetchError, "request failed"):
            source.fetch()

    def test_an_index_without_cards_is_logged_as_an_empty_catalogue(self):
        source, api = build_source(max_contents=FIXTURE_WINDOW)
        fixtures = {
            STORE_URL: "store.html",
            MOVIE_URL: "movie.html",
            TV_URL: "tv.html",
            COMEDY_URL: "comedy.html",
        }
        for url, fixture in fixtures.items():
            api.pages[url] = empty_index(load(fixture))
        with self.assertLogs("availability.sources.prime_video", level="WARNING") as logs:
            result = source.fetch()
        self.assertEqual(result.contents, [])
        self.assertEqual(result.offers, [])
        self.assertEqual(api.detail_requests, [])
        self.assertTrue(
            any("prime_video_catalogue_empty" in line for line in logs.output)
        )


# --------------------------------------------------------------------- parsing


class CatalogueParsingTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source, cls.api = build_source(max_contents=FIXTURE_WINDOW)
        cls.result = cls.source.fetch()

    def _contents(self, provider_content_id):
        return [
            content for content in self.result.contents
            if content.provider_content_id == provider_content_id
        ]

    def _offers(self, provider_content_id, content_type=None):
        offers = [
            offer for offer in self.result.offers
            if offer.provider_content_id == provider_content_id
        ]
        if content_type is not None:
            offers = [offer for offer in offers if offer.content_type == content_type]
        return offers

    def _offer_types(self, provider_content_id, content_type=None):
        return {
            offer.offer_type
            for offer in self._offers(provider_content_id, content_type)
        }

    def test_every_observed_title_becomes_one_content_record(self):
        self.assertEqual(len(self.result.contents), TOTAL_CONTENTS)
        self.assertEqual(
            {content.content_type for content in self.result.contents},
            {ContentType.MOVIE, ContentType.SERIES},
        )
        self.assertTrue(
            all(content.provider_id == "prime" for content in self.result.contents)
        )
        self.assertTrue(
            all(content.source_id == "prime_video" for content in self.result.contents)
        )
        # Season and episode granularity lives on offers, never on content rows.
        self.assertTrue(
            all(content.season_number is None for content in self.result.contents)
        )

    def test_movie_fields_come_from_the_detail_page(self):
        movie = self._contents(MINIONS)[0]
        self.assertEqual(movie.content_type, ContentType.MOVIE)
        self.assertEqual(movie.title, "Minions & Monsters")
        self.assertEqual(movie.release_date, dt.date(2026, 7, 1))
        self.assertEqual(movie.duration, 5397)
        self.assertEqual(movie.url, detail_url(MINIONS))
        self.assertEqual(movie.external_ids, {})
        self.assertTrue(
            movie.poster.startswith("https://m.media-amazon.com/images/")
        )
        self.assertIn("Illumination expands", movie.description)

    def test_release_dates_are_parsed_from_the_recorded_english_month(self):
        hangover = self._contents(THE_HANGOVER)[0]
        self.assertEqual(hangover.release_date, dt.date(2009, 6, 11))
        self.assertEqual(hangover.duration, 5731)

    def test_series_row_is_the_show_not_the_observed_season(self):
        # The observed page is season 6; the series row resolves to the first
        # season's compactGTI (the only show-level id Prime publishes).
        series = self._contents(HANDMAIDS_SHOW)[0]
        self.assertEqual(series.content_type, ContentType.SERIES)
        self.assertEqual(series.title, "The Handmaid's Tale")
        self.assertEqual(series.url, detail_url(HANDMAIDS_SHOW))
        # Seasons carry no trustworthy series premiere year.
        self.assertIsNone(series.release_date)
        self.assertIsNone(series.duration)
        self.assertNotIn(
            HANDMAIDS_TALE, [content.provider_content_id for content in
                             self.result.contents]
        )

    def test_a_single_season_show_keeps_its_own_page_identity(self):
        # Sterling Point lists exactly one season — itself — so there is no
        # other id to resolve to.
        series = self._contents(STERLING_POINT)[0]
        self.assertEqual(series.content_type, ContentType.SERIES)
        self.assertEqual(series.title, "Sterling Point")
        self.assertEqual(series.url, detail_url(STERLING_POINT))

    def test_series_and_season_offers_share_the_show_identity(self):
        offers = self._offers(HANDMAIDS_SHOW)
        self.assertEqual(len(offers), 12)  # series + season 6 + 10 episodes
        self.assertEqual(
            {offer.provider_content_id for offer in offers}, {HANDMAIDS_SHOW}
        )
        by_type = {}
        for offer in offers:
            by_type.setdefault(offer.content_type, []).append(offer)
        self.assertEqual(
            sorted(by_type),
            [ContentType.EPISODE, ContentType.SEASON, ContentType.SERIES],
        )
        # The observed season is 6, and only it is offered.
        self.assertEqual(
            [offer.season_number for offer in by_type[ContentType.SEASON]], [6]
        )
        self.assertIsNone(by_type[ContentType.SERIES][0].season_number)
        self.assertEqual(
            sorted(offer.episode_number for offer in by_type[ContentType.EPISODE]),
            list(range(1, 11)),
        )

    def test_every_offer_granularity_is_present_in_the_window(self):
        breakdown = {}
        for offer in self.result.offers:
            breakdown[offer.content_type] = breakdown.get(offer.content_type, 0) + 1
        self.assertEqual(
            breakdown,
            {
                ContentType.MOVIE: 5,
                ContentType.SERIES: 5,
                ContentType.SEASON: 5,
                ContentType.EPISODE: 35,
            },
        )
        self.assertEqual(len(self._offers(STERLING_POINT)), 10)
        self.assertEqual(len(self._offers(GOOD_OMENS_SHOW)), 3)
        self.assertEqual(len(self._offers(EUPHORIA_SHOW)), 20)

    def test_offer_types_follow_the_page_cue(self):
        self.assertEqual(self._offer_types(MINIONS), {OfferType.BUY})
        self.assertEqual(self._offer_types(IN_THE_GREY), {OfferType.SUBSCRIPTION})
        self.assertEqual(self._offer_types(LOVE_HYPOTHESIS), {OfferType.SUBSCRIPTION})
        self.assertEqual(self._offer_types(STERLING_POINT), {OfferType.SUBSCRIPTION})
        self.assertEqual(
            self._offer_types(GOOD_OMENS_SHOW), {OfferType.SUBSCRIPTION}
        )
        # A channel season: add-on membership, not Prime.
        self.assertEqual(self._offer_types(HANDMAIDS_SHOW), {OfferType.ADDON})
        # "or buy" adds a transactional row on top of the add-on subscription.
        self.assertEqual(
            self._offer_types(EUPHORIA_SHOW), {OfferType.ADDON, OfferType.BUY}
        )

    def test_rent_or_buy_produces_two_distinct_offers(self):
        offers = self._offers(THE_HANGOVER)
        self.assertEqual(len(offers), 2)
        self.assertEqual(
            sorted(offer.offer_type for offer in offers),
            [OfferType.BUY, OfferType.RENT],
        )
        for offer in offers:
            self.assertEqual(offer.country, "NL")
            self.assertEqual(offer.provider_id, "prime")
            self.assertEqual(offer.content_type, ContentType.MOVIE)

    def test_quality_is_mapped_into_the_shared_vocabulary(self):
        minions = self._offers(MINIONS)[0]
        self.assertEqual(minions.quality, "4K")
        # No UHD/HDR rendition published.
        self.assertEqual(self._offers(THE_HANGOVER)[0].quality, "")
        self.assertEqual(
            self._offers(EUPHORIA_SHOW, ContentType.SERIES)[0].quality, "4K"
        )
        # Episode records carry their own rendition flags (none recorded).
        self.assertEqual(
            {
                offer.quality
                for offer in self._offers(EUPHORIA_SHOW, ContentType.EPISODE)
            },
            {""},
        )

    def test_audio_and_subtitle_languages_are_canonical(self):
        minions = self._offers(MINIONS)[0]
        self.assertEqual(minions.audio_languages, ["EN", "NL"])
        self.assertEqual(minions.subtitle_languages, ["NL"])
        hangover = self._offers(THE_HANGOVER)[0]
        # Display qualifiers ("English [CC]", "Dialogue Boost: ...") are
        # stripped before normalization.
        self.assertEqual(hangover.audio_languages, ["EN", "IT"])
        self.assertEqual(
            hangover.subtitle_languages,
            ["EN", "NL", "DE", "ES", "FI", "FR", "IT", "NB", "SV"],
        )
        # A language the canonical list does not know keeps its own name,
        # uppercased — the same fallback CBC Gem and BBC iPlayer report.
        self.assertIn("CATALÀ", self._offers(LOVE_HYPOTHESIS)[0].subtitle_languages)

    def test_season_offers_inherit_the_first_episode_languages(self):
        # Season records ship no language list at all (``None``, never ``[]``),
        # so season and series offers fall back to the first episode's.
        season = self._offers(STERLING_POINT, ContentType.SEASON)[0]
        episode = self._offers(STERLING_POINT, ContentType.EPISODE)[0]
        self.assertIsNotNone(season.audio_languages)
        self.assertEqual(season.audio_languages, episode.audio_languages)
        self.assertEqual(season.subtitle_languages, episode.subtitle_languages)

    def test_record_ids_and_urls_are_stable_per_granularity(self):
        handmaid_season = self._offers(HANDMAIDS_SHOW, ContentType.SEASON)[0]
        self.assertEqual(
            handmaid_season.source_record_id, f"{HANDMAIDS_SHOW}/s6"
        )
        self.assertEqual(handmaid_season.provider_url, detail_url(HANDMAIDS_TALE))

        handmaid_episode = self._offers(HANDMAIDS_SHOW, ContentType.EPISODE)[0]
        self.assertEqual(
            handmaid_episode.source_record_id, f"{HANDMAIDS_SHOW}/s6/e1"
        )
        # Episode URLs come from the page's own episode list.
        self.assertEqual(
            handmaid_episode.provider_url,
            detail_url("0RGYBAJ9OWY9LT3FI0YQQWXF1Q"),
        )

        series = self._offers(HANDMAIDS_SHOW, ContentType.SERIES)[0]
        self.assertEqual(series.source_record_id, HANDMAIDS_SHOW)
        self.assertEqual(series.provider_url, detail_url(HANDMAIDS_SHOW))
        movie = self._offers(MINIONS)[0]
        self.assertEqual(movie.source_record_id, MINIONS)
        self.assertEqual(movie.provider_url, detail_url(MINIONS))

    def test_every_offer_carries_nl_provenance_and_no_lifecycle(self):
        for offer in self.result.offers:
            self.assertEqual(offer.country, "NL")
            self.assertEqual(offer.provider_id, "prime")
            self.assertEqual(offer.source_version, "prime-ssr-v1")
            self.assertIsNone(offer.tmdb_id)
            self.assertIsNone(offer.available_from)
            self.assertIsNone(offer.available_until)
            self.assertTrue(offer.source_record_id)


# ------------------------------------------------------------- cue interpretation


class CueTests(SimpleTestCase):
    def test_the_card_cue_decides_when_the_detail_page_publishes_no_message(self):
        # isPrime is False on this page, so the cue is the only signal: the
        # card's "Available to buy" must turn 20 add-on/transactional offers
        # into buy-only offers.
        source, api = single_title_source(
            EUPHORIA,
            cue="Available to buy",
            detail_html=detail_without_message(load(f"detail_{EUPHORIA}.html")),
            max_contents=FIXTURE_WINDOW,
        )
        result = source.fetch()
        # series + season + 8 episodes, one buy row each.
        self.assertEqual(len(result.offers), 10)
        self.assertEqual(
            {offer.offer_type for offer in result.offers}, {OfferType.BUY}
        )

    def test_the_detail_message_wins_over_the_card_cue(self):
        source, api = single_title_source(
            EUPHORIA,
            cue="Available to buy",
            detail_html=detail_with_message(
                load(f"detail_{EUPHORIA}.html"), "Available to rent or buy"
            ),
            max_contents=FIXTURE_WINDOW,
        )
        result = source.fetch()
        self.assertEqual(
            {offer.offer_type for offer in result.offers},
            {OfferType.RENT, OfferType.BUY},
        )

    def test_first_episode_free_is_reported_as_a_free_offer(self):
        source, api = single_title_source(
            MINIONS,
            detail_html=detail_with_message(
                load(f"detail_{MINIONS}.html"), "First episode free"
            ),
            max_contents=FIXTURE_WINDOW,
        )
        result = source.fetch()
        self.assertEqual(len(result.offers), 1)
        self.assertEqual(result.offers[0].offer_type, OfferType.FREE)

    def test_a_title_with_no_derivable_signal_keeps_one_unknown_offer(self):
        source, api = single_title_source(
            MINIONS,
            detail_html=detail_with_message(load(f"detail_{MINIONS}.html"), ""),
            max_contents=FIXTURE_WINDOW,
        )
        result = source.fetch()
        self.assertEqual(len(result.offers), 1)
        self.assertEqual(result.offers[0].offer_type, OfferType.UNKNOWN)

    def test_pre_order_wording_records_a_purchase_offer(self):
        # Live storefront vocabulary the first captures did not contain: an
        # unreleased title answers "Available to pre-order" while its own buy
        # action reads "Pre-order movie UHD €17.99" — a purchase, recorded
        # like rent/buy rather than left as `unknown`.
        source, api = single_title_source(SPIDER_MAN, max_contents=FIXTURE_WINDOW)
        result = source.fetch()
        self.assertEqual(api.detail_requests, [detail_url(SPIDER_MAN)])
        content = result.contents[0]
        self.assertEqual(content.content_type, ContentType.MOVIE)
        self.assertEqual(content.title, "Spider-Man: Brand New Day")
        self.assertEqual(content.release_date, dt.date(2026, 7, 30))
        self.assertEqual(len(result.offers), 1)
        offer = result.offers[0]
        self.assertEqual(offer.offer_type, OfferType.BUY)
        # ``isPrime`` is False on this page, so the wording is the only signal.
        self.assertNotIn(OfferType.SUBSCRIPTION, {offer.offer_type})
        self.assertEqual(offer.quality, "4K")
        # Empty language lists mean "not published here", never ``[]``.
        self.assertIsNone(offer.audio_languages)
        self.assertIsNone(offer.subtitle_languages)
        # The page's release-date message is prose, not an offer window.
        self.assertIsNone(offer.available_from)
        self.assertIsNone(offer.available_until)

    def test_a_title_only_walk_reads_exactly_one_detail_page(self):
        source, api = single_title_source(MAXTON_HALL, max_contents=FIXTURE_WINDOW)
        result = source.fetch()
        self.assertEqual(api.detail_requests, [detail_url(MAXTON_HALL)])
        self.assertEqual(
            [content.provider_content_id for content in result.contents],
            [MAXTON_SHOW],
        )
        # Observed season 2 of a two-season show: the identity still resolves
        # to season 1, the show's canonical id.
        seasons = [
            offer for offer in result.offers
            if offer.content_type == ContentType.SEASON
        ]
        self.assertEqual([offer.season_number for offer in seasons], [2])
        self.assertEqual(
            {offer.provider_content_id for offer in result.offers},
            {MAXTON_SHOW},
        )


# ------------------------------------------------------------------- persistence


class PrimeVideoRefreshTests(TestCase):
    """prime_video through AvailabilityRefreshService (fixture API, no network)."""

    def setUp(self):
        self.api = FakePrimeApi()
        self.source = PrimeVideoSource(
            client=PrimeVideoClient(requester=self.api), max_contents=FIXTURE_WINDOW
        )
        self.service = AvailabilityRefreshService(source=self.source)
        # ``Provider(slug="prime")`` comes from the provider seed migration and
        # ``Country(code="NL")`` from the Phase 11 country seed.
        self.provider = Provider.objects.get(slug="prime")
        self.minions = Movie.objects.create(
            tmdb_id=9101, title="Minions & Monsters", year=2026, content_type="movie"
        )
        self.hangover = Movie.objects.create(
            tmdb_id=9102, title="The Hangover", year=2009, content_type="movie"
        )
        self.sterling = Movie.objects.create(
            tmdb_id=9103, title="Sterling Point", content_type="series"
        )

    def test_the_declared_scope_is_seeded_for_the_refresh_to_persist_into(self):
        self.assertTrue(Country.objects.filter(code="NL", enabled=True).exists())
        self.assertTrue(Provider.objects.filter(slug="prime", is_enabled=True).exists())

    def test_offers_persist_under_their_own_provenance_and_the_shared_provider(self):
        result = self.service.refresh_catalogue()

        self.assertEqual(result.discovered, TOTAL_OFFERS)
        # 13 of 50 offers resolve locally: Minions 1 + Hangover 2 + Sterling 10.
        self.assertEqual(result.created, 13)
        self.assertEqual(result.skipped, 37)
        self.assertEqual(result.deactivated, 0)
        self.assertEqual(Offer.objects.count(), 13)

        for offer in Offer.objects.select_related("source", "provider", "country"):
            self.assertEqual(offer.source.slug, "prime_video")
            self.assertEqual(offer.provider, self.provider)
            self.assertEqual(offer.country.code, "NL")
            self.assertTrue(offer.currently_available)

        # The canonical identity is provider-keyed: provenance (Source) stays
        # out of it, so two sources describing one provider content converge.
        movie_offer = Offer.objects.get(
            content_type=ContentType.MOVIE, provider_content_id=MINIONS
        )
        self.assertEqual(
            movie_offer.identity,
            f"prime|NL|movie|9101|||buy|{MINIONS}",
        )
        self.assertEqual(movie_offer.tmdb_id, 9101)
        self.assertEqual(
            {offer.tmdb_id for offer in Offer.objects.filter(
                provider_content_id=STERLING_POINT)},
            {9103},
        )
        self.assertEqual(
            {offer.tmdb_id for offer in Offer.objects.filter(
                provider_content_id=THE_HANGOVER)},
            {9102},
        )

    def test_discovery_is_kept_even_when_nothing_maps(self):
        self.service.refresh_catalogue()

        # All eight observed titles persist as provider-native rows, including
        # the five the local catalog does not know.
        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)
        self.assertTrue(
            ProviderContent.objects.filter(
                provider=self.provider, provider_content_id=IN_THE_GREY
            ).exists()
        )
        self.assertFalse(
            Offer.objects.filter(provider_content_id=IN_THE_GREY).exists()
        )
        self.assertEqual(ContentMapping.objects.count(), 3)

    def test_mappings_resolve_with_tiered_confidence(self):
        with self.assertLogs("availability.services.refresh", level="WARNING") as logs:
            self.service.refresh_catalogue()

        minions = ContentMapping.objects.get(
            provider=self.provider, provider_content_id=MINIONS
        )
        self.assertEqual(minions.tmdb_id, 9101)
        self.assertEqual(minions.confidence, 0.9)
        self.assertEqual(minions.match_method, MatchMethod.TITLE_YEAR)
        self.assertEqual(minions.content_type, ContentType.MOVIE)
        # Series carry no provider year -> title-only tier.
        sterling = ContentMapping.objects.get(
            provider=self.provider, provider_content_id=STERLING_POINT
        )
        self.assertEqual(sterling.confidence, 0.7)
        self.assertEqual(sterling.content_type, ContentType.SERIES)
        self.assertTrue(
            any("availability_mapping_unresolved" in line
                and IN_THE_GREY in line for line in logs.output)
        )

    def test_sync_run_records_one_full_nl_run_from_this_source(self):
        result = self.service.refresh_catalogue()

        self.assertEqual(len(result.sync_runs), 1)
        run = result.sync_runs[0]
        run.refresh_from_db()
        self.assertEqual(run.status, SyncStatus.SUCCEEDED)
        self.assertEqual(run.provider, self.provider)
        self.assertEqual(run.country.code, "NL")
        self.assertEqual(run.source.slug, "prime_video")
        self.assertEqual(run.mode, "full")
        self.assertFalse(run.authoritative)
        self.assertEqual(run.items_discovered, 13)
        self.assertEqual(run.offers_created, 13)
        self.assertEqual(run.offers_deactivated, 0)
        self.assertEqual(SyncRun.objects.count(), 1)
        self.assertEqual(Source.objects.get(slug="prime_video").name, "Prime Video")
        # The storefront exposes no cursor to resume from.
        self.assertEqual(SyncCursor.objects.count(), 0)

    def test_a_second_run_is_idempotent(self):
        first = self.service.refresh_catalogue()
        second = self.service.refresh_catalogue()

        self.assertEqual(first.created, 13)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 13)
        self.assertEqual(Offer.objects.count(), 13)
        self.assertEqual(ContentMapping.objects.count(), 3)
        self.assertEqual(SyncRun.objects.count(), 2)

    def test_prime_video_provenance_stays_distinct_from_streaming_availability(self):
        self.service.refresh_catalogue()

        streaming_availability = StubSource(
            slug="streaming_availability",
            response=fetch_result(
                normalized_offer(
                    provider_id="prime",
                    provider_content_id="sa-catalog-42",
                    country="NL",
                    tmdb_id=9101,
                    offer_type=OfferType.SUBSCRIPTION,
                )
            ),
        )
        AvailabilityRefreshService(source=streaming_availability).refresh_content(
            self.minions
        )

        self.assertEqual(Offer.objects.count(), 14)
        self.assertEqual(
            {offer.source.slug for offer in Offer.objects.all()},
            {"prime_video", "streaming_availability"},
        )
        # §7: one canonical Provider row shared by every source.
        self.assertEqual(Provider.objects.filter(slug="prime").count(), 1)

    def test_a_fetch_failure_writes_nothing_at_all(self):
        self.api.failures[STORE_URL] = SourceFetchError("network down")

        with self.assertRaises(SourceFetchError):
            self.service.refresh_catalogue()

        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        self.assertFalse(Source.objects.filter(slug="prime_video").exists())

    def test_targeted_refresh_against_a_catalogue_source_fails_loudly(self):
        with self.assertRaisesMessage(ValueError, "catalogue-driven"):
            self.service.refresh_content(self.minions)

    def test_unknown_scope_hint_is_an_error_not_a_silent_noop(self):
        with self.assertRaisesMessage(ValueError, "provider"):
            self.service.refresh_catalogue(provider="netflix")
        with self.assertRaisesMessage(ValueError, "country"):
            self.service.refresh_catalogue(country="US")
