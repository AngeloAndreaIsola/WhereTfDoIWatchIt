"""Read path: the tracker offer adapter consumes the reconciled view.

``get_local_offers`` is what HTTP handlers, ranking, filtering and watchability
consume. It must expose one offer per reconciled group, only when reconciliation
judges it available, with the field mapping the response layer has always
expected — and it must keep reaching no network, no refresh, no writes.
"""
from dataclasses import replace
from datetime import UTC, timedelta, timezone
from unittest.mock import patch

from django.test import TestCase

from availability.domain.enums import ContentType, OfferType, SourceType
from availability.models import Source
from availability.tests.freshness_helpers import declare_freshness
from availability.tests.reconciliation_fixtures import ReconciliationFixtureMixin
from tracker.models import Movie, WatchlistItem
from tracker.services.availability_reads import _tracker_offer, get_local_offers
from tracker.services.offers import Offer


class LocalOffersReconciliationTests(ReconciliationFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        declare_freshness(self, "recon-source-a", "recon-source-b")
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

    def test_missing_window_stays_unknown_despite_a_verified_observation(self):
        self.offer(last_seen=self.now)

        [offer] = get_local_offers(self.movie)
        response = self.client.get(f"/api/movie/{self.movie.tmdb_id}/availability/")

        self.assertEqual(offer.last_seen_at, self.now.isoformat())
        self.assertIsNone(offer.available_from)
        self.assertIsNone(offer.available_until)
        self.assertEqual(response.status_code, 200)
        [payload] = response.json()["offers"]
        self.assertIsNone(payload["available_from"])
        self.assertIsNone(payload["available_until"])

    def test_agreed_window_survives_ranked_and_watchlist_serialization(self):
        start = self.now - timedelta(days=1)
        end = self.now + timedelta(days=2)
        self.offer(available_from=start, available_until=end)
        self.offer(
            source=self.source_b, provider_content_id="second-source",
            available_from=start, available_until=end,
        )
        WatchlistItem.objects.create(user_id=1, movie=self.movie)

        [offer] = get_local_offers(self.movie)
        ranked = self.client.get(f"/api/movie/{self.movie.tmdb_id}/availability/")
        watchlist = self.client.get("/api/watchlist/report/")

        self.assertEqual(offer.available_from, start.isoformat())
        self.assertEqual(offer.available_until, end.isoformat())
        self.assertEqual(ranked.status_code, 200)
        self.assertEqual(watchlist.status_code, 200)
        [ranked_offer] = ranked.json()["offers"]
        [watchlist_item] = watchlist.json()["report"]
        [watchlist_offer] = watchlist_item["watchability"]["excluded_offers"]
        for payload in (ranked_offer, watchlist_offer):
            self.assertEqual(payload["available_from"], start.isoformat())
            self.assertEqual(payload["available_until"], end.isoformat())

    def test_disputed_end_stays_unknown_while_agreed_start_survives(self):
        start = self.now - timedelta(days=1)
        self.offer(available_from=start, available_until=self.now + timedelta(days=2))
        self.offer(
            source=self.source_b, provider_content_id="second-source",
            available_from=start, available_until=self.now + timedelta(days=3),
        )

        [offer] = get_local_offers(self.movie)

        self.assertEqual(offer.available_from, start.isoformat())
        self.assertIsNone(offer.available_until)

    def test_justwatch_expiry_requires_independent_support(self):
        justwatch, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JustWatch", "type": SourceType.AGGREGATOR},
        )
        end = self.now + timedelta(days=2)
        self.offer(source=justwatch, available_until=end)
        self.offer(provider_content_id="no-declared-window")

        [offer] = get_local_offers(self.movie)
        self.assertIsNone(offer.available_until)

        self.offer(provider_content_id="independent-expiry", available_until=end)

        [offer] = get_local_offers(self.movie)
        self.assertEqual(offer.available_until, end.isoformat())

    def test_window_projection_serializes_offset_aware_dates_in_utc(self):
        end = self.now.astimezone(timezone(timedelta(hours=5, minutes=30))) + timedelta(days=2)
        self.offer(available_until=end)
        [reconciled] = self.reconcile_movie().offers
        [observation] = reconciled.observations
        # PostgreSQL normally returns UTC. Exercise the projection with an
        # equivalent date represented in the source's offset.
        reconciled = replace(
            reconciled, available_until=end,
            observations=(replace(observation, available_until=end),),
        )

        self.assertEqual(_tracker_offer(reconciled).available_until, end.astimezone(UTC).isoformat())

    def test_country_filter_applies_to_the_reconciled_view(self):
        self.offer(provider_content_id="us", country=self.us)
        self.offer(provider_content_id="nl", country=self.nl)

        self.assertEqual(
            [offer.country for offer in get_local_offers(self.movie, country="nl")],
            ["NL"],
        )
        self.assertEqual(len(get_local_offers(self.movie)), 2)

    @patch("tracker.services.enrichment.enrich_movie_from_tmdb")
    def test_series_detail_keeps_windows_at_their_season_and_episode_scope(self, enrich):
        series = Movie.objects.create(
            tmdb_id=900, title="Series", year=2020, content_type="series",
        )
        self.offer(
            tmdb_id=900, content_type=ContentType.SERIES,
            season_number=1, episode_number=1, provider_content_id="s1e1",
            available_until=self.now + timedelta(days=1),
        )
        self.offer(
            tmdb_id=900, content_type=ContentType.SEASON, season_number=1,
            provider_content_id="s1", offer_type=OfferType.FREE,
            available_until=self.now + timedelta(days=2),
        )
        self.offer(
            tmdb_id=900, content_type=ContentType.SERIES,
            season_number=2, episode_number=1, provider_content_id="s2e1",
        )

        self.assertEqual(
            {(o.season_number, o.episode_number, o.available_until)
             for o in get_local_offers(series, season=1)},
            {(1, 1, (self.now + timedelta(days=1)).isoformat()),
             (1, None, (self.now + timedelta(days=2)).isoformat())},
        )
        season_one = self.client.get("/api/prime/series/900/?season=1")
        season_two = self.client.get("/api/prime/series/900/?season=2")

        self.assertEqual(season_one.status_code, 200)
        self.assertEqual(season_two.status_code, 200)
        self.assertEqual(
            {(o["season_number"], o.get("episode_number"), o["available_until"])
             for o in season_one.json()["offers"]},
            {(1, 1, (self.now + timedelta(days=1)).isoformat()),
             (1, None, (self.now + timedelta(days=2)).isoformat())},
        )
        [other_season] = season_two.json()["offers"]
        self.assertEqual(other_season["season_number"], 2)
        self.assertNotIn("available_until", other_season)

    @patch("tracker.services.enrichment.enrich_movie_from_tmdb")
    def test_movie_and_series_with_same_tmdb_id_keep_distinct_windows(self, enrich):
        tmdb_id = self.movie.tmdb_id
        Movie.objects.create(tmdb_id=tmdb_id, title="Series", year=2020, content_type="series")
        movie_end = self.now + timedelta(days=1)
        series_end = self.now + timedelta(days=2)
        self.offer(tmdb_id=tmdb_id, available_until=movie_end)
        self.offer(tmdb_id=tmdb_id, content_type=ContentType.SERIES, available_until=series_end)

        for kind, end in (("movie", movie_end), ("series", series_end)):
            with self.subTest(content_type=kind):
                response = self.client.get(f"/api/prime/{kind}/{tmdb_id}/")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload[kind]["tmdb_id"], tmdb_id)
                self.assertEqual(payload[kind]["content_type"], kind)
                [offer] = payload["offers"]
                self.assertEqual(offer["available_until"], end.isoformat())

    def test_no_local_rows_returns_no_offers(self):
        self.assertEqual(get_local_offers(self.movie), [])

    @patch("tracker.services.enrichment.enrich_movie_from_tmdb")
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
