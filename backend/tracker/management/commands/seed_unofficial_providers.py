"""Seed imported scraper providers."""
from __future__ import annotations

from django.core.management.base import BaseCommand
from tracker.models import Provider, ProviderCategory

# Unofficial / priority scraper sources per docs/PLAN.md Phase A/B.
# NOTE: bbc_iplayer, raiplay and nhk_world are seeded as Free Streaming
# Providers by the migrations (0011 / 0014 / 0018) — they are public
# broadcasters, not scrapers. This command only seeds unofficial imported
# scraper providers when missing; it will not downgrade a Free provider.
UNOFFICIAL_PROVIDERS = [
    {"slug": "kisskh", "name": "KissKH", "country": "", "domain": "kisskh.co", "url": "https://kisskh.co"},
    {"slug": "hydrahd", "name": "HydraHD", "country": "", "domain": "hydrahd.gd", "url": "https://hydrahd.gd"},
    {"slug": "animeworld", "name": "AnimeWorld", "country": "", "domain": "www.animeworld.ac", "url": "https://www.animeworld.ac"},
    {"slug": "streamingcommunity", "name": "StreamingCommunity", "country": "IT", "domain": "streaming-community.army", "url": "https://streaming-community.army"},
    {"slug": "cineblog01", "name": "CineBlog01", "country": "IT", "domain": "cineblog001.run", "url": "https://cineblog001.run"},
    {"slug": "altadefinizione", "name": "AltaDefinizione", "country": "IT", "domain": "altadefinizionex.ink", "url": "https://altadefinizionex.ink"},
]


class Command(BaseCommand):
    help = "Seed imported unofficial scraper providers."

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
                if provider.category != ProviderCategory.SCRAPER:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Skipped {info['name']}: existing category "
                            f"{provider.category} is preserved"
                        )
                    )
                    continue
                changed = []
                if provider.domain != info["domain"]:
                    provider.domain = info["domain"]
                    changed.append("domain")
                config = dict(provider.config or {})
                if config.get("home_url") != info["url"]:
                    config["home_url"] = info["url"]
                    provider.config = config
                    changed.append("config")
                if changed:
                    provider.save(update_fields=changed)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Updated {info['name']} source domain: {', '.join(changed)}"
                        )
                    )

        self.stdout.write(
            self.style.SUCCESS(f"Done. Providers created: {created_prov}")
        )
