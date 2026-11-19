"""Reject any migration outside the reviewed incremental deployment plan."""
import os
import sys
sys.path.insert(0,'/app')
if not __debug__:
    raise RuntimeError('Migration checks require Python assertions enabled')
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

executor=MigrationExecutor(connection)
plan=[(m.app_label,m.name,backwards) for m,backwards in executor.migration_plan(executor.loader.graph.leaf_nodes())]
expected=[('tracker','0057_incremental_catalogue_reconciliation',False),('tracker','0058_baseline_completed_catalogue',False)]
assert plan in (expected,expected[1:],[]),plan
assert ('tracker','0056_movie_tmdb_content_type_identity') in executor.loader.applied_migrations
assert ('availability','0016_contentmapping_match_evidence') in executor.loader.applied_migrations
print('REVIEWED_PLAN',plan)
