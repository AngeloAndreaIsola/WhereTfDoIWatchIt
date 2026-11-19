#!/usr/bin/env python3
"""Offline discovery -> eligibility -> verified reconciliation preview on a clone.

Run in an internal-only network with external sockets denied. Requires an exact
incremental_* PostgreSQL clone and the completed inventory. All scenario writes
roll back; clone-only sequence restoration leaves the complete snapshot intact.
No reconciliation apply, canonical creation, provider requests or TMDb requests.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--host', required=True)
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[1] / 'backend'
    sys.path.insert(0, str(backend if backend.exists() else Path.cwd()))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from django.db import connection, transaction
    from django.test import override_settings
    from availability.models import ContentMapping, Offer, Source
    from availability.services.candidate_gateway import TMDbCandidateGateway
    from availability.tests.test_candidate_gateway import FakeResponse
    from tracker.models import CatalogueDiscoveryState, DiscoveredCatalogueRecord, Provider
    from tracker.services.canonical_integrity import snapshot
    from tracker.services.catalogue_discovery import run_discovery
    from tracker.services.catalogue_reconciliation import _type_evidence, _provider_content_id
    from tracker.services.discovery_http import DiscoveryHTTP
    from tracker.services.incremental_catalogue_reconciliation import eligible_records, run_incremental_reconciliation
    from tracker.tests.discovery_fixtures import FakeClock
    from tracker.tests.test_discovery_controls import PERMISSION, listing, response

    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database()')
        actual = cursor.fetchone()[0]
    if (connection.vendor != 'postgresql' or not args.database.startswith('incremental_')
            or actual != args.database or connection.settings_dict['HOST'] != args.host):
        parser.error('Require exact disposable incremental_* PostgreSQL clone target')

    def sequences():
        result = {}
        with connection.cursor() as cur:
            cur.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
            for (name,) in cur.fetchall():
                cur.execute(f'SELECT last_value,is_called FROM {connection.ops.quote_name(name)}')
                result[name] = cur.fetchone()
        return result

    def rows():
        result = {}
        with connection.cursor() as cur:
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            for (table,) in cur.fetchall():
                cur.execute(f'SELECT to_jsonb(t)::text FROM {connection.ops.quote_name(table)} t ORDER BY to_jsonb(t)::text')
                result[table] = hashlib.sha256(json.dumps(cur.fetchall()).encode()).hexdigest()
        return result

    before, seq_before, baseline_rows = snapshot(connection, transaction), sequences(), rows()
    assert DiscoveredCatalogueRecord.objects.count() == 5095
    assert ContentMapping.objects.count() == 4769 and not Offer.objects.exists()
    assert not list(eligible_records('hydrahd'))
    provider = Provider.objects.get(slug='hydrahd')
    state = CatalogueDiscoveryState.objects.get(provider=provider)
    checkpoint_before = state.cursor['modes']['historical']['parts'].copy()
    assert checkpoint_before == {'movies': 132, 'tv-shows': 77}
    report = dict(database=actual, host=args.host, external_http_attempts=0,
        evidence_quality='deterministic fixtures; not real source permission or TMDb identity evidence')
    discovery_clock, tmdb_clock = FakeClock(), FakeClock()

    class FixtureGateway(TMDbCandidateGateway):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, sleep_fn=tmdb_clock.sleep, monotonic_fn=tmdb_clock.monotonic, **kwargs)

    title, tmdb_id = 'Offline Canary Movie', 1999998001
    def candidates(*, ambiguous=False, contradictory=False):
        results = [{'id': tmdb_id, 'title': title, 'release_date': '2020-01-01'}]
        if ambiguous:
            results.append({'id': tmdb_id+1, 'title': title, 'release_date': '2020-01-01'})
        return [FakeResponse(200, {'results': results}), FakeResponse(200, {
            'id': tmdb_id, 'title': 'Contradictory Title' if contradictory else title,
            'release_date': '2020-01-01'})]

    module = 'tracker.services.incremental_catalogue_reconciliation'
    # Catch accidental use of any un-fixtured Requests path. The test container
    # additionally denies external DNS/sockets and has no external route.
    with patch('requests.sessions.Session.request', side_effect=AssertionError('unfixtured HTTP forbidden')), \
            patch(module+'.TMDbCandidateGateway', FixtureGateway), override_settings(TMDB_API_KEY='fixture-only'):
        with transaction.atomic():
            with patch('tracker.services.catalogue_discovery.permission_for', return_value=PERMISSION), \
                    patch('tracker.services.discovery_http.public_addresses', return_value=['93.184.216.34']), \
                    patch.object(DiscoveryHTTP, '_send', side_effect=[response(body='User-agent: *\nAllow: /'), response(body=listing(title))]) as http:
                discovery = run_discovery('hydrahd', mode='historical', partition='movies', max_pages=1,
                    request_budget=5, delay=2, retry_limit=0, clock=discovery_clock)
            assert http.call_count == discovery['requests_made'] == 2
            assert discovery['pages_by_partition'] == {'movies': [133]}
            assert discovery['checkpoint']['parts'] == {'movies': 133, 'tv-shows': 77}
            record = DiscoveredCatalogueRecord.objects.get(provider=provider, title=title)
            assert [(r.pk, why) for r,why in eligible_records('hydrahd')] == [(record.pk, 'new_discovery')]
            assert _type_evidence(record)[0] == 'movie' and record.classification_state == 'PENDING'

            def preview(responses):
                stable = rows()
                with patch('requests.Session.get', side_effect=responses) as tmdb:
                    result = run_incremental_reconciliation('hydrahd', limit=2, request_budget=8,
                        interval=2, retries=0, allow_network=True, apply=False, record_ids=[record.pk])
                assert rows() == stable, 'dry-run changed a table'
                assert tmdb.call_count == result['tmdb_budget']['attempted']
                return result

            proposal = preview(candidates())
            assert proposal['records'][0]['status'] == 'VERIFIED'
            assert proposal['records'][0]['tmdb_id'] == tmdb_id
            assert proposal['tmdb_budget']['attempted'] == 2
            assert proposal['records'][0]['verification_source'] == 'tmdb_search_and_details'
            repeated_preview = preview(candidates())
            assert repeated_preview['records'][0] == proposal['records'][0]
            ambiguous = preview(candidates(ambiguous=True))
            assert ambiguous['records'][0]['status'] == 'AMBIGUOUS'
            assert ambiguous['tmdb_budget']['attempted'] == 1
            contradiction = preview(candidates(contradictory=True))
            assert contradiction['records'][0]['status'] == 'CONFLICTING'

            with transaction.atomic():
                # Explicit clone-only contradictory fixture, rolled back before
                # comparison; preview itself must never change the fixture.
                existing = ContentMapping.objects.filter(content_type='movie').first()
                ContentMapping.objects.create(pk=-1999998001, provider=provider,
                    source=Source.objects.get(slug='hydrahd'), provider_content_id=_provider_content_id(record),
                    content_type='movie', tmdb_id=existing.tmdb_id, match_method='manual')
                conflict = preview(candidates())
                assert conflict['records'][0]['status'] == 'CONFLICTING'
                assert conflict['records'][0]['association_conflict']
                transaction.set_rollback(True)

            # Replaying an unchanged moving listing on the next checkpoint page
            # is detected as repeated nonterminal content; never reset a cursor.
            identities_before = list(DiscoveredCatalogueRecord.objects.order_by('pk').values_list('pk','identity_url'))
            with patch('tracker.services.catalogue_discovery.permission_for', return_value=PERMISSION), \
                    patch('tracker.services.discovery_http.public_addresses', return_value=['93.184.216.34']), \
                    patch.object(DiscoveryHTTP, '_send', side_effect=[response(body='User-agent: *\nAllow: /'), response(body=listing(title))]):
                replay = run_discovery('hydrahd', mode='historical', partition='movies', max_pages=1,
                    request_budget=5, delay=2, retry_limit=0, clock=discovery_clock)
            assert replay['items_inserted'] == 0 and replay['checkpoint']['parts']['movies'] == 133
            assert list(DiscoveredCatalogueRecord.objects.order_by('pk').values_list('pk','identity_url')) == identities_before
            assert not Offer.objects.exists() and ContentMapping.objects.count() == 4769
            delta = rows()
            changed = sorted(t for t,v in baseline_rows.items() if delta[t] != v)
            assert changed == ['tracker_cataloguediscoverystate', 'tracker_discoveredcataloguerecord']
            report.update(discovery=discovery, discovery_id=record.pk, classification='movie',
                proposal=proposal, repeated_preview_identical=True, ambiguity=ambiguous,
                contradiction=contradiction, existing_mapping_conflict=conflict, discovery_replay=replay,
                canonical_associations=4769, playback_offers=0,
                scenario_changed_tables=changed,
                dry_run_all_tables_unchanged=True, tmdb_fixture_attempts=Counter(
                    verified=2, repeated_preview=2, ambiguous=1, contradictory_details=2, existing_mapping_conflict=2))
            transaction.set_rollback(True)
    # PostgreSQL sequences are nontransactional. Restore only the checked clone.
    with connection.cursor() as cur:
        for name,(value,called) in seq_before.items():
            cur.execute('SELECT setval(%s::regclass,%s,%s)', [name,value,called])
    after = snapshot(connection, transaction)
    assert before == after and sequences() == seq_before
    report.update(rollback_preserved_all_tables=True, tables_verified=len(after)-1,
        application_tables_verified=sum(t.startswith(('tracker_','availability_')) for t in after),
        sequences_restored_and_verified=len(seq_before), status='OFFLINE_DISCOVERY_CANARY_PASSED')
    print(json.dumps(report, indent=2, default=str))


if __name__ == '__main__':
    main()
