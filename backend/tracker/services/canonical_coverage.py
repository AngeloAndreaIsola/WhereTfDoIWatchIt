"""Coverage comes from completed journals, including every held source record."""
from collections import Counter, defaultdict


def batch_coverage(rows, *, previously_evaluated_ids, inventory_count, previously_associated_count,
                   requests, cache_hits, independent_validation):
    previous = set(previously_evaluated_ids)
    ids = [row['record_id'] for row in rows]
    if len(ids) != len(set(ids)) or previous.intersection(ids):
        raise ValueError('Coverage batches must be distinct from all prior evaluated records')
    if any(row['status'] not in ('VERIFIED', 'AMBIGUOUS', 'UNMATCHED', 'CONFLICTING', 'HELD') for row in rows):
        raise ValueError('Only completed outcomes count as evaluated; interrupted records remain pending')
    if len(previous) + len(ids) > inventory_count:
        raise ValueError('Evaluated inventory exceeds discovery count')

    def summarize(group):
        status = Counter(row['status'] for row in group)
        return {'newly_evaluated': len(group),
                'bootstrap_tmdb_requests': sum(r['metrics']['tmdb_requests'] for r in group)
                    if all('tmdb_requests' in r.get('metrics', {}) for r in group) else None,
                'bootstrap_cache_hits': sum(r['metrics']['cache_hits'] for r in group)
                    if all('cache_hits' in r.get('metrics', {}) for r in group) else None,
                'exact_candidates': sum(row.get('metrics', {}).get('exact_candidates', 0) for row in group),
                'verified_associations': status['VERIFIED'], 'rejected_candidates': sum(
                    row.get('metrics', {}).get('rejected_candidates', 0) for row in group),
                'ambiguous_candidates': status['AMBIGUOUS'], 'unmatched_records': status['UNMATCHED'],
                'conflicting_records': status['CONFLICTING'], 'held_from_rollout': status['HELD'], 'unique_canonical_titles': len({
                    (r['tmdb_id'], r['content_type']) for r in group if r['status'] == 'VERIFIED'}),
                'matching_yield': status['VERIFIED'] / len(group) if group else 0}

    groups = defaultdict(list)
    for row in rows:
        groups[(row['provider'], row['content_type'] or 'unresolved')].append(row)
    result = summarize(rows)
    result.update(previously_evaluated=len(previous), tmdb_requests=requests, cache_hits=cache_hits,
                  independent_validation_results=independent_validation,
                  remaining_unevaluated_inventory=inventory_count - len(previous) - len(rows),
                  remaining_unassociated_inventory=inventory_count - previously_associated_count - result['verified_associations'],
                  by_provider_and_content_type=[{'provider': provider, 'content_type': media, **summarize(group)}
                      for (provider, media), group in sorted(groups.items())])
    return result
