from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0060_retain_movie_identity_audits")]

    operations = [
        migrations.AddField(
            model_name="movie",
            name="is_quarantined",
            field=models.BooleanField(default=False),
        ),
    ]
