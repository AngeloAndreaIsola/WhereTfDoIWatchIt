#!/usr/bin/env python3
"""Snapshot/verify a restored, migrated canonical_* PostgreSQL clone only.

Run snapshot before import, snapshot after import, replay, then verify. This
checks the actual rows, including every pending discovery, rather than relying
on command counters. No network requests, migrations or writes to DB occur.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

backend = Path(__file__).resolve().parents[1] / 'backend'
sys.path.insert(0, str(backend if (backend / 'manage.py').exists() else Path.cwd()))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--before', type=Path)
    parser.add_argument('--imported', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('phase', choices=('snapshot', 'verify'))
    args = parser.parse_args()
    import django
    django.setup()
    from django.db import connection, transaction
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database()')
        actual = cursor.fetchone()[0]
    if (connection.vendor != 'postgresql' or not args.database.startswith('canonical_')
            or actual != args.database or connection.settings_dict['HOST'] != args.host):
        parser.error('Require the exact expected disposable PostgreSQL clone')
    if args.output.exists():
        parser.error('Output already exists; preserve audit artifacts')
    result = snapshot(connection, transaction)
    if args.phase == 'verify':
        if not all((args.before, args.imported, args.manifest)):
            parser.error('verify requires --before --imported --manifest')
        result = verify(json.loads(args.before.read_text()), json.loads(args.imported.read_text()),
                        result, json.loads(args.manifest.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    if args.phase == 'verify' and not result['passed']:
        raise SystemExit('Clone verification failed; inspect the saved report')


if __name__ == '__main__':
    main()
