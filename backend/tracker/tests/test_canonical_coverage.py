from django.test import SimpleTestCase
from tracker.services.canonical_coverage import batch_coverage


class CanonicalCoverageTests(SimpleTestCase):
    def coverage(self, rows, previous=(1,)):
        return batch_coverage(rows, previously_evaluated_ids=previous, inventory_count=10,
                              previously_associated_count=1, requests=4, cache_hits=1,
                              independent_validation={'confirmed': 1})

    def test_held_and_unmatched_count_as_evaluated_not_associated(self):
        rows = [{'record_id': pk, 'status': status, 'provider': 'hydrahd', 'content_type': 'movie',
                 'tmdb_id': 90 if status == 'VERIFIED' else None,
                 'metrics': {'exact_candidates': int(status == 'VERIFIED'), 'rejected_candidates': 2}}
                for pk, status in [(2, 'VERIFIED'), (3, 'AMBIGUOUS'), (4, 'UNMATCHED')]]
        report = self.coverage(rows)
        self.assertEqual(report['newly_evaluated'], 3)
        self.assertEqual(report['verified_associations'], 1)
        self.assertEqual(report['remaining_unevaluated_inventory'], 6)
        self.assertEqual(report['remaining_unassociated_inventory'], 8)
        self.assertEqual(report['matching_yield'], 1 / 3)
        self.assertEqual(report['rejected_candidates'], 6)
        self.assertEqual(report['by_provider_and_content_type'][0]['ambiguous_candidates'], 1)

    def test_rejects_repeated_evaluations_and_incomplete_outcomes(self):
        row = {'record_id': 2, 'status': 'UNMATCHED', 'provider': 'hydrahd', 'content_type': 'movie'}
        for rows, previous in [([row, row], (1,)), ([row], (2,)),
                               ([{**row, 'status': 'BUDGET_EXHAUSTED'}], (1,))]:
            with self.subTest(rows=rows, previous=previous), self.assertRaises(ValueError):
                self.coverage(rows, previous)

    def test_explicit_rollout_hold_is_evaluated_without_becoming_a_success(self):
        result = self.coverage([{'record_id': 2, 'status': 'HELD', 'provider': 'hydrahd',
                                 'content_type': 'movie', 'tmdb_id': 90}])
        self.assertEqual(result['newly_evaluated'], 1)
        self.assertEqual(result['held_from_rollout'], 1)
        self.assertEqual(result['verified_associations'], 0)
        self.assertEqual(result['remaining_unassociated_inventory'], 9)
