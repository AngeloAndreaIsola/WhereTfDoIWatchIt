# Generated migration to add release_date column to Movie

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0030_add_missing_model_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="movie",
            name="release_date",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
