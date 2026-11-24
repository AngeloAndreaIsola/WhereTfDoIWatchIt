from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("tracker", "0062_titleavailabilitycheck")]

    operations = [
        migrations.AddField(
            model_name="titleavailabilitycheck",
            name="successful_run_ids",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="titleavailabilitycheck",
            name="in_progress",
            field=models.BooleanField(default=False),
        ),
    ]
