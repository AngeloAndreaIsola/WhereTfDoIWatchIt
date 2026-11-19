"""Audit every retained proof and export the exact proposed rollout batch."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from availability.models import ContentMapping, ProviderContent, Source, TmdbDetailsCache, TmdbSearchCache
from availability.services.candidate_gateway import TMDbCandidateGateway
from tracker.models import CanonicalBootstrapEvidence, DiscoveredCatalogueRecord, Movie
from tracker.management.commands.bootstrap_canonical_catalogue import source_snapshot
from tracker.services.canonical_rollout import observation, observation_digest, title_group, validate_association
from tracker.services.catalogue_reconciliation import _provider_content_id


class Command(BaseCommand):
    help = 'Audit all evidence in a prior reconciliation report and export a self-contained offline manifest.'

    def add_arguments(self, parser):
        parser.add_argument('--audit', required=True)
        parser.add_argument('--output', required=True)
        parser.add_argument('--exclude-record-id', action='append', type=int, default=[])
        parser.add_argument('--exclusion-reason')

    def handle(self, *args, **options):
        source = Path(options['audit']).read_bytes()
        audit = json.loads(source)
        exclusions = set(options['exclude_record_id'])
        if exclusions and not options['exclusion_reason']:
            raise CommandError('Every manual exclusion needs an explicit reason')
        retained = audit['verified_record_evidence']
        if len({r['record_id'] for r in retained}) != len(retained):
            raise CommandError('Duplicate retained evidence')
        if not exclusions.issubset({r['record_id'] for r in retained}):
            raise CommandError('Exclusion is not in the retained evidence')
        records = DiscoveredCatalogueRecord.objects.select_related('provider').order_by('pk')
        groups = Counter(title_group(r) for r in records.iterator())
        by_id = {r.pk: r for r in records}
        rows, outcomes = [], []
        for retained_row in retained:
            pk = retained_row['record_id']
            outcome = {'source_record_id': pk, 'retained_evidence_valid': False, 'included': False}
            try:
                record = by_id[pk]
                if any(retained_row.get(k) != v for k, v in source_snapshot(record).items()):
                    raise CommandError('Retained evidence differs from current source')
                canonical = {'tmdb_id': retained_row['tmdb_id'], 'content_type': retained_row['content_type']}
                search = TmdbSearchCache.objects.get(
                    query=TMDbCandidateGateway._normalize_query(record.title), year=retained_row['source_year'],
                    content_type=canonical['content_type'], locale='en-US', endpoint_version='v3', is_negative=False)
                details = TmdbDetailsCache.objects.get(**canonical, locale='en-US', endpoint_version='v3', is_negative=False)
                native = _provider_content_id(record)
                row = {'source_record_id': pk, 'source_observation': observation(record),
                       'source_sha256': observation_digest(record), 'provider_content_id': native,
                       'canonical_id': canonical, 'media_type': canonical['content_type'],
                       'verification_evidence': {'method': 'exact_title_year_type', 'retained': retained_row,
                           'search': {'query': search.query, 'year': search.year, 'content_type': search.content_type,
                                      'retrieved_at': str(search.created_at), 'expires_at': str(search.expires_at),
                                      'results': search.results},
                           'details': {'retrieved_at': str(details.created_at), 'expires_at': str(details.expires_at),
                                       'payload': details.details}},
                       'expected_database_changes': {
                           'Movie': 'preserve' if Movie.objects.filter(**canonical).exists() else 'insert_typed_identity',
                           'CanonicalBootstrapEvidence': 'preserve' if CanonicalBootstrapEvidence.objects.filter(discovered_record_id=pk).exists() else 'insert_record_specific_evidence',
                           'ProviderContent': 'preserve' if ProviderContent.objects.filter(provider=record.provider, provider_content_id=native).exists() else 'insert_unavailable_identity',
                           'ContentMapping': 'preserve' if ContentMapping.objects.filter(provider=record.provider, provider_content_id=native).exists() else 'insert_verified_mapping',
                           'DiscoveredCatalogueRecord': 'preserve_identity; classify', 'Offer': 'no_change',
                           'ScrapedContentMapping': 'no_change',
                           'Source': 'preserve' if Source.objects.filter(slug=record.provider.slug).exists() else 'insert_disabled_identity_source'}}
                validate_association(row, record, groups)
                outcome['retained_evidence_valid'] = True
                if pk in exclusions:
                    outcome['exclusion_reason'] = options['exclusion_reason']
                else:
                    rows.append(row)
                    outcome['included'] = True
            except (CommandError, KeyError, TmdbSearchCache.DoesNotExist, TmdbDetailsCache.DoesNotExist) as exc:
                outcome['exclusion_reason'] = str(exc)
            outcomes.append(outcome)
        if not rows:
            raise CommandError('No associations passed full audit')
        manifest = {'schema_version': 1, 'created_at': str(timezone.now()),
                    'source_audit_sha256': hashlib.sha256(source).hexdigest(),
                    'canonical_id_namespace': '(TMDb ID, content_type); database primary keys are allocated at import',
                    'live_authorization': 'Separate explicit authorization required; this export authorizes no live writes',
                    'associations': rows, 'audit': outcomes,
                    'expected_changes': {
                        'canonical': len({tuple(r['canonical_id'].values()) for r in rows
                                          if r['expected_database_changes']['Movie'] != 'preserve'}),
                        'evidence': sum(r['expected_database_changes']['CanonicalBootstrapEvidence'] != 'preserve' for r in rows),
                        'mappings': sum(r['expected_database_changes']['ContentMapping'] != 'preserve' for r in rows),
                        'provider_content': sum(r['expected_database_changes']['ProviderContent'] != 'preserve' for r in rows),
                        'disabled_identity_sources': sorted({r['source_observation']['provider'] for r in rows
                            if r['expected_database_changes']['Source'] != 'preserve'}), 'offers': 0},
                    'preserve': ['all discovery identities', 'legacy mappings', 'discovery checkpoints', 'provider settings'],
                    'independent_validation_limit': 'Year-free queries share TMDb authority; not provider-detail ground truth'}
        path = Path(options['output'])
        if path.exists():
            raise CommandError('Manifest already exists; preserve reviewed files')
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode()
        path.write_bytes(payload)
        self.stdout.write(json.dumps({'audited': len(outcomes), 'retained_evidence_valid': sum(r['retained_evidence_valid'] for r in outcomes),
                                      'included': len(rows), 'excluded': [r for r in outcomes if not r['included']],
                                      'sha256': hashlib.sha256(payload).hexdigest()}, indent=2))
