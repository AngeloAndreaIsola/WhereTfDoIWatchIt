from django.db import migrations


def move_bbc_to_free(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    Provider.objects.filter(slug="bbc_iplayer").update(category="free")


def reverse_move_bbc_to_free(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    Provider.objects.filter(slug="bbc_iplayer").update(category="scraper")


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0013_merge_20260830_1219"),
    ]

    operations = [
        migrations.RunPython(move_bbc_to_free, reverse_move_bbc_to_free),
    ]
