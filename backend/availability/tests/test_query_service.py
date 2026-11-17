from unittest.mock import patch

from django.test import TestCase

from availability.domain.enums import ContentType, OfferType, SourceType
from availability.models import Country, Offer, Source
from availability.services.query import AvailabilityQueryService
from tracker.models import Provider


class AvailabilityQueryServiceTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(slug="local-query-netflix", defaults={"name": "Netflix"})
        self.source, _ = Source.objects.get_or_create(
            slug="availability-api", defaults={"name": "Availability API", "type": SourceType.API}
        )
        self.us, _ = Country.objects.get_or_create(code="US", defaults={"name": "United States"})
        self.nl, _ = Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands"})
        self.service = AvailabilityQueryService()

    def offer(self, *, tmdb_id=10, content_type=ContentType.MOVIE, country=None,
              offer_type=OfferType.SUBSCRIPTION, currently_available=True,
              audio_languages=None, provider_content_id="", season_number=None,
              episode_number=None):
        country = country or self.us
        if content_type == ContentType.SERIES:
            season_number = 1 if season_number is None else season_number
            episode_number = 1 if episode_number is None else episode_number
        return Offer.objects.create(
            provider=self.provider,
            country=country,
            source=self.source,
            content_type=content_type,
            tmdb_id=tmdb_id,
            season_number=season_number,
            episode_number=episode_number,
            offer_type=offer_type,
            provider_content_id=provider_content_id,
            audio_languages=audio_languages,
            subtitle_languages=[],
            currently_available=currently_available,
        )

    def test_returns_current_movie_offers_only(self):
        current = self.offer(tmdb_id=100)
        self.offer(tmdb_id=100, currently_available=False, provider_content_id="old")
        self.offer(tmdb_id=101, provider_content_id="other")

        self.assertEqual(list(self.service.get_for_content(100, ContentType.MOVIE)), [current])

    def test_returns_series_offers(self):
        current = self.offer(tmdb_id=200, content_type=ContentType.SERIES)

        self.assertEqual(list(self.service.get_for_content(200, ContentType.SERIES)), [current])

    def test_filters_by_country_without_hiding_other_countries_by_default(self):
        us_offer = self.offer(tmdb_id=300)
        nl_offer = self.offer(tmdb_id=300, country=self.nl, provider_content_id="nl")

        self.assertEqual(set(self.service.get_for_content(300, ContentType.MOVIE)), {us_offer, nl_offer})
        self.assertEqual(list(self.service.get_for_content(300, ContentType.MOVIE, country="nl")), [nl_offer])

    def test_preserves_offer_type_and_distinguishes_null_from_empty_languages(self):
        unknown_audio = self.offer(tmdb_id=400, audio_languages=None)
        explicit_no_audio = self.offer(
            tmdb_id=400,
            offer_type=OfferType.FREE,
            audio_languages=[],
            provider_content_id="free",
        )

        rows = list(self.service.get_for_content(400, ContentType.MOVIE))
        self.assertEqual({row.offer_type for row in rows}, {OfferType.SUBSCRIPTION, OfferType.FREE})
        self.assertIs(next(row for row in rows if row.pk == unknown_audio.pk).audio_languages, None)
        self.assertEqual(next(row for row in rows if row.pk == explicit_no_audio.pk).audio_languages, [])

    def test_returns_empty_queryset_for_unknown_local_content(self):
        self.assertEqual(list(self.service.get_for_content(999, ContentType.MOVIE)), [])

    @patch("tracker.services.streaming_api.StreamingAvailabilityClient.get_availability")
    def test_never_fetches_externally(self, get_availability):
        get_availability.side_effect = AssertionError("query service fetched externally")

        list(self.service.get_for_content(999, ContentType.MOVIE))
        get_availability.assert_not_called()
