from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0055_cataloguediscoverystate_heartbeat_at_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="movie",
            name="tmdb_id",
            field=models.IntegerField(db_index=True),
        ),
        migrations.AddConstraint(
            model_name="movie",
            constraint=models.UniqueConstraint(
                fields=("tmdb_id", "content_type"),
                name="uniq_movie_tmdb_content_type",
            ),
        ),
    ]
