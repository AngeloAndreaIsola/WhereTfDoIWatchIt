"""Tests for server-side preferences defaults and seed_catalog command."""
from __future__ import annotations

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from tracker.models import Preference, Region
from tracker.services.preferences import resolve_preferences


class ResolvePreferencesDefaultsTests(TestCase):
    """resolve_preferences() must fall back to settings.DEFAULT_PREFERENCE."""

    def test_resolve_preferences_returns_defaults_when_no_row(self):
        """When no Preference row exists, return the hardcoded defaults.

        Uses user_id=999 to avoid the seeded empty Preference (user_id=1)
        created by migration 0002_seed_empty_preference.
        """
        # Confirm no row exists for this user.
        self.assertFalse(Preference.objects.filter(user_id=999).exists())

        result = resolve_preferences(user_id=999)

        defaults = settings.DEFAULT_PREFERENCE
        self.assertEqual(result["audio_languages"], defaults["audio_languages"])
        self.assertEqual(
            result["subtitle_languages"], defaults["subtitle_languages"]
        )
        self.assertEqual(
            result["candidate_countries"], defaults["candidate_countries"]
        )

    def test_resolve_preferences_returns_db_values_when_exists(self):
        """When a Preference row exists, return the DB values, not defaults.

        Uses user_id=999 to avoid the seeded empty Preference (user_id=1)
        created by migration 0002_seed_empty_preference.
        """
        Preference.objects.create(
            user_id=999,
            audio_languages=["en", "fr"],
            subtitle_languages=["es"],
            candidate_countries=["us", "ca"],
        )

        result = resolve_preferences(user_id=999)
        self.assertEqual(result["audio_languages"], ["en", "fr"])
        self.assertEqual(result["subtitle_languages"], ["es"])
        self.assertEqual(result["candidate_countries"], ["us", "ca"])


class SeedCatalogTests(TestCase):
    """seed_catalog management command: idempotent Region seeding."""

    def test_seed_catalog_creates_regions(self):
        """Seeding --providers=amazon creates a Region row per country."""
        call_command("seed_catalog", "--providers=amazon")

        amazon_regions = Region.objects.filter(provider="amazon")
        self.assertEqual(amazon_regions.count(), 249)

    def test_seed_catalog_idempotent(self):
        """Running the command twice must not create duplicate rows."""
        call_command("seed_catalog", "--providers=amazon")
        count_after_first = Region.objects.count()

        call_command("seed_catalog", "--providers=amazon")

        self.assertEqual(Region.objects.count(), count_after_first)
        # The unique constraint (provider, country) still holds.
        self.assertEqual(Region.objects.filter(provider="amazon").count(), 249)

    def test_seed_catalog_skips_unwanted_providers(self):
        """Seeding --providers=netflix must not create any amazon rows."""
        call_command("seed_catalog", "--providers=netflix")

        self.assertEqual(Region.objects.filter(provider="amazon").count(), 0)
        self.assertEqual(Region.objects.filter(provider="netflix").count(), 249)
