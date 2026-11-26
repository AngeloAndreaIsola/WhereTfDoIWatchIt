"""Shared contract fixtures, normalized into the same inputs as live reads."""
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest import TestCase

from tracker.services.entitlement import (
    CoverageRule, OfferFact, OfferKey, Selection, evaluate_entitlement, project_title,
)


CORPUS_PATH = Path('docs/contracts/issue103-entitlement-examples.json')
TEST_FILE = Path(__file__).resolve()
FIXTURES = TEST_FILE.parents[3] / CORPUS_PATH
if not FIXTURES.is_file():
    FIXTURES = TEST_FILE.parents[2] / CORPUS_PATH


def typed_scope(scope):
    if scope.startswith('season:'):
        return {'content_type': 'series', 'season': int(scope.split(':')[1])}
    return {'content_type': scope, 'season': None}


def run_case(corpus, case):
    as_of = datetime.fromisoformat(corpus['asOf'])
    observed = as_of - timedelta(days=1)
    viewer = corpus['viewerDefaults'] | case['viewer']
    selection = Selection(
        viewer['provider'], viewer['billing'], viewer['holding'],
        {None: 'unspecified', 'unmapped': 'unsupported'}.get(viewer['plan'], 'selected'),
        viewer['plan'], {'orbit_via_aster': viewer['channel']},
    )
    rows = []
    for overrides in case['offers']:
        offer = corpus['offerDefaults'] | overrides
        provider, kind = offer['provider'], offer['kind']
        hosted = provider == 'orbit_via_aster'
        key = OfferKey(provider, offer['market'], kind, title_id=corpus['titleId'],
                       source='synthetic', package_id=provider, variant='default',
                       **typed_scope(offer['scope']))
        fact = OfferFact(key, offer['evidence'], observed_at=observed,
                         package_resolved=provider != 'orbit_unknown',
                         host='aster' if hosted else '', channel=provider if hosted else '')
        rules = []
        if offer['rule'] != 'none':
            effect, plan_id = offer['rule'].split(':')
            rule_key = key if offer['ruleScope'] == 'same' else replace(key, **typed_scope(offer['ruleScope']))
            rules = [CoverageRule(rule_key, viewer['billing'], plan_id, value, observed,
                                  as_of + timedelta(days=2), observed,
                                  datetime.fromisoformat(offer['ruleUntil']), 'Synthetic review',
                                  offer['portability'])
                     for value in (('allow', 'deny') if effect == 'conflict' else (effect,))]
        entitlement = evaluate_entitlement(fact, rules, selection, as_of)
        country = offer['market']
        locality = ('local' if country == viewer['current'] else 'vpn' if country in viewer['vpn']
                    else 'out_of_reach' if country else 'unknown')
        rows.append((entitlement, locality, viewer['languageMatch']))
    return rows


class SharedEntitlementFixtures(TestCase):
    def test_corpus(self):
        corpus = json.loads(FIXTURES.read_text())
        self.assertEqual(len(corpus['cases']), 34)
        self.assertEqual(sum(len(case['expect']['rows']) for case in corpus['cases']), 37)
        for case in corpus['cases']:
            with self.subTest(case=case['id']):
                rows = run_case(corpus, case)
                actual = [[row.verdict, row.reason] for row, _, _ in rows]
                self.assertEqual(actual, case['expect']['rows'])
                self.assertEqual(project_title(rows), case['expect']['title'])
                reversed_rows = run_case(corpus, case | {'offers': case['offers'][::-1]})
                self.assertEqual(reversed_rows, rows[::-1])
                self.assertEqual(project_title(reversed_rows), case['expect']['title'])
