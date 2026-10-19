"""Tests for the repeatable JustWatch provider-catalogue sync (task 5).

The sync must be *idempotent* and *additive*: re-running it never duplicates
rows, never rewrites an existing provider, and never touches the user's
``Subscription`` selections. Unmapped packages are reported for a human to map,
never guessed at, and a single market's fetch failure never looks like an empty
catalogue.
"""
from unittest.mock import patch

from django.test import TestCase

from availability.sources.justwatch_client import JustWatchPackage
from tracker.models import ExternalProviderMapping, Provider, ProviderCategory, Subscription
from tracker.services.provider_catalogue import (
    JUSTWATCH_INTEGRATION,
    sync_provider_catalogue,
)


def pkg(technical_name, clear_name="", short_name="", monetization=("FLATRATE",)):
    return JustWatchPackage(
        clear_name=clear_name or technical_name.title(),
        technical_name=technical_name,
        short_name=short_name,
        monetization_types=list(monetization),
    )


class _FakeClient:
    def __init__(self, by_country):
        self._by_country = by_country
        self.calls = []

    def providers(self, country):
        self.calls.append(country)
        result = self._by_country.get(country)
        if isinstance(result, Exception):
            raise result
        return result or []


class ProviderCatalogueSyncTests(TestCase):
    def _drop(self, slug):
        """Remove a provider (and its mappings) so absolute counts are meaningful."""
        Provider.objects.filter(slug=slug).delete()

    def test_discovers_and_creates_disabled_provider_and_mapping(self):
        self._drop("prime")
        client = _FakeClient({"NL": [pkg("amazonprimevideo", "Prime Video", "prv")]})
        report = sync_provider_catalogue(["NL"], client=client)

        provider = Provider.objects.get(slug="prime")
        self.assertFalse(provider.is_enabled, "newly discovered providers stay disabled")
        mapping = ExternalProviderMapping.objects.get(
            provider=provider, integration_name=JUSTWATCH_INTEGRATION, country="NL"
        )
        self.assertEqual(mapping.external_id, "amazonprimevideo")
        self.assertEqual(report["mappings_created"], ["prime:NL"])
        self.assertIn("prime", report["created_providers"])
        self.assertEqual(report["unmapped_count"], 0)

    def test_idempotent_second_run_is_a_no_op(self):
        client = _FakeClient({"US": [pkg("netflix", "Netflix", "nfx")]})
        sync_provider_catalogue(["US"], client=client)
        providers_after_first = Provider.objects.filter(slug="netflix").count()
        mappings_after_first = ExternalProviderMapping.objects.filter(
            integration_name=JUSTWATCH_INTEGRATION, country="US"
        ).count()

        second = sync_provider_catalogue(["US"], client=client)

        self.assertEqual(Provider.objects.filter(slug="netflix").count(), providers_after_first)
        self.assertEqual(
            ExternalProviderMapping.objects.filter(
                integration_name=JUSTWATCH_INTEGRATION, country="US"
            ).count(),
            mappings_after_first,
        )
        self.assertEqual(second["mappings_created"], [])
        self.assertEqual(second["created_providers"], [])

    def test_preserves_existing_provider_and_user_subscriptions(self):
        provider, _ = Provider.objects.update_or_create(
            slug="netflix",
            defaults={
                "name": "My Netflix", "category": ProviderCategory.OFFICIAL,
                "is_enabled": True, "is_major": True, "icon_name": "custom",
            },
        )
        sub = Subscription.objects.create(user_id=1, provider=provider, name="Netflix")

        client = _FakeClient({"US": [pkg("netflix", "Netflix Inc", "nfx")]})
        sync_provider_catalogue(["US"], client=client)

        provider.refresh_from_db()
        self.assertEqual(provider.name, "My Netflix", "existing fields are never rewritten")
        self.assertTrue(provider.is_enabled)
        self.assertEqual(provider.icon_name, "custom")
        # The user's selection is untouched.
        sub.refresh_from_db()
        self.assertEqual(sub.access_mode, "included")
        self.assertTrue(Subscription.objects.filter(pk=sub.pk).exists())

    def test_unmapped_package_is_reported_not_guessed(self):
        client = _FakeClient({
            "NL": [pkg("amazonprimevideowithads", "Prime Video with Ads", "pva")],
            "DE": [pkg("amazonprimevideowithads", "Prime Video with Ads", "pva")],
        })
        report = sync_provider_catalogue(["NL", "DE"], client=client)

        self.assertEqual(report["mapped"], [])
        self.assertEqual(Provider.objects.filter(slug="amazonprimevideowithads").count(), 0)
        self.assertEqual(len(report["unmapped"]), 1)
        entry = report["unmapped"][0]
        self.assertEqual(entry["technical_name"], "amazonprimevideowithads")
        self.assertEqual(sorted(entry["countries"]), ["DE", "NL"])

    def test_fetch_failure_is_an_error_not_an_empty_catalogue(self):
        client = _FakeClient({
            "US": RuntimeError("boom"),
            "NL": [pkg("netflix", "Netflix", "nfx")],
        })
        report = sync_provider_catalogue(["US", "NL"], client=client)

        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(report["errors"][0]["country"], "US")
        self.assertIn("boom", report["errors"][0]["error"])
        # The reachable market still reconciled.
        self.assertIn("netflix", report["providers_touched"])


class ProviderCatalogueSyncEndpointTests(TestCase):
    def test_sync_endpoint_returns_report(self):
        client = _FakeClient({"NL": [pkg("netflix", "Netflix", "nfx")]})
        with patch(
            "tracker.services.provider_catalogue.JustWatchClient", return_value=client
        ):
            resp = self.client.post(
                "/api/providers/sync/", data={"country": "NL"}, content_type="application/json"
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["countries"], ["NL"])
        self.assertIn("netflix", body["providers_touched"])

    def test_sync_endpoint_accepts_comma_separated_countries(self):
        client = _FakeClient({"NL": [], "IT": []})
        with patch(
            "tracker.services.provider_catalogue.JustWatchClient", return_value=client
        ):
            resp = self.client.post(
                "/api/providers/sync/", data={"country": "NL,IT"}, content_type="application/json"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(sorted(resp.json()["countries"]), ["IT", "NL"])


class DiscoveredProviderSurfacingTests(TestCase):
    """A mapped-but-disabled service is visible and selectable, not hidden.

    Catalogue sync discovers services *disabled* so it never silently widens what
    the app covers. But hiding them entirely would defeat "complete the
    catalogue": the service-access manager must be able to show them and let the
    user declare access, which is what activates the provider row.
    """

    def _discovered(self):
        provider = Provider.objects.create(
            slug="videoland",
            name="Videoland",
            category=ProviderCategory.OFFICIAL,
            is_enabled=False,
            is_major=False,
            icon_name="videoland",
        )
        ExternalProviderMapping.objects.create(
            provider=provider,
            integration_name=JUSTWATCH_INTEGRATION,
            country="NL",
            external_id="videoland",
        )
        return provider

    def test_discovered_provider_is_listed_though_disabled(self):
        self._discovered()
        data = self.client.get("/api/providers/").json()
        row = next((p for p in data if p["slug"] == "videoland"), None)
        self.assertIsNotNone(row, "a mapped-but-disabled service must still be listed")
        self.assertFalse(row["is_enabled"])
        self.assertFalse(row["is_subscribed"])

    def test_declaring_access_activates_a_discovered_provider(self):
        self._discovered()
        resp = self.client.post(
            "/api/providers/videoland/toggle/",
            data={"access_mode": "included"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["is_subscribed"])

        provider = Provider.objects.get(slug="videoland")
        self.assertTrue(provider.is_enabled, "owning a service activates its catalogue")
        self.assertTrue(
            Subscription.objects.filter(user_id=1, provider=provider).exists()
        )

    def test_provider_without_mapping_and_disabled_is_not_listed(self):
        Provider.objects.create(
            slug="not_a_real_source",
            name="Nope",
            category=ProviderCategory.OFFICIAL,
            is_enabled=False,
            is_major=False,
            icon_name="not_a_real_source",
        )
        data = self.client.get("/api/providers/").json()
        self.assertNotIn("not_a_real_source", {p["slug"] for p in data})
