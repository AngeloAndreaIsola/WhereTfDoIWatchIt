#!/usr/bin/env python3
"""Reproduce bounded catalogue validation on a restored PostgreSQL clone.

Supply clone DB credentials through the normal Django environment, never flags.
Example: python scripts/canonical_scale_validation.py --database canonical_scale
    --host canonical-scale-db --output /evidence --batch 1 plan
The script refuses every database whose name does not start with canonical_.
Management commands remain the separately authorized live rollout boundary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import sys
from collections import defaultdict

backend_root = Path(__file__).resolve().parents[1] / 'backend'
if not (backend_root / 'manage.py').exists():
    backend_root = Path.cwd()  # backend-only container with scripts mounted under /app/scripts
sys.path.insert(0, str(backend_root))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--batch', type=int, default=1)
    parser.add_argument('--previous-audit', type=Path, action='append', default=[])
    parser.add_argument('--sample-per-type', type=int, default=None)
    parser.add_argument('--allow-network', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--request-budget', type=int, default=350)
    parser.add_argument('phase', choices=('audit', 'plan', 'bootstrap', 'reconcile', 'sample'))
    args = parser.parse_args()
    if args.batch < 1 or (args.sample_per_type is not None and not 1 <= args.sample_per_type <= 25):
        parser.error('Require positive batch number and sample-per-type 1..25')
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connection
    from tracker.models import (
        CanonicalBootstrapEvidence, CatalogueDiscoveryState, DiscoveredCatalogueRecord,
        Movie, ScrapedContentMapping,
    )
    from tracker.services.catalogue_reconciliation import _extract_year, _normal_title, _type_evidence
    from availability.models import ContentMapping, Offer, ProviderContent, TmdbDetailsCache, TmdbSearchCache
    from availability.services.candidate_gateway import CandidateDiscoveryBudget, TMDbCandidateGateway

    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database()')
        actual_database = cursor.fetchone()[0]
    if (connection.vendor != 'postgresql' or not args.database.startswith('canonical_')
            or actual_database != args.database or connection.settings_dict['HOST'] != args.host):
        parser.error('Target must be the exact expected disposable PostgreSQL clone and host')
    args.output.mkdir(parents=True, exist_ok=True)
    prefix = args.output / f'batch-{args.batch}'
    ids_path = Path(f'{prefix}-ids.json')

    def write(path, value):
        Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + '\n')

    def digest(queryset):
        return hashlib.sha256(json.dumps(list(queryset), sort_keys=True, default=str).encode()).hexdigest()

    if args.phase == 'audit':
        report = {'database': actual_database, 'counts': {}, 'digests': {}}
        for model in (DiscoveredCatalogueRecord, ScrapedContentMapping, Movie,
                      CanonicalBootstrapEvidence, ContentMapping, ProviderContent, Offer,
                      TmdbSearchCache, TmdbDetailsCache):
            report['counts'][model.__name__] = model.objects.count()
            report['digests'][model.__name__] = digest(model.objects.order_by('pk').values())
        report['discovery_identity_digest'] = digest(DiscoveredCatalogueRecord.objects.order_by('pk').values(
            'id', 'provider_id', 'provider_native_id', 'source_url', 'identity_url', 'title',
            'original_metadata', 'source_urls_seen'))
        report['discovery_checkpoints_digest'] = digest(CatalogueDiscoveryState.objects.order_by('pk').values())
        write(args.output / 'audit.json', report)
        return

    if args.phase == 'plan':
        if ids_path.exists():
            parser.error('Manifest already exists; preserve it for resumption')
        records = list(DiscoveredCatalogueRecord.objects.select_related('provider').order_by('pk'))
        groups = defaultdict(list)
        for record in records:
            groups[(record.provider_id, _type_evidence(record)[0], _extract_year(record)[0],
                    _normal_title(record.title))].append(record.pk)
        excluded = set(CanonicalBootstrapEvidence.objects.values_list('discovered_record_id', flat=True))
        for audit_path in args.previous_audit:
            audit = json.loads(audit_path.read_text())
            excluded.update(pk for batch in audit['batches'] for pk in batch['record_ids'])
        if args.batch > 2 and not args.previous_audit:
            parser.error('Continuation requires --previous-audit including every evaluated/pending record')
        if args.batch == 1:
            selected = {r.pk for r in records if r.provider.slug == 'animeworld'}
            selected.update(pk for group in groups.values() if len(group) > 1 for pk in group)
            targets = [('movie', 100), ('series', 71)]
        else:
            for previous in args.output.glob('batch-*-ids.json'):
                excluded.update(json.loads(previous.read_text()))
            if args.batch == 2 and not (args.output / 'batch-1-ids.json').exists():
                parser.error('Batch 2 requires the first frozen selection')
            selected = set()
            targets = [('movie', 150), ('series', 100)]
        rng = random.Random(20261116 + args.batch)
        for kind, target in targets:
            existing = sum(r.pk in selected and r.provider.slug == 'hydrahd'
                           and _type_evidence(r)[0] == kind for r in records)
            pool = [r.pk for r in records if r.provider.slug == 'hydrahd'
                    and _type_evidence(r)[0] == kind and r.pk not in selected | excluded]
            selected.update(rng.sample(pool, min(max(0, target - existing), len(pool))))
        selected.difference_update(excluded)
        if len(selected) > 250:
            parser.error('Strata exceed 250; review inventory and choose explicit IDs')
        write(ids_path, sorted(selected))
        return

    ids = json.loads(ids_path.read_text())
    if not isinstance(ids, list) or not 0 < len(ids) <= 250 or len(ids) != len(set(ids)):
        parser.error('Require 1..250 unique explicit record IDs')
    selection = [flag for pk in ids for flag in ('--record-id', str(pk))]
    mode = 'apply' if args.apply else 'dry'
    if args.phase == 'bootstrap':
        flags = [*selection, '--limit', str(len(ids)), '--request-budget', str(args.request_budget),
                 '--interval', '0.3', '--retries', '2', '--checkpoint-file', f'{prefix}-{mode}-checkpoint.json']
        if args.allow_network:
            flags.append('--allow-network')
        if args.apply:
            flags.append('--apply')
        with Path(f'{prefix}-{mode}.json').open('w') as output:
            call_command('bootstrap_canonical_catalogue', *flags, stdout=output)
    elif args.phase == 'reconcile':
        with Path(f'{prefix}-reconcile-{mode}.json').open('w') as output:
            call_command('reconcile_unofficial_catalogue', *selection,
                         *(['--apply'] if args.apply else []), stdout=output)
    else:
        candidates = [Path(f'{prefix}-{suffix}') for suffix in (
            'dry-checkpoint.json', 'dry.json', 'apply-checkpoint.json', 'apply.json')]
        journal = next((path for path in candidates if path.exists()), None)
        if journal is None:
            parser.error('Run bootstrap before sampling')
        report = json.loads(journal.read_text())
        budget = CandidateDiscoveryBudget(args.request_budget)
        gateway = TMDbCandidateGateway(budget=budget, min_request_interval_seconds=.3)
        gateway.stop_on_rate_limit = True
        rng = random.Random(70 + args.batch)
        sample = []
        for kind in ('movie', 'series'):
            pool = [r for r in report['records'] if r['status'] == 'VERIFIED' and r['content_type'] == kind]
            sample_size = args.sample_per_type or (15 if args.batch == 1 else 10)
            for row in rng.sample(pool, min(sample_size, len(pool))):
                candidates = gateway.search(row['title'], content_type=kind, authorize_network=args.allow_network)
                sample.append({**row, 'independent_search_confirmed': any(
                    c['tmdb_id'] == row['tmdb_id'] and c['year'] == row['year']
                    and c['content_type'] == kind for c in candidates)})
        write(f'{prefix}-sample.json', {'sample': sample, 'budget': budget.to_dict(),
              'limitation': 'Same TMDb authority; year-free search is an independent query, not independent ground truth.'})


if __name__ == '__main__':
    main()
