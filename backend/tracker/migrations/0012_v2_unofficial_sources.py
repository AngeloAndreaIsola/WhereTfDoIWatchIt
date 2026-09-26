"""v2 unofficial sources — add Provider.config, Provider.country_code, Unofficial category

Also seeds the priority scraper providers (RaiPlay, BBC, NHK) so the v2/unofficial
endpoints and the catalog UI have sources to work with out of the box.
"""
from django.db import migrations, models


def seed_unofficial_providers(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    rows = [
        {"slug": "rai-play", "name": "RaiPlay", "country_code": "IT", "domain": "raiplay.it"},
        {"slug": "bbc", "name": "BBC", "country_code": "GB", "domain": "bbc.co.uk"},
        {"slug": "nhk", "name": "NHK", "country_code": "JP", "domain": "nhk.or.jp"},
    ]
    for r in rows:
        Provider.objects.get_or_create(
            slug=r["slug"],
            defaults={
                "name": r["name"],
                "category": "scraper",
                "is_enabled": True,
                "country_code": r["country_code"],
                "domain": r["domain"],
                "config": {},
            },
        )


def reverse_seed_unofficial_providers(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    Provider.objects.filter(slug__in=["rai-play", "bbc", "nhk"], category="scraper").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0011_seed_free_streaming_providers"),
    ]

    operations = [
        migrations.AddField(
            model_name="provider",
            name="config",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="provider",
            name="country_code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Primary country for this provider, if region-specific.",
                max_length=2,
            ),
        ),
        migrations.AlterField(
            model_name="provider",
            name="category",
            field=models.CharField(
                choices=[
                    ("official", "Official / Major Provider"),
                    ("free", "Free Streaming Provider"),
                    ("scraper", "Scraper / Small Source"),
                    ("unofficial", "Unofficial / Aggregator Source"),
                ],
                default="official",
                max_length=20,
            ),
        ),
        migrations.RunPython(seed_unofficial_providers, reverse_seed_unofficial_providers),
    ]
