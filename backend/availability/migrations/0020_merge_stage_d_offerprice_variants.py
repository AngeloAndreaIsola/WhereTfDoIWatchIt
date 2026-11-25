# Merge migration combining the independent 0018 (#198 offer price variants)
# and 0019 (#206 Stage D run ledger) leaves, both children of 0017.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("availability", "0018_offerprice_variants"),
        ("availability", "0019_stage_d_run_ledger"),
    ]

    operations = []
