from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("tracker", "0051_kisskh_pending_classification")]

    operations = [
        migrations.CreateModel(
            name="DiscoveredCatalogueRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider_native_id", models.CharField(blank=True, default="", max_length=255)),
                ("title", models.CharField(max_length=255)),
                ("source_url", models.URLField(max_length=500)),
                ("discovered_at", models.DateTimeField(auto_now_add=True)),
                ("original_metadata", models.JSONField(blank=True, default=dict)),
                ("classification_state", models.CharField(choices=[("PENDING", "Pending"), ("CLASSIFIED", "Classified")], default="PENDING", max_length=12)),
                ("classified_content_type", models.CharField(blank=True, choices=[("movie", "Movie"), ("series", "Series")], max_length=12, null=True)),
                ("discovery_verification_state", models.CharField(choices=[("OBSERVED", "Observed in provider listing"), ("VERIFIED", "Verified")], default="OBSERVED", max_length=12)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("provider", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="discovered_catalogue_records", to="tracker.provider")),
            ],
        ),
        migrations.AddConstraint(
            model_name="discoveredcataloguerecord",
            constraint=models.UniqueConstraint(condition=models.Q(("provider_native_id", ""), _negated=True), fields=("provider", "provider_native_id"), name="uniq_discovered_provider_native_id"),
        ),
        migrations.AddConstraint(
            model_name="discoveredcataloguerecord",
            constraint=models.UniqueConstraint(condition=models.Q(("provider_native_id", "")), fields=("provider", "source_url"), name="uniq_discovered_provider_source_url"),
        ),
        migrations.AddIndex(
            model_name="discoveredcataloguerecord",
            index=models.Index(fields=["provider", "classification_state"], name="idx_disc_catalog_class"),
        ),
    ]
