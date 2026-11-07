"""BBC iPlayer source: catalogue parsing, pagination, failure semantics.

Fixture-based (``bbc_fixtures.FakeBBCIPlayer``): no test performs network I/O and
no test touches the database (``SimpleTestCase`` doubles as a guard — the
source must stay persistence-free). The refresh tests are the counterpart and
run against a real database.

Fixture catalogue (recorded payloads):

    A-Z letters  0-9, d..l, o..w, y   empty (entities: [])
                 a  [abadas]          pagination.totalPages 2 -> truncated
                 b  [bobs-world]
                 c  [comic-relief]
                 e  [eastenders]
                 f  [folk-the-world]
                 m, s, t              truncated, no entities
                 n  [nickel-boys]
                 x  live card + href-less card -> both skipped
                 z  no `entities` key at all

    abadas         series, slices "Series 1" (2 eps) + "Series 2" (1 ep)
                   -> 6 offers (series + 2 seasons + 3 episodes)
    bobs-world     series, slice "Specials", 1 unnumbered special -> 1 offer
    comic-relief   movie, no versions at all                      -> 0 offers
    eastenders     series, slice "Episodes", 4 dated soaps over 2 pages
                   -> 1 offer (numbering never invented)
    folk-the-world series, slice "Series 1", 2 eps over 2 pages
                   -> 4 offers (series + season + 2 episodes)
    nickel-boys    movie, original version with an availability window
                   -> 1 offer

    totals: 6 contents (4 series, 2 movies), 13 offers
"""
import datetime as dt

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
    BBCIPlayerSource,
    ContentRequest,
    SourceFetchError,
    source_registry,
)
from availability.sources.bbc_iplayer import (
    BBCIPlayerClient,
    SOURCE_VERSION,
    STATE_MARKER,
    _decode_cursor,
)
from tracker.models import Movie, Provider
from tracker.services.bulk_refresh import (
    _catalogue_source_slugs,
    _ensure_declared_countries,
)

from .bbc_fixtures import FakeBBCIPlayer, load_pages

TOTAL_CONTENTS = 6
TOTAL_OFFERS = 13
MAPPED_OFFERS = 7      # abadas 6 + nickel-boys 1
UNMAPPED_OFFERS = 6    # bobs-world 1 + eastenders 1 + folk-the-world 4
BRAND_ABADAS = "/iplayer/episodes/b01qm16p"     # fixture key (pid-based URL)
BRAND_ABADAS_HREF = "/iplayer/episodes/b01qm16p/abadas"  # A-Z href -> provider_url
PAGE_FOLK = "/iplayer/episodes/m000ffff1"
PAGE_EE = "/iplayer/episodes/b006m86d"
PAGE_NICKEL = "/iplayer/episode/m00327ht"
FOLK_PAGE_2 = f"{PAGE_FOLK}?seriesId=m000ffff1-structural-1&page=2"


def build_source(api=None, *, batch_size=None, delay=0.0):
    api = api or FakeBBCIPlayer()
    kwargs = {} if batch_size is None else {"batch_size": batch_size}
    source = BBCIPlayerSource(
        client=BBCIPlayerClient(requester=api, delay=delay), **kwargs
    )
    return source, api


def _offer_key(offer):
    """A comparable identity for a :class:`ProviderOffer` (it has no ``eq`` key)."""
    return (
        offer.provider_content_id,
        offer.content_type,
        offer.season_number,
        offer.episode_number,
        offer.offer_type,
        offer.provider_url,
    )


class RegistrationTests(SimpleTestCase):
    def test_bbc_iplayer_source_is_registered(self):
        source = source_registry.get("bbc_iplayer")
        self.assertIsInstance(source, BBCIPlayerSource)
        self.assertEqual(source.slug, "bbc_iplayer")
        self.assertEqual(source.source_type, SourceType.WEB)

    def test_capabilities_declare_catalogue_non_authoritative_scoped_source(self):
        capabilities = source_registry.get("bbc_iplayer").capabilities
        self.assertEqual(capabilities.supported_providers, frozenset({"bbc_iplayer"}))
        self.assertEqual(capabilities.supported_countries, frozenset({"GB"}))
        self.assertEqual(
            capabilities.supported_content_types,
            frozenset({ContentType.MOVIE, ContentType.SERIES, ContentType.SEASON,
                       ContentType.EPISODE}),
        )
        self.assertTrue(capabilities.supports_catalogue_sync)
        # The A-Z index is capped at 200 entries per letter: absence is never
        # evidence, so unseen offers must never be deactivated.
        self.assertFalse(capabilities.supports_authoritative_snapshots)
        self.assertFalse(capabilities.supports_incremental_sync)
        # iPlayer publishes no external ids and no language metadata.
        self.assertFalse(capabilities.supports_tmdb_ids)
        self.assertFalse(capabilities.supports_languages)
        self.assertTrue(capabilities.supports_seasons)
        self.assertTrue(capabilities.supports_episodes)

    def test_targeted_content_request_is_rejected(self):
        source, _api = build_source()
        with self.assertRaisesMessage(ValueError, "catalogue-driven"):
            source.fetch(ContentRequest(tmdb_id=550, title="Fight Club"))

    def test_fetch_without_content_walks_the_catalogue(self):
        source, _api = build_source()
        result = source.fetch()
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)
        self.assertEqual(len(result.offers), TOTAL_OFFERS)


class SnapshotSemanticsTests(SimpleTestCase):
    def test_result_never_claims_authoritativeness_or_cursor(self):
        source, _api = build_source()
        result = source.fetch()
        self.assertFalse(result.authoritative)
        self.assertFalse(result.complete_snapshot)
        self.assertEqual(result.sync_mode, SyncMode.FULL)
        # The pages expose letter and slice coordinates, not a cursor token.
        self.assertIsNone(result.cursor)


class CatalogueParsingTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source, cls.api = build_source()
        cls.result = cls.source.fetch()

    def _offers(self, pid, content_type=None):
        offers = [o for o in self.result.offers if o.provider_content_id == pid]
        if content_type is not None:
            offers = [o for o in offers if o.content_type == content_type]
        return offers

    def _content(self, pid):
        return next(c for c in self.result.contents if c.provider_content_id == pid)

    def test_every_catalogue_item_becomes_a_content_record(self):
        self.assertEqual(
            {c.provider_content_id for c in self.result.contents},
            {"b01qm16p", "m000bbbb1", "m000cccc1", "b006m86d", "m000ffff1",
             "m00327ht"},
        )

    def test_brand_is_series_and_single_programme_is_movie(self):
        self.assertEqual(self._content("b01qm16p").content_type, ContentType.SERIES)
        self.assertEqual(self._content("m00327ht").content_type, ContentType.MOVIE)

    def test_offer_granularity_counts(self):
        counts = {}
        for offer in self.result.offers:
            counts[offer.content_type] = counts.get(offer.content_type, 0) + 1
        self.assertEqual(counts, {
            ContentType.SERIES: 4,  # every brand with at least one episode
            ContentType.MOVIE: 1,   # nickel-boys (comic-relief has no version)
            ContentType.SEASON: 3,  # abadas 1-2, folk-the-world 1
            ContentType.EPISODE: 5, # abadas 3, folk-the-world 2
        })

    def test_movie_offer_fields(self):
        offer = self._offers("m00327ht", ContentType.MOVIE)[0]
        self.assertEqual(offer.provider_id, "bbc_iplayer")
        self.assertEqual(offer.country, "GB")
        self.assertIsNone(offer.tmdb_id)      # no external ids at the source
        self.assertIsNone(offer.season_number)
        self.assertIsNone(offer.episode_number)
        self.assertEqual(offer.offer_type, OfferType.FREE)
        self.assertEqual(
            offer.provider_url,
            "https://www.bbc.co.uk/iplayer/episode/m00327ht/nickel-boys",
        )
        self.assertEqual(offer.quality, "")
        self.assertIsNone(offer.audio_languages)   # no language payload at all
        self.assertIsNone(offer.subtitle_languages)
        self.assertEqual(
            offer.available_from, dt.datetime(2026, 9, 29, 0, 16, 21,
                                              tzinfo=dt.timezone.utc)
        )
        self.assertEqual(
            offer.available_until, dt.datetime(2026, 11, 28, 1, 16, 21,
                                               tzinfo=dt.timezone.utc)
        )
        self.assertEqual(offer.source_record_id, "m00327ht")
        self.assertEqual(offer.source_version, SOURCE_VERSION)

    def test_release_date_is_the_programme_release_not_the_window(self):
        # releaseDateTime 2024 vs a window that only opened in 2026.
        self.assertEqual(
            self._content("m00327ht").release_date, dt.date(2024, 1, 1)
        )

    def test_series_release_date_is_none(self):
        # Brand pages publish no first-air year, so series resolve title-only.
        for content in self.result.contents:
            if content.content_type == ContentType.SERIES:
                self.assertIsNone(content.release_date, content.provider_content_id)

    def test_every_offer_attaches_to_a_discovered_content(self):
        # Offers must carry the *title's* pid or the refresh service can never
        # find a ContentMapping for them.
        discovered = {c.provider_content_id for c in self.result.contents}
        for offer in self.result.offers:
            self.assertIn(offer.provider_content_id, discovered)

    def test_series_season_and_episode_rows_share_the_brand_pid(self):
        show = self._offers("b01qm16p", ContentType.SERIES)[0]
        seasons = self._offers("b01qm16p", ContentType.SEASON)
        episodes = self._offers("b01qm16p", ContentType.EPISODE)
        self.assertIsNone(show.season_number)
        self.assertEqual({s.season_number for s in seasons}, {1, 2})
        self.assertEqual(
            show.provider_url, "https://www.bbc.co.uk" + BRAND_ABADAS_HREF
        )
        self.assertEqual(
            show.source_record_id, "b01qm16p",
        )
        season_2 = next(s for s in seasons if s.season_number == 2)
        self.assertEqual(
            season_2.provider_url,
            "https://www.bbc.co.uk" + BRAND_ABADAS_HREF
            + "?seriesId=b01qm16p-structural-2-b01qlgnf",
        )
        first_episode = next(e for e in episodes
                             if e.season_number == 1 and e.episode_number == 1)
        # Episodes link to their own canonical page but stay keyed by brand pid.
        self.assertEqual(
            first_episode.provider_url,
            "https://www.bbc.co.uk/iplayer/episode/b016kd5d",
        )
        self.assertEqual(first_episode.source_record_id, "b016kd5d")

    def test_brand_episode_windows_come_from_the_version_end_only(self):
        for offer in self.result.offers:
            if offer.content_type != ContentType.EPISODE:
                continue
            self.assertIsNone(offer.available_from, offer.source_record_id)
            self.assertIsNotNone(offer.available_until, offer.source_record_id)
        for offer in self._offers("b01qm16p"):
            if offer.content_type is ContentType.SERIES:
                self.assertIsNone(offer.available_from)
                self.assertIsNone(offer.available_until)

    def test_slice_position_falls_back_when_the_default_cannot_carry_the_season(
        self,
    ):
        # folk-the-world's episode subtitle has no "Series N:" prefix; the
        # slice title supplies the season and subtitle.slice the position.
        episodes = self._offers("m000ffff1", ContentType.EPISODE)
        self.assertEqual(
            {(e.season_number, e.episode_number) for e in episodes},
            {(1, 1), (1, 2)},
        )

    def test_unparseable_numbering_is_skipped_never_invented(self):
        # Dated soap instalments and a "Christmas Special" carry no position:
        # the episodes stay reachable through the series-level offer only.
        self.assertEqual(
            [o.content_type for o in self._offers("b006m86d")],
            [ContentType.SERIES],
        )
        self.assertEqual(
            [o.content_type for o in self._offers("m000bbbb1")],
            [ContentType.SERIES],
        )

    def test_a_slice_that_is_not_a_season_never_becomes_one(self):
        # "Specials" (and the unparseable numbering above) produce neither a
        # season nor an episode row.
        self.assertNotIn(
            ContentType.SEASON,
            {o.content_type for o in self._offers("m000bbbb1")},
        )

    def test_single_programme_without_versions_is_content_without_offers(self):
        # Listed on the index but nothing playable yet: a catalogue entry, not
        # an offer — and never a fetch failure.
        self.assertIn("m000cccc1", {c.provider_content_id
                                    for c in self.result.contents})
        self.assertEqual(self._offers("m000cccc1"), [])

    def test_every_offer_is_free_and_carries_no_language_claim(self):
        for offer in self.result.offers:
            self.assertEqual(offer.offer_type, OfferType.FREE,
                             offer.source_record_id)
            self.assertIsNone(offer.audio_languages, offer.source_record_id)
            self.assertIsNone(offer.subtitle_languages, offer.source_record_id)
            self.assertEqual(offer.quality, "", offer.source_record_id)

    def test_poster_recipe_placeholder_is_resolved(self):
        self.assertEqual(
            self._content("b01qm16p").poster,
            "https://ichef.bbci.co.uk/images/ic/640x360/p017mqg6.jpg",
        )
        self.assertEqual(
            self._content("m00327ht").poster,
            "https://ichef.bbci.co.uk/images/ic/640x360/p0pb2b09.jpg",
        )

    def test_description_is_the_short_synopsis(self):
        self.assertEqual(
            self._content("m00327ht").description,
            "Young Elwood Curtis finds himself in the brutal regime of a "
            "Florida reform school.",
        )
        self.assertEqual(
            self._content("b01qm16p").description,
            "Pop-up book adventures with the curious Abadas",
        )


class PaginationTests(SimpleTestCase):
    def test_every_slice_page_of_a_brand_is_walked(self):
        source, api = build_source()
        source.fetch()
        self.assertEqual(
            [url for url in api.requests
             if url.startswith("https://www.bbc.co.uk" + PAGE_EE)],
            [
                "https://www.bbc.co.uk" + PAGE_EE,
                "https://www.bbc.co.uk" + PAGE_EE + "?seriesId=unsliced&page=2",
            ],
        )
        self.assertEqual(
            len([u for u in api.requests
                 if u.startswith("https://www.bbc.co.uk" + PAGE_FOLK)]),
            2,
        )

    def test_page_two_results_are_consumed(self):
        source, api = build_source()
        result = source.fetch()
        # folk-the-world's second page holds the second numbered episode.
        self.assertIn(
            "m000ffff3",
            {o.source_record_id for o in result.offers
             if o.provider_content_id == "m000ffff1"},
        )

    def test_a_non_default_slice_is_fetched_with_its_series_id(self):
        source, api = build_source()
        source.fetch()
        requested = api.urls_containing(BRAND_ABADAS)
        self.assertEqual(len(requested), 2)
        self.assertIn("seriesId=b01qm16p-structural-2-b01qlgnf&page=1",
                      requested[1])

    def test_a_z_is_never_probed_beyond_page_one(self):
        # The origin ignores `page` on /iplayer/a-z/*: probing it would look
        # like pagination while silently returning byte-identical results.
        source, api = build_source()
        with self.assertLogs("availability.sources.bbc_iplayer",
                             level="WARNING") as logs:
            source.fetch()
        a_z_urls = [u for u in api.requests if "/iplayer/a-z/" in u]
        self.assertEqual(len(a_z_urls), 27)
        for url in a_z_urls:
            self.assertNotIn("?", url)
        truncated = [line for line in logs.output
                     if "bbc_iplayer_letter_truncated" in line]
        self.assertTrue(any("letter=a" in line for line in truncated))
        self.assertTrue(any("page=1 of 2" in line for line in truncated))

    def test_slice_walk_stops_when_upstream_page_does_not_advance(self):
        api = FakeBBCIPlayer()
        pages = load_pages()
        # The origin clamps our page=2 request back to page 1 (same results,
        # currentPage never advancing): the walk must stop, not loop.
        clamped = pages[PAGE_FOLK]
        clamped["pagination"]["currentPage"] = 1
        clamped["page"]["seriesPagination"]["page"] = 1
        pages[FOLK_PAGE_2] = clamped
        api.pages = pages
        source, _api = build_source(api)

        with self.assertLogs("availability.sources.bbc_iplayer",
                             level="WARNING") as logs:
            result = source.fetch()

        self.assertTrue(any("bbc_iplayer_slice_stalled" in line
                            for line in logs.output))
        # Requested once, parsed once, terminated instead of looping.
        self.assertEqual(
            len([u for u in api.requests
                 if u.startswith("https://www.bbc.co.uk" + PAGE_FOLK)]),
            2,
        )
        self.assertEqual(
            {(o.season_number, o.episode_number)
             for o in result.offers if o.provider_content_id == "m000ffff1"
             and o.content_type == ContentType.EPISODE},
            {(1, 1)},
        )

    def test_missing_slice_page_fails_loudly_instead_of_looking_empty(self):
        api = FakeBBCIPlayer()
        del api.pages[FOLK_PAGE_2]
        source, _api = build_source(api)
        with self.assertRaises(SourceFetchError):
            source.fetch()

    def test_transport_failure_mid_run_is_not_an_empty_result(self):
        api = FakeBBCIPlayer()
        api.failures["seriesId="] = requests.Timeout("too slow")
        source, _api = build_source(api)
        with self.assertRaises(SourceFetchError):
            source.fetch()

    def test_unknown_letter_fails_loudly_instead_of_looking_empty(self):
        api = FakeBBCIPlayer()
        del api.atoz["q"]
        source, _api = build_source(api)
        with self.assertRaises(SourceFetchError):
            source.fetch()


class UnsupportedPayloadTests(SimpleTestCase):
    def test_brand_page_without_a_header_is_rejected(self):
        api = FakeBBCIPlayer()
        del api.pages[BRAND_ABADAS]["header"]
        source, _api = build_source(api)
        with self.assertRaises(SourceFetchError):
            source.fetch()

    def test_programme_page_without_an_episode_is_rejected(self):
        api = FakeBBCIPlayer()
        del api.pages[PAGE_NICKEL]["episode"]
        source, _api = build_source(api)
        with self.assertRaises(SourceFetchError):
            source.fetch()

    def test_page_without_an_embedded_state_is_rejected(self):
        source = BBCIPlayerSource(
            client=BBCIPlayerClient(
                requester=lambda url: "<html><body>gone</body></html>"
            )
        )
        with self.assertRaises(SourceFetchError):
            source.fetch()

    def test_malformed_state_json_is_rejected(self):
        source = BBCIPlayerSource(
            client=BBCIPlayerClient(
                requester=lambda url: f"{STATE_MARKER} = {{not json}};"
            )
        )
        with self.assertRaises(SourceFetchError):
            source.fetch()

    def test_non_string_response_is_rejected(self):
        source = BBCIPlayerSource(client=BBCIPlayerClient(requester=lambda url: None))
        with self.assertRaises(SourceFetchError):
            source.fetch()

    def test_unsupported_and_hrefless_index_cards_are_skipped(self):
        # Letter x lists a live channel and an editorial card with no item
        # link: neither is a catalogue entry, and neither is fetched.
        source, api = build_source()
        result = source.fetch()
        self.assertFalse(
            any("p0xxxxx1" in c.provider_content_id for c in result.contents)
        )
        self.assertEqual(api.urls_containing("/iplayer/live/"), [])
        self.assertEqual(api.urls_containing("p0xxxxx1"), [])

    def test_missing_title_is_skipped_not_invented(self):
        api = FakeBBCIPlayer()
        api.pages[PAGE_NICKEL]["episode"]["title"] = ""
        # The A-Z card is the only other place a title could come from.
        api.atoz["n"]["programmes"]["n"]["entities"][0]["props"]["title"] = ""
        source, _api = build_source(api)
        result = source.fetch()
        self.assertNotIn(
            "m00327ht", {c.provider_content_id for c in result.contents}
        )
        self.assertNotIn(
            "m00327ht", {o.provider_content_id for o in result.offers}
        )
        self.assertEqual(len(result.contents), TOTAL_CONTENTS - 1)
        self.assertEqual(len(result.offers), TOTAL_OFFERS - 1)


class DeduplicationTests(SimpleTestCase):
    def test_the_same_pid_in_two_letters_is_fetched_once(self):
        api = FakeBBCIPlayer()
        api.atoz["b"]["programmes"]["b"]["entities"].append(
            {"props": {"href": BRAND_ABADAS, "title": "Abadas"},
             "meta": {"episodesAvailable": 3}}
        )
        source, _api = build_source(api)
        result = source.fetch()

        # 2 requests for abadas: the brand page and its second slice — not one
        # brand page per index appearance.
        self.assertEqual(len(api.urls_containing("/iplayer/episodes/b01qm16p")), 2)
        self.assertEqual(
            [c.provider_content_id for c in result.contents].count("b01qm16p"), 1
        )
        self.assertEqual(len(result.offers), TOTAL_OFFERS)


class CheckpointWalkTests(SimpleTestCase):
    """A bounded batch checkpoints and resumes instead of restarting the A-Z."""

    def test_bounded_batch_checkpoints_and_resumes(self):
        api = FakeBBCIPlayer()
        source = build_source(api, batch_size=2)[0]

        first = source.fetch()
        checkpoint = _decode_cursor(first.cursor)
        self.assertEqual(checkpoint["letter"], "c")
        self.assertEqual(len(first.contents), 2)
        # The walk stopped long before the later letters were reached.
        self.assertFalse(api.urls_containing(PAGE_EE))
        self.assertFalse(api.urls_containing(PAGE_FOLK))

        second = source.fetch(cursor=first.cursor)
        self.assertEqual(
            _decode_cursor(second.cursor)["letter"], "f",
            "resuming continues past the checkpointed entry into later letters",
        )
        # Resuming continued into eastenders without refetching a..b.
        self.assertTrue(api.urls_containing(PAGE_EE))
        self.assertNotIn(
            "b01qm16p", {c.provider_content_id for c in second.contents}
        )

    def test_resumption_sums_to_the_full_catalogue_without_duplicates(self):
        full = build_source(FakeBBCIPlayer())[0].fetch()

        source = build_source(FakeBBCIPlayer(), batch_size=2)[0]
        contents, offers, cursor = [], [], None
        for _ in range(30):
            result = source.fetch(cursor=cursor)
            contents.extend(result.contents)
            offers.extend(result.offers)
            cursor = result.cursor
            if cursor is None:
                break

        self.assertEqual(
            [c.provider_content_id for c in contents],
            [c.provider_content_id for c in full.contents],
        )
        self.assertEqual(
            sorted(_offer_key(o) for o in offers),
            sorted(_offer_key(o) for o in full.offers),
        )

    def test_a_late_detail_failure_fails_the_batch_and_is_retried(self):
        api = FakeBBCIPlayer()
        api.failures[PAGE_FOLK] = SourceFetchError("outage")
        source = build_source(api, batch_size=6)[0]

        # A page that cannot be read mid-batch fails the whole fetch: never a
        # smaller successful run.
        with self.assertRaises(SourceFetchError):
            source.fetch()

        # The retry resumes at the letter that failed and completes the walk.
        del api.failures[PAGE_FOLK]
        retry = source.fetch(cursor='{"v": 1, "letter": "f", "after": ""}')
        self.assertIn("m000ffff1", {c.provider_content_id for c in retry.contents})
        self.assertIsNone(retry.cursor)

    def test_a_failure_before_any_progress_is_still_a_failure(self):
        api = FakeBBCIPlayer()
        api.failures[BRAND_ABADAS] = SourceFetchError("outage")
        with self.assertRaises(SourceFetchError):
            build_source(api, batch_size=6)[0].fetch()

    def test_unreadable_or_stale_cursor_restarts_safely(self):
        api = FakeBBCIPlayer()
        source = build_source(api, batch_size=2)[0]
        self.assertIsNotNone(source.fetch(cursor="not json").cursor)
        # A checkpoint entry that vanished from the letter restarts that
        # letter rather than skipping ahead.
        stale = source.fetch(cursor='{"v": 1, "letter": "e", "after": "gone"}')
        self.assertIsNotNone(stale.cursor)

    def test_a_completed_walk_returns_no_cursor(self):
        api = FakeBBCIPlayer()
        source = build_source(api, batch_size=3)[0]
        cursor = None
        for _ in range(30):
            result = source.fetch(cursor=cursor)
            cursor = result.cursor
            if cursor is None:
                break
        self.assertIsNone(cursor)
        final = source.fetch(cursor='{"v": 1, "letter": "z", "after": ""}')
        self.assertEqual(final.contents, [])
        self.assertIsNone(final.cursor)


class BBCIPlayerRefreshTestCase(TestCase):
    def setUp(self):
        self.api = FakeBBCIPlayer()
        self.source = BBCIPlayerSource(client=BBCIPlayerClient(requester=self.api))
        self.service = AvailabilityRefreshService(source=self.source)
        self.provider, _ = Provider.objects.get_or_create(
            slug="bbc_iplayer", defaults={"name": "BBC iPlayer"}
        )
        Country.objects.get_or_create(code="GB", defaults={"name": "United Kingdom"})

        self.nickel = Movie.objects.create(
            tmdb_id=9101, title="Nickel Boys", year=2024, content_type="movie"
        )
        self.abadas = Movie.objects.create(
            tmdb_id=9102, title="Abadas", content_type="series"
        )

    def _mapping(self, provider_content_id):
        return ContentMapping.objects.get(
            provider=self.provider, provider_content_id=provider_content_id
        )


class EndToEndRefreshTests(BBCIPlayerRefreshTestCase):
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
            {o.tmdb_id for o in Offer.objects.filter(provider_content_id="b01qm16p")},
            {9102},
        )
        self.assertEqual(
            {o.tmdb_id for o in Offer.objects.filter(provider_content_id="m00327ht")},
            {9101},
        )

    def test_offer_granularity_survives_persistence(self):
        self.service.refresh_catalogue()
        breakdown = {}
        for offer in Offer.objects.all():
            breakdown[offer.content_type] = breakdown.get(offer.content_type, 0) + 1
        self.assertEqual(breakdown, {
            "series": 1,   # abadas
            "movie": 1,    # nickel-boys
            "season": 2,   # abadas 1-2
            "episode": 3,  # abadas 1x01, 1x02, 2x01
        })

    def test_identity_and_key_fields_are_canonical(self):
        self.service.refresh_catalogue()
        movie_offer = Offer.objects.get(
            content_type=ContentType.MOVIE, provider_content_id="m00327ht"
        )
        self.assertEqual(
            movie_offer.identity, "bbc_iplayer|GB|movie|9101|||free|m00327ht"
        )
        self.assertEqual(movie_offer.provider, self.provider)
        self.assertEqual(movie_offer.country.code, "GB")
        self.assertEqual(movie_offer.source.slug, "bbc_iplayer")
        self.assertEqual(movie_offer.offer_type, OfferType.FREE)
        self.assertIsNone(movie_offer.audio_languages)
        self.assertIsNone(movie_offer.subtitle_languages)
        self.assertEqual(
            movie_offer.available_from.replace(tzinfo=dt.timezone.utc),
            dt.datetime(2026, 9, 29, 0, 16, 21, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(
            movie_offer.available_until.replace(tzinfo=dt.timezone.utc),
            dt.datetime(2026, 11, 28, 1, 16, 21, tzinfo=dt.timezone.utc),
        )
        self.assertTrue(movie_offer.currently_available)

    def test_sync_run_lifecycle_and_counters(self):
        result = self.service.refresh_catalogue()

        self.assertEqual(len(result.sync_runs), 1)
        run = result.sync_runs[0]
        run.refresh_from_db()
        self.assertEqual(run.status, SyncStatus.SUCCEEDED)
        self.assertEqual(run.provider, self.provider)
        self.assertEqual(run.country.code, "GB")
        self.assertEqual(run.source.slug, "bbc_iplayer")
        self.assertEqual(run.mode, "full")
        self.assertFalse(run.authoritative)
        self.assertEqual(run.items_discovered, MAPPED_OFFERS)
        self.assertEqual(run.items_processed, MAPPED_OFFERS)
        self.assertEqual(run.offers_created, MAPPED_OFFERS)
        self.assertEqual(run.offers_updated, 0)
        self.assertEqual(run.offers_deactivated, 0)
        self.assertEqual(SyncRun.objects.count(), 1)

    def test_provider_native_content_survives_unresolved_mapping(self):
        self.service.refresh_catalogue()

        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)
        eastenders = ProviderContent.objects.get(provider_content_id="b006m86d")
        self.assertEqual(eastenders.title, "EastEnders")
        self.assertEqual(eastenders.content_type, ContentType.SERIES)
        self.assertFalse(
            ContentMapping.objects.filter(provider_content_id="b006m86d").exists()
        )
        self.assertFalse(
            Offer.objects.filter(provider_content_id="b006m86d").exists()
        )

    def test_mappings_are_created_with_tiered_confidence(self):
        self.service.refresh_catalogue()

        self.assertEqual(ContentMapping.objects.count(), 2)
        # Exact title + release year resolved uniquely -> strongest tier.
        nickel = self._mapping("m00327ht")
        self.assertEqual(nickel.tmdb_id, 9101)
        self.assertEqual(nickel.confidence, 0.9)
        self.assertEqual(nickel.match_method, MatchMethod.TITLE_YEAR)
        self.assertEqual(nickel.content_type, ContentType.MOVIE)
        self.assertIsNotNone(nickel.last_verified)
        # Brand pages carry no first-air year -> title-only tier.
        abadas = self._mapping("b01qm16p")
        self.assertEqual(abadas.tmdb_id, 9102)
        self.assertEqual(abadas.confidence, 0.7)
        self.assertEqual(abadas.content_type, ContentType.SERIES)

    def test_source_row_is_created_on_first_refresh(self):
        self.service.refresh_catalogue()
        source_row = Source.objects.get(slug="bbc_iplayer")
        self.assertEqual(source_row.name, "BBC iPlayer")
        self.assertTrue(source_row.enabled)

    def test_no_cursor_is_invented_for_page_numbered_pages(self):
        self.service.refresh_catalogue()
        self.assertEqual(SyncCursor.objects.count(), 0)

    def test_second_run_reuses_mappings_and_is_idempotent(self):
        first = self.service.refresh_catalogue()
        second = self.service.refresh_catalogue()

        self.assertEqual(first.created, MAPPED_OFFERS)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, MAPPED_OFFERS)
        self.assertEqual(second.skipped, UNMAPPED_OFFERS)
        self.assertEqual(Offer.objects.count(), MAPPED_OFFERS)
        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)
        self.assertEqual(ContentMapping.objects.count(), 2)
        self.assertEqual(SyncRun.objects.count(), 2)
        self.assertEqual(
            SyncRun.objects.filter(status=SyncStatus.SUCCEEDED).count(), 2
        )


class NonDeactivationTests(BBCIPlayerRefreshTestCase):
    def test_item_disappearing_from_the_catalogue_is_never_deactivated(self):
        self.service.refresh_catalogue()
        offers_before = set(
            Offer.objects.filter(provider_content_id="b01qm16p")
            .values_list("pk", flat=True)
        )
        self.assertTrue(offers_before)

        # Second fetch: abadas vanished from the A-Z index entirely.
        self.api.atoz["a"]["programmes"]["a"]["entities"] = []
        result = self.service.refresh_catalogue()

        self.assertEqual(result.deactivated, 0)
        self.assertEqual(
            set(
                Offer.objects.filter(provider_content_id="b01qm16p")
                .values_list("currently_available", flat=True)
            ),
            {True},
        )
        self.assertEqual(
            set(Offer.objects.filter(pk__in=offers_before).values_list(
                "pk", flat=True)),
            offers_before,
        )


class FailureTests(BBCIPlayerRefreshTestCase):
    def test_fetch_failure_writes_nothing_at_all(self):
        self.api.failures["/iplayer/a-z/"] = SourceFetchError("network down")

        with self.assertRaises(SourceFetchError):
            self.service.refresh_catalogue()

        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(ContentMapping.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        self.assertFalse(Source.objects.filter(slug="bbc_iplayer").exists())

    def test_targeted_refresh_against_a_catalogue_source_fails_loudly(self):
        with self.assertRaises(ValueError):
            self.service.refresh_content(self.nickel)

    def test_unknown_scope_hint_is_an_error_not_a_silent_noop(self):
        with self.assertRaisesMessage(ValueError, "provider"):
            self.service.refresh_catalogue(provider="netflix")
        with self.assertRaisesMessage(ValueError, "country"):
            self.service.refresh_catalogue(country="US")


class CountryScopeTests(BBCIPlayerRefreshTestCase):
    def test_disabled_country_skips_every_offer_but_still_maps_content(self):
        Country.objects.filter(code="GB").update(enabled=False)

        result = self.service.refresh_catalogue()

        self.assertEqual(result.created, 0)
        self.assertEqual(result.skipped, TOTAL_OFFERS)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        # Mapping is country-independent: content identity still gets recorded.
        self.assertEqual(ContentMapping.objects.count(), 2)


class SchedulingIntegrationTests(BBCIPlayerRefreshTestCase):
    def test_source_is_discovered_by_the_scheduled_catalogue_refresh(self):
        to_refresh, disabled = _catalogue_source_slugs()
        self.assertIn("bbc_iplayer", to_refresh)
        self.assertNotIn("bbc_iplayer", disabled)

    def test_manual_source_override_is_accepted(self):
        self.assertEqual(_catalogue_source_slugs("bbc_iplayer"), (["bbc_iplayer"], []))

    def test_declared_country_scope_is_initialized_before_any_write(self):
        Country.objects.filter(code="GB").delete()

        with self.assertLogs("tracker.services.bulk_refresh",
                             level="WARNING") as logs:
            _ensure_declared_countries(
                "bbc_iplayer", BBCIPlayerSource.capabilities
            )

        self.assertTrue(Country.objects.filter(code="GB").exists())
        self.assertTrue(
            any("catalogue_refresh_country_initialized" in line
                and "source=bbc_iplayer" in line for line in logs.output)
        )


class CheckpointPersistenceTests(BBCIPlayerRefreshTestCase):
    """Bounded batches persist durably, and a failed batch is retried in full."""

    def setUp(self):
        super().setUp()
        self.api = FakeBBCIPlayer()
        self.source = BBCIPlayerSource(
            client=BBCIPlayerClient(requester=self.api, delay=0.0), batch_size=2
        )
        self.service = AvailabilityRefreshService(source=self.source)

    def test_a_failed_batch_keeps_the_checkpoint_and_earlier_rows(self):
        self.service.refresh_catalogue()          # letters a, b
        self.assertEqual(_decode_cursor(SyncCursor.objects.get().cursor)["letter"], "c")
        first_batch_offers = Offer.objects.count()
        self.assertEqual(first_batch_offers, 6)   # abadas only

        self.service.refresh_catalogue()          # letters c, e
        self.assertEqual(_decode_cursor(SyncCursor.objects.get().cursor)["letter"], "f")
        self.assertEqual(Offer.objects.count(), first_batch_offers)

        # A failing next batch must not move the checkpoint nor lose rows.
        self.api.failures[PAGE_FOLK] = SourceFetchError("outage")
        with self.assertRaises(SourceFetchError):
            self.service.refresh_catalogue()
        self.assertEqual(_decode_cursor(SyncCursor.objects.get().cursor)["letter"], "f")
        self.assertEqual(Offer.objects.count(), first_batch_offers)

        # The retry resumes at the failing letter and completes the crawl; a
        # finished walk drops its checkpoint instead of leaving it behind.
        del self.api.failures[PAGE_FOLK]
        while SyncCursor.objects.exists():
            self.service.refresh_catalogue()
        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)
        self.assertEqual(Offer.objects.count(), MAPPED_OFFERS)
        self.assertFalse(SyncCursor.objects.exists())
