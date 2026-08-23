# Generated migration for Provider.config JSONField

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0009_availability_url_movie_poster_path_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="provider",
            name="config",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
