"""Add-on channel support: sync identity, persistence, and entitlement gating.

The requirement this pins:

- an add-on channel is a *distinct* canonical provider whose base platform is
  declared by the source (``config["addon_parent"]``), never guessed;
- holding the channel never grants the base, and holding the base never grants
  the channel — an add-on offer is accessible only when **both** entitlements
  are held;
- the requirement is surfaced explicitly on the offer data
  (``is_addon`` / ``required_base``);
- an unresolved package is kept with an explicit reason code instead of being
  dropped.
"""
from django.test import TestCase

from availability.sources.justwatch_client import JustWatchPackage
from tracker.models import (
    Movie,
    PackageReasonCode,
    PackageResolutionStatus,
    Preference,
    Provider,
    ProviderCataloguePackage,
    ProviderCategory,
    Subscription,
)
from tracker.services.access import AccessType, AccessVerdict, build_user_access, resolve_title as _resolve_title
from tracker.services.offers import Offer
from tracker.services.provider_catalogue import sync_provider_catalogue


def pkg(technical_name, clear_name="", short_name="", monetization=("FLATRATE",)):
    return JustWatchPackage(
        clear_name=clear_name or technical_name.title(),
        technical_name=technical_name,
        short_name=short_name,
        monetization_types=list(monetization),
    )

from tracker.tests.reviewed_entitlement import reviewed_access


def resolve_title(movie, offers, pref, *, current_country=None, access=None, user_subscriptions=None):
    if access is None:
        access = build_user_access(pref.user_id)
    access = reviewed_access(
        offers, access, current_country=current_country or pref.current_country,
        hosts={"hayu_amazon": "prime", "mgm_plus": "prime"},
    )
    return _resolve_title(movie, offers, pref, access=access, current_country=current_country)


class _FakeClient:
    def __init__(self, by_country):
        self._by_country = by_country

    def providers(self, country):
        return self._by_country.get(country, [])


class AddonCatalogueSyncTests(TestCase):
    def test_sync_creates_addon_provider_with_base_platform(self):
        client = _FakeClient({
            "NL": [
                pkg("amazonprimevideo", "Amazon Prime Video", "prv"),
                pkg("amazonhayu", "Hayu Amazon Channel", "ahy"),
            ]
        })
        report = sync_provider_catalogue(["NL"], client=client)

        base = Provider.objects.get(slug="prime")
        channel = Provider.objects.get(slug="hayu_amazon")
        # ``prime`` may already be seeded; the point is the sync adds the
        # channel disabled and never rewrites the existing base.
        self.assertIn("hayu_amazon", report["created_providers"])
        self.assertNotIn("prime", report["created_providers"])
        self.assertFalse(channel.is_enabled)
        self.assertTrue(channel.is_addon)
        self.assertEqual(channel.addon_parent_slug, "prime")
        self.assertFalse(base.is_addon)
        self.assertEqual(channel.category, ProviderCategory.OFFICIAL)
        self.assertIn(
            {"country": "NL", "slug": "hayu_amazon", "technical_name": "amazonhayu", "parent": "prime"},
            report["addons"],
        )
        self.assertEqual(
            report["resolution_counts"][PackageResolutionStatus.ADDON], 1
        )

    def test_sync_is_idempotent_for_addons(self):
        client = _FakeClient({"NL": [pkg("amazonhayu", "Hayu Amazon Channel", "ahy")]})
        sync_provider_catalogue(["NL"], client=client)
        second = sync_provider_catalogue(["NL"], client=client)

        self.assertEqual(Provider.objects.filter(slug="hayu_amazon").count(), 1)
        self.assertEqual(second["created_providers"], [])
        self.assertEqual(second["mappings_created"], [])

    def test_unresolved_package_is_persisted_with_reason(self):
        client = _FakeClient({
            "NL": [
                pkg("amazonmystery", "Mystery Amazon Channel", "amy"),
                pkg("somecinema", "Some Cinema", "", monetization=("CINEMA",)),
                pkg("mysterybrand", "Mystery Brand", "", monetization=("FLATRATE",)),
            ]
        })
        sync_provider_catalogue(["NL"], client=client)

        reasons = {
            row.reason_code: row
            for row in ProviderCataloguePackage.objects.filter(country="NL")
        }
        self.assertEqual(
            reasons[PackageReasonCode.ADDON_PARENT_UNRESOLVED].technical_name,
            "amazonmystery",
        )
        self.assertEqual(
            reasons[PackageReasonCode.UNSUPPORTED_OFFER_TYPE].technical_name,
            "somecinema",
        )
        self.assertEqual(
            reasons[PackageReasonCode.NEEDS_MANUAL_REVIEW].technical_name,
            "mysterybrand",
        )
        for row in reasons.values():
            self.assertEqual(row.resolution_status, PackageResolutionStatus.UNRESOLVED)
            self.assertEqual(row.resolved_provider_slug, "")
            self.assertTrue(row.reason)


class AddonEntitlementTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=9100, title="Add-on Title", year=2024, content_type="movie"
        )
        self.base, _ = Provider.objects.get_or_create(
            slug="prime",
            defaults={"name": "Amazon Prime Video", "category": ProviderCategory.OFFICIAL, "is_enabled": True},
        )
        self.channel, _ = Provider.objects.update_or_create(
            slug="hayu_amazon",
            defaults={
                "name": "Hayu Amazon Channel",
                "category": ProviderCategory.OFFICIAL,
                "is_enabled": True,
                "config": {"addon_parent": "prime"},
            },
        )
        self.pref = Preference.objects.create(
            user_id=1,
            audio_languages=["EN"],
            subtitle_languages=["EN"],
            current_country="NL",
            candidate_countries=[],
            vpn_enabled=False,
        )

    def _subscribe(self, provider, mode="included"):
        return Subscription.objects.create(
            user_id=1, provider=provider, name=provider.name, access_mode=mode
        )

    def _resolve(self):
        offer = Offer(
            country="NL",
            service="hayu_amazon",
            offer_type="subscription",
            quality="hd",
            url="u",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )
        return resolve_title(self.movie, [offer], self.pref, current_country="NL")

    def test_channel_without_base_is_not_accessible(self):
        self._subscribe(self.channel, mode="addon")
        res = self._resolve()

        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        classified = res.offers[0]
        self.assertTrue(classified.is_addon)
        self.assertFalse(classified.is_entitled)
        self.assertEqual(classified.access_type, AccessType.ADDON)
        self.assertEqual(classified.required_base, "prime")
        self.assertEqual(classified.required_base_display, self.base.name)
        self.assertTrue(
            any("base subscription" in r for r in classified.rejection_reasons)
        )

    def test_base_without_channel_is_not_accessible(self):
        self._subscribe(self.base, mode="included")
        res = self._resolve()

        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_ELSEWHERE)
        classified = res.offers[0]
        self.assertFalse(classified.is_entitled)
        self.assertTrue(
            any("add-on channel" in r for r in classified.rejection_reasons)
        )

    def test_base_and_channel_grant_access(self):
        self._subscribe(self.base, mode="included")
        self._subscribe(self.channel, mode="addon")
        res = self._resolve()

        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_HERE)
        self.assertTrue(res.offers[0].is_accessible)
        self.assertIn(AccessType.ADDON, res.access_flags["present_access_types"])
        self.assertTrue(res.access_flags["has_addon"])
        self.assertFalse(res.access_flags["has_subscription"])

    def test_standalone_channel_offer_is_not_addon(self):
        # A provider with no declared parent must not be treated as an add-on.
        standalone, _ = Provider.objects.get_or_create(
            slug="hayu",
            defaults={"name": "Hayu", "category": ProviderCategory.OFFICIAL, "is_enabled": True},
        )
        self._subscribe(standalone, mode="included")
        offer = Offer(
            country="NL", service="hayu", offer_type="subscription", quality="hd", url="u",
            audio_languages=["EN"], subtitle_languages=["EN"],
        )
        res = resolve_title(self.movie, [offer], self.pref, current_country="NL")
        self.assertEqual(res.verdict, AccessVerdict.AVAILABLE_HERE)
        self.assertFalse(res.offers[0].is_addon)
        self.assertEqual(res.offers[0].access_type, AccessType.SUBSCRIPTION)


class ProviderPackagesEndpointTests(TestCase):
    def test_unresolved_packages_are_inspectable_with_a_reason(self):
        client = _FakeClient({
            "NL": [
                pkg("amazonprimevideo", "Amazon Prime Video", "prv"),
                pkg("mysterybrand", "Mystery Brand", "", monetization=("FLATRATE",)),
            ]
        })
        sync_provider_catalogue(["NL"], client=client)

        resp = self.client.get("/api/providers/packages/?country=NL&resolved=false")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        row = body["results"][0]
        self.assertEqual(row["technical_name"], "mysterybrand")
        self.assertEqual(row["reason_code"], PackageReasonCode.NEEDS_MANUAL_REVIEW)
        self.assertTrue(row["reason"])
        self.assertEqual(row["resolved_provider_slug"], "")

        resolved = self.client.get("/api/providers/packages/?country=NL&resolved=true").json()
        self.assertEqual(resolved["count"], 1)
        self.assertEqual(resolved["results"][0]["resolved_provider_slug"], "prime")
