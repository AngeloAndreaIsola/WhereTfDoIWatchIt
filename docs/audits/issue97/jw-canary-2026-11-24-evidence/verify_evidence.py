#!/usr/bin/env python3
"""Read-only verifier for local JW-CANARY-v1b evidence and Git trees."""
import argparse
import collections
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote_to_bytes

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
PACKAGE_PATH = HERE.relative_to(REPO).as_posix()
RUN_PATH = 'runs/jw-canary-2026-11-24'
EXT_KEY = ['title', 'jw_id', 'country', 'provider', 'monetization_type', 'quality', 'price', 'currency']
LIT_KEY = ['country', 'provider', 'monetization_type', 'quality', 'price', 'currency']
LABELS = ['SAME_SOURCE_ECHO', 'UNLICENSED']


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def dedup(rows, keys):
    seen, out = set(), []
    for row in rows:
        k = tuple(row.get(f) for f in keys)
        if k not in seen:
            seen.add(k)
            out.append(row)
    return out


def load_json(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def git(*args, **kwargs):
    return subprocess.run(
        ['git', *args], cwd=REPO, capture_output=True, check=True, **kwargs
    ).stdout


def file_bytes(path):
    return path.read_bytes() if path is not None and path.is_file() else None


def file_status(raw, record):
    if raw is None:
        return 'UNVERIFIABLE'
    if sha256_bytes(raw) != record['sha256'] or len(raw) != record['bytes']:
        return 'FAIL'
    return 'PASS'


def custody_path(item, alias, private_id):
    return item['path'].replace(f'spec/{alias}', f'spec/{private_id[:8]}')


def private_function_id(manifest, root):
    record = next(item for item in manifest['source_inventory'] if item['path'] == 'spec/manifest.json')
    raw = file_bytes(root / record['path'])
    if file_status(raw, record) != 'PASS':
        return None
    return json.loads(raw)['function_id']


def check_files(manifest, root, download_log, private_id):
    checks = []
    for item in manifest['source_inventory']:
        path = item['path']
        location = item['location']
        if location == 'local_download_log':
            raw = file_bytes(download_log or root / path)
        elif location == 'historical_git_blob':
            try:
                raw = git('show', f"{item['source_commit']}:{path}")
            except subprocess.CalledProcessError:
                raw = None
        elif private_id is None and f"spec/{manifest['function_alias']}" in path:
            raw = None
        else:
            raw = file_bytes(root / custody_path(item, manifest['function_alias'], private_id or ''))
        checks.append({'path': path, 'location': location, 'status': file_status(raw, item)})
    statuses = collections.Counter(row['status'] for row in checks)
    return {
        'checked': len(checks),
        'PASS': statuses['PASS'],
        'FAIL': statuses['FAIL'],
        'UNVERIFIABLE': statuses['UNVERIFIABLE'],
        'restricted_custody_pass': sum(
            row['location'] == 'restricted_custody' and row['status'] == 'PASS' for row in checks
        ),
        'exceptions': [row for row in checks if row['status'] != 'PASS'],
    }


def normalize_raw(rows):
    """The documented-in-this-package derivation from raw Notte results to offers.json."""
    out = []
    for src in rows:
        r = dict(src)
        r['country'] = r['country'].upper()
        r['flags'] = list(r['flags']) + ['country_uppercased'] + LABELS
        r['source_labels'] = list(LABELS)
        for field in ('audio_languages', 'subtitle_languages'):
            if r[field] == 'UNKNOWN':
                r[field] = ['UNKNOWN']
        out.append(r)
    return out


def compute_metrics(root):
    run = root / RUN_PATH
    offers = load_json(run / 'offers.json')
    dune = load_json(run / 'call-02-dune.json')['result']
    inception = load_json(run / 'call-03-inception-retry.json')['result']
    rejects = load_json(run / 'rejects.json')
    vlog = load_json(run / 'validation_log.json')
    batch1 = load_json(run / 'batch-01.json')
    batch2 = load_json(run / 'batch-02.json')
    batch3 = load_json(run / 'batch-03.json')
    probe = load_json(run / 'probe-01.json')
    coverage = load_json(run / 'coverage.json')
    ext, lit = dedup(offers, EXT_KEY), dedup(offers, LIT_KEY)
    def counts(rows):
        return {p: n for (_, p), n in collections.Counter(
            (r['country'], r['provider']) for r in rows).items()}
    def one(values):
        v = sorted(set(values))
        return v[0] if len(v) == 1 else v
    norm = sorted(json.dumps(r, sort_keys=True, separators=(',', ':')) for r in normalize_raw(dune + inception))
    actual = sorted(json.dumps(r, sort_keys=True, separators=(',', ':')) for r in offers)
    def unknown_lists(field):
        return sum(1 for r in offers if isinstance(r[field], list) and r[field] == ['UNKNOWN'])
    def observed_range(rows):
        return [min(r['observed_at'] for r in rows), max(r['observed_at'] for r in rows)]
    return {
        'call_02_records': len(dune),
        'call_03_records': len(inception),
        'raw_records': len(dune) + len(inception),
        'offers_json_records': len(offers),
        'rejects': len(rejects),
        'dedup_extended_kept': len(ext),
        'dedup_extended_removed': len(offers) - len(ext),
        'dedup_literal_kept': len(lit),
        'dedup_literal_removed': len(offers) - len(lit),
        'normalizations': len(vlog['normalizations']),
        'normalization_field': one(n['field'] for n in vlog['normalizations']),
        'normalization_from': one(str(n['from']) for n in vlog['normalizations']),
        'normalization_to': one(str(n['to']) for n in vlog['normalizations']),
        'unknown_audio_lists': unknown_lists('audio_languages'),
        'unknown_subtitle_lists': unknown_lists('subtitle_languages'),
        'countries': dict(collections.Counter(r['country'] for r in offers)),
        'object_types': dict(collections.Counter(r['object_type'] for r in offers)),
        'available_true': sum(1 for r in offers if r['available'] is True),
        'monetization': dict(collections.Counter(r['monetization_type'] for r in offers)),
        'near_duplicate_price_groups': len(vlog['near_duplicate_price_groups']),
        'source_label_sets': [list(x) for x in sorted({tuple(r['source_labels']) for r in offers})],
        'provider_counts_full': counts(offers),
        'provider_counts_literal_dedup': counts(lit),
        'offers_equals_normalized_raw': norm == actual,
        'checkpoint_batch_counts': [batch1['offers'], batch2['record_count'], batch3['record_count']],
        'checkpoint_first_failed': batch1['terminal_status'] == 'failed',
        'checkpoint_retry_of': batch3.get('retry_of'),
        'collection_invocations': len([batch1, batch2, batch3]),
        'validation_probe_invocations': probe['probe'],
        'per_title_attempt_index': {'Inception': [batch1['attempt'], batch3['attempt']],
                                    'Dune': [batch2['attempt']]},
        'global_collection_call_index': {'Inception': [entry['attempt'] for entry in coverage['attempt_history']],
                                         'Dune': [batch2['batch']]},
        'observed_at_ranges': [observed_range(dune), observed_range(inception)],
        'has_tmdb_field': any('tmdb' in key.lower() for r in dune + inception for key in r),
    }


def check(name, condition):
    return {'metric': name, 'status': 'PASS' if condition else 'FAIL'}


def compare(expected, actual):
    return [check(name, actual.get(name) == value) for name, value in expected.items()]


UUID = re.compile(rb'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', re.I)
SECRET_PATTERNS = {
    'signed_query': re.compile(
        rb'[?&](?:X-Amz-[\w-]+|X-Goog-[\w-]+|Signature|Policy|Key-Pair-Id)=[^&\s"<>]+', re.I
    ),
    'authorization_value': re.compile(rb'\b(?:Bearer|Basic)[ \t]+[A-Za-z0-9._~+/=-]{16,}', re.I),
    'github_token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'),
    'openai_key': re.compile(rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}'),
    'aws_access_key': re.compile(rb'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'),
    'private_key': re.compile(rb'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----'),
}


def decoded(raw):
    return unquote_to_bytes(raw).replace(rb'\u0026', b'&').replace(rb'\u003d', b'=')


def sensitive_patterns(raw):
    material = decoded(raw)
    return [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(material)]


def sanitization_scan():
    hits = []
    files = sorted(p for p in HERE.rglob('*') if p.is_file())
    for p in files:
        path = p.relative_to(HERE).as_posix().encode()
        raw = p.read_bytes()
        patterns = sensitive_patterns(raw)
        if UUID.search(decoded(raw + b'\n' + path)):
            patterns.append('uuid')
        for name in patterns:
            hits.append({'file': UUID.sub(b'[redacted]', path).decode(), 'pattern': name})
    return {'files_scanned': len(files), 'hits': hits, 'status': 'PASS' if not hits else 'FAIL'}


def artifact_checks(manifest, actual, root):
    """Cross-check publishable summaries against the retained measurements."""
    sdir = HERE / 'sanitized'
    providers = load_json(sdir / 'provider_summary.json')
    att = load_json(sdir / 'attempt_accounting.json')['namespaces']
    collection = att['notte_collection_invocations']
    probe = att['notte_validation_probe_invocations']
    jw = att['internal_justwatch_http_attempts']
    ident = load_json(sdir / 'identity_provenance.json')
    show = load_json(sdir / 'function_show.json')
    original = load_json(sdir / 'function_show_original.json')
    schema = load_json(sdir / 'function_schema.json')
    raw_show = load_json(root / RUN_PATH / 'function_show_redeploy.json')
    declared = manifest['declared_hashes']
    qualified = {'params_hash', 'previous_spec_sha256', 'tool_list_schema_sha256'}
    fields = raw_show['response_format']['$defs']['OfferRecord']['required']
    checks = [
        check('provider summary for 56 observed offers',
              providers['provider_counts_all_56'] == actual['provider_counts_full']
              and providers['counts_sum']['all_56'] == 56),
        check('provider summary for 34 historical rows',
              providers['provider_counts_literal_dedup_34'] == actual['provider_counts_literal_dedup']
              and providers['counts_sum']['literal_dedup_34'] == 34),
        check('collection invocations and validation probes',
              collection['count'] == actual['collection_invocations'] == 3
              and collection['limit'] == 6
              and probe['count'] == actual['validation_probe_invocations'] == 1 and probe['limit'] == 3),
        check('distinct attempt indices',
              all({title: att[name][title] for title in ('Inception', 'Dune')} == actual[name]
                  for name in ('per_title_attempt_index', 'global_collection_call_index'))),
        check('observation timestamps',
              [row['observed_at_utc_range'] for row in collection['items'][1:]] == actual['observed_at_ranges']),
        check('internal HTTP attempts UNKNOWN and static estimate qualified',
              jw['measured_count'] is None and jw['ledger_retained'] is False
              and jw['status'] == 'UNKNOWN'
              and jw['static_estimate']['nominal_actions_per_successful_invocation'] == 4
              and jw['static_estimate']['status'] == 'ESTIMATE_NOT_MEASURED'),
        check('no direct TMDb join field in canary records',
              not actual['has_tmdb_field'] and ident['tmdb_ids_in_canary_records'] is False),
        check('function alias consistent across derivatives',
              all(record['function_alias'] == manifest['function_alias'] for record in (show, original, schema))),
        check('redeploy show metadata',
              all(show[field] == raw_show[field]
                  for field in ('latest_version', 'versions', 'published', 'shared', 'updated_at'))),
        check('schema field projection and source digest',
              schema['output']['required_fields'] == fields
              and schema['provenance']['source_spec_sha256'] == declared['spec_sha256']['value']),
        check('exactly three declared historical digests remain unverifiable',
              {name for name, rec in declared.items() if rec['status'] == 'UNVERIFIABLE'} == qualified),
    ]
    inventory = {row['path']: row for row in manifest['source_inventory']}
    for name, rec in declared.items():
        if name in qualified:
            valid = rec['status'] == 'UNVERIFIABLE' and rec.get('provenance') and rec.get('reason')
        elif name == 'show_json_sha256':
            valid = rec['status'] == 'VERIFIED' and rec['value'] == original['raw_sha256']
        else:
            item = inventory.get(rec.get('reproduced_from'), {})
            valid = rec['status'] == 'VERIFIED' and rec['value'] == item.get('sha256')
        checks.append(check('hash status qualified: ' + name, valid))
    return checks


def additional_inputs(manifest, original_show, tmdb_root):
    show = manifest['declared_hashes']['show_json_sha256']
    checks = [{
        'input': 'pre-redeploy function show',
        'status': file_status(file_bytes(original_show),
                              {'sha256': show['value'], 'bytes': show['reproduced_bytes']}),
    }]
    identity = load_json(HERE / 'sanitized/identity_provenance.json')['external_tmdb_evidence']
    with (REPO / identity['committed_file']).open(encoding='utf-8', newline='') as fh:
        rows = list(csv.DictReader(fh))
    for record in identity['rows']:
        filename = Path(record['retained_payload']).name
        raw = file_bytes(tmdb_root / filename if tmdb_root is not None else None)
        matches = any(
            all(row[field] == str(record[field])
                for field in ('tmdb_id', 'media_type', 'canonical_title', 'year', 'source', 'evidence_sha256'))
            for row in rows
        )
        status = 'UNVERIFIABLE' if raw is None else 'FAIL'
        if raw is not None and matches and sha256_bytes(raw) == record['evidence_sha256']:
            status = 'PASS'
        checks.append({'input': filename, 'status': status})
    return checks


def read_tree(revision):
    entries = []
    for entry in git('ls-tree', '-r', '-z', revision).split(b'\0'):
        if not entry:
            continue
        meta, path = entry.split(b'\t', 1)
        _, kind, oid = meta.split()
        if kind == b'blob':
            entries.append((path, oid))
    batch = io.BytesIO(git('cat-file', '--batch', input=b''.join(oid + b'\n' for _, oid in entries)))
    for path, oid in entries:
        actual_oid, kind, size = batch.readline().split()
        if actual_oid != oid or kind != b'blob':
            raise ValueError('Unexpected Git blob response')
        raw = batch.read(int(size))
        if batch.read(1) != b'\n':
            raise ValueError('Incomplete Git blob response')
        yield path, raw


def scan_tree(manifest, root, private_id, revision):
    if private_id is None:
        report = {'mode': 'git-tree-scan', 'overall': 'UNVERIFIABLE', 'reason': 'Restricted spec manifest unavailable'}
        print(json.dumps(report, indent=2))
        return 2
    items = [item for item in manifest['source_inventory'] if item['location'] == 'restricted_custody']
    original_paths = {custody_path(item, manifest['function_alias'], private_id).encode() for item in items}
    raw_paths = original_paths | {f'{RUN_PATH}/download_redeploy.log'.encode()}
    raw_hashes = {item['sha256'] for item in items}
    raw_hashes.update(item['sha256'] for item in manifest['source_inventory'] if item['location'] == 'local_download_log')
    raw_hashes.add(manifest['declared_hashes']['show_json_sha256']['value'])
    private_uuid = private_id.lower().encode()
    private_stem = private_uuid[:8]
    ids = {private_uuid}
    source_commit = manifest['source_custody']['source_commit']
    for item in items:
        path = custody_path(item, manifest['function_alias'], private_id)
        material = file_bytes(root / path)
        status = file_status(material, item)
        if status == 'PASS' and material != git('show', f'{source_commit}:{path}'):
            status = 'FAIL'
        if status != 'PASS':
            report = {'mode': 'git-tree-scan', 'overall': status, 'path': item['path']}
            print(json.dumps(report, indent=2))
            return 2 if status == 'UNVERIFIABLE' else 1
        ids.update(UUID.findall(decoded(material).lower()))
    base = manifest['review_base_sha']
    changed_paths = set(git('diff', '--name-only', '--no-renames', '-z', base, revision).split(b'\0'))
    ignore = git('check-ignore', '--no-index', '-z', '--stdin',
                 input=b''.join(path + b'\0' for path in sorted(raw_paths)))
    ignored_count = len(ignore.rstrip(b'\0').split(b'\0'))
    path_hits = []
    digest_hits = []
    credential_hits = []
    inherited_credential_hits = []
    identifiers = []
    inherited_identifiers = []
    package_files = 0
    tree_files = 0
    for path, raw in read_tree(revision):
        tree_files += 1
        safe_path = UUID.sub(b'[redacted]', path.replace(private_stem, b'[alias]')).decode()
        package_file = path.startswith(PACKAGE_PATH.encode() + b'/')
        package_files += package_file
        if path in raw_paths or path.startswith(RUN_PATH.encode() + b'/'):
            path_hits.append(safe_path)
        if sha256_bytes(raw) in raw_hashes:
            digest_hits.append(safe_path)
        material = decoded(raw + b'\n' + path).lower()
        uuid_hits = set(UUID.findall(material))
        if package_file and (uuid_hits or private_stem in material):
            identifiers.append(safe_path)
        elif uuid_hits & ids:
            hits = identifiers if path in changed_paths or private_uuid in uuid_hits else inherited_identifiers
            hits.append(safe_path)
        patterns = sensitive_patterns(raw)
        if patterns:
            hits = credential_hits if path in changed_paths else inherited_credential_hits
            hits.append({'path': safe_path, 'patterns': patterns})
    ok = (
        not (path_hits or digest_hits or credential_hits or identifiers)
        and ignored_count == len(raw_paths)
        and len(items) == manifest['source_custody']['files']
    )
    report = {
        'mode': 'git-tree-scan',
        'tree_files_scanned': tree_files,
        'package_files_scanned': package_files,
        'custody_hash_entries_checked': len(items),
        'custody_blobs_byte_compared': len(items),
        'raw_paths_ignored': ignored_count,
        'raw_paths_expected_ignored': len(raw_paths),
        'tracked_raw_paths': path_hits,
        'raw_digest_hits': digest_hits,
        'restricted_identifier_hits': identifiers,
        'inherited_provider_identifier_hits': inherited_identifiers,
        'new_credential_pattern_hits': credential_hits,
        'inherited_credential_pattern_hits': inherited_credential_hits,
        'overall': 'PASS' if ok else 'FAIL',
    }
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


def self_test():
    fx = load_json(HERE / 'fixtures/safe_fixture.json')
    rows = fx['records']
    ext, lit = dedup(rows, EXT_KEY), dedup(rows, LIT_KEY)
    actual = {
        'raw_count': len(rows),
        'dedup_extended_kept': len(ext),
        'dedup_extended_removed': len(rows) - len(ext),
        'dedup_literal_kept': len(lit),
        'dedup_literal_removed': len(rows) - len(lit),
        'unknown_audio_lists': sum(1 for r in rows if r['audio_languages'] == ['UNKNOWN']),
        'unknown_subtitle_lists': sum(1 for r in rows if r['subtitle_languages'] == ['UNKNOWN']),
    }
    checks = compare(fx['expected'], actual)
    sanitization = sanitization_scan()
    ok = all(c['status'] == 'PASS' for c in checks) and sanitization['status'] == 'PASS'
    print(json.dumps({'mode': 'synthetic-self-test', 'checks': checks,
                      'sanitization': sanitization,
                      'overall': 'PASS' if ok else 'FAIL'}, indent=2, ensure_ascii=False))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest', type=Path, default=HERE / 'manifest.json')
    ap.add_argument('--evidence-root', type=Path, help='restricted custody root containing runs/ and spec/')
    ap.add_argument('--download-log', type=Path, help='local redeploy download log outside the evidence tree')
    ap.add_argument('--original-show', type=Path, help='retained pre-redeploy Notte show')
    ap.add_argument('--tmdb-evidence-root', type=Path, help='directory of retained external TMDb evidence')
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument('--self-test', action='store_true', help='verify the synthetic fixture and scan the package')
    modes.add_argument('--scan-tree', metavar='REV', help='scan a Git tree against restricted hashes and identifiers')
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.evidence_root is None:
        ap.error('--evidence-root is required unless --self-test is used')
    manifest = load_json(args.manifest)
    private_id = private_function_id(manifest, args.evidence_root)
    if args.scan_tree:
        return scan_tree(manifest, args.evidence_root, private_id, args.scan_tree)

    files = check_files(manifest, args.evidence_root, args.download_log, private_id)
    checks = []
    if files['restricted_custody_pass'] == manifest['source_custody']['files']:
        actual = compute_metrics(args.evidence_root)
        checks = compare(manifest['expected_metrics'], actual)
        checks.extend([
            check('checkpoint batch counts', actual['checkpoint_batch_counts'] == [0, 27, 29]),
            check('checkpoint first failed', actual['checkpoint_first_failed']),
            check('checkpoint retries batch-01', actual['checkpoint_retry_of'] == 'batch-01'),
        ])
        checks.extend(artifact_checks(manifest, actual, args.evidence_root))

    supporting = additional_inputs(manifest, args.original_show, args.tmdb_evidence_root)
    sanitization = sanitization_scan()
    supporting_status = {row['status'] for row in supporting}
    if (files['FAIL'] or any(c['status'] != 'PASS' for c in checks)
            or 'FAIL' in supporting_status or sanitization['status'] != 'PASS'):
        overall = 'FAIL'
    elif files['UNVERIFIABLE'] or 'UNVERIFIABLE' in supporting_status:
        overall = 'UNVERIFIABLE'
    else:
        overall = 'PASS'
    report = {
        'mode': 'offline-evidence-verification',
        'file_hashes': files,
        'supporting_inputs': supporting,
        'checks': checks,
        'sanitization': sanitization,
        'overall': overall,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return {'PASS': 0, 'FAIL': 1, 'UNVERIFIABLE': 2}[overall]


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        print(json.dumps({'overall': 'FAIL', 'reason': 'Could not read an evidence input or Git tree'}))
        sys.exit(1)
