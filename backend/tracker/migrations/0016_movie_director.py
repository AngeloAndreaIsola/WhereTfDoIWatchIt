from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tracker', '0015_movie_runtime_minutes_movie_tmdb_rating'),
    ]

    operations = [
        migrations.AddField(
            model_name='movie',
            name='director',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
