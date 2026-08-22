# Generated migration for Provider system domain and last_scraped_at fields

from django.db import migrations, models


def seed_provider_domains(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    Provider.objects.filter(slug="kisskh").update(domain="kisskh.co")
    Provider.objects.filter(slug="hydrahd").update(domain="hydrahd.ru")
    Provider.objects.filter(slug="animeworld").update(domain="animeworld.ac")


def reverse_seed_provider_domains(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0007_provider_system"),
    ]

    operations = [
        migrations.AddField(
            model_name="provider",
            name="domain",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="provider",
            name="last_scraped_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(seed_provider_domains, reverse_seed_provider_domains),
    ]
