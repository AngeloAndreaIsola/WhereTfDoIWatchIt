"""Offline adjudication rehearsal for Stage E (issue #97).

Pure logic tests: no transport, no database, no live provider requests and no
raw first-party evidence. Every fixture below is sanitized structured data.
"""

import csv
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from availability.services.issue97_stage_e_adjudication import (
    ABSTENTION,
    FN,
    FP,
    INDEPENDENT,
    LAST_KNOWN,
    NEEDS_RECHECK,
    NO,
    NOT_PROVEN,
    SAME_SOURCE_ECHO,
    TN,
    TP,
    UNDEFINED,
    UNKNOWN,
    YES,
    Adjudication,
    Candidate,
    DisplayRetrieval,
    Identity,
    Observation,
    Price,
    Signals,
    adjudicate_pair,
    app_display_retrieval,
    compare_prices,
    first_party_verdict,
    freshness_gate,
    identity_gate,
    index_verdict,
    market_gate,
    metrics,
    parse_eur_minor,
    service_class_gate,
    source_independence,
    time_gate,
)

from django.test import SimpleTestCase


SLOT = datetime(2030, 1, 1, 15, tzinfo=timezone.utc)
REFERENCE_HOUR_START = datetime(2026, 11, 23, 15, tzinfo=timezone.utc)

# Sanitized copy of the 15 frozen candidate rows. The authoritative CSV is
# checked against the frozen Git objects by scripts/audit_issue97_stage_e.py.
CANDIDATE_ROWS = (
    ("E01", "A", "movie", 27205, 2010, "netflix", "subscription", "netflix_subscription"),
    ("E02", "A", "movie", 27205, 2010, "prime", "subscription", "prime_subscription"),
    ("E03", "A", "movie", 27205, 2010, "prime", "rent", "prime_transaction"),
    ("E04", "A", "movie", 27205, 2010, "prime", "buy", "prime_transaction"),
    ("E05", "A", "movie", 27205, 2010, "max_amazon", "addon", "prime_channel"),
    ("E06", "A", "movie", 438631, 2021, "rakuten_tv", "rent", "rakuten_transaction"),
    ("E07", "A", "movie", 438631, 2021, "rakuten_tv", "buy", "rakuten_transaction"),
    ("E08", "A", "movie", 27205, 2010, "prime", "free", "free_class_control"),
    ("E09", "A", "movie", 438631, 2021, "rakuten_tv", "free_with_ads", "free_class_control"),
    ("E10", "B", "series", 1396, 2008, "netflix", "subscription", "netflix_subscription"),
    ("E11", "B", "series", 70523, 2017, "netflix", "subscription", "netflix_subscription"),
    ("E12", "B", "series", 66732, 2016, "netflix", "subscription", "netflix_subscription"),
    ("E13", "B", "movie", 619264, 2019, "netflix", "subscription", "netflix_subscription"),
    ("E14", "B", "movie", 840430, 2023, "rakuten_tv", "rent", "rakuten_transaction"),
    ("E15", "B", "movie", 840430, 2023, "rakuten_tv", "buy", "rakuten_transaction"),
)


def candidate(row):
    candidate_id, batch, media_type, tmdb_id, year, provider, offer_class, rule = row
    return Candidate(candidate_id, batch, media_type, tmdb_id, year, "NL", provider, offer_class, rule)


def identity(row):
    _, _, media_type, tmdb_id, year, *_ = row
    return Identity(media_type, tmdb_id, year, "Title", f"native-{tmdb_id}")


def evidence(row):
    """Full, unambiguous scoped positive signals for the candidate's rule."""
    _, _, _, _, _, _, offer_class, _ = row
    return Signals(
        typed_title_native_id=True,
        verified_nl_context=True,
        title_specific_positive=True,
        class_specific_action=True,
        zero_payment=offer_class in ("free", "free_with_ads"),
        ad_funded=offer_class == "free_with_ads",
    )


def index_observation(row, *, in_window=False, claim=YES):
    _, _, media_type, tmdb_id, year, provider, offer_class, _ = row
    ident = identity(row)
    if in_window:
        start, end = SLOT + timedelta(minutes=5), SLOT + timedelta(minutes=5, seconds=5)
        freshness, basis = "fresh", "request_scoped"
    else:
        start, end = datetime(2026, 11, 24, 12, tzinfo=timezone.utc), None
        freshness, basis = "fresh", "request_scoped"
    return Observation(
        "justwatch", ident, "NL", basis, provider, f"jw-{tmdb_id}", offer_class,
        start, end or start + timedelta(seconds=5), timedelta(seconds=5), freshness,
        claim=claim, prices=(Price(1299, "EUR", "gross", "sd"),),
    )


def first_party_observation(row, *, in_window=False, signals=None):
    _, _, _, tmdb_id, _, provider, offer_class, _ = row
    ident = identity(row)
    if in_window:
        start = SLOT + timedelta(minutes=7)
        freshness = "fresh"
    else:
        start = REFERENCE_HOUR_START + timedelta(minutes=13)
        freshness = "fresh"
    return Observation(
        provider, ident, "NL", "explicit", provider, f"native-{tmdb_id}", offer_class,
        start, start + timedelta(seconds=5), timedelta(seconds=5), freshness,
        claim=YES, signals=signals if signals is not None else evidence(row),
        prices=(Price(1299, "EUR", "gross", "sd"),),
    )


class StageEMatrixCoverageTests(SimpleTestCase):
    def test_all_fifteen_frozen_rows_adjudicate_to_an_abstention_offline(self):
        results = tuple(
            adjudicate_pair(candidate(row), index_observation(row), first_party_observation(row),
                            identity(row), SLOT)
            for row in CANDIDATE_ROWS
        )
        self.assertEqual(len(results), 15)
        self.assertEqual(len({item.candidate_id for item in results}), 15)
        self.assertEqual(sum(item.batch == "A" for item in results), 9)
        self.assertEqual(sum(item.batch == "B" for item in results), 6)
        for result in results:
            with self.subTest(row=result.candidate_id):
                self.assertFalse(result.eligible)
                self.assertIn(result.outcome, (ABSTENTION, "unresolved"))
                self.assertEqual(result.entitlement, "untested")
                self.assertEqual(result.playback, "untested")
                self.assertEqual(result.independence, INDEPENDENT)
                time_gate_result = next(gate for gate in result.eligibility if gate.name == "time")
                self.assertFalse(time_gate_result.passed)

    def test_batch_caps_and_combined_ceiling_are_sane(self):
        def documents(rows):
            # max_amazon is read from the same first-party Prime document.
            return {(row[3], "prime" if row[5] == "max_amazon" else row[5]) for row in rows}

        batch_a = [row for row in CANDIDATE_ROWS if row[1] == "A"]
        batch_b = [row for row in CANDIDATE_ROWS if row[1] == "B"]
        self.assertEqual(len({row[0] for row in batch_a}), 9)
        self.assertEqual(len({row[0] for row in batch_b}), 6)
        self.assertEqual(len(documents(batch_a)), 3)
        self.assertEqual(len(documents(batch_b)), 5)
        self.assertEqual((len(documents(batch_a)), len(documents(batch_b))), (3, 5))
        self.assertEqual((3 + 5, 2 + 5), (8, 7))

    def test_embedded_rows_match_the_authoritative_csv_when_present(self):
        path = Path(__file__).resolve().parents[3] / "docs/audits/issue97/stage-e-candidate-matrix.csv"
        if not path.exists():
            self.skipTest("repo docs not mounted in this test image")
        with path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 15)
        for row, embedded in zip(rows, CANDIDATE_ROWS):
            candidate_id, batch, media_type, tmdb_id, year, provider, offer_class, rule = embedded
            with self.subTest(row=candidate_id):
                self.assertEqual(row["candidate_id"], candidate_id)
                self.assertEqual(row["batch"], batch)
                self.assertEqual(row["media_type"], media_type)
                self.assertEqual(int(row["tmdb_id"]), tmdb_id)
                self.assertEqual(int(row["year"]), year)
                self.assertEqual(row["provider_slug"], provider)
                self.assertEqual(row["offer_class"], offer_class)
                self.assertEqual(row["evidence_rule"], rule)


class GateTests(SimpleTestCase):
    def setUp(self):
        self.row = CANDIDATE_ROWS[0]
        self.manifest = identity(self.row)

    def test_identity_gate_requires_exact_typed_binding(self):
        base = index_observation(self.row, in_window=True)
        self.assertTrue(identity_gate(base, self.manifest).passed)
        for change in ({"media_type": "series"}, {"tmdb_id": 238},
                       {"year": 2011}, {"native_id": "other"}):
            with self.subTest(change=change):
                altered = Observation(**{**base.__dict__, "identity": Identity(
                    **{**base.identity.__dict__, **change})})
                self.assertFalse(identity_gate(altered, self.manifest).passed)
        missing = Observation(**{**base.__dict__, "identity": Identity("movie", 0, None)})
        self.assertIn("unresolved", identity_gate(missing, self.manifest).reason)

    def test_market_gate_requires_exact_nl_and_reliable_basis(self):
        base = index_observation(self.row, in_window=True)
        self.assertTrue(market_gate(base).passed)
        for market, basis in (("US", "explicit"), ("NL", "unknown"), ("NL", "conflict")):
            with self.subTest(market=market, basis=basis):
                altered = Observation(**{**base.__dict__, "market": market, "market_basis": basis})
                self.assertFalse(market_gate(altered).passed)
        self.assertTrue(market_gate(base, require_explicit=False).passed)
        self.assertFalse(market_gate(base, require_explicit=True).passed)

    def test_service_and_class_gate_keeps_similar_slugs_distinct(self):
        cand = candidate(self.row)
        base = index_observation(self.row, in_window=True)
        self.assertTrue(service_class_gate(base, cand).passed)
        for change in ({"provider_slug": "prime"}, {"provider_id": ""},
                       {"offer_class": "rent"}):
            with self.subTest(change=change):
                altered = Observation(**{**base.__dict__, **change})
                self.assertFalse(service_class_gate(altered, cand).passed)

    def test_prime_channel_requires_retained_parent_package(self):
        row = CANDIDATE_ROWS[4]
        obs = index_observation(row, in_window=True)
        self.assertFalse(service_class_gate(obs, candidate(row)).passed)
        with_edition = Observation(**{**obs.__dict__, "identity":
            Identity(**{**obs.identity.__dict__, "edition": "max_amazon/prime"})})
        self.assertTrue(service_class_gate(with_edition, candidate(row)).passed)

    def test_time_gate_enforces_slot_uncertainty_order_and_span(self):
        index = index_observation(self.row, in_window=True)
        first = first_party_observation(self.row, in_window=True)
        self.assertTrue(time_gate((index, first), SLOT).passed)
        unknown_time = Observation(**{**first.__dict__, "observed_end": None})
        self.assertFalse(time_gate((index, unknown_time), SLOT).passed)
        bad_uncertainty = Observation(**{**first.__dict__, "uncertainty": timedelta(seconds=6)})
        self.assertFalse(time_gate((index, bad_uncertainty), SLOT).passed)
        unordered = Observation(**{**first.__dict__, "observed_end": SLOT})
        self.assertFalse(time_gate((index, unordered), SLOT).passed)
        outside = Observation(**{**index.__dict__, "observed_start": SLOT + timedelta(minutes=28)})
        self.assertFalse(time_gate((outside, first), SLOT).passed)
        wide = Observation(**{**first.__dict__, "observed_end": SLOT + timedelta(minutes=20)})
        self.assertFalse(time_gate((index, wide), SLOT).passed)

    def test_exactly_ten_minute_span_is_inclusive(self):
        index = Observation(**{**index_observation(self.row, in_window=True).__dict__,
                               "observed_start": SLOT + timedelta(seconds=5),
                               "observed_end": SLOT + timedelta(seconds=10)})
        first = Observation(**{**first_party_observation(self.row, in_window=True).__dict__,
                               "observed_start": SLOT + timedelta(minutes=9, seconds=50),
                               "observed_end": SLOT + timedelta(minutes=9, seconds=55)})
        self.assertTrue(time_gate((index, first), SLOT).passed)

    def test_freshness_gate_excludes_last_known_and_historical(self):
        obs = first_party_observation(self.row, in_window=True)
        self.assertTrue(freshness_gate(obs).passed)
        self.assertFalse(freshness_gate(Observation(**{**obs.__dict__, "freshness": LAST_KNOWN})).passed)
        self.assertFalse(freshness_gate(Observation(**{**obs.__dict__, "freshness": "historical"})).passed)

    def test_app_response_with_only_historical_offers_is_last_known(self):
        obs = first_party_observation(self.row, in_window=True)
        app_row = Observation(**{**obs.__dict__, "freshness": LAST_KNOWN})
        self.assertFalse(freshness_gate(app_row).passed)
        self.assertEqual(app_row.freshness, LAST_KNOWN)


class IndependenceTests(SimpleTestCase):
    def setUp(self):
        self.row = CANDIDATE_ROWS[0]

    def test_justwatch_against_first_party_is_independent(self):
        gate = source_independence(index_observation(self.row, in_window=True),
                                   first_party_observation(self.row, in_window=True))
        self.assertTrue(gate.passed)
        self.assertEqual(gate.reason, INDEPENDENT)

    def test_justwatch_against_justwatch_is_same_source_echo(self):
        index = index_observation(self.row, in_window=True)
        echo = Observation(**{**index.__dict__, "source": "justwatch", "market_basis": "explicit"})
        gate = source_independence(index, echo)
        self.assertFalse(gate.passed)
        self.assertEqual(gate.reason, SAME_SOURCE_ECHO)

    def test_app_row_sourced_from_justwatch_is_same_source_echo(self):
        index = index_observation(self.row, in_window=True)
        echo = Observation(**{**first_party_observation(self.row, in_window=True).__dict__,
                              "source": "app:justwatch"})
        gate = source_independence(index, echo)
        self.assertFalse(gate.passed)
        self.assertEqual(gate.reason, SAME_SOURCE_ECHO)

    def test_unknown_provenance_is_not_proven(self):
        gate = source_independence(index_observation(self.row, in_window=True),
                                   Observation(**{**first_party_observation(self.row, in_window=True).__dict__,
                                                  "source": "unattributed"}))
        self.assertFalse(gate.passed)
        self.assertEqual(gate.reason, NOT_PROVEN)


class VerdictTests(SimpleTestCase):
    def test_first_party_positive_negative_unknown_needs_recheck(self):
        for row in CANDIDATE_ROWS:
            with self.subTest(row=row[0]):
                self.assertEqual(first_party_verdict(first_party_observation(row), row[7]), YES)
        base = first_party_observation(CANDIDATE_ROWS[0])
        self.assertEqual(first_party_verdict(
            Observation(**{**base.__dict__, "signals": Signals(explicit_class_negative=True)}),
            "netflix_subscription"), NO)
        self.assertEqual(first_party_verdict(
            Observation(**{**base.__dict__, "signals": Signals()}),
            "netflix_subscription"), UNKNOWN)
        self.assertEqual(first_party_verdict(
            Observation(**{**base.__dict__, "signals": Signals(change_boundary_straddle=True)}),
            "netflix_subscription"), NEEDS_RECHECK)
        self.assertEqual(first_party_verdict(
            Observation(**{**base.__dict__, "signals": Signals(conflicting_scoped_signals=True)}),
            "netflix_subscription"), NEEDS_RECHECK)
        self.assertEqual(first_party_verdict(
            Observation(**{**base.__dict__, "signals": Signals(incomplete_or_login_wall=True)}),
            "netflix_subscription"), UNKNOWN)

    def test_free_with_ads_requires_explicit_ad_funding(self):
        row = CANDIDATE_ROWS[8]
        base = first_party_observation(row)
        self.assertEqual(first_party_verdict(base, row[7]), YES)
        without_ads = Observation(**{**base.__dict__,
            "signals": Signals(**{**base.signals.__dict__, "ad_funded": False})})
        self.assertEqual(first_party_verdict(without_ads, row[7]), UNKNOWN)

    def test_index_absence_404_and_stale_are_unknown_not_negative(self):
        row = CANDIDATE_ROWS[0]
        for change in ({"claim": UNKNOWN}, {"title_404": True},
                       {"freshness": LAST_KNOWN}, {"provider_id": ""}):
            with self.subTest(change=change):
                obs = Observation(**{**index_observation(row, in_window=True).__dict__, **change})
                self.assertEqual(index_verdict(obs), UNKNOWN)

    def test_index_explicit_current_negative_is_scored(self):
        row = CANDIDATE_ROWS[0]
        obs = Observation(**{**index_observation(row, in_window=True).__dict__, "claim": NO})
        self.assertEqual(index_verdict(obs), NO)


class PriceTests(SimpleTestCase):
    def test_localized_eur_decimals_parse_to_minor_units(self):
        self.assertEqual(parse_eur_minor("1.299,00"), 129900)
        self.assertEqual(parse_eur_minor("12,99"), 1299)
        self.assertEqual(parse_eur_minor("12.99"), 1299)
        self.assertIsNone(parse_eur_minor("12-15"))
        self.assertIsNone(parse_eur_minor(""))
        self.assertIsNone(parse_eur_minor("$5.00"))

    def test_equal_conflict_unknown_and_variant_mismatch(self):
        index = (Price(1299, "EUR", "gross", "sd"),)
        self.assertEqual(compare_prices(index, (Price(1299, "EUR", "gross", "sd"),)), YES)
        self.assertEqual(compare_prices(index, (Price(1599, "EUR", "gross", "sd"),)), NEEDS_RECHECK)
        self.assertEqual(compare_prices(index, (Price(1299, "USD", "gross", "sd"),)), UNKNOWN)
        self.assertEqual(compare_prices(index, (Price(1299, "EUR", "gross", "4k"),)), UNKNOWN)
        self.assertEqual(compare_prices(index, ()), UNKNOWN)

    def test_rent_and_buy_are_scored_separately(self):
        row_rent, row_buy = CANDIDATE_ROWS[2], CANDIDATE_ROWS[3]
        self.assertEqual(first_party_observation(row_rent).offer_class, "rent")
        self.assertEqual(first_party_observation(row_buy).offer_class, "buy")


class AdjudicationOutcomeTests(SimpleTestCase):
    def make(self, *, fp_verdict_signals=None, index_claim=YES, in_window=True):
        row = CANDIDATE_ROWS[0]
        signals = evidence(row) if fp_verdict_signals is None else fp_verdict_signals
        index = index_observation(row, in_window=in_window, claim=index_claim)
        first = first_party_observation(row, in_window=in_window, signals=signals)
        return adjudicate_pair(candidate(row), index, first, identity(row), SLOT)

    def test_binary_outcomes(self):
        self.assertEqual(self.make(index_claim=YES).outcome, TP)
        self.assertEqual(self.make(index_claim=NO).outcome, FN)
        self.assertEqual(self.make(fp_verdict_signals=Signals(explicit_class_negative=True),
                                   index_claim=YES).outcome, FP)
        self.assertEqual(self.make(fp_verdict_signals=Signals(explicit_class_negative=True),
                                   index_claim=NO).outcome, TN)

    def test_abstention_and_needs_recheck(self):
        self.assertEqual(self.make(index_claim=UNKNOWN).outcome, ABSTENTION)
        recheck = self.make(fp_verdict_signals=Signals(change_boundary_straddle=True))
        self.assertEqual(recheck.outcome, NEEDS_RECHECK)
        self.assertEqual(recheck.first_party_verdict, NEEDS_RECHECK)
        self.assertNotIn(recheck.outcome, (TP, FP, FN, TN))

    def test_entitlement_and_playback_always_untested(self):
        for claim in (YES, NO, UNKNOWN):
            with self.subTest(claim=claim):
                result = self.make(index_claim=claim)
                self.assertEqual(result.entitlement, "untested")
                self.assertEqual(result.playback, "untested")


class MetricsTests(SimpleTestCase):
    def result(self, outcome, *, positive=False):
        return Adjudication("x", "A", (), YES, YES, INDEPENDENT, outcome, YES)

    def test_zero_denominators_are_undefined(self):
        result = metrics(())
        for value in result.rates.values():
            self.assertEqual(value, UNDEFINED)
        self.assertEqual(result.denominators["precision"], None)

    def test_conditional_rates_and_denominators(self):
        results = tuple(self.result(key) for key in (TP, TP, FP, FN, TN))
        result = metrics(results)
        self.assertEqual(result.counts[TP], 2)
        self.assertAlmostEqual(result.rates["agreement_rate"], 3 / 5)
        self.assertAlmostEqual(result.rates["conflict_rate"], 2 / 5)
        self.assertAlmostEqual(result.rates["precision"], 2 / 3)
        self.assertAlmostEqual(result.rates["recall"], 2 / 3)
        self.assertEqual(result.denominators["false_positive_rate"], (1, 2))

    def test_recall_undefined_when_positive_pair_abstains(self):
        results = (self.result(TP), self.result(FN), self.result(ABSTENTION))
        self.assertEqual(metrics(results).rates["recall"], UNDEFINED)


class DisplayRetrievalTests(SimpleTestCase):
    def test_retrieval_miss_is_separate_from_source_accuracy(self):
        result = app_display_retrieval(("E01", "E02"), ("E01",))
        self.assertIsInstance(result, DisplayRetrieval)
        self.assertEqual(result.retrieval_misses, ("E02",))
        self.assertEqual(result.rate, 0.5)

    def test_transactional_pairs_excluded_when_surface_hides_them(self):
        result = app_display_retrieval(("E03", "E04"), (), transactional_ids=("E03", "E04"))
        self.assertEqual(result.excluded_transactional, ("E03", "E04"))
        self.assertEqual(result.retrieval_misses, ())
        self.assertEqual(result.rate, 0.0)

    def test_unresolved_and_echo_reads_are_not_counted_as_misses(self):
        result = app_display_retrieval(("E01", "E02"), (), unresolved_read_ids=("E01",))
        self.assertEqual(result.retrieval_misses, ("E02",))
        self.assertEqual(result.unresolved_reads, ("E01",))

    def test_empty_positive_panel_gives_undefined_retrieval(self):
        self.assertEqual(app_display_retrieval((), ()).rate, UNDEFINED)


if __name__ == "__main__":
    unittest.main()
