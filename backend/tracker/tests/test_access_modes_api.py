"""API-level tests for access modes, the file's VPN preference and the
access-type filter (tasks 2/3/4).

Covers the write path behind the service-access manager (the ``access_mode``
key on the provider toggle), the ``access_mode`` exposure on the provider list,
the ``vpn_enabled`` preference round-trip, and the ``access=`` filter on the
composable discovery filter.
"""
from django.test import TestCase

from tracker.models import Movie, Preference, Provider, ProviderCategory, Subscription
from tracker.services.filtering import apply_composable_filters
from tracker.tests.availability_factories import local_offer


class ProviderAccessModeEndpointTests(TestCase):
    def setUp(self):
        self.netflix, _ = Provider.objects.update_or_create(
            slug="netflix",
            defaults={"name": "Netflix", "category": ProviderCategory.OFFICIAL, "is_enabled": True},
        )

    def _toggle(self, mode):
        return self.client.post(
            "/api/providers/netflix/toggle/",
            data={"access_mode": mode},
            content_type="application/json",
        )

    def test_set_access_mode_creates_subscription(self):
        resp = self._toggle("addon")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["is_subscribed"])
        self.assertEqual(body["access_mode"], "addon")
        sub = Subscription.objects.get(user_id=1, provider=self.netflix)
        self.assertEqual(sub.access_mode, "addon")

    def test_set_access_mode_is_idempotent_and_updates(self):
        self._toggle("included")
        resp = self._toggle("rent_buy")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["created"])
        self.assertEqual(
            Subscription.objects.get(user_id=1, provider=self.netflix).access_mode,
            "rent_buy",
        )

    def test_access_mode_none_removes_subscription(self):
        self._toggle("included")
        resp = self._toggle("none")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["is_subscribed"])
        self.assertFalse(Subscription.objects.filter(user_id=1, provider=self.netflix).exists())

    def test_invalid_access_mode_is_rejected(self):
        resp = self._toggle("banana")
        self.assertEqual(resp.status_code, 400)

    def test_bodyless_post_keeps_legacy_toggle(self):
        first = self.client.post("/api/providers/netflix/toggle/")
        self.assertTrue(first.json()["is_subscribed"])
        second = self.client.post("/api/providers/netflix/toggle/")
        self.assertFalse(second.json()["is_subscribed"])

    def test_providers_list_exposes_access_mode(self):
        self._toggle("addon")
        resp = self.client.get("/api/providers/")
        self.assertEqual(resp.status_code, 200)
        item = next(p for p in resp.json() if p["slug"] == "netflix")
        self.assertEqual(item["access_mode"], "addon")

    def test_subscriptions_endpoint_accepts_access_mode(self):
        resp = self.client.post(
            "/api/subscriptions/",
            data={"provider_slug": "netflix", "access_mode": "free"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["access_mode"], "free")
        self.assertEqual(
            Subscription.objects.get(user_id=1, provider=self.netflix).access_mode, "free"
        )


class VpnPreferenceTests(TestCase):
    def test_vpn_enabled_round_trips_through_preferences(self):
        resp = self.client.put(
            "/api/preferences/",
            data={"vpn_enabled": True},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Preference.objects.get(user_id=1).vpn_enabled)

        get_resp = self.client.get("/api/preferences/")
        self.assertTrue(get_resp.json()["vpn_enabled"])


class AccessTypeFilterTests(TestCase):
    def setUp(self):
        self.pref = Preference.objects.create(
            user_id=1,
            audio_languages=["EN"],
            subtitle_languages=["EN"],
            candidate_countries=[],
            current_country="US",
        )
        self.movie_rent = Movie.objects.create(
            tmdb_id=7001, title="Rent Only", year=2020, content_type="movie"
        )
        self.movie_sub = Movie.objects.create(
            tmdb_id=7002, title="On Netflix", year=2021, content_type="movie"
        )
        local_offer(self.movie_rent, country="US", service="rakuten_tv",
                    offer_type="rent", url="https://rakuten.tv/x")
        local_offer(self.movie_sub, country="US", service="netflix",
                    offer_type="subscription", url="https://netflix.com/x",
                    audio_languages=["EN"], subtitle_languages=["EN"])

    def _filter(self, params):
        items, _ = apply_composable_filters(
            [self.movie_rent, self.movie_sub], params, self.pref, current_country="US"
        )
        return {item["movie"].tmdb_id for item in items}

    def test_filter_rent(self):
        self.assertEqual(self._filter({"access": "rent"}), {7001})

    def test_filter_subscription(self):
        self.assertEqual(self._filter({"access": "subscription"}), {7002})

    def test_filter_all_returns_everything(self):
        self.assertEqual(self._filter({"access": "all"}), {7001, 7002})

    def test_no_access_filter_returns_everything(self):
        self.assertEqual(self._filter({}), {7001, 7002})

    def test_filter_multi_value(self):
        self.assertEqual(self._filter({"access": "rent,buy"}), {7001})
