"""Offline, all-or-nothing application of an explicitly reviewed association manifest."""
from collections import Counter
import hashlib
import json

from django.core.management.base import CommandError
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from availability.models import ContentMapping, ProviderContent
from availability.services.candidate_gateway import TMDbCandidateGateway
from tracker.models import CanonicalBootstrapEvidence, DiscoveredCatalogueRecord, Movie
from tracker.management.commands.bootstrap_canonical_catalogue import Command as Bootstrap, source_snapshot
from tracker.services.catalogue_reconciliation import (
    _apply_classification, _extract_year, _normal_title, _provider_content_id,
    _trusted_external_tmdb_id, _type_evidence,
)


def observation(record):
    """Retain original source metadata, even fields not used by the matcher."""
    return {**source_snapshot(record), 'provider_id': record.provider_id,
            'original_metadata': record.original_metadata, 'source_urls_seen': record.source_urls_seen}


def observation_digest(record):
    return hashlib.sha256(json.dumps(observation(record), sort_keys=True).encode()).hexdigest()


def title_group(record):
    return (record.provider_id, _type_evidence(record)[0], _extract_year(record)[0], _normal_title(record.title))


def validate_association(row, record, groups):
    """Recheck every proof rather than trusting a VERIFIED label in an export."""
    def require(condition, reason):
        if not condition:
            raise CommandError(f"Record {record.pk}: {reason}")
    require(row['source_record_id'] == record.pk and row['source_observation'] == observation(record)
            and row['source_sha256'] == observation_digest(record), 'source observation changed')
    media, year = _type_evidence(record)[0], _extract_year(record)[0]
    canonical = row['canonical_id']
    require(media in ('movie', 'series') and year is not None, 'source year/type missing')
    require(row['media_type'] == media and canonical['content_type'] == media
            and type(canonical['tmdb_id']) is int and canonical['tmdb_id'] > 0, 'invalid typed canonical ID')
    require(row['provider_content_id'] == _provider_content_id(record), 'provider identity differs')
    proof = row['verification_evidence']
    require(proof['method'] == 'exact_title_year_type', 'unsupported verification method')
    for entry in ('search', 'details'):
        expires = parse_datetime(proof[entry]['expires_at'])
        require(expires is not None and timezone.is_aware(expires) and expires > timezone.now(), 'proof expired')
    search = proof['search']
    require(search['query'] == TMDbCandidateGateway._normalize_query(record.title) and search['year'] == year
            and search['content_type'] == media, 'search scope differs')
    candidates = search['results']
    exact = {c['tmdb_id'] for c in candidates if c.get('content_type') == media
             and c.get('year') == year and _normal_title(c.get('title')) == _normal_title(record.title)}
    require(bool(_normal_title(record.title)) and exact == {canonical['tmdb_id']}, 'search is not uniquely exact')
    require(not any(c.get('search_truncated') for c in candidates) and groups[title_group(record)] == 1,
            'incomplete search or competing source releases')
    details = proof['details']['payload']
    require(details.get('tmdb_id') == canonical['tmdb_id'] and details.get('content_type') == media
            and details.get('year') == year and _normal_title(record.title) in {
                _normal_title(details.get('title')), _normal_title(details.get('original_title'))},
            'details contradict identifier/title/year/type')
    trusted = _trusted_external_tmdb_id(record)
    require(not trusted or int(trusted) == canonical['tmdb_id'], 'trusted identifier conflicts')
    movie = Movie.objects.filter(**canonical).first()
    require(movie is None or movie.year == year, 'canonical year conflicts')
    evidence = CanonicalBootstrapEvidence.objects.filter(discovered_record=record).select_related('movie').first()
    require(evidence is None or (evidence.movie.tmdb_id == canonical['tmdb_id']
            and evidence.movie.content_type == media and all(
                evidence.evidence.get(k) == v for k, v in source_snapshot(record).items())), 'existing proof conflicts')
    mapping = ContentMapping.objects.filter(provider=record.provider, provider_content_id=row['provider_content_id']).first()
    require(mapping is None or (mapping.content_type == media and mapping.tmdb_id == canonical['tmdb_id']),
            'existing provider mapping conflicts')
    contents = ProviderContent.objects.filter(provider=record.provider, provider_content_id=row['provider_content_id'])
    for content in contents:
        require(content.content_type == media and content.season_number is None and content.episode_number is None
                and content.provider_url == record.source_url
                and content.metadata.get('discovered_catalogue_record_id') == record.pk,
                'provider identity already belongs to another observation')
    require(mapping is None or (mapping.content_id is not None and contents.filter(pk=mapping.content_id).exists()),
            'mapping provider-content relationship differs')


class ManifestGateway:
    """No HTTP or cache mutation path: only the reviewed evidence is available."""
    def __init__(self, row):
        self.proof = row['verification_evidence']

    def search(self, *args, **kwargs):
        return self.proof['search']['results']

    def get_details(self, *args, **kwargs):
        return self.proof['details']['payload']


@transaction.atomic
def apply_manifest(manifest, *, apply=False):
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('associations'), list):
        raise CommandError('Unsupported manifest schema')
    rows = manifest['associations']
    if not 0 < len(rows) <= 500:
        raise CommandError('Manifest must contain 1..500 associations')
    ids = [r['source_record_id'] for r in rows]
    keys = [(r['source_observation']['provider_id'], r['provider_content_id']) for r in rows]
    if len(set(ids)) != len(ids) or len(set(keys)) != len(keys):
        raise CommandError('Duplicate source or provider identity in manifest')
    if apply and connection.vendor == 'postgresql':
        # Serialize identity writes so a concurrent insert cannot invalidate the
        # complete-title-group or provider-identity checks after preflight.
        with connection.cursor() as cursor:
            cursor.execute('LOCK TABLE tracker_discoveredcataloguerecord, tracker_movie, '
                           'tracker_canonicalbootstrapevidence, availability_providercontent, '
                           'availability_contentmapping, availability_source IN SHARE ROW EXCLUSIVE MODE')
    records = DiscoveredCatalogueRecord.objects.select_related('provider').order_by('pk')
    groups = Counter(title_group(r) for r in records.iterator())
    selected = records.filter(pk__in=ids)
    if apply:
        selected = selected.select_for_update()
    by_id = {r.pk: r for r in selected}
    if set(by_id) != set(ids):
        raise CommandError('Manifest discoveries are missing')
    for row in rows:
        validate_association(row, by_id[row['source_record_id']], groups)
    before = {'canonical': Movie.objects.count(), 'evidence': CanonicalBootstrapEvidence.objects.count(),
              'mappings': ContentMapping.objects.count(), 'provider_content': ProviderContent.objects.count()}
    actions = Counter()
    if apply:
        bootstrap = Bootstrap()
        bootstrap.title_groups = groups
        for row in rows:
            record = by_id[row['source_record_id']]
            result, totals = {}, Counter()
            bootstrap.evaluate(record, row['media_type'], _extract_year(record)[0], ManifestGateway(row),
                               {'allow_network': False, 'apply': True}, totals, result)
            if result.get('status') != 'VERIFIED' or result['tmdb_id'] != row['canonical_id']['tmdb_id']:
                raise CommandError(f'Record {record.pk}: bootstrap failed exact manifest validation')
            movie = Movie.objects.get(**row['canonical_id'])
            match = {'status': 'MATCH_VERIFIED', 'confidence': 1.0,
                     'evidence': ['record-specific TMDb ID/details verified identifier, title, year and media type'],
                     'candidates': [{'movie_id': movie.pk, **row['canonical_id'], 'title': movie.title,
                                     'year': movie.year}]}
            action = _apply_classification(record, row['media_type'], _extract_year(record)[0], match)
            if action not in ('canonical_mapping_created', 'existing_canonical_mapping_preserved'):
                raise CommandError(f'Record {record.pk}: unexpected reconciliation action {action}')
            actions[action] += 1
    after = {'canonical': Movie.objects.count(), 'evidence': CanonicalBootstrapEvidence.objects.count(),
             'mappings': ContentMapping.objects.count(), 'provider_content': ProviderContent.objects.count()}
    return {'mode': 'APPLY' if apply else 'DRY_RUN', 'associations_validated': len(rows),
            'changes': {k: after[k] - v for k, v in before.items()}, 'actions': dict(actions),
            'tmdb_requests': 0, 'offers_created': 0}
