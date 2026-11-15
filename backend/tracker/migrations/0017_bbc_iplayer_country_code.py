"""Set the country_code for BBC iPlayer so the catalog can show 'BBC iPlayer (GB)'.

The free-streaming-provider seed (0011) did not set country_code on bbc_iplayer.
Without it the watchability best-offer renders as 'bbc_iplayer ()' because the
offer has no country of its own.
"""

from django.db import migrations


def set_bbc_country(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    Provider.objects.filter(slug="bbc_iplayer").update(country_code="GB")


def unset_bbc_country(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    Provider.objects.filter(slug="bbc_iplayer").update(country_code="")


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0016_movie_director"),
    ]

    operations = [
        migrations.RunPython(set_bbc_country, unset_bbc_country),
    ]
