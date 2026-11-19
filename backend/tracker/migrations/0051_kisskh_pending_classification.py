from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("tracker", "0050_scraped_provider_native_identity")]

    operations = [
        migrations.CreateModel(
            name="KissKHPendingClassification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("native_id", models.CharField(max_length=255)),
                ("title", models.CharField(max_length=255)),
                ("partition", models.CharField(max_length=16)),
                ("detail_url", models.URLField(max_length=500)),
                ("release_year", models.IntegerField(blank=True, null=True)),
                ("listing_observation", models.JSONField(blank=True, default=dict)),
                ("detail_enrichment", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("PENDING", "Pending"), ("RESOLVED", "Resolved")], default="PENDING", max_length=12)),
                ("resolved_content_type", models.CharField(blank=True, choices=[("movie", "Movie"), ("series", "Series")], max_length=12, null=True)),
                ("classification_reason", models.CharField(blank=True, default="", max_length=255)),
                ("detail_attempts", models.PositiveIntegerField(default=0)),
                ("last_detail_error", models.TextField(blank=True, default="")),
                ("first_seen", models.DateTimeField(auto_now_add=True)),
                ("last_seen", models.DateTimeField(auto_now=True)),
                ("provider", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="kisskh_pending_classifications", to="tracker.provider")),
            ],
        ),
        migrations.AddConstraint(
            model_name="kisskhpendingclassification",
            constraint=models.UniqueConstraint(fields=("provider", "native_id", "partition"), name="uniq_kisskh_pending_provider_id_partition"),
        ),
        migrations.AddIndex(
            model_name="kisskhpendingclassification",
            index=models.Index(fields=["provider", "status"], name="idx_kisskh_pending_status"),
        ),
    ]
