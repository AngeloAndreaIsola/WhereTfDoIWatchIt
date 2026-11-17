"""Acknowledge existing observations only; never repeat bulk reconciliation."""
from django.db import migrations
import hashlib
from datetime import datetime, timezone as datetime_timezone


# Read-only verified boundary of the completed live inventory. Do not adopt
# later discoveries/edits merely because they exist when deployment occurs.
BASELINE_MAX_RECORD_ID = 5095
BASELINE_UPDATED_THROUGH = datetime(2026, 11, 17, 23, 9, 14, 771940, tzinfo=datetime_timezone.utc)


def baseline(apps, schema_editor):
    from tracker.services.catalogue_reconciliation_inputs import fingerprint_v1

    alias = schema_editor.connection.alias
    records = apps.get_model("tracker", "DiscoveredCatalogueRecord")
    states = apps.get_model("tracker", "CatalogueReconciliationRecord")
    mappings = set(apps.get_model("availability", "ContentMapping").objects.using(alias).values_list(
        "provider_id", "provider_content_id"))
    pending = []
    acknowledged = records.objects.using(alias).filter(
        pk__lte=BASELINE_MAX_RECORD_ID, updated_at__lte=BASELINE_UPDATED_THROUGH,
    )
    for record in acknowledged.iterator(chunk_size=500):
        native_id = str(record.provider_native_id or "").strip()
        if not native_id or len(native_id) > 100:
            native_id = "url:" + hashlib.sha256((record.identity_url or record.source_url).encode()).hexdigest()
        pending.append(states(
            discovered_record_id=record.pk, input_fingerprint=fingerprint_v1(record),
            status="ASSOCIATED" if (record.provider_id, native_id) in mappings else "BASELINED",
            processed_at=BASELINE_UPDATED_THROUGH,
            result={"reason": "Existing inventory already evaluated; await changed inputs, new evidence or explicit review"},
        ))
        if len(pending) == 500:
            states.objects.using(alias).bulk_create(pending, ignore_conflicts=True)
            pending = []
    states.objects.using(alias).bulk_create(pending, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0057_incremental_catalogue_reconciliation"),
        ("availability", "0016_contentmapping_match_evidence"),
    ]
    operations = [migrations.RunPython(baseline, migrations.RunPython.noop)]
