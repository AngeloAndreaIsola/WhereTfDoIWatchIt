"""Tests for exact canonical provider entitlement matching (no substring containment).

Verifies the invariants:
1. Exact matching: subscription to `viaplay` never grants `viaplay_sports`, and vice versa.
2. Separation of access types: Prime base membership never implies channels/add-ons or rentals/purchases.
3. Add-on channels require their own declared `addon` entitlement and their base platform.
4. Negative tests for similar slug prefixes/suffixes (`max` vs `max_amazon`, `apple` vs `apple_tv_store`, `now` vs `snow`, `mubi` vs `mubi_amazon`, `youtube` vs `youtubetv`).
5. Only verified 1:1 aliases are accepted (`netflixkids` <-> `netflix`, `amazonprimevideo` <-> `prime`, `appletvplus` <-> `apple`, `hbo` <-> `max`, `bbc` <-> `bbc_iplayer`, `rai-play` <-> `raiplay`).
6. Unregistered/unowned providers remain visible as viewing options (e.g. `AVAILABLE_ELSEWHERE`) but never classified as user-entitled.
7. Unknown access modes never default to `included`.
8. Free providers (BVOD/AVOD) auto-count as free even without declared subscriptions.
9. Users with no subscriptions cannot access subscription offers, but free offers remain watchable.
"""
from django.test import TestCase

from tracker.models import Movie, Preference, Provider, ProviderCategory, Subscription
from tracker.services.access import (
    AccessType,
    AccessVerdict,
    Locality,
    SubscriptionAccessMode,
    UserAccess,
    build_explicit_access,
    build_user_access,
    classify_offer,
    normalize_service_slug,
    resolve_title,
)
from tracker.services.offers import Offer


class EntitlementExactMatchingTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=8801, title="Entitlement Test Movie", year=2022, content_type="movie"
        )
        self.pref = Preference.objects.create(
            user_id=1,
            audio_languages=["EN"],
            subtitle_languages=["EN"],
            candidate_countries=[],
            current_country="NL",
            vpn_enabled=False,
        )

    def _sub(self, slug, mode=SubscriptionAccessMode.INCLUDED, name=None, category=ProviderCategory.OFFICIAL, config=None):
        provider, _ = Provider.objects.get_or_create(
            slug=slug,
            defaults={
                "name": name or slug.replace("_", " ").title(),
                "category": category,
                "is_enabled": True,
                "config": config or {},
            },
        )
        return Subscription.objects.create(
            user_id=1, provider=provider, name=provider.name, access_mode=mode
        )

    def _offer(self, service, offer_type="subscription", country="NL"):
        return Offer(
            country=country,
            service=service,
            offer_type=offer_type,
            quality="hd",
            url=f"https://example.com/{service}",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )

    # -----------------------------------------------------------------------
    # 1. Viaplay vs Viaplay Sports (prefix / substring isolation)
    # -----------------------------------------------------------------------
    def test_viaplay_subscription_does_not_grant_viaplay_sports(self):
        """Holding Viaplay must NEVER grant entitlement to Viaplay Sports."""
        self._sub("viaplay")
        res = resolve_title(self.movie, [self._offer("viaplay_sports")], self.pref, current_country="NL")

        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        self.assertEqual(len(res.offers), 1)
        classified = res.offers[0]
        self.assertFalse(classified.is_entitled)
        self.assertFalse(classified.is_accessible)
        self.assertTrue(any("No active subscription" in r for r in classified.rejection_reasons))

    def test_viaplay_sports_subscription_does_not_grant_viaplay(self):
        """Holding Viaplay Sports must NEVER grant entitlement to base Viaplay."""
        self._sub("viaplay_sports")
        res = resolve_title(self.movie, [self._offer("viaplay")], self.pref, current_country="NL")

        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        self.assertEqual(len(res.offers), 1)
        classified = res.offers[0]
        self.assertFalse(classified.is_entitled)
        self.assertFalse(classified.is_accessible)

    def test_viaplay_and_viaplay_sports_both_held_entitle_each_separately(self):
        """Holding both grants access to both offers."""
        self._sub("viaplay")
        self._sub("viaplay_sports")
        res = resolve_title(
            self.movie,
            [self._offer("viaplay"), self._offer("viaplay_sports")],
            self.pref,
            current_country="NL",
        )
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_HERE)
        self.assertEqual(len(res.accessible_offers), 2)
        self.assertTrue(all(o.is_entitled for o in res.offers))

    # -----------------------------------------------------------------------
    # 2. Prime base vs channels/add-on/rental
    # -----------------------------------------------------------------------
    def test_prime_base_does_not_grant_amazon_channels(self):
        """Prime base subscription does not grant add-on channels like hayu_amazon."""
        self._sub("prime")
        Provider.objects.get_or_create(
            slug="hayu_amazon",
            defaults={"name": "Hayu Amazon Channel", "category": ProviderCategory.OFFICIAL, "is_enabled": True, "config": {"addon_parent": "prime"}},
        )
        res = resolve_title(self.movie, [self._offer("hayu_amazon")], self.pref, current_country="NL")

        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        classified = res.offers[0]
        self.assertTrue(classified.is_addon)
        self.assertFalse(classified.is_entitled)
        self.assertTrue(any("add-on channel" in r for r in classified.rejection_reasons))

    def test_amazon_channel_without_prime_base_is_not_entitled(self):
        """Holding the add-on channel without the base platform Prime is not entitled."""
        channel_sub = self._sub(
            "hayu_amazon",
            mode=SubscriptionAccessMode.ADDON,
            config={"addon_parent": "prime"},
        )
        # Ensure base provider exists in provider_map
        Provider.objects.get_or_create(
            slug="prime",
            defaults={"name": "Prime Video", "category": ProviderCategory.OFFICIAL, "is_enabled": True},
        )
        res = resolve_title(self.movie, [self._offer("hayu_amazon")], self.pref, current_country="NL")

        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        classified = res.offers[0]
        self.assertTrue(classified.is_addon)
        self.assertFalse(classified.is_entitled)
        self.assertTrue(any("Requires Prime Video" in r for r in classified.rejection_reasons))

    def test_prime_base_and_channel_both_held_grants_access(self):
        """Holding base Prime Video and the add-on channel entitles the channel."""
        self._sub("prime")
        self._sub(
            "hayu_amazon",
            mode=SubscriptionAccessMode.ADDON,
            config={"addon_parent": "prime"},
        )
        res = resolve_title(self.movie, [self._offer("hayu_amazon")], self.pref, current_country="NL")

        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_HERE)
        self.assertTrue(res.offers[0].is_accessible)
        self.assertEqual(res.offers[0].access_type, AccessType.ADDON)

    def test_addon_offer_not_entitled_by_included_mode(self):
        """An ADDON access type requires access_mode='addon', not 'included'."""
        access = UserAccess(included={"hayu_amazon", "prime"}, addon=set())
        self.assertFalse(access.entitles("hayu_amazon", AccessType.ADDON))

    def test_prime_base_does_not_grant_rentals(self):
        """Holding included Prime Video subscription does not grant rental/purchase offers."""
        self._sub("prime", mode=SubscriptionAccessMode.INCLUDED)
        res = resolve_title(
            self.movie,
            [self._offer("prime", offer_type="rent")],
            self.pref,
            current_country="NL",
        )
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        self.assertEqual(res.status_symbol, "🟠")
        self.assertFalse(res.offers[0].is_entitled)
        self.assertFalse(res.access_flags["has_rent"])

    def test_prime_with_rent_buy_account_enables_purchase_only(self):
        """Declaring rent_buy account enables rental offer as purchase_only."""
        self._sub("prime", mode=SubscriptionAccessMode.RENT_BUY)
        res = resolve_title(
            self.movie,
            [self._offer("prime", offer_type="rent")],
            self.pref,
            current_country="NL",
        )
        self.assertEqual(res.verdict, AccessVerdict.PURCHASE_ONLY)
        self.assertEqual(res.status_symbol, "🟣")
        self.assertTrue(res.offers[0].is_entitled)
        self.assertTrue(res.access_flags["has_rent"])

    # -----------------------------------------------------------------------
    # 3. Similar slug prefixes/suffixes (negative tests)
    # -----------------------------------------------------------------------
    def test_max_does_not_grant_max_amazon(self):
        self._sub("max")
        res = resolve_title(self.movie, [self._offer("max_amazon")], self.pref, current_country="NL")
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        self.assertFalse(res.offers[0].is_entitled)

    def test_apple_does_not_grant_apple_tv_store_or_apple_tv_amazon(self):
        self._sub("apple")
        res_store = resolve_title(self.movie, [self._offer("apple_tv_store", offer_type="rent")], self.pref, current_country="NL")
        self.assertFalse(res_store.offers[0].is_entitled)

        res_channel = resolve_title(self.movie, [self._offer("apple_tv_amazon")], self.pref, current_country="NL")
        self.assertFalse(res_channel.offers[0].is_entitled)

    def test_now_does_not_grant_snow(self):
        self._sub("now")
        res = resolve_title(self.movie, [self._offer("snow")], self.pref, current_country="NL")
        self.assertFalse(res.offers[0].is_entitled)

    def test_mubi_does_not_grant_mubi_amazon(self):
        self._sub("mubi")
        res = resolve_title(self.movie, [self._offer("mubi_amazon")], self.pref, current_country="NL")
        self.assertFalse(res.offers[0].is_entitled)

    def test_youtube_free_does_not_grant_youtubetv(self):
        self._sub("youtube", mode=SubscriptionAccessMode.FREE)
        res = resolve_title(self.movie, [self._offer("youtubetv")], self.pref, current_country="NL")
        self.assertFalse(res.offers[0].is_entitled)

    # -----------------------------------------------------------------------
    # 4. Verified alias acceptance
    # -----------------------------------------------------------------------
    def test_netflixkids_alias_accepted_for_netflix(self):
        self._sub("netflix")
        res = resolve_title(self.movie, [self._offer("netflixkids")], self.pref, current_country="NL")
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_HERE)
        self.assertTrue(res.offers[0].is_entitled)

    def test_netflix_accepted_for_netflixkids_subscription(self):
        self._sub("netflixkids")
        res = resolve_title(self.movie, [self._offer("netflix")], self.pref, current_country="NL")
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_HERE)
        self.assertTrue(res.offers[0].is_entitled)

    def test_amazonprimevideo_and_amazonprime_accepted_for_prime(self):
        self._sub("prime")
        res1 = resolve_title(self.movie, [self._offer("amazonprimevideo")], self.pref, current_country="NL")
        self.assertTrue(res1.offers[0].is_entitled)

        res2 = resolve_title(self.movie, [self._offer("amazonprime")], self.pref, current_country="NL")
        self.assertTrue(res2.offers[0].is_entitled)

        res3 = resolve_title(self.movie, [self._offer("prime-video")], self.pref, current_country="NL")
        self.assertTrue(res3.offers[0].is_entitled)

    def test_appletvplus_accepted_for_apple(self):
        self._sub("apple")
        res = resolve_title(self.movie, [self._offer("appletvplus")], self.pref, current_country="NL")
        self.assertTrue(res.offers[0].is_entitled)

    def test_hbo_accepted_for_max(self):
        self._sub("max")
        res = resolve_title(self.movie, [self._offer("hbo")], self.pref, current_country="NL")
        self.assertTrue(res.offers[0].is_entitled)

    def test_broadcaster_legacy_aliases(self):
        self._sub("bbc_iplayer", mode=SubscriptionAccessMode.FREE, category=ProviderCategory.BVOD)
        res_bbc = resolve_title(self.movie, [self._offer("bbc", offer_type="free")], self.pref, current_country="NL")
        self.assertTrue(res_bbc.offers[0].is_entitled)

        self._sub("raiplay", mode=SubscriptionAccessMode.FREE, category=ProviderCategory.BVOD)
        res_rai = resolve_title(self.movie, [self._offer("rai-play", offer_type="free")], self.pref, current_country="NL")
        self.assertTrue(res_rai.offers[0].is_entitled)

    # -----------------------------------------------------------------------
    # 5. Unknown service / unregistered provider
    # -----------------------------------------------------------------------
    def test_unregistered_provider_remains_visible_but_never_entitled(self):
        """Unregistered provider is visible in offers list but never entitled."""
        self._sub("netflix")
        offer = self._offer("some_obscure_service")
        res = resolve_title(self.movie, [offer], self.pref, current_country="NL")

        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        self.assertEqual(len(res.offers), 1)
        self.assertFalse(res.offers[0].is_entitled)
        self.assertFalse(res.offers[0].is_accessible)
        # Verify it is visible in the returned offers and excluded_offers
        self.assertEqual(res.offers[0].service, "some_obscure_service")
        self.assertEqual(len(res.excluded_offers), 1)

    # -----------------------------------------------------------------------
    # 6. Unknown access mode on Subscription
    # -----------------------------------------------------------------------
    def test_unknown_access_mode_never_grants_included_access(self):
        """A subscription with an unrecognized access mode must not silently grant access."""
        Provider.objects.get_or_create(
            slug="bogus_service",
            defaults={"name": "Bogus Service", "category": ProviderCategory.OFFICIAL, "is_enabled": True},
        )
        Subscription.objects.create(
            user_id=1, name="Bogus Service", access_mode="invalid_mode"
        )
        access = build_user_access(1)
        self.assertFalse(access.entitles("bogus_service", AccessType.SUBSCRIPTION))
        self.assertFalse(access.entitles("bogus_service", AccessType.ADDON))
        self.assertFalse(access.entitles("bogus_service", AccessType.FREE))
        self.assertFalse(access.entitles("bogus_service", AccessType.RENT))

    # -----------------------------------------------------------------------
    # 7. Free provider auto-entitlement
    # -----------------------------------------------------------------------
    def test_free_bvod_provider_auto_counts_without_subscription(self):
        """BVOD provider auto-qualifies even when user has no subscriptions."""
        Provider.objects.get_or_create(
            slug="npo_start",
            defaults={"name": "NPO Start", "category": ProviderCategory.BVOD, "is_enabled": True},
        )
        access = build_user_access(1)
        self.assertTrue(access.entitles("npo_start", AccessType.FREE))

        res = resolve_title(
            self.movie,
            [self._offer("npo_start", offer_type="free")],
            self.pref,
            current_country="NL",
        )
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_HERE)
        self.assertTrue(res.offers[0].is_accessible)

    # -----------------------------------------------------------------------
    # 8. User with no subscriptions
    # -----------------------------------------------------------------------
    def test_user_with_no_subscriptions(self):
        """User with no subscriptions cannot access subscription offers, but free offers work."""
        access = build_user_access(9999)  # Non-existent user
        self.assertFalse(access.entitles("netflix", AccessType.SUBSCRIPTION))
        self.assertFalse(access.entitles("prime", AccessType.SUBSCRIPTION))
        self.assertFalse(access.entitles("viaplay", AccessType.SUBSCRIPTION))

        res_sub = resolve_title(
            self.movie,
            [self._offer("netflix", offer_type="subscription")],
            self.pref,
            current_country="NL",
            access=access,
        )
        self.assertEqual(res_sub.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        self.assertFalse(res_sub.offers[0].is_entitled)

    # -----------------------------------------------------------------------
    # 9. build_explicit_access canonicalization
    # -----------------------------------------------------------------------
    def test_build_explicit_access_canonicalizes_slugs(self):
        access = build_explicit_access(["NetflixKids", "  prime-video  ", "viaplay"])
        self.assertIn("netflix", access.included)
        self.assertIn("prime", access.included)
        self.assertIn("viaplay", access.included)
        self.assertNotIn("viaplay_sports", access.included)
        self.assertNotIn("hayu_amazon", access.included)
        self.assertTrue(access.entitles("netflix", AccessType.SUBSCRIPTION))
        self.assertTrue(access.entitles("netflixkids", AccessType.SUBSCRIPTION))
        self.assertTrue(access.entitles("prime", AccessType.SUBSCRIPTION))
        self.assertTrue(access.entitles("amazonprimevideo", AccessType.SUBSCRIPTION))
        self.assertFalse(access.entitles("viaplay_sports", AccessType.SUBSCRIPTION))
