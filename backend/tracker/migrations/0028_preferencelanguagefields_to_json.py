# Migration to fix Preference model language fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0026_allow_null_current_country"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        'ALTER TABLE "tracker_preference" ALTER COLUMN "audio_languages" TYPE jsonb USING to_jsonb("audio_languages")',
                        'ALTER TABLE "tracker_preference" ALTER COLUMN "subtitle_languages" TYPE jsonb USING to_jsonb("subtitle_languages")',
                        'ALTER TABLE "tracker_preference" ALTER COLUMN "candidate_countries" TYPE jsonb USING to_jsonb("candidate_countries")',
                    ),
                    reverse_sql=(
                        'ALTER TABLE "tracker_preference" ALTER COLUMN "audio_languages" TYPE text[] USING "audio_languages"::text[]',
                        'ALTER TABLE "tracker_preference" ALTER COLUMN "subtitle_languages" TYPE text[] USING "subtitle_languages"::text[]',
                        'ALTER TABLE "tracker_preference" ALTER COLUMN "candidate_countries" TYPE text[] USING "candidate_countries"::text[]',
                    ),
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="preference",
                    name="audio_languages",
                    field=models.JSONField(default=list, blank=True),
                ),
                migrations.AlterField(
                    model_name="preference",
                    name="subtitle_languages",
                    field=models.JSONField(default=list, blank=True),
                ),
                migrations.AlterField(
                    model_name="preference",
                    name="candidate_countries",
                    field=models.JSONField(default=list, blank=True),
                ),
            ],
        ),
    ]
