"""Read path: the tracker offer adapter consumes the reconciled view.

``get_local_offers`` is what HTTP handlers, ranking, filtering and watchability
consume. It must expose one offer per reconciled group, only when reconciliation
judges it available, with the field mapping the response layer has always
expected — and it must keep reaching no network, no refresh, no writes.
"""
from unittest.mock import patch

from django.test import TestCase

from availability.domain.enums import ContentType, OfferType
from availability.tests.reconciliation_fixtures import ReconciliationFixtureMixin
from tracker.models import Movie
from tracker.services.availability_reads import get_local_offers
from tracker.services.offers import Offer


class LocalOffersReconciliationTests(ReconciliationFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)

    def test_only_available_offers_reach_the_tracker(self):
        self.offer(
            provider_content_id="sub-live",
            provider_url="https://example.test/live",
            quality="4K",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )
        # Still one offer: another source deactivating it does not outweigh
        # a source that still lists it.
        self.offer(provider_content_id="sub-gone", currently_available=False)
        # Nothing lists this one anymore, so it never reaches ranking.
        self.offer(
            provider_content_id="free-gone",
            offer_type=OfferType.FREE,
            currently_available=False,
        )

        offers = get_local_offers(self.movie)

        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].offer_type, OfferType.SUBSCRIPTION)

    def test_observations_from_two_sources_present_one_offer(self):
        self.offer(
            provider_content_id="sa-550",
            provider_url="https://a.test/550",
            quality="4K",
            audio_languages=["EN"],
        )
        self.offer(provider_content_id="sb-550", source=self.source_b)

        offers = get_local_offers(self.movie)

        self.assertEqual(len(offers), 1)
        self.assertEqual(
            [offer.service for offer in offers], ["recon-netflix"],
        )

    def test_fields_keep_the_tracker_representation(self):
        self.offer(
            provider_content_id="pc",
            provider_url="https://example.test/watch",
            quality="4K",
            audio_languages=["EN", "IT"],
            subtitle_languages=["EN"],
        )

        [offer] = get_local_offers(self.movie)

        self.assertIsInstance(offer, Offer)
        self.assertEqual(offer.country, "US")
        self.assertEqual(offer.service, "recon-netflix")
        self.assertEqual(offer.offer_type, "subscription")
        self.assertEqual(offer.quality, "4K")
        self.assertEqual(offer.url, "https://example.test/watch")
        self.assertEqual(offer.audio_languages, ["EN", "IT"])
        self.assertEqual(offer.subtitle_languages, ["EN"])
        self.assertIsInstance(offer.audio_languages, list)
        # Dict-like access the response layer relies on still works.
        self.assertEqual(offer.get("country"), "US")
        self.assertEqual(offer.get("service"), "recon-netflix")

    def test_language_fields_are_none_when_no_source_reports_them(self):
        self.offer(provider_content_id="pc")

        [offer] = get_local_offers(self.movie)

        self.assertIsNone(offer.audio_languages)
        self.assertIsNone(offer.subtitle_languages)

    def test_country_filter_applies_to_the_reconciled_view(self):
        self.offer(provider_content_id="us", country=self.us)
        self.offer(provider_content_id="nl", country=self.nl)

        self.assertEqual(
            [offer.country for offer in get_local_offers(self.movie, country="nl")],
            ["NL"],
        )
        self.assertEqual(len(get_local_offers(self.movie)), 2)

    def test_series_reads_reconcile_series_and_season_rows(self):
        series = Movie.objects.create(
            tmdb_id=900, title="Series", year=2020, content_type="series",
        )
        self.offer(
            tmdb_id=900, content_type=ContentType.SERIES,
            season_number=1, episode_number=1, provider_content_id="s1e1",
        )
        self.offer(
            tmdb_id=900, content_type=ContentType.SEASON, season_number=1,
            provider_content_id="s1", offer_type=OfferType.FREE,
        )
        self.offer(
            tmdb_id=900, content_type=ContentType.SERIES,
            season_number=2, episode_number=1, provider_content_id="s2e1",
        )

        self.assertEqual(len(get_local_offers(series, season=1)), 2)
        self.assertEqual(len(get_local_offers(series, season=2)), 1)

    def test_no_local_rows_returns_no_offers(self):
        self.assertEqual(get_local_offers(self.movie), [])

    @patch("tracker.services.offer_pipeline.enrich_movie_from_tmdb")
    def test_availability_endpoint_serves_the_reconciled_view(self, enrich):
        self.offer(provider_content_id="sa-550", quality="4K")
        self.offer(provider_content_id="sb-550", source=self.source_b, quality="4K")

        response = self.client.get(f"/api/movie/{self.movie.tmdb_id}/availability/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        # Two raw observations, one effective offer for the viewer.
        self.assertEqual(len(payload["offers"]), 1)
        self.assertEqual(payload["cache"]["offers"], 1)
        enrich.assert_not_called()

    @patch("tracker.services.streaming_api.StreamingAvailabilityClient.get_availability")
    @patch("availability.services.refresh.AvailabilityRefreshService.refresh_content")
    @patch("availability.services.refresh.AvailabilityRefreshService.refresh_catalogue")
    def test_reads_never_fetch_or_refresh(self, refresh_catalogue, refresh_content,
                                          fetch):
        self.offer(provider_content_id="pc")

        offers = get_local_offers(self.movie)
        response = self.client.get(f"/api/movie/{self.movie.tmdb_id}/availability/")

        self.assertEqual(len(offers), 1)
        self.assertEqual(response.status_code, 200)
        fetch.assert_not_called()
        refresh_content.assert_not_called()
        refresh_catalogue.assert_not_called()
