"""Unit tests for watchability evaluation engine."""
from django.test import TestCase
from tracker.models import Movie, Preference, Provider, Subscription
from tracker.services.offers import Offer
from tracker.services.watchability import (
    RefreshState,
    WatchabilityStatus,
    evaluate_watchability,
)


class WatchabilityEvaluationTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=1001,
            title="Inception",
            year=2010,
            content_type="movie",
        )
        self.provider_netflix, _ = Provider.objects.get_or_create(
            slug="netflix",
            defaults={"name": "Netflix", "is_enabled": True},
        )
        self.provider_prime, _ = Provider.objects.get_or_create(
            slug="prime",
            defaults={"name": "Prime Video", "is_enabled": True},
        )
        Subscription.objects.create(
            user_id=1,
            provider=self.provider_netflix,
            name="Netflix",
        )
        self.pref = Preference.objects.create(
            user_id=1,
            audio_languages=["EN", "JA"],
            subtitle_languages=["EN"],
            candidate_countries=["JP", "IT"],
        )

    def test_watchable_here(self):
        offers = [Offer(
            country="US",
            service="netflix",
            offer_type="subscription",
            quality="4k",
            url="https://netflix.com/watch/1",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )]
        res = evaluate_watchability(
            self.movie,
            offers,
            self.pref,
            current_country="US",
        )
        self.assertEqual(res.status, WatchabilityStatus.WATCHABLE_HERE)
        self.assertEqual(res.status_symbol, "🟢")
        self.assertIsNotNone(res.best_offer)
        self.assertEqual(res.best_offer.country, "US")

    def test_watchable_vpn(self):
        offers = [Offer(
            country="JP",
            service="netflix",
            offer_type="subscription",
            quality="hd",
            url="https://netflix.com/watch/jp",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )]
        res = evaluate_watchability(
            self.movie,
            offers,
            self.pref,
            current_country="US",
        )
        self.assertEqual(res.status, WatchabilityStatus.WATCHABLE_VPN)
        self.assertEqual(res.status_symbol, "🟡")
        self.assertEqual(res.best_offer.country, "JP")

    def test_unconfigured_vpn_country_not_recommended(self):
        offers = [Offer(
            country="BR",
            service="netflix",
            offer_type="subscription",
            quality="4k",
            url="https://netflix.com/watch/br",
        )]
        res = evaluate_watchability(
            self.movie,
            offers,
            self.pref,
            current_country="US",
        )
        self.assertEqual(res.status, WatchabilityStatus.NOT_WATCHABLE)
        self.assertEqual(res.status_symbol, "⚫")
        self.assertIsNone(res.best_offer)
        self.assertTrue(any("not in your configured VPN regions" in exp for exp in res.explanation))

    def test_rent_buy_excluded(self):
        offers = [Offer(
            country="US",
            service="prime",
            offer_type="rent",
            quality="hd",
            url="https://amazon.com/rent",
        )]
        res = evaluate_watchability(
            self.movie,
            offers,
            self.pref,
            current_country="US",
        )
        self.assertEqual(res.status, WatchabilityStatus.NOT_WATCHABLE)
        self.assertEqual(res.status_symbol, "⚫")
        self.assertEqual(len(res.excluded_offers), 1)
        self.assertIn("Only available to rent", res.excluded_offers[0].rejection_reasons[0])

    def test_scraped_source_bypasses_vpn_but_respects_language_match(self):
        offers = [Offer(
            country="BR",
            service="scraped_site",
            offer_type="free",
            quality="hd",
            url="https://scraped.site/watch",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
            is_scraped_source=True,
        )]
        res = evaluate_watchability(
            self.movie,
            offers,
            self.pref,
            current_country="US",
            user_subscriptions={"scraped_site"},
        )
        self.assertEqual(res.status, WatchabilityStatus.WATCHABLE_HERE)
        self.assertEqual(len(res.watchable_offers), 1)
        self.assertTrue(res.watchable_offers[0].is_language_match)

    def test_scraped_source_fails_language_match(self):
        offers = [Offer(
            country="BR",
            service="scraped_site",
            offer_type="free",
            quality="hd",
            url="https://scraped.site/watch",
            audio_languages=["FR"],
            subtitle_languages=["FR"],
            is_scraped_source=True,
        )]
        res = evaluate_watchability(
            self.movie,
            offers,
            self.pref,
            current_country="US",
            user_subscriptions={"scraped_site"},
        )
        self.assertEqual(res.status, WatchabilityStatus.NOT_WATCHABLE)
        self.assertEqual(res.status_label, "Not matching language filter")
        self.assertEqual(len(res.excluded_offers), 1)
        self.assertFalse(res.excluded_offers[0].is_language_match)
        self.assertIn("Does not satisfy language rules", res.excluded_offers[0].rejection_reasons)

    def test_local_offer_wins_over_vpn_when_best(self):
        offers = [
            Offer(
                country="IN",
                service="prime",
                offer_type="subscription",
                quality="4k",
                url="https://primevideo.in/test",
                audio_languages=["EN"],
                subtitle_languages=["EN"],
            ),
            Offer(
                country="IT",
                service="prime",
                offer_type="subscription",
                quality="hd",
                url="https://primevideo.it/test",
                audio_languages=["EN"],
                subtitle_languages=["EN"],
            ),
        ]
        res = evaluate_watchability(
            self.movie,
            offers,
            self.pref,
            current_country="IT",
            user_subscriptions={"prime"},
        )
        self.assertEqual(res.best_offer.country, "IT")
        self.assertTrue(res.best_offer.is_current_country)

    def test_unknown_language_marked_as_unknown(self):
        offers = [Offer(
            country="US",
            service="scraped_site",
            offer_type="free",
            quality="hd",
            url="https://scraped.site/watch",
            audio_languages=[],
            subtitle_languages=[],
            is_scraped_source=True,
        )]
        res = evaluate_watchability(
            self.movie,
            offers,
            self.pref,
            current_country="US",
            user_subscriptions={"scraped_site"},
        )
        self.assertEqual(res.status, WatchabilityStatus.NOT_WATCHABLE)
        self.assertEqual(res.status_label, "Not matching language filter (language data unknown)")
        self.assertEqual(len(res.excluded_offers), 1)
        self.assertFalse(res.excluded_offers[0].is_language_match)
        self.assertIn("Language information unknown", res.excluded_offers[0].rejection_reasons)


class RefreshStateBadgeTests(TestCase):
    """With no offers stored, the queue's evidence picks the badge.

    ⚫ Not available (checked, nothing found) vs 🔵 Checking (queued) vs
    ⚪ Unknown (never fetched / last attempt failed) — the three states the
    watchlist "Refresh all" UX depends on.
    """

    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=2001, title="Nothing Yet", year=2021, content_type="movie"
        )
        netflix, _ = Provider.objects.get_or_create(
            slug="netflix",
            defaults={"name": "Netflix", "is_enabled": True},
        )
        Subscription.objects.create(
            user_id=1, provider=netflix, name="Netflix"
        )
        self.pref = Preference.objects.create(
            user_id=1,
            audio_languages=["EN"],
            subtitle_languages=["EN"],
            candidate_countries=["US"],
        )

    def _evaluate(self, refresh_state):
        return evaluate_watchability(
            self.movie,
            [],
            self.pref,
            current_country="US",
            refresh_state=refresh_state,
        )

    def test_checked_but_empty_reads_not_available(self):
        res = self._evaluate(RefreshState.CHECKED)

        self.assertEqual(res.status, WatchabilityStatus.NOT_WATCHABLE)
        self.assertEqual(res.status_symbol, "⚫")
        self.assertEqual(res.status_label, "Not available")
        self.assertEqual(res.refresh_state, RefreshState.CHECKED)

    def test_pending_but_never_checked_reads_checking(self):
        res = self._evaluate(RefreshState.PENDING)

        self.assertEqual(res.status, WatchabilityStatus.CHECKING)
        self.assertEqual(res.status_symbol, "🔵")
        self.assertEqual(res.status_label, "Checking availability…")
        self.assertEqual(res.refresh_state, RefreshState.PENDING)

    def test_failed_attempt_reads_unknown_with_a_reason(self):
        res = self._evaluate(RefreshState.FAILED)

        self.assertEqual(res.status, WatchabilityStatus.UNKNOWN)
        self.assertEqual(res.status_symbol, "⚪")
        self.assertEqual(res.status_label, "Unknown — last check failed")
        self.assertEqual(res.refresh_state, RefreshState.FAILED)

    def test_no_queue_knowledge_reads_plain_unknown(self):
        res = self._evaluate(None)

        self.assertEqual(res.status, WatchabilityStatus.UNKNOWN)
        self.assertEqual(res.status_symbol, "⚪")
        self.assertEqual(res.status_label, "Unknown")
        self.assertIsNone(res.refresh_state)

    def test_known_offers_win_over_a_queued_recheck(self):
        offers = [Offer(
            country="US",
            service="netflix",
            offer_type="subscription",
            quality="hd",
            url="https://netflix.com/watch/1",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )]

        res = evaluate_watchability(
            self.movie,
            offers,
            self.pref,
            current_country="US",
            refresh_state=RefreshState.PENDING,
        )

        self.assertEqual(res.status, WatchabilityStatus.WATCHABLE_HERE)
        self.assertEqual(res.status_symbol, "🟢")
        # The queue state stays visible even though it did not decide.
        self.assertEqual(res.refresh_state, RefreshState.PENDING)

    def test_to_dict_exposes_refresh_state(self):
        res = self._evaluate(RefreshState.CHECKED)

        self.assertEqual(res.to_dict()["refresh_state"], RefreshState.CHECKED)
        self.assertEqual(res.to_dict()["status_symbol"], "⚫")
