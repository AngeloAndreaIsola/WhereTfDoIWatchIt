# Generated migration to fix: first_air_date on Movie, added_at on WatchlistItem, source_indicator on Provider

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0029_add_missing_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="movie",
            name="first_air_date",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="provider",
            name="source_indicator",
            field=models.CharField(
                blank=True,
                default="",
                max_length=20,
                help_text="Label for the source: 'Official', 'BVOD', 'Scraper', etc.",
            ),
        ),
    ]
