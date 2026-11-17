"""Read-only database snapshots and preservation checks for canonical rollout."""
import hashlib
import json

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def snapshot(connection, transaction):
    result = {}
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        for (table,) in cursor.fetchall():
            cursor.execute(f'SELECT to_jsonb(t) FROM {connection.ops.quote_name(table)} t ORDER BY to_jsonb(t)::text')
            rows = [json.loads(r[0]) if isinstance(r[0], str) else r[0] for r in cursor.fetchall()]
            result[table] = {'count': len(rows), 'sha256': digest(rows),
                'row_hashes': {str(row.get('id', digest(row))): digest(row) for row in rows}}
            if table == 'tracker_discoveredcataloguerecord':
                result['discovery_identity'] = {'count': len(rows), 'sha256': digest([
                    {k: v for k, v in r.items() if k not in ('updated_at', 'classification_state', 'classified_content_type')}
                    for r in rows])}
    return result


def verify(before, imported, replayed, manifest):
    from availability.models import ContentMapping, Offer, Source
    from tracker.models import CanonicalBootstrapEvidence, DiscoveredCatalogueRecord, Movie
    from tracker.services.canonical_rollout import observation_digest
    changed_tables = {'tracker_movie', 'tracker_canonicalbootstrapevidence', 'tracker_discoveredcataloguerecord',
                      'availability_providercontent', 'availability_contentmapping', 'availability_source'}
    ids = {str(r['source_record_id']) for r in manifest['associations']}
    checks = {
        'all_discovery_identities_preserved': before['discovery_identity'] == imported['discovery_identity'],
        'all_other_tables_unchanged': all(v == imported[k] for k, v in before.items() if k not in changed_tables),
        'legacy_3300_rows_unchanged': before['tracker_scrapedcontentmapping'] == imported['tracker_scrapedcontentmapping']
            and imported['tracker_scrapedcontentmapping']['count'] == 3300,
        'provider_settings_unchanged': before['tracker_provider'] == imported['tracker_provider'],
        'checkpoints_unchanged': before['tracker_cataloguediscoverystate'] == imported['tracker_cataloguediscoverystate'],
        'pending_records_preserved_in_full': all(v == imported['tracker_discoveredcataloguerecord']['row_hashes'].get(k)
            for k, v in before['tracker_discoveredcataloguerecord']['row_hashes'].items() if k not in ids),
        'all_existing_provider_content_preserved': all(v == imported['availability_providercontent']['row_hashes'].get(k)
            for k, v in before['availability_providercontent']['row_hashes'].items()),
        'existing_source_settings_preserved': all(v == imported['availability_source']['row_hashes'].get(k)
            for k, v in before['availability_source']['row_hashes'].items()),
        'no_offers': Offer.objects.count() == 0 and before['availability_offer'] == imported['availability_offer'],
        'replay_every_table_and_timestamp_unchanged': imported == replayed,
    }
    expected_models = [('canonical', 'tracker_movie'), ('evidence', 'tracker_canonicalbootstrapevidence'),
                       ('mappings', 'availability_contentmapping'), ('provider_content', 'availability_providercontent')]
    checks['expected_row_inserts'] = all(imported[table]['count'] - before[table]['count'] == manifest['expected_changes'][key]
                                        for key, table in expected_models)
    checks['expected_source_inserts'] = (imported['availability_source']['count'] - before['availability_source']['count']
                                         == len(manifest['expected_changes']['disabled_identity_sources']))
    checks['new_sources_disabled_identity_only'] = all(
        not s.enabled and s.configuration.get('identity_only') is True
        and s.configuration.get('availability_enabled') is False
        for s in Source.objects.exclude(pk__in=before['availability_source']['row_hashes']))
    errors = []
    for row in manifest['associations']:
        record = DiscoveredCatalogueRecord.objects.select_related('provider').get(pk=row['source_record_id'])
        movie = Movie.objects.get(**row['canonical_id'])
        proof = CanonicalBootstrapEvidence.objects.get(discovered_record=record)
        mapping = ContentMapping.objects.select_related('content').get(provider=record.provider,
                                                       provider_content_id=row['provider_content_id'])
        if not (movie.year == row['source_observation']['source_year'] and proof.movie_id == movie.pk
                and mapping.tmdb_id == movie.tmdb_id and mapping.content_type == movie.content_type
                and mapping.content.provider_id == record.provider_id
                and mapping.content.provider_content_id == row['provider_content_id']
                and mapping.content.content_type == movie.content_type
                and not mapping.content.currently_available and observation_digest(record) == row['source_sha256']):
            errors.append(record.pk)
    checks['every_canonical_and_provider_relationship_correct'] = not errors
    return {'checks': checks, 'passed': all(checks.values()), 'relationship_errors': errors,
            'associations_checked': len(ids), 'unselected_records_checked': before['tracker_discoveredcataloguerecord']['count'] - len(ids),
            'counts': {k: v['count'] for k, v in replayed.items() if k in changed_tables | {'availability_offer', 'tracker_scrapedcontentmapping'}},
            'before_sha256': digest(before), 'after_sha256': digest(imported), 'replay_sha256': digest(replayed)}



REQUIRED_MIGRATIONS = [
    ('availability', '0016_contentmapping_match_evidence'),
    ('tracker', '0054_discovered_catalogue_last_seen'),
    ('tracker', '0055_cataloguediscoverystate_heartbeat_at_and_more'),
    ('tracker', '0056_movie_tmdb_content_type_identity'),
]
ADDED_COLUMNS = {
    'tracker_discoveredcataloguerecord': {'last_seen_at'},
    'tracker_cataloguediscoverystate': {'heartbeat_at', 'lease_expires_at', 'lease_owner'},
    'availability_contentmapping': {'match_evidence'},
}


def schema_status(connection):
    """Inspect history and physical objects; neither history nor schema is repaired."""
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    result = {'pending_migrations': [f'{m.app_label}.{m.name}' for m, backwards in plan],
              'history': sorted(f'{a}.{n}' for a, n in executor.loader.applied_migrations),
              'objects': {}, 'missing_requirements': []}
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
        for table in (*ADDED_COLUMNS, 'tracker_movie', 'tracker_canonicalbootstrapevidence'):
            if table not in tables:
                result['objects'][table] = None
                result['missing_requirements'].append(f'missing table {table}')
                continue
            columns = connection.introspection.get_table_description(cursor, table)
            constraints = connection.introspection.get_constraints(cursor, table)
            result['objects'][table] = {
                'columns': {c.name: {'type_code': c.type_code, 'nullable': c.null_ok} for c in columns},
                'constraints_and_indexes': constraints}
            for field in sorted(ADDED_COLUMNS.get(table, ())):
                if field not in {c.name for c in columns}:
                    result['missing_requirements'].append(f'missing column {table}.{field}')
        movie = result['objects']['tracker_movie']
        constraints = movie['constraints_and_indexes'] if movie else {}
        typed = constraints.get('uniq_movie_tmdb_content_type', {})
        if not (typed.get('unique') and typed.get('columns') == ['tmdb_id', 'content_type']):
            result['missing_requirements'].append('missing typed Movie unique constraint')
        if any(c.get('unique') and c.get('columns') == ['tmdb_id'] for c in constraints.values()):
            result['missing_requirements'].append('obsolete global Movie.tmdb_id unique constraint')
        evidence = result['objects']['tracker_canonicalbootstrapevidence']
        if evidence:
            required_fields = {'id', 'source_kind', 'verification_method', 'evidence', 'verified_at', 'discovered_record_id', 'movie_id'}
            if not required_fields.issubset(evidence['columns']):
                result['missing_requirements'].append('incomplete canonical bootstrap evidence columns')
            ec = evidence['constraints_and_indexes'].values()
            if not any(c.get('unique') and c.get('columns') == ['discovered_record_id'] for c in ec):
                result['missing_requirements'].append('missing record-specific evidence unique constraint')
            for field, table in [('discovered_record_id', 'tracker_discoveredcataloguerecord'), ('movie_id', 'tracker_movie')]:
                if not any(c.get('columns') == [field] and c.get('foreign_key') == (table, 'id') for c in ec):
                    result['missing_requirements'].append(f'missing evidence foreign key {field}')
        for table, field in [('tracker_discoveredcataloguerecord', 'last_seen_at'), ('availability_contentmapping', 'match_evidence')]:
            obj = result['objects'][table]
            if obj and field in obj['columns'] and obj['columns'][field]['nullable']:
                result['missing_requirements'].append(f'unexpected nullable field {table}.{field}')
        for app, name in REQUIRED_MIGRATIONS:
            if (app, name) not in executor.loader.applied_migrations:
                result['missing_requirements'].append(f'unapplied {app}.{name}')
    result['missing_requirements'].sort()
    result['ready'] = not result['pending_migrations'] and not result['missing_requirements']
    return result


def capture_state(connection, transaction):
    """Capture old-column projections so migration preservation is testable."""
    tables = snapshot(connection, transaction)
    projections = {}
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        schema = schema_status(connection)
        for table, added in ADDED_COLUMNS.items():
            cursor.execute(f'SELECT to_jsonb(t) FROM {connection.ops.quote_name(table)} t ORDER BY id')
            rows = [json.loads(r[0]) if isinstance(r[0], str) else r[0] for r in cursor.fetchall()]
            projections[table] = digest([{k: v for k, v in r.items() if k not in added} for r in rows])
            if table == 'tracker_discoveredcataloguerecord':
                projections['last_seen_expected'] = digest([(r['id'], r['updated_at'] or r['discovered_at']) for r in rows])
                projections['last_seen_actual'] = digest([(r['id'], r.get('last_seen_at')) for r in rows])
        cursor.execute('SELECT app,name FROM django_migrations ORDER BY app,name')
        history = [list(row) for row in cursor.fetchall()]
        framework = {}
        for table in ('django_content_type', 'auth_permission'):
            cursor.execute(f'SELECT to_jsonb(t) FROM {table} t ORDER BY id')
            framework[table] = [json.loads(r[0]) if isinstance(r[0], str) else r[0] for r in cursor.fetchall()]
    return json.loads(json.dumps({'tables': tables, 'schema': schema,
                                  'old_column_projections': projections, 'migration_history': history,
                                  'framework_identity_rows': framework}, default=str))


def verify_migrations(before, after):
    old, new = before['tables'], after['tables']
    evidence = 'tracker_canonicalbootstrapevidence'
    allowed = set(ADDED_COLUMNS) | {'tracker_movie', 'django_migrations', 'discovery_identity', 'auth_permission', 'django_content_type'}
    checks = {
        'exactly_expected_table_added': set(new) - set(old) == {evidence} and not set(old) - set(new),
        'all_unrelated_rows_and_hashes_unchanged': all(v == new[k] for k, v in old.items() if k not in allowed),
        'existing_data_table_counts_unchanged': all(old[k]['count'] == new[k]['count'] for k in old if k not in ('django_migrations', 'auth_permission', 'django_content_type')),
        'all_old_column_values_unchanged': all(before['old_column_projections'][k] == after['old_column_projections'][k] for k in ADDED_COLUMNS),
        'last_seen_backfill_matches_original_timestamps': before['old_column_projections']['last_seen_expected'] == after['old_column_projections']['last_seen_actual'],
        'exactly_four_expected_migrations_added': {tuple(x) for x in after['migration_history']} - {tuple(x) for x in before['migration_history']} == set(REQUIRED_MIGRATIONS)
            and set(map(tuple, before['migration_history'])).issubset(set(map(tuple, after['migration_history']))),
        'new_evidence_table_empty': new.get(evidence, {}).get('count') == 0,
        'discoveries_5095_preserved': old['tracker_discoveredcataloguerecord']['count'] == new['tracker_discoveredcataloguerecord']['count'] == 5095,
        'legacy_3300_preserved': old['tracker_scrapedcontentmapping'] == new['tracker_scrapedcontentmapping'] and new['tracker_scrapedcontentmapping']['count'] == 3300,
        'providers_and_checkpoints_preserved': old['tracker_provider'] == new['tracker_provider']
            and before['old_column_projections']['tracker_cataloguediscoverystate'] == after['old_column_projections']['tracker_cataloguediscoverystate'],
        'offers_remain_zero': old['availability_offer'] == new['availability_offer'] and new['availability_offer']['count'] == 0,
        'schema_ready': after['schema']['ready'],
    }
    framework_before, framework_after = before['framework_identity_rows'], after['framework_identity_rows']
    added = {}
    checks['existing_framework_rows_preserved'] = True
    for table in ('django_content_type', 'auth_permission'):
        original = {row['id']: row for row in framework_before[table]}
        current = {row['id']: row for row in framework_after[table]}
        checks['existing_framework_rows_preserved'] &= all(current.get(pk) == row for pk, row in original.items())
        added[table] = [row for pk, row in current.items() if pk not in original]
    types = added['django_content_type']
    checks['exact_expected_content_type_added'] = len(types) == 1 and types[0]['app_label'] == 'tracker' and types[0]['model'] == 'canonicalbootstrapevidence'
    permissions = added['auth_permission']
    checks['exact_four_expected_permissions_added'] = (len(types) == 1 and len(permissions) == 4
        and {r['codename'] for r in permissions} == {f'{verb}_canonicalbootstrapevidence' for verb in ('add', 'change', 'delete', 'view')}
        and all(r['content_type_id'] == types[0]['id'] for r in permissions))
    return {'passed': all(checks.values()), 'checks': checks, 'expected_framework_additions': added}
