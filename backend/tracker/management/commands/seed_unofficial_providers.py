"""Seed unofficial scraping providers (RaiPlay, BBC, NHK) and create regions."""
from __future__ import annotations

from django.core.management.base import BaseCommand
from tracker.models import Provider, ProviderCategory

# Unofficial / priority scraper sources per docs/PLAN.md Phase A/B.
# NOTE: bbc_iplayer, rai-play and nhk are seeded as Free Streaming Providers by
# the migrations (0011 / 0014 / 0018) — they are public broadcasters, not scrapers.
# This command only seeds the small imported scraper sources (KissKH, HydraHD,
# AnimeWorld, StreamingCommunity, CineBlog01, AltaDefinizione) when missing;
# it will not downgrade a Free provider back to scraper.
UNOFFICIAL_PROVIDERS = [
    {"slug": "kisskh", "name": "KissKH", "country": "", "domain": "kisskh.co", "url": "https://kisskh.co"},
    {"slug": "hydrahd", "name": "HydraHD", "country": "", "domain": "hydrahd.org", "url": "https://hydrahd.org"},
    {"slug": "animeworld", "name": "AnimeWorld", "country": "", "domain": "animeworld.to", "url": "https://animeworld.to"},
    {"slug": "streamingcommunity", "name": "StreamingCommunity", "country": "IT", "domain": "streaming-community.army", "url": "https://streaming-community.army"},
    {"slug": "cineblog01", "name": "CineBlog01", "country": "IT", "domain": "cineblog001.run", "url": "https://cineblog001.run"},
    {"slug": "altadefinizione", "name": "AltaDefinizione", "country": "IT", "domain": "altadefinizionex.ink", "url": "https://altadefinizionex.ink"},
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
