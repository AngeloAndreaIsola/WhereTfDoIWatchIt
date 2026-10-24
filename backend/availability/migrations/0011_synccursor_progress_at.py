"""Add ``SyncCursor.progress_at`` and backfill it for existing walks.

``progress_at`` is the liveness timestamp (see the model docstring); it is the
field stall detection reads. Schema-wise it is a plain additive nullable column,
so no existing row is invalidated and nothing is dropped.

The backfill is the part worth reading. Left as ``NULL``, every pre-existing
checkpoint row would immediately report as stalled — ``is_stalled`` treats "no
recorded progress" as "not progressing", which is the right rule for a *new*
row but a false alarm for a row written before the column existed. So
``progress_at`` is seeded from ``updated_at``: that column is ``auto_now``, so
for a checkpoint row it already *is* the timestamp of the last write to the row,
which is precisely the last recorded progress. The backfill therefore recovers
the real value rather than inventing one.

``last_successful_sync`` is deliberately untouched. It was previously stamped on
every checkpoint write (a bug this phase fixes: a partial run must not look
complete), so its existing values are not trustworthy as completion times.
Rewriting them would be inventing history; leaving them is honest, and they
converge to correct values as each walk completes under the new rules.

Reverse is a no-op: dropping the column discards the backfilled values, and
re-adding it re-runs this backfill.
"""
from django.db import migrations, models


def backfill_progress_at(apps, schema_editor):
    SyncCursor = apps.get_model("availability", "SyncCursor")
    # ``F("updated_at")`` rather than a Python loop: this is one UPDATE for the
    # whole table, and it cannot drift from the row it reads.
    from django.db.models import F

    SyncCursor.objects.filter(progress_at__isnull=True).update(
        progress_at=F("updated_at")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("availability", "0010_offerevidence"),
    ]

    operations = [
        migrations.AddField(
            model_name="synccursor",
            name="progress_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_progress_at, migrations.RunPython.noop),
    ]
