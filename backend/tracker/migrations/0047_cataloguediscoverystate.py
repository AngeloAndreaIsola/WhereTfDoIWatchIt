from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("tracker", "0046_availabilityrefresh_country")]
    operations = [
        migrations.CreateModel(
            name="CatalogueDiscoveryState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("IDLE", "IDLE"), ("RUNNING", "RUNNING"), ("PARTIAL", "PARTIAL"), ("COMPLETED", "COMPLETED"), ("BLOCKED", "BLOCKED"), ("FAILED", "FAILED")], default="IDLE", max_length=12)),
                ("mode", models.CharField(choices=[("full", "full"), ("incremental", "incremental"), ("targeted", "targeted")], default="full", max_length=12)),
                ("cursor", models.JSONField(blank=True, default=dict)),
                ("report", models.JSONField(blank=True, default=dict)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("last_success_at", models.DateTimeField(blank=True, null=True)),
                ("last_successful_checkpoint", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("provider", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="catalogue_discovery", to="tracker.provider")),
            ],
            options={"verbose_name": "catalogue discovery state"},
        ),
    ]
