import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0061_movie_is_quarantined"),
    ]

    operations = [
        migrations.AddField(
            model_name="refreshrequest",
            name="manual",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="refreshrequest",
            name="outcome",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Not recorded / legacy row"),
                    ("offers_found", "Offers found"),
                    ("no_offer", "Checked — no offer"),
                    ("inconclusive", "Checked — availability inconclusive"),
                    ("title_not_found", "Upstream title not found"),
                ],
                db_index=True,
                default="",
                help_text="Result of a successful source check; blank for failures or legacy rows.",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="TitleAvailabilityCheck",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tmdb_id", models.IntegerField()),
                ("content_type", models.CharField(max_length=10)),
                ("country", models.CharField(blank=True, max_length=2)),
                ("source", models.CharField(max_length=50)),
                ("last_attempted_at", models.DateTimeField()),
                ("last_succeeded_at", models.DateTimeField(blank=True, null=True)),
                ("movie", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="tracker.movie")),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("movie", "tmdb_id", "content_type", "country", "source"),
                        name="uniq_title_availability_check_scope",
                    ),
                ],
            },
        ),
    ]
