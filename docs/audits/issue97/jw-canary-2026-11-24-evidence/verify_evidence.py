#!/usr/bin/env python3
"""Offline, sanitized verifier for the JW-CANARY-v1b evidence package.

No network, no database, no Django, no provider calls. Reads raw evidence only
from an owner-supplied --evidence-root; writes nothing. Output contains only
aggregates, hashes and PASS/FAIL rows -- never raw provider records or headers.
"""
import argparse, collections, hashlib, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
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


def check_files(manifest, root):
    rows = {'checked': 0, 'PASS': 0, 'FAIL': 0, 'MISSING': 0}
    for item in manifest['source_inventory']:
        p = root / item['path']
        rows['checked'] += 1
        if not p.is_file():
            rows['MISSING'] += 1
            continue
        raw = p.read_bytes()
        ok = sha256_bytes(raw) == item['sha256'] and len(raw) == item['bytes']
        rows['PASS' if ok else 'FAIL'] += 1
    return rows


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
    run = root / 'runs/jw-canary-2026-11-24'
    offers = load_json(run / 'offers.json')
    dune = load_json(run / 'call-02-dune.json')['result']
    inception = load_json(run / 'call-03-inception-retry.json')['result']
    rejects = load_json(run / 'rejects.json')
    vlog = load_json(run / 'validation_log.json')
    batch1 = load_json(run / 'batch-01.json')
    batch2 = load_json(run / 'batch-02.json')
    batch3 = load_json(run / 'batch-03.json')
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
    }


def compare(expected, actual):
    checks = []
    for name, want in expected.items():
        got = actual.get(name)
        checks.append({'metric': name, 'status': 'PASS' if got == want else 'FAIL',
                       'expected': want if name != 'source_label_sets' else '[labels]',
                       'actual': got if name != 'source_label_sets' else '[labels]'})
    return checks


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
    ok = all(c['status'] == 'PASS' for c in checks)
    print(json.dumps({'mode': 'synthetic-self-test', 'checks': checks,
                      'overall': 'PASS' if ok else 'FAIL'}, indent=2, ensure_ascii=False))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest', type=Path, default=HERE / 'manifest.json')
    ap.add_argument('--evidence-root', type=Path, help='directory containing runs/ and spec/ from the private canary')
    ap.add_argument('--self-test', action='store_true', help='run synthetic fixture checks only; no raw evidence needed')
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.evidence_root is None:
        ap.error('--evidence-root is required unless --self-test is used')
    manifest = load_json(args.manifest)
    files = check_files(manifest, args.evidence_root)
    actual = compute_metrics(args.evidence_root)
    checks = compare(manifest['expected_metrics'], actual)
    checks.append({'metric': 'checkpoint_batch_counts = [0, 27, 29]',
                   'status': 'PASS' if actual['checkpoint_batch_counts'] == [0, 27, 29] else 'FAIL',
                   'expected': [0, 27, 29], 'actual': actual['checkpoint_batch_counts']})
    checks.append({'metric': 'checkpoint_first_failed', 'status': 'PASS' if actual['checkpoint_first_failed'] else 'FAIL',
                   'expected': True, 'actual': actual['checkpoint_first_failed']})
    checks.append({'metric': 'checkpoint_retry_of batch-01', 'status': 'PASS' if actual['checkpoint_retry_of'] == 'batch-01' else 'FAIL',
                   'expected': 'batch-01', 'actual': actual['checkpoint_retry_of']})
    ok = (files['FAIL'] == 0 and files['MISSING'] == 0 and all(c['status'] == 'PASS' for c in checks))
    print(json.dumps({'mode': 'offline-evidence-verification', 'evidence_root': '[redacted path]',
                      'file_hashes': files, 'checks': checks,
                      'overall': 'PASS' if ok else 'FAIL'}, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
