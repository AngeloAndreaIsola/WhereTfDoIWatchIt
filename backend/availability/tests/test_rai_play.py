"""RaiPlay source: sitemap discovery, SSR hydration parsing, failure semantics.

Fixture-based (``rai_play_fixtures.FakeRaiPlayApi``): no test performs network
I/O and no test touches the database (``SimpleTestCase`` doubles as a guard —
the source must stay persistence-free).

Fixture catalogue (recorded pages):

    sitemap index -> programmi.08.2026, programmi.09.2026
                     (+ editorial children generiche, dirette, speciali,
                      collezioni.09.2026, video.09.2026 — never requested)

    08.2026   follemente, normalpeople, sulcamminodifrancesco-…
    09.2026   follemente (duplicate entry), programma-ritirato (404),
              settembre

    follemente          movie   2024  video present: free, open window,
                                      no subtitles published
    normalpeople        series  2020  video present: free, open window,
                                      season/episode labels, IT+EN subtitles
    programma-ritirato          (page gone)      -> nothing
    sulcammino di …     series  2026  video gone  -> content, no offer
    settembre           movie   2022  video present: free, open window,
                                      IT subtitle published

    Walk order after interleaving the two monthly sitemaps and de-duplicating
    the repeated ``follemente`` entry: follemente, normalpeople,
    programma-ritirato, sulcammino…, settembre -> 4 contents, 3 offers.
"""
import datetime as dt
import json
import threading
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from availability.domain.enums import ContentType, OfferType, SourceType, SyncMode
from availability.sources import (
    AvailabilitySource,
    ContentRequest,
    RaiPlayClient,
    RaiPlayConfigurationError,
    RaiPlayHttpBudget,
    RaiPlayHttpBudgetExceeded,
    RaiPlaySource,
    SourceFetchError,
    source_registry,
)
from availability.sources.rai_play import (
    ALLOWED_RAIPLAY_HOSTS,
    MAX_REDIRECT_HOPS,
    _is_trailer,
    _parse_timestamp,
    _seconds,
    _state_from_payload,
    _validate_raiplay_url,
)

from .rai_play_fixtures import (
    COLLEZIONI_09,
    DIRETTE,
    FOLLEMENTE,
    FOLLEMENTE_ID,
    FOLLEMENTE_PLAYER,
    FOLLEMENTE_VIDEO,
    FOLLEMENTE_VIDEO_ID,
    GENERICHE,
    NORMAL_PEOPLE,
    NORMAL_PEOPLE_ID,
    NORMAL_PEOPLE_VIDEO,
    PROGRAMME_SITEMAP_08,
    PROGRAMME_SITEMAP_09,
    PROGRAMME_URLS,
    RETIRED,
    SETTEMBRE,
    SETTEMBRE_ID,
    SETTEMBRE_VIDEO,
    SETTEMBRE_VIDEO_ID,
    SPECIALI,
    SUL_CAMMINO,
    SUL_CAMMINO_ID,
    SUL_CAMMINO_VIDEO,
    VIDEO_SITEMAP_09,
    FakeRaiPlayApi,
    access_spec,
    load_record,
    page_state,
    serve_video_record,
    url_entry,
    with_state,
)

TOTAL_CONTENTS = 4
TOTAL_OFFERS = 3

# Editorial and live URLs, none of which the source may ever request.
LIVE_CHANNEL = "https://www.raiplay.it/dirette/rai1"
COLLECTION = "https://www.raiplay.it/collezioni/cinema"
EDITORIAL_CHILDREN = [GENERICHE, DIRETTE, SPECIALI, COLLEZIONI_09, VIDEO_SITEMAP_09]


def build_source(api=None, **source_kwargs):
    api = api or FakeRaiPlayApi()
    return RaiPlaySource(client=RaiPlayClient(requester=api), **source_kwargs), api


def set_state(api, url, mutate):
    """Rebuild one recorded page with ``mutate(state)`` applied in place."""
    api.pages[url] = with_state(api.pages[url], mutate)


class RegistrationTests(SimpleTestCase):
    def test_rai_play_source_is_registered(self):
        source = source_registry.get("rai_play")
        self.assertIsInstance(source, RaiPlaySource)
        self.assertEqual(source.slug, "rai_play")
        self.assertEqual(source.name, "RaiPlay")
        self.assertEqual(source.source_type, SourceType.WEB)

    def test_source_conforms_to_the_interface(self):
        self.assertIsInstance(source_registry.get("rai_play"), AvailabilitySource)

    def test_capabilities_declare_a_catalogue_non_authoritative_it_scoped_source(self):
        capabilities = source_registry.get("rai_play").capabilities
        self.assertEqual(capabilities.supported_providers, frozenset({"raiplay"}))
        self.assertEqual(capabilities.supported_countries, frozenset({"IT"}))
        self.assertEqual(
            capabilities.supported_content_types,
            frozenset({ContentType.MOVIE, ContentType.SERIES}),
        )
        self.assertTrue(capabilities.supports_catalogue_sync)
        # Sitemap entries go stale and the walk is bounded: absence is never
        # evidence that a title disappeared.
        self.assertFalse(capabilities.supports_authoritative_snapshots)
        self.assertFalse(capabilities.supports_incremental_sync)
        # RaiPlay publishes no TMDb ids.
        self.assertFalse(capabilities.supports_tmdb_ids)
        # season/episode are labels on RaiPlay, not integers (module docstring).
        self.assertFalse(capabilities.supports_seasons)
        self.assertFalse(capabilities.supports_episodes)
        self.assertTrue(capabilities.supports_languages)

    def test_targeted_content_request_is_rejected(self):
        source, _api = build_source()
        with self.assertRaisesMessage(ValueError, "catalogue-driven"):
            source.fetch(ContentRequest(tmdb_id=550, title="Fight Club"))

    def test_fetch_without_content_walks_the_catalogue(self):
        source, _api = build_source()
        result = source.fetch()
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)
        self.assertEqual(len(result.offers), TOTAL_OFFERS)

    def test_scope_hints_are_accepted_but_never_change_the_reported_scope(self):
        source, _api = build_source()
        result = source.fetch(provider="raiplay", country="IT")
        self.assertEqual(len(result.offers), TOTAL_OFFERS)
        self.assertEqual({offer.country for offer in result.offers}, {"IT"})
        self.assertEqual({offer.provider_id for offer in result.offers}, {"raiplay"})


class SnapshotSemanticsTests(SimpleTestCase):
    def test_result_never_claims_authoritativeness_and_is_resumable(self):
        source, _api = build_source()
        result = source.fetch()
        self.assertFalse(result.authoritative)
        self.assertFalse(result.complete_snapshot)
        self.assertEqual(result.sync_mode, SyncMode.FULL)
        # This fixture's catalogue fits in one window, so the walk is exhausted
        # and correctly emits no checkpoint. Resumability — including the
        # bounded-request guarantee — is proven by CatalogueCheckpointTests.
        self.assertIsNone(result.cursor)


class SitemapDiscoveryTests(SimpleTestCase):
    def test_only_programme_sitemaps_are_requested(self):
        source, api = build_source()
        source.fetch()
        requested = set(api.requests)
        self.assertIn("https://www.raiplay.it/sitemap.xml", requested)
        self.assertIn(PROGRAMME_SITEMAP_08, requested)
        self.assertIn(PROGRAMME_SITEMAP_09, requested)
        for child in EDITORIAL_CHILDREN:
            self.assertNotIn(child, requested)

    def test_interleaved_entries_are_walked_in_the_recorded_order(self):
        source, api = build_source()
        source.fetch()
        self.assertEqual(api.programme_requests, PROGRAMME_URLS)

    def test_a_duplicate_sitemap_entry_is_fetched_once(self):
        source, api = build_source()
        source.fetch()
        self.assertEqual(api.programme_requests.count(FOLLEMENTE), 1)
        self.assertEqual(
            len(api.programme_requests), len(set(api.programme_requests))
        )

    def test_index_without_programme_sitemaps_is_a_failure_not_an_empty_catalogue(self):
        source, api = build_source()
        api.index = api.index.replace("programmi.08.2026.xml", "collezioni.08.2026.xml") \
                             .replace("programmi.09.2026.xml", "video.09.2026.xml")
        with self.assertRaisesMessage(SourceFetchError, "no programme sitemaps"):
            source.fetch()
        self.assertEqual(
            [url for url in api.requests if url.endswith(".xml")],
            ["https://www.raiplay.it/sitemap.xml"],
        )

    def test_malformed_sitemap_is_a_failure(self):
        source, api = build_source()
        api.sitemaps[PROGRAMME_SITEMAP_08] = "<urlset><loc>not closed"
        with self.assertRaisesMessage(SourceFetchError, "malformed sitemap"):
            source.fetch()

    def test_non_xml_sitemap_answer_is_a_failure(self):
        source, api = build_source()
        api.sitemaps[PROGRAMME_SITEMAP_08] = "<html><body>consent</body></html>"
        with self.assertRaisesMessage(SourceFetchError, "sitemap root"):
            source.fetch()

    def test_a_gone_sitemap_is_a_failure_not_an_empty_page(self):
        source, api = build_source()
        api.sitemaps[PROGRAMME_SITEMAP_09] = None
        with self.assertRaisesMessage(SourceFetchError, "sitemap is gone"):
            source.fetch()


class CatalogueParsingTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source, cls.api = build_source()
        cls.result = cls.source.fetch()

    def _content(self, provider_content_id):
        return [c for c in self.result.contents
                if c.provider_content_id == provider_content_id]

    def _offers(self, provider_content_id):
        return [o for o in self.result.offers
                if o.provider_content_id == provider_content_id]

    def test_every_observable_programme_becomes_a_content_record(self):
        self.assertEqual(
            {c.title for c in self.result.contents},
            {
                "Follemente",
                "Normal People",
                "Sul cammino di Francesco - La vita di Biagio Conte",
                "Settembre",
            },
        )
        self.assertEqual(
            {c.content_type for c in self.result.contents},
            {ContentType.MOVIE, ContentType.SERIES},
        )
        self.assertTrue(all(c.provider_id == "raiplay" for c in self.result.contents))
        self.assertTrue(all(c.source_id == "rai_play" for c in self.result.contents))

    def test_provider_content_id_is_the_programme_uniquename(self):
        # RaiPlay's own stable id, the one its video pages point back at
        # (``program_uniquename``), shared by content, offer and mapping.
        self.assertEqual(self._content(FOLLEMENTE_ID)[0].title, "Follemente")
        self.assertEqual(self._content(NORMAL_PEOPLE_ID)[0].title, "Normal People")
        self.assertEqual(self._content(SUL_CAMMINO_ID)[0].title,
                         "Sul cammino di Francesco - La vita di Biagio Conte")
        self.assertEqual(self._content(SETTEMBRE_ID)[0].title, "Settembre")

    def test_movie_fields_come_from_the_programme_and_video_pages(self):
        movie = self._content(FOLLEMENTE_ID)[0]
        self.assertEqual(movie.content_type, ContentType.MOVIE)
        self.assertEqual(movie.title, "Follemente")
        # Year precision: Jan 1 of the programme's production year.
        self.assertEqual(movie.release_date, dt.date(2024, 1, 1))
        self.assertEqual(movie.url, FOLLEMENTE)
        # The representative video *is* the movie, so its runtime is the film's.
        self.assertEqual(movie.duration, 5564)  # 01:32:44
        self.assertEqual(
            movie.poster,
            "https://www.raiplay.it/dl/img/2026/09/21/1789993067590_2048x1152.jpg",
        )
        self.assertGreater(len(movie.description), 100)
        self.assertEqual(movie.external_ids, {})

    def test_series_carry_no_release_date_or_duration(self):
        series = self._content(NORMAL_PEOPLE_ID)[0]
        self.assertEqual(series.content_type, ContentType.SERIES)
        self.assertIsNone(series.release_date)
        # One episode's runtime says nothing about the programme.
        self.assertIsNone(series.duration)
        self.assertEqual(series.url, NORMAL_PEOPLE)

    def test_a_programme_whose_video_is_gone_still_gets_a_content_record(self):
        # The catalogue row is observable; only the offer is lost.
        self.assertEqual(len(self._content(SUL_CAMMINO_ID)), 1)
        self.assertEqual(self._offers(SUL_CAMMINO_ID), [])

    def test_movie_offer_carries_the_full_access_window_and_video_facts(self):
        offers = self._offers(FOLLEMENTE_ID)
        self.assertEqual(len(offers), 1)
        offer = offers[0]
        self.assertEqual(offer.offer_type, OfferType.FREE)
        self.assertEqual(offer.country, "IT")
        self.assertEqual(offer.provider_id, "raiplay")
        self.assertEqual(offer.content_type, ContentType.MOVIE)
        # The observed programme URL, never the video's own URL.
        self.assertEqual(offer.provider_url, FOLLEMENTE)
        # The underlying RaiPlay record behind the offer, not the programme.
        self.assertEqual(offer.source_record_id, FOLLEMENTE_VIDEO_ID)
        self.assertEqual(offer.source_version, "rai-play-ssr-v1")
        self.assertIsNone(offer.tmdb_id)
        self.assertIsNone(offer.season_number)
        self.assertIsNone(offer.episode_number)
        # ``uhd`` is null on every captured page -> no structured quality.
        self.assertEqual(offer.quality, "")
        # RaiPlay publishes no audio-language field at all.
        self.assertIsNone(offer.audio_languages)
        # A known-empty subtitle mapping stays [], never None.
        self.assertEqual(offer.subtitle_languages, [])
        # Naive provider timestamps are read as UTC.
        self.assertEqual(
            offer.available_from, dt.datetime(2026, 9, 16, tzinfo=dt.timezone.utc)
        )
        # Compact ``+0000`` offsets are normalized on the way in.
        self.assertEqual(
            offer.available_until,
            dt.datetime(2036, 6, 4, 21, 59, 42, tzinfo=dt.timezone.utc),
        )

    def test_series_offer_carries_no_video_facts_at_all(self):
        offers = self._offers(NORMAL_PEOPLE_ID)
        self.assertEqual(len(offers), 1)
        offer = offers[0]
        self.assertEqual(offer.content_type, ContentType.SERIES)
        self.assertEqual(offer.offer_type, OfferType.FREE)
        self.assertEqual(offer.country, "IT")
        # The access spec belongs to one representative episode: a series
        # offer gets no window, no subtitles, no quality.
        self.assertIsNone(offer.available_from)
        self.assertIsNone(offer.available_until)
        self.assertIsNone(offer.subtitle_languages)
        self.assertEqual(offer.quality, "")
        self.assertEqual(offer.provider_url, NORMAL_PEOPLE)

    def test_every_offer_is_free_from_italy_with_no_tmdb_id(self):
        for offer in self.result.offers:
            self.assertEqual(offer.offer_type, OfferType.FREE)
            self.assertEqual(offer.country, "IT")
            self.assertEqual(offer.provider_id, "raiplay")
            self.assertIsNone(offer.tmdb_id)
            self.assertIsNone(offer.season_number)
            self.assertIsNone(offer.episode_number)


class NumberingTests(SimpleTestCase):
    def test_no_season_or_episode_offers_exist_at_all(self):
        source, _api = build_source()
        result = source.fetch()
        self.assertEqual(
            {c.content_type for c in result.contents},
            {ContentType.MOVIE, ContentType.SERIES},
        )
        for offer in result.offers:
            self.assertIsNone(offer.season_number)
            self.assertIsNone(offer.episode_number)

    def test_video_page_numbering_labels_never_become_offer_numbering(self):
        # The recorded episode publishes season/episode as *labels* ("1"/"1");
        # RaiPlay never states which episode a programme's ``first_item`` is,
        # so the offer stays programme-level.
        video = page_state(FakeRaiPlayApi().pages[NORMAL_PEOPLE_VIDEO])
        self.assertEqual(video["season"], "1")
        self.assertEqual(video["episode"], "1")

        source, _api = build_source()
        result = source.fetch()
        offers = [o for o in result.offers
                  if o.provider_content_id == NORMAL_PEOPLE_ID]
        self.assertEqual(len(offers), 1)
        self.assertIsNone(offers[0].season_number)
        self.assertIsNone(offers[0].episode_number)


class AccessModelTests(SimpleTestCase):
    def test_the_nologinrequired_category_is_still_free(self):
        source, api = build_source()
        set_state(api, FOLLEMENTE_VIDEO, lambda s: access_spec(s).update(
            category="nologinrequired", login_required=False
        ))
        result = source.fetch()
        offers = [o for o in result.offers if o.provider_content_id == FOLLEMENTE_ID]
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].offer_type, OfferType.FREE)

    def test_an_unknown_category_is_normalized_to_unknown_and_logged(self):
        source, api = build_source()
        set_state(api, FOLLEMENTE_VIDEO, lambda s: access_spec(s).update(
            category="premium", login_required=True
        ))
        with self.assertLogs("availability.sources.rai_play", level="WARNING") as logs:
            result = source.fetch()
        self.assertIn("rai_play_unknown_access", "\n".join(logs.output))
        offers = [o for o in result.offers if o.provider_content_id == FOLLEMENTE_ID]
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].offer_type, OfferType.UNKNOWN)

    def test_a_page_publishing_no_access_spec_skips_the_offer_only(self):
        source, api = build_source()
        set_state(api, FOLLEMENTE_VIDEO, lambda s: s["schema"].update(
            potentialAction=None
        ))
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        output = "\n".join(logs.output)
        self.assertIn("rai_play_offer_skipped", output)
        self.assertIn("reason=no_access_spec", output)
        # Catalogue row stands; only the offer goes away.
        self.assertIn(FOLLEMENTE_ID, {c.provider_content_id for c in result.contents})
        self.assertEqual(
            [o for o in result.offers if o.provider_content_id == FOLLEMENTE_ID], []
        )
        self.assertEqual(len(result.offers), TOTAL_OFFERS - 1)

    def test_a_region_the_source_does_not_declare_is_skipped(self):
        source, api = build_source()
        set_state(api, FOLLEMENTE_VIDEO, lambda s: access_spec(s)["eligibleRegion"].update(
            name="US"
        ))
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        output = "\n".join(logs.output)
        self.assertIn("reason=region_mismatch", output)
        self.assertIn("region=US", output)
        self.assertEqual(
            [o for o in result.offers if o.provider_content_id == FOLLEMENTE_ID], []
        )

    def test_a_missing_region_is_skipped_as_unobservable(self):
        # The catalogue is Italian, but a spec that states no region states
        # nothing about where it plays.
        source, api = build_source()
        set_state(api, FOLLEMENTE_VIDEO, lambda s: access_spec(s).pop(
            "eligibleRegion"
        ))
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        self.assertIn("reason=region_unobserved", "\n".join(logs.output))
        self.assertEqual(
            [o for o in result.offers if o.provider_content_id == FOLLEMENTE_ID], []
        )


class OfferWindowTests(SimpleTestCase):
    def test_an_offer_whose_window_has_closed_is_skipped(self):
        source, api = build_source()
        set_state(api, FOLLEMENTE_VIDEO, lambda s: access_spec(s).update(
            availabilityEnds="2020-01-01T00:00:00.000"
        ))
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        self.assertIn("reason=expired", "\n".join(logs.output))
        # The catalogue row still stands; only the offer goes away.
        self.assertIn(FOLLEMENTE_ID, {c.provider_content_id for c in result.contents})
        self.assertEqual(
            [o for o in result.offers if o.provider_content_id == FOLLEMENTE_ID], []
        )
        self.assertEqual(len(result.offers), TOTAL_OFFERS - 1)

    def test_an_offer_that_has_not_opened_yet_is_skipped(self):
        source, api = build_source()
        set_state(api, FOLLEMENTE_VIDEO, lambda s: access_spec(s).update(
            availabilityStarts="2030-01-01T00:00:00.000"
        ))
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        self.assertIn("reason=window_not_open", "\n".join(logs.output))
        self.assertEqual(
            [o for o in result.offers if o.provider_content_id == FOLLEMENTE_ID], []
        )

    def test_a_series_offer_is_never_window_checked(self):
        # Its spec has a window too, but it belongs to one representative
        # episode — the offer carries none, so nothing can expire.
        source, api = build_source()
        set_state(api, NORMAL_PEOPLE_VIDEO, lambda s: access_spec(s).update(
            availabilityEnds="2020-01-01T00:00:00.000"
        ))
        result = source.fetch()
        offers = [o for o in result.offers if o.provider_content_id == NORMAL_PEOPLE_ID]
        self.assertEqual(len(offers), 1)
        self.assertIsNone(offers[0].available_until)


class TimestampParsingTests(SimpleTestCase):
    def test_naive_provider_timestamps_are_read_as_utc(self):
        parsed = _parse_timestamp("2026-09-16T00:00:00.000")
        self.assertEqual(parsed, dt.datetime(2026, 9, 16, tzinfo=dt.timezone.utc))

    def test_the_compact_offset_form_is_normalized(self):
        parsed = _parse_timestamp("2036-06-04T21:59:42+0000")
        self.assertEqual(parsed, dt.datetime(2036, 6, 4, 21, 59, 42,
                                             tzinfo=dt.timezone.utc))

    def test_an_unparsable_timestamp_is_unobserved_not_a_failure(self):
        self.assertIsNone(_parse_timestamp("soon"))
        self.assertIsNone(_parse_timestamp(""))
        self.assertIsNone(_parse_timestamp(None))

    def test_duration_clock_strings_are_seconds(self):
        self.assertEqual(_seconds("01:32:44"), 5564)
        self.assertEqual(_seconds("29:03"), 29 * 60 + 3)
        self.assertIsNone(_seconds("92:99:99"))
        self.assertIsNone(_seconds(None))


class LanguageAndQualityTests(SimpleTestCase):
    def test_a_known_empty_subtitle_mapping_stays_empty(self):
        source, _api = build_source()
        result = source.fetch()
        follemente = [o for o in result.offers
                      if o.provider_content_id == FOLLEMENTE_ID][0]
        self.assertEqual(follemente.subtitle_languages, [])

    def test_published_subtitles_are_normalized_to_uppercase_codes(self):
        source, _api = build_source()
        result = source.fetch()
        settembre = [o for o in result.offers
                     if o.provider_content_id == SETTEMBRE_ID][0]
        self.assertEqual(settembre.subtitle_languages, ["IT"])

    def test_absent_subtitle_metadata_is_none_not_empty(self):
        source, api = build_source()
        set_state(api, SETTEMBRE_VIDEO, lambda s: s.pop("subtitlesArray"))
        result = source.fetch()
        settembre = [o for o in result.offers
                     if o.provider_content_id == SETTEMBRE_ID][0]
        self.assertIsNone(settembre.subtitle_languages)

    def test_an_unreadable_subtitle_mapping_is_none_not_empty(self):
        source, api = build_source()
        set_state(api, SETTEMBRE_VIDEO, lambda s: s.update(subtitlesArray="it"))
        result = source.fetch()
        settembre = [o for o in result.offers
                     if o.provider_content_id == SETTEMBRE_ID][0]
        self.assertIsNone(settembre.subtitle_languages)

    def test_audio_languages_are_never_assumed(self):
        # No audio-language field exists on RaiPlay, and Italian audio is
        # never inferred from the Italian catalogue.
        source, _api = build_source()
        result = source.fetch()
        self.assertTrue(all(o.audio_languages is None for o in result.offers))

    def test_truthy_uhd_maps_to_the_shared_quality_vocabulary(self):
        source, api = build_source()
        set_state(api, FOLLEMENTE_VIDEO, lambda s: s.update(uhd=True))
        result = source.fetch()
        follemente = [o for o in result.offers
                      if o.provider_content_id == FOLLEMENTE_ID][0]
        self.assertEqual(follemente.quality, "4K")


class VideoObservationTests(SimpleTestCase):
    def test_a_programme_that_answered_404_is_skipped(self):
        source, api = build_source()
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        output = "\n".join(logs.output)
        self.assertIn("rai_play_content_missing", output)
        self.assertIn(RETIRED, output)
        self.assertIn("reason=not_found", output)
        self.assertIn(RETIRED, api.programme_requests)
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)
        self.assertNotIn(RETIRED, {c.provider_content_id for c in result.contents})

    def test_a_video_that_answered_404_costs_the_offer_not_the_content(self):
        source, api = build_source()
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        output = "\n".join(logs.output)
        self.assertIn("reason=video_not_found", output)
        self.assertIn(SUL_CAMMINO_VIDEO, api.video_requests)
        self.assertIn(SUL_CAMMINO_ID, {c.provider_content_id for c in result.contents})
        self.assertEqual(
            [o for o in result.offers if o.provider_content_id == SUL_CAMMINO_ID], []
        )

    def test_a_programme_without_a_first_item_costs_the_offer_only(self):
        source, api = build_source()
        set_state(api, FOLLEMENTE, lambda s: s.update(first_item=""))
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        self.assertIn("reason=no_first_item", "\n".join(logs.output))
        self.assertIn(FOLLEMENTE_ID, {c.provider_content_id for c in result.contents})
        self.assertEqual(
            [o for o in result.offers if o.provider_content_id == FOLLEMENTE_ID], []
        )
        self.assertNotIn(FOLLEMENTE_VIDEO, api.video_requests)

    def test_a_video_page_that_is_not_a_video_costs_the_offer_only(self):
        source, api = build_source()
        set_state(api, FOLLEMENTE_VIDEO, lambda s: s.update(data_type="program"))
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        self.assertIn("reason=not_a_video", "\n".join(logs.output))
        self.assertIn(FOLLEMENTE_ID, {c.provider_content_id for c in result.contents})
        self.assertEqual(
            [o for o in result.offers if o.provider_content_id == FOLLEMENTE_ID], []
        )

    def test_a_trailer_video_is_skipped_with_a_trailer_record(self):
        source, api = build_source()

        def make_trailer(state):
            state["weblink"] = "/video/2026/09/Follemente---Trailer.html"
            state["path_id"] = "/video/2026/09/Follemente---Trailer.json"

        set_state(api, FOLLEMENTE_VIDEO, make_trailer)
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        output = "\n".join(logs.output)
        self.assertIn("rai_play_trailer_skipped", output)
        self.assertIn(FOLLEMENTE_ID, {c.provider_content_id for c in result.contents})
        self.assertEqual(
            [o for o in result.offers if o.provider_content_id == FOLLEMENTE_ID], []
        )
        # Still recorded as the programme's representative video: the request
        # happened, only its content was refused.
        self.assertIn(FOLLEMENTE_VIDEO, api.video_requests)

    def test_an_unwatchable_layout_never_becomes_a_content_record(self):
        source, api = build_source()
        set_state(api, FOLLEMENTE, lambda s: s.update(layout="collection"))
        with self.assertLogs("availability.sources.rai_play", level="WARNING") as logs:
            result = source.fetch()
        self.assertIn("rai_play_unsupported_content", "\n".join(logs.output))
        self.assertNotIn(FOLLEMENTE_ID, {c.provider_content_id for c in result.contents})
        self.assertEqual(len(result.contents), TOTAL_CONTENTS - 1)

    def test_a_page_without_identity_is_skipped_as_unparseable(self):
        source, api = build_source()
        set_state(api, FOLLEMENTE, lambda s: s.update(uniquename=""))
        with self.assertLogs("availability.sources.rai_play", level="WARNING") as logs:
            result = source.fetch()
        self.assertIn("rai_play_unparseable_content", "\n".join(logs.output))
        self.assertNotIn(FOLLEMENTE_ID, {c.provider_content_id for c in result.contents})

    def test_a_page_that_is_not_a_programme_is_skipped(self):
        source, api = build_source()
        set_state(api, FOLLEMENTE, lambda s: s.update(data_type="collection"))
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        self.assertIn("reason=not_a_programme", "\n".join(logs.output))
        self.assertNotIn(FOLLEMENTE_ID, {c.provider_content_id for c in result.contents})


class VideoRecordRepresentationTests(SimpleTestCase):
    """``first_item`` answered with the bare JSON record (the shape served
    live on 2026-09-30): the source must reach the player page through the
    record's own ``weblink``, never through a constructed URL.
    """

    def test_the_record_leads_to_the_player_page_and_the_offer_survives(self):
        source, api = build_source()
        player_url = serve_video_record(api)
        self.assertEqual(player_url, FOLLEMENTE_PLAYER)
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        self.assertNotIn("reason=no_player_page", "\n".join(logs.output))
        self.assertNotIn("reason=not_a_video", "\n".join(logs.output))
        # Record first, player page second, and exactly one extra request.
        self.assertEqual(api.video_requests[:2], [FOLLEMENTE_VIDEO, FOLLEMENTE_PLAYER])
        self.assertEqual(len(api.video_requests), 5)
        offer = [o for o in result.offers if o.provider_content_id == FOLLEMENTE_ID]
        self.assertEqual(len(offer), 1)
        self.assertEqual(offer[0].offer_type, OfferType.FREE)
        self.assertEqual(offer[0].source_record_id, FOLLEMENTE_VIDEO_ID)
        content = [c for c in result.contents if c.provider_content_id == FOLLEMENTE_ID]
        self.assertEqual(len(content), 1)
        # Movie facts come from the player page, not the record.
        self.assertEqual(content[0].duration, 5564)
        self.assertEqual(content[0].release_date, dt.date(2024, 1, 1))

    def test_a_record_without_a_weblink_costs_the_offer_not_the_content(self):
        source, api = build_source()
        serve_video_record(api)
        state = json.loads(load_record())
        state.pop("weblink", None)
        api.pages[FOLLEMENTE_VIDEO] = json.dumps(state, ensure_ascii=False)
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        output = "\n".join(logs.output)
        self.assertIn("reason=no_player_page", output)
        self.assertIn(FOLLEMENTE_ID, {c.provider_content_id for c in result.contents})
        self.assertEqual(
            [o for o in result.offers if o.provider_content_id == FOLLEMENTE_ID], []
        )

    def test_a_player_page_that_answered_404_costs_the_offer_not_the_content(self):
        source, api = build_source()
        player_url = serve_video_record(api)
        api.pages[player_url] = None
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        output = "\n".join(logs.output)
        self.assertIn("reason=video_not_found", output)
        self.assertIn(player_url, api.video_requests)
        self.assertIn(FOLLEMENTE_ID, {c.provider_content_id for c in result.contents})
        self.assertEqual(
            [o for o in result.offers if o.provider_content_id == FOLLEMENTE_ID], []
        )

    def test_a_player_page_without_the_marker_is_a_systemic_failure(self):
        source, api = build_source()
        player_url = serve_video_record(api)
        api.pages[player_url] = "<!DOCTYPE html><html><body>consent</body></html>"
        with self.assertRaises(SourceFetchError):
            source.fetch()

    def test_the_record_itself_never_carries_an_access_model(self):
        # Why the follow-up exists: the bare document has no schema at all.
        state = json.loads(load_record())
        self.assertNotIn("schema", state)
        self.assertNotIn("data_type", state)
        self.assertEqual(state.get("path_id", "").rsplit("/", 1)[-1],
                         "Follemente-05d711e6-e70a-401f-8dc4-982fa42e85ae.json")


class EditorialUrlTests(SimpleTestCase):
    def test_a_live_channel_leaked_into_a_sitemap_is_never_requested(self):
        source, api = build_source()
        api.sitemaps[PROGRAMME_SITEMAP_08] = api.sitemaps[PROGRAMME_SITEMAP_08].replace(
            "</urlset>", url_entry(LIVE_CHANNEL) + "</urlset>"
        )
        with self.assertLogs("availability.sources.rai_play", level="INFO") as logs:
            result = source.fetch()
        self.assertIn("rai_play_live_content_skipped", "\n".join(logs.output))
        self.assertNotIn(LIVE_CHANNEL, api.requests)
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)

    def test_a_non_programme_url_is_never_requested(self):
        source, api = build_source()
        api.sitemaps[PROGRAMME_SITEMAP_09] = api.sitemaps[PROGRAMME_SITEMAP_09].replace(
            "</urlset>", url_entry(COLLECTION) + "</urlset>"
        )
        with self.assertLogs("availability.sources.rai_play", level="WARNING") as logs:
            result = source.fetch()
        self.assertIn("rai_play_unsupported_content", "\n".join(logs.output))
        self.assertIn("reason=not_a_programme_path", "\n".join(logs.output))
        self.assertNotIn(COLLECTION, api.requests)
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)


class CatalogueBoundTests(SimpleTestCase):
    def test_a_capped_walk_mixes_movies_and_series_and_logs_the_truncation(self):
        source, api = build_source(max_contents=2)
        with self.assertLogs("availability.sources.rai_play", level="WARNING") as logs:
            result = source.fetch()
        self.assertIn("rai_play_catalogue_truncated", "\n".join(logs.output))
        # Round-robin across monthly sitemaps, so a small cap is not all movies.
        self.assertEqual(api.programme_requests, [FOLLEMENTE, NORMAL_PEOPLE])
        self.assertEqual(
            {c.content_type for c in result.contents},
            {ContentType.MOVIE, ContentType.SERIES},
        )

    def test_no_bound_walks_every_unique_programme_entry(self):
        source, api = build_source(max_contents=None)
        source.fetch()
        self.assertEqual(api.programme_requests, PROGRAMME_URLS)
        self.assertEqual(len(api.video_requests), 4)

    def test_a_run_that_observes_nothing_is_logged_but_still_succeeds(self):
        source, api = build_source()
        for url in PROGRAMME_URLS:
            api.pages[url] = None
        with self.assertLogs("availability.sources.rai_play", level="WARNING") as logs:
            result = source.fetch()
        self.assertIn("rai_play_catalogue_nothing_observed", "\n".join(logs.output))
        self.assertEqual(result.contents, [])
        self.assertEqual(result.offers, [])
        self.assertFalse(result.authoritative)

    def test_a_run_that_observes_contents_but_no_offer_is_logged(self):
        source, api = build_source()
        for url in (FOLLEMENTE_VIDEO, NORMAL_PEOPLE_VIDEO, SETTEMBRE_VIDEO):
            api.pages[url] = None
        with self.assertLogs("availability.sources.rai_play", level="WARNING") as logs:
            result = source.fetch()
        self.assertIn("rai_play_catalogue_no_offers", "\n".join(logs.output))
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)
        self.assertEqual(result.offers, [])


class FailureVsEmptyTests(SimpleTestCase):
    def test_a_page_without_the_state_marker_is_a_failure(self):
        source, api = build_source()
        api.pages[FOLLEMENTE] = "<!DOCTYPE html><html><body>consent</body></html>"
        with self.assertRaisesMessage(SourceFetchError, "no window.WashiContext"):
            source.fetch()

    def test_an_undecodable_state_is_a_failure(self):
        source, api = build_source()
        api.pages[FOLLEMENTE] = api.pages[FOLLEMENTE].split(
            "window.WashiContext = ", 1
        )[0] + 'window.WashiContext = {"title":'
        with self.assertRaisesMessage(SourceFetchError, "Malformed"):
            source.fetch()

    def test_a_requester_failure_is_a_failure(self):
        source, api = build_source()
        api.failures["follemente"] = requests.ConnectionError("boom")
        with self.assertRaisesMessage(SourceFetchError, "boom"):
            source.fetch()

    def test_a_source_fetch_error_from_the_requester_propagates_unchanged(self):
        source, api = build_source()
        api.failures["follemente"] = SourceFetchError("upstream 500")
        with self.assertRaisesMessage(SourceFetchError, "upstream 500"):
            source.fetch()

    def test_a_non_text_answer_is_a_failure(self):
        source, api = build_source()
        api.pages[FOLLEMENTE] = b"<html>binary</html>"
        with self.assertRaisesMessage(SourceFetchError, "response type"):
            source.fetch()

    def test_an_unknown_url_is_a_failure_rather_than_an_empty_page(self):
        source, _api = build_source()
        with self.assertRaisesMessage(SourceFetchError, "fixture missing"):
            source.client.programme_page("https://www.raiplay.it/programmi/nope")

    def test_a_stale_programme_entry_never_fails_the_walk(self):
        # 404 on one programme is a skip, not a failed refresh.
        source, api = build_source()
        result = source.fetch()
        self.assertIn(RETIRED, api.programme_requests)
        self.assertEqual(len(result.contents), TOTAL_CONTENTS)
        self.assertEqual(len(result.offers), TOTAL_OFFERS)


class StateDecodingTests(SimpleTestCase):
    def test_the_recorded_programme_page_decodes_to_its_hydration_object(self):
        state = _state_from_payload(FakeRaiPlayApi().pages[FOLLEMENTE], url=FOLLEMENTE)
        self.assertEqual(state["data_type"], "program")
        self.assertEqual(state["layout"], "single")
        self.assertEqual(state["uniquename"], FOLLEMENTE_ID)
        self.assertEqual(state["first_item"],
                         "/video/2026/09/Follemente-05d711e6-e70a-401f-8dc4-982fa42e85ae.json")

    def test_a_bare_json_record_decodes_without_the_marker(self):
        state = _state_from_payload(load_record(), url=FOLLEMENTE_VIDEO)
        self.assertEqual(state["type"], "RaiPlay Video Item")
        self.assertIsNone(state.get("data_type"))
        self.assertNotIn("schema", state)
        self.assertEqual(
            state["weblink"],
            "/video/2026/09/Follemente-05d711e6-e70a-401f-8dc4-982fa42e85ae.html",
        )

    def test_an_html_page_without_the_marker_is_not_read_as_json(self):
        html = '<!DOCTYPE html><html><script>var x = {"a": 1};</script></html>'
        with self.assertRaisesMessage(SourceFetchError, "no window.WashiContext"):
            _state_from_payload(html, url="x")

    def test_a_missing_marker_is_a_failure_not_an_empty_state(self):
        with self.assertRaisesMessage(SourceFetchError, "no window.WashiContext"):
            _state_from_payload("<!DOCTYPE html><html></html>", url="x")

    def test_a_marker_without_an_object_is_a_failure(self):
        with self.assertRaisesMessage(SourceFetchError, "carries no object"):
            _state_from_payload('<script>window.WashiContext = ["x"];</script>', url="x")

    def test_a_value_that_is_not_strict_json_is_a_failure(self):
        with self.assertRaisesMessage(SourceFetchError, "Malformed"):
            _state_from_payload('<script>window.WashiContext = {"a": oops};</script>',
                                url="x")

    def test_a_trailer_slug_is_recognized_from_the_record_own_url(self):
        trailer = {"weblink": "/video/2026/09/Settembre---Trailer.html",
                   "path_id": "/video/2026/09/Settembre---Trailer.json"}
        clean = {"weblink": "/video/2026/09/Follemente-05d711e6.html",
                 "path_id": "/video/2026/09/Follemente-05d711e6.json"}
        self.assertTrue(_is_trailer(trailer))
        self.assertFalse(_is_trailer(clean))
        self.assertFalse(_is_trailer({}))


class RaiPlayHttpBudgetUnitTests(SimpleTestCase):
    def test_limit_must_be_positive_integer(self):
        for invalid in (0, -1, -100, None, "10", 3.14, []):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    RaiPlayHttpBudget(invalid)

    def test_reserve_increments_attempted_and_exhausts_with_structured_exception(self):
        budget = RaiPlayHttpBudget(limit=2)
        self.assertEqual(budget.attempted, 0)

        budget.reserve("https://www.raiplay.it/1", stage="sitemap")
        self.assertEqual(budget.attempted, 1)

        budget.reserve("https://www.raiplay.it/2", stage="programme")
        self.assertEqual(budget.attempted, 2)

        with self.assertRaises(RaiPlayHttpBudgetExceeded) as ctx:
            budget.reserve("https://www.raiplay.it/3", stage="video")

        exc = ctx.exception
        self.assertEqual(exc.limit, 2)
        self.assertEqual(exc.attempted, 2)
        self.assertEqual(exc.blocked_url, "https://www.raiplay.it/3")
        self.assertEqual(exc.stage, "video")
        self.assertIn("limit=2", str(exc))
        self.assertIn("attempted=2", str(exc))
        self.assertIn("stage='video'", str(exc))
        # Attempted count does not increment on failed reservation
        self.assertEqual(budget.attempted, 2)

    def test_thread_safe_reservation(self):
        limit = 25
        budget = RaiPlayHttpBudget(limit=limit)
        successful = []
        exhausted = []

        def worker(idx):
            try:
                budget.reserve(f"https://www.raiplay.it/{idx}")
                successful.append(idx)
            except RaiPlayHttpBudgetExceeded:
                exhausted.append(idx)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(budget.attempted, limit)
        self.assertEqual(len(successful), limit)
        self.assertEqual(len(exhausted), 25)


class RaiPlayUrlValidationTests(SimpleTestCase):
    def test_allowed_raiplay_hosts_and_https_are_accepted(self):
        _validate_raiplay_url("https://www.raiplay.it/programmi/follemente")
        _validate_raiplay_url("https://raiplay.it/sitemap.xml")

    def test_insecure_scheme_is_rejected(self):
        with self.assertRaises(SourceFetchError) as ctx:
            _validate_raiplay_url("http://www.raiplay.it/programmi/follemente")
        self.assertIn("insecure scheme", str(ctx.exception))

    def test_arbitrary_external_hosts_are_rejected(self):
        for bad_url in (
            "https://attacker.com/evil",
            "https://raiplay.it.evil.com/fake",
            "https://google.com/",
            "https://sub.raiplay.it/foo",
        ):
            with self.subTest(bad_url=bad_url):
                with self.assertRaises(SourceFetchError) as ctx:
                    _validate_raiplay_url(bad_url)
                self.assertIn("disallowed host", str(ctx.exception))

    def test_disallowed_ports_are_rejected(self):
        with self.assertRaises(SourceFetchError) as ctx:
            _validate_raiplay_url("https://www.raiplay.it:8443/programmi")
        self.assertIn("disallowed port", str(ctx.exception))


class RaiPlayRedirectAndBudgetTests(SimpleTestCase):
    @patch("requests.get")
    def test_initial_request_reserves_one_token(self, mock_get):
        from availability.sources.rai_play import USER_AGENT
        mock_response = MagicMock(status_code=200, text="<xml></xml>")
        mock_get.return_value = mock_response

        budget = RaiPlayHttpBudget(limit=5)
        client = RaiPlayClient(delay=0, budget=budget)
        client._http("https://www.raiplay.it/sitemap.xml", stage="sitemap_index")

        self.assertEqual(budget.attempted, 1)
        mock_get.assert_called_once_with(
            "https://www.raiplay.it/sitemap.xml",
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=client.timeout,
            allow_redirects=False,
        )

    @patch("requests.get")
    def test_each_redirect_hop_consumes_one_token(self, mock_get):
        r1 = MagicMock(status_code=301, headers={"Location": "/hop1"})
        r2 = MagicMock(status_code=302, headers={"Location": "https://www.raiplay.it/hop2"})
        r3 = MagicMock(status_code=200, text="ok")
        mock_get.side_effect = [r1, r2, r3]

        budget = RaiPlayHttpBudget(limit=5)
        client = RaiPlayClient(delay=0, budget=budget)
        text = client._http("https://www.raiplay.it/start", stage="programme")

        self.assertEqual(text, "ok")
        self.assertEqual(budget.attempted, 3)
        self.assertEqual(mock_get.call_count, 3)

    @patch("requests.get")
    def test_redirect_to_external_host_rejected_without_network_dispatch(self, mock_get):
        r1 = MagicMock(status_code=302, headers={"Location": "https://evil.com/phish"})
        mock_get.side_effect = [r1]

        budget = RaiPlayHttpBudget(limit=5)
        client = RaiPlayClient(delay=0, budget=budget)

        with self.assertRaises(SourceFetchError) as ctx:
            client._http("https://www.raiplay.it/start")

        self.assertIn("disallowed host", str(ctx.exception))
        # Initial request consumed 1 token, but evil.com was NEVER dispatched
        self.assertEqual(budget.attempted, 1)
        self.assertEqual(mock_get.call_count, 1)

    @patch("requests.get")
    def test_redirect_to_insecure_scheme_rejected_without_dispatch(self, mock_get):
        r1 = MagicMock(status_code=301, headers={"Location": "http://www.raiplay.it/unencrypted"})
        mock_get.side_effect = [r1]

        budget = RaiPlayHttpBudget(limit=5)
        client = RaiPlayClient(delay=0, budget=budget)

        with self.assertRaises(SourceFetchError) as ctx:
            client._http("https://www.raiplay.it/start")

        self.assertIn("insecure scheme", str(ctx.exception))
        self.assertEqual(budget.attempted, 1)
        self.assertEqual(mock_get.call_count, 1)

    @patch("requests.get")
    def test_redirect_missing_location_header_raises_error(self, mock_get):
        r1 = MagicMock(status_code=302, headers={})
        mock_get.side_effect = [r1]

        budget = RaiPlayHttpBudget(limit=5)
        client = RaiPlayClient(delay=0, budget=budget)

        with self.assertRaises(SourceFetchError) as ctx:
            client._http("https://www.raiplay.it/start")

        self.assertIn("missing Location header", str(ctx.exception))
        self.assertEqual(budget.attempted, 1)

    @patch("requests.get")
    def test_redirect_loop_detected(self, mock_get):
        r1 = MagicMock(status_code=302, headers={"Location": "https://www.raiplay.it/loop"})
        r2 = MagicMock(status_code=302, headers={"Location": "https://www.raiplay.it/start"})
        mock_get.side_effect = [r1, r2]

        budget = RaiPlayHttpBudget(limit=5)
        client = RaiPlayClient(delay=0, budget=budget)

        with self.assertRaises(SourceFetchError) as ctx:
            client._http("https://www.raiplay.it/start")

        self.assertIn("redirect loop detected", str(ctx.exception))
        self.assertEqual(budget.attempted, 2)

    @patch("requests.get")
    def test_max_redirect_hops_exceeded_raises_error(self, mock_get):
        responses = [
            MagicMock(status_code=302, headers={"Location": f"/hop{i}"})
            for i in range(1, 7)
        ]
        mock_get.side_effect = responses

        budget = RaiPlayHttpBudget(limit=10)
        client = RaiPlayClient(delay=0, budget=budget)

        with self.assertRaises(SourceFetchError) as ctx:
            client._http("https://www.raiplay.it/start")

        self.assertIn(f"exceeded maximum redirect hops ({MAX_REDIRECT_HOPS})", str(ctx.exception))
        # 1 initial + 5 hops dispatched = 6 attempts made
        self.assertEqual(budget.attempted, 6)

    @patch("requests.get")
    def test_budget_exhaustion_on_redirect_hop_blocks_next_dispatch(self, mock_get):
        r1 = MagicMock(status_code=301, headers={"Location": "/next_page"})
        mock_get.side_effect = [r1]

        # Budget allows exactly 1 attempt; initial request consumes it, redirect hop must fail
        budget = RaiPlayHttpBudget(limit=1)
        client = RaiPlayClient(delay=0, budget=budget)

        with self.assertRaises(RaiPlayHttpBudgetExceeded) as ctx:
            client._http("https://www.raiplay.it/start", stage="video")

        self.assertEqual(ctx.exception.limit, 1)
        self.assertEqual(ctx.exception.attempted, 1)
        self.assertEqual(ctx.exception.blocked_url, "https://www.raiplay.it/next_page")
        self.assertEqual(ctx.exception.stage, "video")
        self.assertEqual(mock_get.call_count, 1)

    def test_live_http_without_budget_and_without_requester_raises_configuration_error(self):
        client = RaiPlayClient()
        with self.assertRaises(RaiPlayConfigurationError) as ctx:
            client._http("https://www.raiplay.it/sitemap.xml")
        self.assertIn("finite HTTP budget", str(ctx.exception))


class RaiPlaySourceBudgetTests(SimpleTestCase):
    def test_budget_created_once_per_fetch_invocation(self):
        source = RaiPlaySource(
            client=RaiPlayClient(requester=FakeRaiPlayApi()),
            max_http_requests=50,
        )
        source.fetch()
        first_budget = source.client.budget
        self.assertIsNotNone(first_budget)
        self.assertEqual(first_budget.limit, 50)

        source.fetch()
        second_budget = source.client.budget
        self.assertIsNotNone(second_budget)
        self.assertEqual(second_budget.limit, 50)
        self.assertIsNot(first_budget, second_budget)

    def test_fetch_without_configured_budget_in_production_raises_configuration_error(self):
        source = RaiPlaySource()  # Live client without requester or budget
        with self.assertRaises(RaiPlayConfigurationError):
            source.fetch()
