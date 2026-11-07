"""Remap pending/queued RefreshRequest rows onto the intent hierarchy.

The Phase C priority ladder replaces ``NEW 10 / WATCHLIST 20 / INTENT 30 /
STALE 40 / BACKGROUND 50`` with the full user-intent order
(``EXPLICIT 10 < NEW 20 < RECENTLY_VIEWED 30 < VISIBLE 40 < WATCHLIST 50 <
WATCHED 60 < STALE 70 < BACKGROUND 80``). Existing rows keep their meaning:

- old ``INTENT`` (a detail view or a manual action, previously written for
  both) becomes ``RECENTLY_VIEWED`` — the far more common producer — while
  the new call sites enqueue ``EXPLICIT`` for deliberate actions from now on;
- everything else shifts by the same relative order, so a queued watchlist
  addition still drains after ``NEW`` titles and before the stale sweep.

The remap is a single CASE statement so a row is never observed mid-swap
(e.g. old ``NEW=10`` landing on the slot old ``WATCHLIST=20`` still uses).
"""
from django.db import migrations
from django.db.models import Case, F, When, Value

# old priority value -> new priority value
_FORWARD = {10: 20, 20: 50, 30: 30, 40: 70, 50: 80}
# new priority value -> old priority value (best effort; values that did not
# exist before, e.g. VISIBLE/WATCHED, are left as-is)
_REVERSE = {20: 10, 30: 30, 50: 20, 70: 40, 80: 50, 10: 30}


def _remap(apps, schema_editor, mapping):
    RefreshRequest = apps.get_model("tracker", "RefreshRequest")
    RefreshRequest.objects.update(
        priority=Case(
            *[When(priority=old, then=Value(new)) for old, new in mapping.items()],
            default=F("priority"),
        )
    )


def forwards(apps, schema_editor):
    _remap(apps, schema_editor, _FORWARD)


def backwards(apps, schema_editor):
    _remap(apps, schema_editor, _REVERSE)


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0030_refreshpriority_intent_hierarchy"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
