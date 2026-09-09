"""Tests for Phase C v2/unofficial API endpoints (catalog, providers, offers)."""
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIClient

from tracker.models import (
    Movie,
    Provider,
    ProviderCategory,
    ScrapedContentMapping,
)


def _make_provider(slug, category=ProviderCategory.SCRAPER, country="IT"):
    """Create a Provider for testing. If the provider already exists (e.g. from a
    seed migration), the category is force-set so the test gets the category it asked for.
    See references/test-provider-seed-conflict.md for the rationale."""
    provider, _ = Provider.objects.get_or_create(
        slug=slug,
        defaults={
            "name": slug,
            "category": category,
            "is_enabled": True,
            "country_code": country,
            "domain": f"{slug}.example.com",
        },
    )
    # Ensure category is correct even if provider already exists from a seed migration
    if provider.category != category:
        provider.category = category
        provider.save()
    return provider
