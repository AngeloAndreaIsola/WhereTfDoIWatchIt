from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("availability", "0016_contentmapping_match_evidence")]

    operations = [
        migrations.CreateModel(
            name="JustWatchRequestBudget",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("evidence_reference", models.CharField(max_length=255, unique=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("last_reserved_at", models.DateTimeField(blank=True, null=True)),
                ("in_progress", models.BooleanField(default=False)),
                ("denied", models.BooleanField(default=False)),
                ("blocked_until", models.DateTimeField(blank=True, null=True)),
            ],
        ),
    ]
