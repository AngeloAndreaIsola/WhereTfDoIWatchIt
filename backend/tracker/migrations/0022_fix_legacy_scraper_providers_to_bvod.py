"""Fix legacy scraper-seeded broadcaster providers that should be BVOD.

Migration 0012 seeded rai-play, bbc, nhk as "scraper" category. Migration
0011 concurrently seeded raiplay, bbc_iplayer, nhk_world as "free" (now "bvod").
The legacy-hyphenated slugs remain "scraper" in databases that ran 0012.
This migration converts those legacy scraper broadcaster slugs to "bvod".
"""

from django.db import migrations

LEGACY_BROADCASTER_SLUGS = ["rai-play", "bbc", "nhk"]


def fix_legacy_broadcasters(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    updated = Provider.objects.filter(
        slug__in=LEGACY_BROADCASTER_SLUGS, category="scraper"
    ).update(category="bvod")
    if updated:
        print(f"Migrated {updated} legacy broadcaster providers from 'scraper' to 'bvod'")


def reverse_fix(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    Provider.objects.filter(
        slug__in=LEGACY_BROADCASTER_SLUGS, category="bvod"
    ).update(category="scraper")


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0021_alter_movie_director"),
    ]

    operations = [
        migrations.RunPython(fix_legacy_broadcasters, reverse_fix),
    ]