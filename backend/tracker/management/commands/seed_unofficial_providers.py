"""Seed unofficial scraping providers (RaiPlay, BBC, NHK) and create regions."""
from __future__ import annotations

from django.core.management.base import BaseCommand
from tracker.models import Provider, ProviderCategory

# Unofficial / priority scraper sources per docs/PLAN.md Phase A/B.
UNOFFICIAL_PROVIDERS = [
    {"slug": "rai-play", "name": "RaiPlay", "country": "IT", "domain": "raiplay.it", "url": "https://www.raiplay.it"},
    {"slug": "bbc", "name": "BBC", "country": "GB", "domain": "bbc.co.uk", "url": "https://www.bbc.co.uk"},
    {"slug": "nhk", "name": "NHK", "country": "JP", "domain": "nhk.or.jp", "url": "https://www.nhk.or.jp"},
]


class Command(BaseCommand):
    help = "Seed unofficial scraping providers (RaiPlay, BBC, NHK)."

    def handle(self, *args, **options):
        created_prov = 0

        for info in UNOFFICIAL_PROVIDERS:
            provider, prov_created = Provider.objects.get_or_create(
                slug=info["slug"],
                defaults={
                    "name": info["name"],
                    "category": ProviderCategory.SCRAPER,
                    "is_enabled": True,
                    "domain": info["domain"],
                    "country_code": info["country"],
                    "config": {"home_url": info["url"]},
                },
            )
            if prov_created:
                created_prov += 1
                self.stdout.write(self.style.SUCCESS(f"Created provider: {info['name']}"))
            else:
                # Ensure category is SCRAPER even if the provider already existed.
                if provider.category != ProviderCategory.SCRAPER:
                    provider.category = ProviderCategory.SCRAPER
                    provider.save(update_fields=["category"])
                    self.stdout.write(self.style.WARNING(f"Updated {info['name']} to SCRAPER category"))

        self.stdout.write(
            self.style.SUCCESS(f"Done. Providers created: {created_prov}")
        )
