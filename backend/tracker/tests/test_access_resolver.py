"""Regression tests for access, language and reach under reviewed fixture facts."""
from django.test import TestCase

from tracker.models import Movie, Preference, Provider, ProviderCategory, Subscription
from tracker.services.access import (
    AccessType,
    AccessVerdict,
    Locality,
    SubscriptionAccessMode,
    build_user_access,
    build_explicit_access,
    resolve_title as _resolve_title,
)
from tracker.services.offers import Offer
from tracker.services.ranking import rank_offers
from tracker.services.watchability import WatchabilityStatus, evaluate_watchability

from tracker.tests.reviewed_entitlement import reviewed_access


def resolve_title(movie, offers, pref, *, current_country=None, access=None, user_subscriptions=None):
    if access is None:
        access = build_user_access(pref.user_id) if user_subscriptions is None else build_explicit_access(user_subscriptions)
    access = reviewed_access(
        offers, access, current_country=current_country or pref.current_country,
        hosts={"hayu_amazon": "prime", "mgm_plus": "prime"},
    )
    return _resolve_title(movie, offers, pref, access=access, current_country=current_country)


class AccessResolverTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=9001, title="Test Title", year=2001, content_type="movie"
        )
        self.netflix, _ = Provider.objects.get_or_create(
            slug="netflix",
            defaults={"name": "Netflix", "category": ProviderCategory.OFFICIAL, "is_enabled": True},
        )

    # -- helpers -----------------------------------------------------------
    def _sub(self, slug, mode=SubscriptionAccessMode.INCLUDED, name=None, category=None):
        provider, _ = Provider.objects.get_or_create(
            slug=slug,
            defaults={
                "name": name or slug.replace("_", " ").title(),
                "category": category or ProviderCategory.OFFICIAL,
                "is_enabled": True,
            },
        )
        return Subscription.objects.create(
            user_id=1, provider=provider, name=provider.name, access_mode=mode
        )

    def _pref(self, *, audio=("EN",), subs=("EN",), regions=(), current="US", vpn_enabled=False):
        return Preference.objects.create(
            user_id=1,
            audio_languages=list(audio),
            subtitle_languages=list(subs),
            candidate_countries=list(regions),
            current_country=current,
            vpn_enabled=vpn_enabled,
        )

    def _offer(self, **kwargs):
        kwargs.setdefault("quality", "hd")
        return Offer(**kwargs)

    # -- 1 ----------------------------------------------------------------
    def test_01_included_local_language_match_is_available_here(self):
        self._sub("netflix")
        pref = self._pref()
        res = resolve_title(
            self.movie,
            [self._offer(country="US", service="netflix", offer_type="subscription",
                         url="u", audio_languages=["EN"], subtitle_languages=["EN"])],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_HERE)
        self.assertEqual(res.status_symbol, "🟢")
        self.assertTrue(res.best_offer.is_accessible)
        self.assertEqual(res.best_offer.locality, Locality.LOCAL)

    # -- 2 ----------------------------------------------------------------
    def test_02_included_but_other_country_is_vpn_not_here(self):
        self._sub("netflix")
        pref = self._pref(regions=["JP"], vpn_enabled=True)
        res = resolve_title(
            self.movie,
            [self._offer(country="JP", service="netflix", offer_type="subscription",
                         url="u", audio_languages=["EN"], subtitle_languages=["EN"])],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_VPN)
        self.assertEqual(res.status_symbol, "🟡")
        self.assertNotEqual(res.verdict, AccessVerdict.AVAILABLE_HERE)
        self.assertEqual(res.best_offer.locality, Locality.VPN)

    # -- 3 ----------------------------------------------------------------
    def test_03_cross_country_without_vpn_regions_is_not_accessible(self):
        self._sub("netflix")
        pref = self._pref(regions=(), vpn_enabled=False)
        res = resolve_title(
            self.movie,
            [self._offer(country="BR", service="netflix", offer_type="subscription",
                         url="u", audio_languages=["EN"], subtitle_languages=["EN"])],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        self.assertEqual(res.status_symbol, "🟠")
        self.assertIsNone(res.best_offer)
        self.assertTrue(
            any("VPN access is disabled" in r for r in res.explanation)
        )

    # -- 4 ----------------------------------------------------------------
    def test_04_cross_country_outside_configured_regions_is_not_accessible(self):
        self._sub("netflix")
        pref = self._pref(regions=["JP", "IT"], vpn_enabled=True)
        res = resolve_title(
            self.movie,
            [self._offer(country="BR", service="netflix", offer_type="subscription",
                         url="u", audio_languages=["EN"], subtitle_languages=["EN"])],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        self.assertTrue(
            any("not in your configured VPN regions" in r for r in res.explanation)
        )

    # -- 5 ----------------------------------------------------------------
    def test_05_vpn_enabled_without_regions_reaches_any_country(self):
        self._sub("netflix")
        pref = self._pref(regions=(), vpn_enabled=True)
        res = resolve_title(
            self.movie,
            [self._offer(country="BR", service="netflix", offer_type="subscription",
                         url="u", audio_languages=["EN"], subtitle_languages=["EN"])],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_VPN)

    def test_unknown_offer_country_does_not_become_local_availability(self):
        self._sub("netflix")
        pref = self._pref(current="NL")
        res = resolve_title(
            self.movie,
            [self._offer(country="", service="netflix", offer_type="subscription",
                         url="u", audio_languages=["EN"], subtitle_languages=["EN"])],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.UNKNOWN)
        self.assertIsNone(res.best_offer)
        self.assertFalse(res.offers[0].is_accessible)
        self.assertEqual(res.offers[0].locality, Locality.UNKNOWN)

    def test_explicit_global_scraped_offer_remains_available(self):
        pref = self._pref(current="NL", regions=["JP"])
        offer = self._offer(
            country="US", service="legacy_scraper", offer_type="free",
            url="u", is_scraped_source=True,
            territorial_scope="worldwide",
            audio_languages=["EN"], subtitle_languages=["EN"],
        )
        res = resolve_title(
            self.movie,
            [offer],
            pref,
            user_subscriptions={"legacy_scraper"},
        )
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_HERE)
        ranked = rank_offers([offer], pref, current_country="NL")[0]
        self.assertEqual(ranked.territorial_scope, "worldwide")
        self.assertEqual(ranked.to_dict()["territorial_scope"], "worldwide")

    def test_scraped_offer_in_other_country_is_not_local_without_vpn(self):
        Provider.objects.update_or_create(
            slug="community_stream",
            defaults={
                "name": "Community Stream",
                "category": ProviderCategory.SCRAPER,
                "is_enabled": True,
            },
        )
        pref = self._pref(current="NL", regions=["US"], vpn_enabled=False)
        res = resolve_title(
            self.movie,
            [self._offer(
                country="US", service="community_stream", offer_type="free",
                url="u", is_scraped_source=True,
                audio_languages=["EN"], subtitle_languages=["EN"],
            )],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        self.assertFalse(res.offers[0].is_accessible)
        self.assertEqual(res.offers[0].locality, Locality.OUT_OF_REACH)
        self.assertTrue(any("Available in US" in reason for reason in res.explanation))
        self.assertIn(
            "Available in US, but VPN access is disabled",
            res.offers[0].rejection_reasons,
        )

    def test_scraped_offer_in_explicit_vpn_country_is_vpn_reachable(self):
        Provider.objects.update_or_create(
            slug="community_stream",
            defaults={
                "name": "Community Stream",
                "category": ProviderCategory.SCRAPER,
                "is_enabled": True,
            },
        )
        pref = self._pref(current="NL", vpn_enabled=True, regions=["US"])
        res = resolve_title(
            self.movie,
            [self._offer(
                country="US", service="community_stream", offer_type="free",
                url="u", is_scraped_source=True,
                audio_languages=["EN"], subtitle_languages=["EN"],
            )],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_VPN)
        self.assertEqual(res.best_offer.locality, Locality.VPN)
        self.assertTrue(res.best_offer.is_accessible)

    def test_scraper_provenance_does_not_make_blank_country_worldwide(self):
        Provider.objects.update_or_create(
            slug="community_stream",
            defaults={
                "name": "Community Stream",
                "category": ProviderCategory.SCRAPER,
                "is_enabled": True,
            },
        )
        pref = self._pref(current="NL", vpn_enabled=False)
        res = resolve_title(
            self.movie,
            [self._offer(
                country="", service="community_stream", offer_type="free",
                url="u", is_scraped_source=True,
                audio_languages=["EN"], subtitle_languages=["EN"],
            )],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.UNKNOWN)
        self.assertEqual(res.offers[0].locality, Locality.UNKNOWN)
        self.assertFalse(res.offers[0].is_accessible)

    def test_provider_home_country_does_not_fill_missing_offer_territory(self):
        provider, _ = Provider.objects.update_or_create(
            slug="regional_free_tv",
            defaults={
                "name": "Regional Free TV",
                "category": ProviderCategory.BVOD,
                "is_enabled": True,
                "country_code": "GB",
            },
        )
        offer = self._offer(
            country="", service=provider.slug, offer_type="free", url="u",
            audio_languages=["EN"], subtitle_languages=["EN"],
        )
        from tracker.services.offer_pipeline import OfficialOffersPipeline
        selected = OfficialOffersPipeline()._qualify_offers(
            [offer], {provider.slug: provider}, "prime"
        )
        self.assertEqual(selected[0].country, "")
        result = resolve_title(
            self.movie, selected, self._pref(current="NL"),
        )
        self.assertEqual(result.verdict, AccessVerdict.UNKNOWN)
        self.assertEqual(result.offers[0].locality, Locality.UNKNOWN)

    def test_provenance_changes_ranking_category_not_geographic_reach(self):
        pref = self._pref(current="NL")
        official, scraped = rank_offers(
            [
                self._offer(country="US", service="netflix", offer_type="subscription",
                            url="official", audio_languages=["EN"],
                            subtitle_languages=["EN"]),
                self._offer(country="US", service="netflix", offer_type="subscription",
                            url="scraped", is_scraped_source=True,
                            source_category="unofficial", audio_languages=["EN"],
                            subtitle_languages=["EN"]),
            ],
            pref,
            current_country="NL",
        )
        self.assertEqual({offer.country for offer in (official, scraped)}, {"US"})
        self.assertEqual(official.source_category, "official")
        self.assertEqual(scraped.source_category, "unofficial")
        self.assertNotEqual(official.source_rank, scraped.source_rank)
        official_result = resolve_title(
            self.movie,
            [self._offer(country="US", service="netflix", offer_type="subscription",
                         url="official", audio_languages=["EN"],
                         subtitle_languages=["EN"])],
            pref,
            user_subscriptions={"netflix"},
        )
        scraped_result = resolve_title(
            self.movie,
            [self._offer(country="US", service="netflix", offer_type="subscription",
                         url="scraped", is_scraped_source=True,
                         source_category="unofficial", audio_languages=["EN"],
                         subtitle_languages=["EN"])],
            pref,
            user_subscriptions={"netflix"},
        )
        self.assertEqual(official_result.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        self.assertEqual(scraped_result.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        self.assertEqual(official_result.offers[0].locality, Locality.OUT_OF_REACH)
        self.assertEqual(scraped_result.offers[0].locality, Locality.OUT_OF_REACH)

    def test_unknown_territory_with_unknown_language_stays_unknown(self):
        self._sub("netflix")
        pref = self._pref(current="NL")
        res = resolve_title(
            self.movie,
            [self._offer(country="", service="netflix", offer_type="subscription",
                         url="u")],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.UNKNOWN)
        self.assertIsNone(res.offers[0].is_language_match)
        self.assertEqual(res.offers[0].locality, Locality.UNKNOWN)

    def test_unknown_territory_does_not_claim_purchase_only(self):
        self._sub("rakuten_tv", mode=SubscriptionAccessMode.RENT_BUY)
        pref = self._pref(current="NL")
        res = resolve_title(
            self.movie,
            [self._offer(country="", service="rakuten_tv", offer_type="rent",
                         url="u", audio_languages=["EN"])],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.UNKNOWN)

    # -- 6 ----------------------------------------------------------------
    def test_06_reviewed_base_and_channel_grant_access(self):
        self._sub("prime")
        self._sub("mgm_plus", mode=SubscriptionAccessMode.ADDON)
        pref = self._pref()
        res = resolve_title(
            self.movie,
            [self._offer(country="US", service="mgm_plus", offer_type="addon",
                         url="u", audio_languages=["EN"], subtitle_languages=["EN"])],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_HERE)
        self.assertEqual(res.best_offer.access_type, AccessType.ADDON)

    # -- 7 ----------------------------------------------------------------
    def test_07_reviewed_free_with_ads_and_public_free(self):
        self._sub("tubi", mode=SubscriptionAccessMode.FREE)
        pref = self._pref()
        free_ads = resolve_title(
            self.movie,
            [self._offer(country="US", service="tubi", offer_type="free_with_ads",
                         url="u", audio_languages=["EN"], subtitle_languages=["EN"])],
            pref,
        )
        self.assertEqual(free_ads.verdict, AccessVerdict.AVAILABLE_HERE)

        # The reviewed fixture asserts that the broadcaster offer is public.
        Provider.objects.update_or_create(
            slug="bbc_iplayer",
            defaults={
                "name": "BBC iPlayer",
                "category": ProviderCategory.BVOD,
                "is_enabled": True,
            },
        )
        bvod = resolve_title(
            self.movie,
            [self._offer(country="US", service="bbc_iplayer", offer_type="free",
                         url="u", audio_languages=["EN"], subtitle_languages=["EN"],
                         is_scraped_source=False)],
            pref,
        )
        self.assertEqual(bvod.verdict, AccessVerdict.AVAILABLE_HERE)

    # -- 8 ----------------------------------------------------------------
    def test_08_reviewed_rent_without_account_is_purchase_only(self):
        pref = self._pref()
        res = resolve_title(
            self.movie,
            [self._offer(country="US", service="rakuten_tv", offer_type="rent",
                         url="u", audio_languages=["EN"], subtitle_languages=["EN"])],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.PURCHASE_ONLY)
        self.assertTrue(res.access_flags["has_rent"])
        self.assertFalse(res.purchase_offers[0].is_entitled)

    # -- 9 ----------------------------------------------------------------
    def test_09_rent_with_account_is_purchase_only(self):
        self._sub("rakuten_tv", mode=SubscriptionAccessMode.RENT_BUY)
        pref = self._pref()
        res = resolve_title(
            self.movie,
            [self._offer(country="US", service="rakuten_tv", offer_type="rent",
                         url="u", audio_languages=["EN"], subtitle_languages=["EN"])],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.PURCHASE_ONLY)
        self.assertEqual(res.status_symbol, "🟣")
        self.assertEqual(len(res.purchase_offers), 1)
        self.assertTrue(res.access_flags["has_rent"])
        self.assertTrue(res.access_flags["requires_payment_only"])

    # -- 10 ---------------------------------------------------------------
    def test_10_language_mismatch_reported_as_language_not_subscription(self):
        self._sub("netflix")
        pref = self._pref(audio=["JA"], subs=["JA"])
        res = resolve_title(
            self.movie,
            [self._offer(country="US", service="netflix", offer_type="subscription",
                         url="u", audio_languages=["EN"], subtitle_languages=["EN"])],
            pref,
        )
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        self.assertEqual(res.status_label, "Available — not matching language filter")
        self.assertFalse(
            any("No active subscription" in r for r in res.explanation),
            "a language mismatch must not be misattributed to subscriptions",
        )

    def test_10b_missing_language_data_reported_distinctly(self):
        self._sub("netflix")
        pref = self._pref(audio=["EN"], subs=["EN"])
        res = resolve_title(
            self.movie,
            [self._offer(country="US", service="netflix", offer_type="subscription",
                         url="u")],  # no language metadata
            pref,
        )
        self.assertEqual(res.status_label, "Unknown")

    def test_10c_11660_style_reasons_are_real_blockers_only(self):
        """Following (1999) regression: JA-only rule, EN/IT offers, no VPN.

        The old label blamed the subscriptions though the user plainly *has*
        Prime; the real blockers are the language rule and disabled VPN access.
        """
        self._sub("prime")
        pref = self._pref(audio=["EN", "IT", "JA"], subs=["EN", "IT", "JA"],
                         regions=(), current="NL", vpn_enabled=False)
        res = resolve_title(
            self.movie,
            [
                self._offer(country="AU", service="prime", offer_type="subscription",
                            url="u", audio_languages=["EN"], subtitle_languages=["EN"]),
                self._offer(country="IT", service="prime", offer_type="subscription",
                            url="u", audio_languages=["IT"], subtitle_languages=["IT"]),
            ],
            pref,
        )
        self.assertNotEqual(res.verdict, AccessVerdict.AVAILABLE_HERE)
        reasons = " | ".join(res.explanation)
        self.assertIn("VPN access is disabled", reasons)
        self.assertNotIn("No active subscription", reasons)

    # -- cross-surface consistency ---------------------------------------
    def test_watchlist_badge_and_detail_verdict_agree(self):
        self._sub("netflix")
        pref = self._pref()
        offers = [
            self._offer(country="US", service="netflix", offer_type="subscription",
                        url="u", audio_languages=["EN"], subtitle_languages=["EN"]),
        ]
        detail = resolve_title(self.movie, offers, pref)
        badge = evaluate_watchability(
            self.movie, offers, pref, access=reviewed_access(offers, build_user_access(1), current_country=pref.current_country),
        )
        self.assertEqual(detail.verdict, AccessVerdict.AVAILABLE_HERE)
        self.assertEqual(badge.status, WatchabilityStatus.WATCHABLE_HERE)
        self.assertEqual(badge.status_symbol, detail.status_symbol)

    # -- access-mode bucketing -------------------------------------------
    def test_build_user_access_buckets_by_access_mode(self):
        self._sub("netflix", mode=SubscriptionAccessMode.INCLUDED)
        self._sub("mgm_plus", mode=SubscriptionAccessMode.ADDON)
        self._sub("tubi", mode=SubscriptionAccessMode.FREE)
        self._sub("rakuten_tv", mode=SubscriptionAccessMode.RENT_BUY)
        self._sub("peacock", mode=SubscriptionAccessMode.NONE)  # explicitly unowned

        access = build_user_access(1)
        self.assertTrue(access.entitles("netflix", AccessType.SUBSCRIPTION))
        self.assertTrue(access.entitles("mgm_plus", AccessType.ADDON))
        self.assertTrue(access.entitles("tubi", AccessType.FREE))
        self.assertTrue(access.entitles("rakuten_tv", AccessType.RENT))
        self.assertFalse(access.entitles("peacock", AccessType.SUBSCRIPTION))
        # An add-on does not grant plain subscription rights on its own.
        self.assertFalse(access.entitles("mgm_plus", AccessType.SUBSCRIPTION))
        self.assertFalse(access.entitles("netflix", AccessType.RENT))
