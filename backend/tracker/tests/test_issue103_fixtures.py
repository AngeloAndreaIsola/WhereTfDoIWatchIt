"""Shared contract fixtures, normalized into the same inputs as live reads."""
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest import TestCase

from tracker.services.entitlement import (
    CoverageRule, OfferFact, OfferKey, Selection, evaluate_entitlement, project_title,
)


TEST_FILE = Path(__file__).resolve()


def read_fixtures(path):
    for root in (TEST_FILE.parents[3], TEST_FILE.parents[2]):
        candidate = root / path
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise FileNotFoundError(path)


def typed_scope(scope):
    if scope.startswith('season:'):
        return {'content_type': 'series', 'season': int(scope.split(':')[1])}
    return {'content_type': scope, 'season': None}


def run_case(corpus, case):
    as_of = datetime.fromisoformat(corpus['asOf'])
    observed = as_of - timedelta(days=1)
    viewer = corpus['viewerDefaults'] | case['viewer']
    selection = None if viewer.get('noSelection') else Selection(
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
        def timestamp(name, default=None):
            value = offer.get(name, default)
            return datetime.fromisoformat(value) if isinstance(value, str) else value

        fact = OfferFact(key, offer['evidence'], observed_at=timestamp('observedAt', observed),
                         package_resolved=provider != 'orbit_unknown',
                         host='aster' if hosted else '', channel=provider if hosted else '',
                         source_supported=offer.get('sourceSupported', True),
                         public_conditions_met=offer.get('publicConditionsMet', True),
                         conflicted=offer.get('conflicted', False),
                         starts_at=timestamp('startsAt'), ends_at=timestamp('endsAt'))
        rules = []
        if offer['rule'] != 'none':
            effect, plan_id = offer['rule'].split(':')
            rule_key = key if offer['ruleScope'] == 'same' else replace(key, **typed_scope(offer['ruleScope']))
            effects = ('allow', 'deny') if effect == 'conflict' else (effect,)
            portability = offer['portability']
            territories = ('allowed', 'denied') if portability == 'conflicting' else (portability,)
            rules = [
                CoverageRule(
                    rule_key, viewer['billing'], plan_id, value, timestamp('ruleObservedAt', observed),
                    timestamp('ruleFreshUntil', as_of + timedelta(days=2)),
                    timestamp('ruleStartsAt', observed), timestamp('ruleUntil'), 'Synthetic review',
                    territory,
                )
                for value in effects for territory in territories
            ]
        entitlement = evaluate_entitlement(fact, rules, selection, as_of)
        country = offer['market']
        locality = ('local' if country == viewer['current'] else 'vpn' if country in viewer['vpn']
                    else 'out_of_reach' if country else 'unknown')
        rows.append((entitlement, locality, viewer['languageMatch']))
    return rows


class SharedEntitlementFixtures(TestCase):
    def test_corpus(self):
        corpus = read_fixtures('docs/contracts/issue103-entitlement-examples.json')
        self.assert_corpus(corpus, 34, 37)

    def test_adversarial_corpus(self):
        corpus = read_fixtures('docs/tests/issue103-adversarial-cases.json')
        self.assert_corpus(corpus | corpus['conventions'] | {'cases': corpus['evaluatorCases']}, 42, 48)

    def assert_corpus(self, corpus, case_count, row_count):
        self.assertEqual(len(corpus['cases']), case_count)
        self.assertEqual(sum(len(case['expect']['rows']) for case in corpus['cases']), row_count)
        for case in corpus['cases']:
            with self.subTest(case=case['id']):
                rows = run_case(corpus, case)
                actual = [[row.verdict, row.reason] for row, _, _ in rows]
                self.assertEqual(actual, case['expect']['rows'])
                self.assertEqual(project_title(rows), case['expect']['title'])
                reversed_rows = run_case(corpus, case | {'offers': case['offers'][::-1]})
                self.assertEqual(reversed_rows, rows[::-1])
                self.assertEqual(project_title(reversed_rows), case['expect']['title'])
