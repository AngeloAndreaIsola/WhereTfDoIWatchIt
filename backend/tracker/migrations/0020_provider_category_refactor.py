"""Refactor ProviderCategory: FREE -> BVOD, UNOFFICIAL -> SCRAPER, add choices.

Changes:
- 'free' -> 'bvod' (Broadcaster Video on Demand)
- 'unofficial' -> 'scraper' (normalize to existing SCRAPER category)
- Update Provider model choices to reflect new taxonomy
"""

from django.db import migrations, models


def migrate_free_to_bvod(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    updated_free = Provider.objects.filter(category="free").update(category="bvod")
    updated_unofficial = Provider.objects.filter(category="unofficial").update(category="scraper")
    if updated_free:
        print(f"Migrated {updated_free} providers from 'free' to 'bvod'")
    if updated_unofficial:
        print(f"Migrated {updated_unofficial} providers from 'unofficial' to 'scraper'")


def reverse_migrate(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    Provider.objects.filter(category="bvod").update(category="free")
    # Cannot reverse 'scraper' -> 'unofficial' since some were already 'scraper'


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0019_add_free_streaming_providers"),
    ]

    operations = [
        migrations.AlterField(
            model_name="provider",
            name="category",
            field=models.CharField(
                choices=[
                    ("official", "Official / Major Provider"),
                    ("bvod", "Broadcaster Video on Demand"),
                    ("avod", "Ad-Supported Video on Demand"),
                    ("tvod", "Transactional Video on Demand"),
                    ("fast", "Free Ad-Supported Streaming TV"),
                    ("scraper", "Scraper / Small Source"),
                ],
                default="official",
                max_length=20,
            ),
        ),
        migrations.RunPython(migrate_free_to_bvod, reverse_migrate),
    ]