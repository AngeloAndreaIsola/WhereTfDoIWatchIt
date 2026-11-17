from django.db import migrations, models
from django.utils import timezone


def backfill_last_seen(apps, schema_editor):
    Record = apps.get_model("tracker", "DiscoveredCatalogueRecord")
    for row in Record.objects.only("id", "updated_at", "discovered_at").iterator(chunk_size=1000):
        row.last_seen_at = row.updated_at or row.discovered_at or timezone.now()
        row.save(update_fields=["last_seen_at"])


class Migration(migrations.Migration):
    dependencies = [("tracker", "0053_catalogue_url_identity_provenance")]

    operations = [
        migrations.AddField(
            model_name="discoveredcataloguerecord",
            name="last_seen_at",
            field=models.DateTimeField(db_index=True, null=True),
        ),
        migrations.RunPython(backfill_last_seen, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="discoveredcataloguerecord",
            name="last_seen_at",
            field=models.DateTimeField(db_index=True, default=timezone.now),
        ),
    ]
