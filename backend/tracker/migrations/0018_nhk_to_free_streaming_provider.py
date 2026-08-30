"""Make NHK a Free Streaming Provider (not a scraper / Unofficial Source).

NHK is an official public broadcaster. Two slugs have existed:
  - "nhk_world" (seeded as free in 0011) — the canonical Free provider.
  - "nhk" (seeded as a scraper in 0012) — a redundant scraper slug.

In some environments "nhk_world" was flipped to scraper (e.g. by the
seed_unofficial_providers command), which wrongly surfaced "NHK (Scraper)"
under Unofficial Sources. This migration re-asserts both NHK slugs as Free
Streaming Providers so they live in the official catalog / Free group and
leave the Unofficial (Imported Small Providers & Sources) tab.
"""

from django.db import migrations


def nhk_to_free(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    # Canonical free provider — fix the live regression where it became a scraper.
    Provider.objects.filter(slug="nhk_world").update(
        category="free",
        is_major=False,
        country_code="JP",
    )
    # Redundant scraper slug — also a Free provider (NHKScraper is repointed to
    # "nhk_world", so this slug carries no scraped content going forward).
    Provider.objects.filter(slug="nhk").update(
        category="free",
        is_major=False,
        country_code="JP",
        name="NHK",
    )


def reverse_nhk_to_free(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    Provider.objects.filter(slug="nhk_world").update(category="scraper")
    Provider.objects.filter(slug="nhk").update(category="scraper")


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0017_bbc_iplayer_country_code"),
    ]

    operations = [
        migrations.RunPython(nhk_to_free, reverse_nhk_to_free),
    ]
