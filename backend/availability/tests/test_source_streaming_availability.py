"""StreamingAvailabilitySource -> DiscoveredContent/ProviderOffer normalization.

Covers Phase 3 source-normalization requirements: content/offer/country/language
normalization, season preservation, and TMDB id propagation. No database writes
happen here — persistence is the refresh service's job.
"""
from unittest.mock import MagicMock

from django.test import TestCase

from availability.domain.enums import ContentType, OfferType
from availability.services.normalize import normalize_content_type
from availability.sources.streaming_availability import StreamingAvailabilitySource
from tracker.models import Movie
from tracker.services.streaming_api import StreamingOffer, StreamingResult, StreamingSeason


def _offer(country="us", service_id="netflix", offer_type="subscription", **kwargs):
    kwargs.setdefault("service_name", service_id.title())
    kwargs.setdefault("quality", "4K")
    kwargs.setdefault("link", "https://example.test/watch")
    kwargs.setdefault("audio_languages", ["en"])
    kwargs.setdefault("subtitle_languages", ["en"])
    return StreamingOffer(country=country, service_id=service_id, offer_type=offer_type, **kwargs)


def _source(result):
    client = MagicMock()
    client.get_availability.return_value = result
    return StreamingAvailabilitySource(client=client), client


class StreamingAvailabilitySourceTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)
        self.series = Movie.objects.create(
            tmdb_id=1396,
            title="Breaking Bad",
            year=2008,
            content_type="series",
            seasons_data=[
                {"season_number": 1, "name": "Season 1"},
                {"season_number": 2, "name": "Season 2"},
            ],
        )

    def test_movie_response_normalizes_to_a_provider_offer(self):
        result = StreamingResult(tmdb_id=550, offers=[_offer()])
        source, client = _source(result)

        response = source.fetch(self.movie)

        client.get_availability.assert_called_once_with(550, content_type="movie")
        self.assertFalse(response.authoritative)
        self.assertEqual(len(response.offers), 1)
        offer = response.offers[0]
        self.assertEqual(offer.provider_id, "netflix")
        self.assertEqual(offer.country, "US")
        self.assertEqual(offer.content_type, ContentType.MOVIE)
        self.assertEqual(offer.tmdb_id, 550)
        self.assertEqual(offer.offer_type, OfferType.SUBSCRIPTION)
        self.assertEqual(offer.provider_url, "https://example.test/watch")
        self.assertEqual(offer.quality, "4k")
        self.assertIsNone(offer.season_number)
        self.assertIsNone(offer.episode_number)

    def test_series_response_normalizes_to_a_series_offer(self):
        result = StreamingResult(
            tmdb_id=1396,
            offers=[_offer(service_id="prime")],
        )
        source, client = _source(result)

        response = source.fetch(self.series)

        # The series row must refresh through the /shows/tv/ twin endpoint.
        client.get_availability.assert_called_once_with(1396, content_type="series")
        offer = response.offers[0]
        self.assertEqual(offer.content_type, ContentType.SERIES)
        self.assertIsNone(offer.season_number, "a series-level offer must not become a season offer")
        self.assertEqual(offer.tmdb_id, 1396)

    def test_season_offer_preserves_season_number(self):
        result = StreamingResult(
            tmdb_id=1396,
            offers=[_offer(service_id="prime")],
            seasons=[
                StreamingSeason(title="Season 2", offers=[_offer(service_id="hulu")]),
            ],
        )
        source, _client = _source(result)

        response = source.fetch(self.series)

        series_offer = next(o for o in response.offers if o.content_type == ContentType.SERIES)
        season_offer = next(o for o in response.offers if o.content_type == ContentType.SEASON)
        self.assertIsNone(series_offer.season_number)
        self.assertEqual(season_offer.season_number, 2)
        self.assertIsNone(season_offer.episode_number, "season offers are not episode offers")
        self.assertEqual(season_offer.tmdb_id, 1396)
        # Series-level and season-level offers are different normalized objects.
        self.assertNotEqual(series_offer.provider_content_id, season_offer.provider_content_id)

    def test_episode_content_type_normalization_stays_distinct_from_season(self):
        # This source never exposes episode-level data; the canonical mapping
        # still keeps episode distinct so a future source can produce it.
        self.assertEqual(normalize_content_type("episode"), ContentType.EPISODE)
        self.assertEqual(normalize_content_type("season"), ContentType.SEASON)
        result = StreamingResult(
            tmdb_id=1396,
            seasons=[StreamingSeason(title="Season 1", offers=[_offer()])],
        )
        source, _client = _source(result)
        response = source.fetch(self.series)
        self.assertTrue(all(o.episode_number is None for o in response.offers))

    def test_provider_id_is_lowercased_and_trimmed(self):
        result = StreamingResult(tmdb_id=550, offers=[_offer(service_id=" PRIME ")])
        source, _client = _source(result)

        response = source.fetch(self.movie)

        self.assertEqual(response.offers[0].provider_id, "prime")

    def test_country_is_normalized_to_uppercase(self):
        result = StreamingResult(tmdb_id=550, offers=[_offer(country="nl")])
        source, _client = _source(result)

        response = source.fetch(self.movie)

        self.assertEqual(response.offers[0].country, "NL")

    def test_malformed_country_fails_the_refresh_loudly(self):
        # §18: a malformed response must fail the refresh, never write garbage.
        result = StreamingResult(tmdb_id=550, offers=[_offer(country="NLD")])
        source, _client = _source(result)

        with self.assertRaises(ValueError):
            source.fetch(self.movie)

    def test_offer_type_normalization_at_the_adapter(self):
        result = StreamingResult(
            tmdb_id=550,
            offers=[_offer(offer_type="subscription"), _offer(service_id="pluto", offer_type="free")],
        )
        source, _client = _source(result)

        response = source.fetch(self.movie)

        self.assertEqual(
            {o.offer_type for o in response.offers},
            {OfferType.SUBSCRIPTION, OfferType.FREE},
        )

    def test_audio_languages_normalize_to_canonical_uppercase(self):
        result = StreamingResult(
            tmdb_id=550,
            offers=[_offer(audio_languages=["eng", "it"], subtitle_languages=["eng"])],
        )
        source, _client = _source(result)

        response = source.fetch(self.movie)

        self.assertEqual(response.offers[0].audio_languages, ["EN", "IT"])
        self.assertEqual(response.offers[0].subtitle_languages, ["EN"])

    def test_subtitle_languages_normalize_to_canonical_uppercase(self):
        result = StreamingResult(
            tmdb_id=550,
            offers=[_offer(audio_languages=["en"], subtitle_languages=["jpn", "eng"])],
        )
        source, _client = _source(result)

        response = source.fetch(self.movie)

        self.assertEqual(response.offers[0].subtitle_languages, ["JA", "EN"])

    def test_unknown_languages_remain_none(self):
        result = StreamingResult(
            tmdb_id=550,
            offers=[_offer(audio_languages=None, subtitle_languages=None)],
        )
        source, _client = _source(result)

        response = source.fetch(self.movie)

        self.assertIsNone(response.offers[0].audio_languages)
        self.assertIsNone(response.offers[0].subtitle_languages)

    def test_known_empty_language_list_remains_empty(self):
        result = StreamingResult(
            tmdb_id=550,
            offers=[_offer(audio_languages=[], subtitle_languages=[])],
        )
        source, _client = _source(result)

        response = source.fetch(self.movie)

        self.assertEqual(response.offers[0].audio_languages, [])
        self.assertEqual(response.offers[0].subtitle_languages, [])

    def test_tmdb_id_is_preserved(self):
        result = StreamingResult(tmdb_id=550, offers=[_offer()])
        source, _client = _source(result)

        response = source.fetch(self.movie)

        self.assertEqual(response.offers[0].tmdb_id, 550)
        self.assertEqual(response.contents[0].external_ids.get("tmdb"), "550")

    def test_source_never_claims_authoritativeness(self):
        # §12: a title-targeted endpoint is not a complete catalogue snapshot.
        result = StreamingResult(tmdb_id=550, offers=[_offer()])
        source, _client = _source(result)

        self.assertFalse(source.fetch(self.movie).authoritative)

    def test_fetch_accepts_a_persistence_free_content_request(self):
        # Phase 4: the interface consumes ContentRequest, but the adapter still
        # tolerates the tracker object directly — results must be identical.
        from availability.sources import ContentRequest

        result = StreamingResult(tmdb_id=550, offers=[_offer()])
        source, _client = _source(result)

        from_movie = source.fetch(self.movie)
        from_request = source.fetch(ContentRequest.from_content(self.movie))

        self.assertEqual(from_movie.offers, from_request.offers)
        self.assertEqual(from_movie.contents, from_request.contents)

    def test_fetch_requires_content_and_ignores_scope_hints(self):
        # Title-targeted source: no content -> loud failure; provider/country
        # hints are accepted for interface compatibility but change nothing.
        result = StreamingResult(tmdb_id=550, offers=[_offer()])
        source, _client = _source(result)

        with self.assertRaises(ValueError):
            source.fetch(None)

        scoped = source.fetch(self.movie, provider="netflix", country="us")
        self.assertEqual(len(scoped.offers), 1)
        self.assertEqual(scoped.offers[0].country, "US")
