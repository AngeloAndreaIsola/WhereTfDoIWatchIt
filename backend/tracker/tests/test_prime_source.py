"""Prime Video gap-fill source (TMDb watch/providers) and its pipeline merge.

The primary source has no ``prime`` subscription row for some titles that are
genuinely on Prime (The Office 2005: upstream carries only a CA addon and AT
buy row). These tests pin the source's contract: read-only, gap-only, Prime
storefront only, and never fatal.
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings

from tracker.models import Movie, Provider, Subscription
from tracker.services.offer_pipeline import OfficialOffersPipeline
from tracker.services.offers import Offer
from tracker.services.prime_source import prime_offers


def _regions():
    """TMDb watch/providers shape for a title Prime carries in NL/DE/US."""
    return {
        "NL": {
            "link": "https://www.themoviedb.org/tv/2316-the-office/watch?locale=NL",
            "flatrate": [
                {"provider_id": 119, "provider_name": "Amazon Prime Video"},
                {"provider_id": 8, "provider_name": "Netflix"},
            ],
        },
        "DE": {
            "link": "https://www.themoviedb.org/tv/2316-the-office/watch?locale=DE",
            "flatrate": [{"provider_id": 9, "provider_name": "Amazon Prime Video"}],
            "buy": [{"provider_id": 10, "provider_name": "Amazon Video"}],
        },
        "GB": {
            "link": "https://www.themoviedb.org/tv/2316-the-office/watch?locale=GB",
            "free": [{"provider_id": 2100, "provider_name": "Amazon Prime Video with Ads"}],
        },
        "US": {
            "link": "https://www.themoviedb.org/tv/2316-the-office/watch?locale=US",
            "buy": [{"provider_id": 10, "provider_name": "Amazon Video"}],
        },
        "AU": {
            "link": "https://www.themoviedb.org/tv/2316-the-office/watch?locale=AU",
            "flatrate": [{"provider_id": 999, "provider_name": "Amazon Prime Channel"}],
        },
    }


class PrimeOffersSourceTests(TestCase):
    """``prime_offers`` turns TMDb regions into offers this app accepts."""

    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=2316, title="The Office", year=2005, content_type="series"
        )
        tmdb_patcher = patch("tracker.services.prime_source.TMDbClient")
        self.addCleanup(tmdb_patcher.stop)
        self.tmdb = tmdb_patcher.start()
        self.tmdb.is_configured.return_value = True
        self.tmdb.return_value.watch_providers.return_value = _regions()

    def test_flatrate_region_becomes_a_subscription_offer(self):
        offers = prime_offers(self.movie)

        by_country = {o.country: o for o in offers}
        self.assertIn("NL", by_country)
        nl = by_country["NL"]
        self.assertEqual(nl.service, "prime")
        self.assertEqual(nl.offer_type, "subscription")
        self.assertTrue(nl.url.startswith("https://www.themoviedb.org/"))
        # Both services exist in the region; only the Prime row is taken.
        self.assertEqual(
            sorted(o.country for o in offers), ["DE", "GB", "NL"]
        )

    def test_free_bucket_is_kept_but_buy_and_channels_are_not(self):
        offers = {o.country: o for o in prime_offers(self.movie)}

        # Ad-supported flatrate-ish tier maps to this app's "free" type.
        self.assertEqual(offers["GB"].offer_type, "free")
        # buy-only region: excluded by design (rent/buy never count).
        self.assertNotIn("US", offers)
        # Prime Video *Channels* are third-party subscriptions, not Prime.
        self.assertNotIn("AU", offers)

    def test_countries_already_covered_by_the_primary_source_are_skipped(self):
        existing = [
            Offer(country="DE", service="prime", offer_type="subscription"),
            Offer(country="DE", service="netflix", offer_type="subscription"),
            Offer(country="NL", service="netflix", offer_type="subscription"),
        ]

        countries = {o.country for o in prime_offers(self.movie, existing=existing)}

        # DE already has a prime row upstream; NL only has netflix, so it stays.
        self.assertEqual(countries, {"NL", "GB"})

    @override_settings(TMDB_API_KEY="")
    def test_unconfigured_key_returns_no_offers(self):
        self.tmdb.is_configured.return_value = False

        self.assertEqual(prime_offers(self.movie), [])
        self.tmdb.return_value.watch_providers.assert_not_called()

    def test_fetch_failure_never_raises(self):
        self.tmdb.return_value.watch_providers.side_effect = Exception("boom")

        self.assertEqual(prime_offers(self.movie), [])

    def test_malformed_payload_is_ignored(self):
        self.tmdb.return_value.watch_providers.return_value = ["not", "a", "dict"]

        self.assertEqual(prime_offers(self.movie), [])


class PrimeGapFillPipelineTests(TestCase):
    """The gap-fill lands in the pipeline result and never breaks a read."""

    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=2316, title="The Office", year=2005, content_type="series"
        )
        self.prime = Provider.objects.get(slug="prime")
        Subscription.objects.create(user_id=1, provider=self.prime, name="Prime Video")
        Subscription.objects.create(
            user_id=1, provider=Provider.objects.get(slug="netflix"), name="Netflix"
        )

        cache_patcher = patch("tracker.services.offer_pipeline.AvailabilityCache")
        enrich_patcher = patch("tracker.services.offer_pipeline.enrich_movie_from_tmdb")
        prime_patcher = patch("tracker.services.offer_pipeline.prime_offers")
        self.addCleanup(cache_patcher.stop)
        self.addCleanup(enrich_patcher.stop)
        self.addCleanup(prime_patcher.stop)
        self.mock_cache_cls = cache_patcher.start()
        enrich_patcher.start()
        self.mock_prime = prime_patcher.start()
        self.mock_prime.return_value = [
            Offer(
                country="NL",
                service="prime",
                offer_type="subscription",
                url="https://www.themoviedb.org/tv/2316/watch?locale=NL",
            )
        ]
        self.read_offers = self.mock_cache_cls.return_value.read_offers
        self.read_offers.return_value = [
            Offer(country="NL", service="netflix", offer_type="subscription",
                  quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])
        ]

    def test_gap_fill_is_appended_to_the_cached_offers(self):
        offers = OfficialOffersPipeline().get_cached_offers(self.movie)

        self.mock_prime.assert_called_once()
        self.assertEqual([o.country for o in offers], ["NL", "NL"])
        self.assertEqual([o.service for o in offers], ["netflix", "prime"])

    def test_gap_fill_receives_the_cached_rows_as_context(self):
        OfficialOffersPipeline().get_cached_offers(self.movie)

        cached = self.mock_prime.call_args.kwargs.get("existing")
        if cached is None:
            cached = self.mock_prime.call_args.args[1]
        self.assertEqual({o.service for o in cached}, {"netflix"})

    def test_gap_filled_offer_reaches_the_ranked_result(self):
        result = OfficialOffersPipeline().run(
            self.movie, current_country="NL"
        ).offers_out

        # The gap-filled row survives qualification (prime is the default
        # provider) and sits next to the primary source's rows.
        self.assertEqual(
            {o["service"] for o in result}, {"Prime Video", "Netflix"}
        )

    def test_gap_fill_runs_for_a_season_view_too(self):
        OfficialOffersPipeline().get_cached_offers(self.movie, season=3)

        self.mock_prime.assert_called_once()

    def test_offline_mode_reads_the_cache_without_the_extra_source(self):
        self.mock_prime.reset_mock()

        with override_settings(PRIME_LIVE_FETCH=False):
            offers = OfficialOffersPipeline().get_cached_offers(self.movie)

        self.assertEqual(len(offers), 1)
        self.mock_prime.assert_not_called()

    def test_gap_fill_failure_still_serves_the_cached_offers(self):
        self.mock_prime.side_effect = RuntimeError("tmdb down")

        offers = OfficialOffersPipeline().get_cached_offers(self.movie)

        self.assertEqual(len(offers), 1)
