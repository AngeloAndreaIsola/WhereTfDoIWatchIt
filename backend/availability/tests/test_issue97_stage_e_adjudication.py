"""Adversarial, transport-free Stage E gates and denominator tests."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from unittest import TestCase

from availability.services.issue97_stage_e_adjudication import (
    Binding, CaptureReceipt, Observation, ReviewedPair, Scope, Variant, Window, adjudicate,
    display_rate, display_retrieval, source_report_retrieval, summarize, time_state,
)

SLOT = datetime(2026, 11, 24, 15, 30, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)
STAMP = SLOT + MINUTE


def digest(value):
    return sha256(value.encode()).hexdigest()


def fixture(provider="netflix", kind="subscription", parent=None):
    scope = Scope("movie", 27205, "Inception", 2010, "theatrical", "NL", provider, kind, parent)
    owner = parent or provider
    index = Binding("justwatch", digest("fixture-index-operation"), "fixture-jw", "Inception",
                    f"fixture-package-{provider}-{kind}", digest("fixture-index-manifest"))
    native = Binding(owner, digest(f"fixture-{owner}-document"), f"fixture-title-{owner}",
                     "Inception", f"fixture-benefit-{provider}-{kind}", digest("fixture-native-manifest"))
    time = Window(STAMP, STAMP, timedelta(seconds=1), STAMP)
    pair = ReviewedPair("fixture-row", scope, index, native, SLOT,
                        CaptureReceipt(digest("fixture-index-bytes"), time),
                        CaptureReceipt(digest("fixture-first-party-bytes"), time))
    action = {"subscription": "subscription_action", "addon": "channel_benefit",
              "rent": "rental_action", "buy": "purchase_action",
              "free": "full_feature_free_without_ads", "free_with_ads": "full_feature_free_with_ads"}[kind]
    idx = Observation(scope, index, time, 200, "title", "request_scoped", "fresh", action,
                      "justwatch", digest("fixture-index-bytes"))
    fp = Observation(scope, native, time, 200, "title", "explicit", "fresh", action,
                     owner, digest("fixture-first-party-bytes"))
    return pair, idx, fp


class BindingTests(TestCase):
    def test_incomplete_reviewed_scope_cannot_be_self_confirmed(self):
        pair, idx, fp = fixture()
        for key, value in (("edition", "unknown"), ("tmdb_id", None), ("year", None)):
            scope = replace(pair.scope, **{key: value})
            self.assertEqual(adjudicate(replace(pair, scope=scope), replace(idx, scope=scope),
                                        replace(fp, scope=scope)).outcome, "unsupported")

    def test_same_mismatch_on_both_observations_still_fails_frozen_scope(self):
        pair, idx, fp = fixture()
        for key, value in (("tmdb_id", 1), ("year", 2009), ("canonical_title", ""),
                           ("media_type", "series"), ("edition", "extended"), ("market", "BE"),
                           ("provider", "prime"), ("offer_class", "rent"), ("parent", "prime")):
            wrong = replace(pair.scope, **{key: value})
            with self.subTest(key=key):
                result = adjudicate(pair, replace(idx, scope=wrong), replace(fp, scope=wrong))
                self.assertEqual(result.outcome, "unsupported")
                self.assertEqual(result.advertised, "unsupported")

    def test_each_role_requires_actual_document_native_title_package_and_review(self):
        pair, idx, fp = fixture()
        self.assertEqual(adjudicate(pair, idx, replace(fp, payload_digest=digest("other-document"))).outcome,
                         "unsupported")
        for side, obs in enumerate((idx, fp)):
            for key, value in (("issuer", "prime"), ("document_ref", digest("other-title")),
                               ("native_id", ""), ("native_id", "unrelated"), ("native_title", ""),
                               ("native_title", "another"), ("package_id", "unmapped"),
                               ("manifest_digest", digest("unreviewed"))):
                args = [idx, fp]
                args[side] = replace(obs, binding=replace(obs.binding, **{key: value}))
                with self.subTest(side=side, key=key, value=value):
                    self.assertEqual(adjudicate(pair, *args).outcome, "unsupported")
            for key in ("native_id", "package_id", "document_ref", "manifest_digest"):
                empty = replace(obs.binding, **{key: ""})
                args = [idx, fp]
                args[side] = replace(obs, binding=empty)
                missing = replace(pair, **{("index", "first_party")[side]: empty})
                self.assertEqual(adjudicate(missing, *args).outcome, "unsupported")

    def test_prime_channel_has_distinct_benefit_and_parent(self):
        pair, idx, fp = fixture("max_amazon", "addon", "prime")
        self.assertEqual(adjudicate(pair, idx, fp).outcome, "TP")
        for change in ({"provider": "max"}, {"parent": None}, {"offer_class": "subscription"}):
            with self.subTest(change=change):
                self.assertEqual(adjudicate(pair, idx, replace(fp, scope=replace(fp.scope, **change))).outcome,
                                 "unsupported")
        self.assertEqual(adjudicate(pair, idx, replace(fp, signal="subscription_action")).outcome,
                         "unresolved_positive")

    def test_negative_requires_review_and_cannot_be_inferred_from_an_empty_class(self):
        pair, idx, fp = fixture()
        no = replace(fp, signal="explicit_class_negative")
        self.assertEqual(adjudicate(pair, idx, no).advertised, "needs_recheck")
        self.assertEqual(adjudicate(pair, idx, replace(no, second_review=True)).outcome, "FP")
        for signal in ("", "no_offers", "trial", "viewer_denied", "another_edition"):
            self.assertEqual(adjudicate(pair, idx, replace(fp, signal=signal)).advertised, "unknown")

    def test_free_controls_require_separate_full_feature_classes(self):
        for kind, wrong in (("free", "full_feature_free_with_ads"),
                            ("free_with_ads", "full_feature_free_without_ads")):
            pair, idx, fp = fixture("prime", kind)
            self.assertEqual(adjudicate(pair, idx, fp).outcome, "TP")
            self.assertEqual(adjudicate(pair, idx, replace(fp, signal=wrong)).advertised, "unknown")

    def test_both_roles_reject_unsuccessful_or_non_title_responses(self):
        pair, idx, fp = fixture()
        for status, kind in ((404, "title"), (302, "title"), (403, "title"), (429, "title"),
                             (200, "login_wall"), (200, "redirect"), (200, "denial"),
                             (200, "shell"), (200, "unsupported"), (200, "other_title")):
            for signal in ("subscription_action", "explicit_class_negative"):
                with self.subTest(status=status, kind=kind, signal=signal):
                    changes = dict(status=status, document_kind=kind, signal=signal, second_review=True)
                    self.assertEqual(adjudicate(pair, idx, replace(fp, **changes)).advertised, "unsupported")
                    self.assertEqual(adjudicate(pair, replace(idx, **changes), fp).index, "unsupported")

    def test_conflicting_territories_and_unestablished_context(self):
        pair, idx, fp = fixture()
        for changes in ({"recorded_market": "BE"}, {"market_basis": "language"},
                        {"market_basis": "request_scoped"}):
            self.assertEqual(adjudicate(pair, idx, replace(fp, **changes)).advertised, "unsupported")
        self.assertEqual(adjudicate(pair, replace(idx, recorded_market="BE"), fp).index, "unsupported")

    def test_app_projection_against_native_vs_same_origin_and_same_bytes(self):
        pair, idx, fp = fixture()
        app = replace(idx, producer="application", document_kind="offers")
        def compare(comparator, read=idx.window):
            return adjudicate(pair, app, comparator, index_name="application", app_read=read)
        self.assertEqual(compare(fp).outcome, "TP")
        self.assertEqual(compare(fp, None).outcome, "unknown")
        prior = STAMP - timedelta(seconds=2)
        self.assertEqual(compare(fp, Window(prior, prior, timedelta(0), prior)).outcome, "unknown")
        self.assertEqual(compare(idx).outcome, "same_source_echo")
        self.assertEqual(compare(replace(fp, payload_digest=idx.payload_digest)).outcome, "same_source_echo")
        self.assertEqual(adjudicate(pair, app, fp).outcome, "unsupported")
        self.assertEqual(adjudicate(pair, idx, fp, index_name="application", app_read=idx.window).outcome,
                         "unsupported")


class ClockTests(TestCase):
    def test_boundaries_apply_to_expanded_bounds_and_originals(self):
        def point(t, seconds=0):
            return Window(t, t, timedelta(seconds=seconds), t)
        self.assertEqual(time_state(SLOT, point(SLOT), point(SLOT + 10 * MINUTE)), "eligible")
        self.assertEqual(time_state(SLOT, point(SLOT), point(SLOT + 10 * MINUTE, 1)), "stale")
        self.assertEqual(time_state(SLOT, point(SLOT + 30 * MINUTE)), "stale")
        self.assertEqual(time_state(SLOT, point(SLOT + 30 * MINUTE - timedelta(seconds=5), 5)), "stale")
        self.assertEqual(time_state(SLOT, point(SLOT, 1)), "stale")
        self.assertEqual(time_state(SLOT, point(STAMP, 5)), "eligible")
        for stamp in (STAMP.replace(tzinfo=None), STAMP.astimezone(timezone(timedelta(hours=1))), None):
            for field in ("original", "start", "end"):
                with self.subTest(field=field, stamp=stamp):
                    self.assertEqual(time_state(SLOT, replace(point(STAMP), **{field: stamp})), "unknown")
            self.assertEqual(time_state(stamp, point(STAMP)), "unknown")
        for seconds in (-1, 5.001):
            self.assertEqual(time_state(SLOT, point(STAMP, seconds)), "unknown")
        self.assertEqual(time_state(SLOT, replace(point(STAMP), uncertainty=None)), "unknown")
        self.assertEqual(time_state(SLOT, replace(point(STAMP), end=SLOT)), "unknown")
        self.assertEqual(time_state(SLOT, replace(point(STAMP), original=STAMP + MINUTE)), "unknown")

    def test_fresh_read_never_refreshes_old_underlying_time(self):
        pair, idx, fp = fixture()
        old = replace(idx.window, original=STAMP - timedelta(days=1))
        app = replace(idx, window=old, producer="application", document_kind="offers")
        self.assertEqual(adjudicate(pair, app, fp, index_name="application", app_read=idx.window).outcome,
                         "unsupported")
        pair = replace(pair, index_receipt=replace(pair.index_receipt, window=old))
        result = adjudicate(pair, app, fp, index_name="application", app_read=idx.window)
        self.assertEqual((result.outcome, result.index, app.window.original),
                         ("stale", "stale", STAMP - timedelta(days=1)))


class PriceTests(TestCase):
    def setUp(self):
        self.pair, idx, fp = fixture("prime", "rent")
        self.hd = Variant("HD", "EUR", "gross", "regular", "public", 499)
        self.sd = replace(self.hd, quality="SD", amount_minor=299)
        self.idx = replace(idx, variants=(self.hd, self.sd), variants_complete=True)
        self.fp = replace(fp, variants=(self.sd, self.hd), variants_complete=True)

    def price(self, idx=None, fp=None):
        return adjudicate(self.pair, idx or self.idx, fp or self.fp).price

    def test_equal_multiple_prices_and_per_variant_conflict(self):
        self.assertEqual(self.price(), "agreement")
        self.assertEqual(self.price(fp=replace(self.fp, variants=(self.hd, replace(self.sd, amount_minor=399)))),
                         "conflict")

    def test_reject_subsets_duplicates_and_unknown_or_mismatched_conditions_on_either_side(self):
        variants = [(self.hd,), (self.hd, self.hd), ()]
        for field, value in (("quality", "UHD"), ("quality", ""), ("currency", "USD"),
                             ("promotion", "discount"), ("package_conditions", "members"),
                             ("basis", "net"), ("basis", "unknown"), ("amount_minor", -1),
                             ("amount_minor", None)):
            variants.append((self.hd, replace(self.sd, **{field: value})))
        for v in variants:
            with self.subTest(v=v):
                self.assertEqual(self.price(idx=replace(self.idx, variants=v)), "unknown")
                self.assertEqual(self.price(fp=replace(self.fp, variants=v)), "unknown")
        self.assertEqual(self.price(fp=replace(self.fp, variants_complete=False)), "unknown")

    def test_price_only_for_two_eligible_current_transactional_positives(self):
        for edits in ({"freshness": "last_known"}, {"status": 404}, {"signal": ""},
                      {"signal": "explicit_class_negative", "second_review": True}, {"disputed": True}):
            self.assertEqual(self.price(fp=replace(self.fp, **edits)), "unscored")
        pair, idx, fp = fixture()
        self.assertEqual(adjudicate(pair, replace(idx, variants=(self.hd,), variants_complete=True),
                                    replace(fp, variants=(self.hd,), variants_complete=True)).price, "unscored")


class AccountingTests(TestCase):
    def test_source_report_trace_and_typed_route_must_match_before_scoring(self):
        pair, idx, fp = fixture()
        title = dict(content_type="movie", tmdb_id=27205, title="Inception", year=2010)
        row = dict(country="NL", service="Netflix", offer_type="subscription", reporting_source="justwatch",
                   availability_evidence="source_reported", observed_at=STAMP.isoformat())
        def score(body, status=200, requested=("movie", 27205, "NL")):
            return source_report_retrieval(pair, fp, idx, requested_scope=requested, read=idx.window,
                                           status=status, body=body, service_label="Netflix").state
        def body(rows):
            return {"movie": title, "source_reported_offers": rows}
        self.assertEqual(score(body([row])), "displayed")
        self.assertEqual(score(body([])), "miss")
        self.assertEqual(score({"movie": title, "message": "Movie not tracked."}, 404), "miss")
        self.assertEqual(score({"movie": title}, 404), "unresolved")
        self.assertEqual(score(body([row]), requested=("series", 27205, "NL")), "unresolved")
        for rows in ([row, row], [{}], None, [{**row, "reporting_source": "app"}],
                     [{**row, "observed_at": SLOT.isoformat()}], [{**row, "observed_at": None}],
                     [{**row, "availability_evidence": "stale"}]):
            self.assertEqual(score(body(rows)), "unresolved")

    def test_every_assertion_cell_and_marginal(self):
        pair, idx, fp = fixture()
        signals = ("subscription_action", "explicit_class_negative", "")
        cells = [["TP", "FP", "unresolved_positive"], ["FN", "TN", "unresolved_negative"],
                 ["abstention", "abstention", "unresolved"]]
        rows = []
        for i, left in enumerate(signals):
            for j, right in enumerate(signals):
                scope = replace(pair.scope, tmdb_id=100 + i * 3 + j)
                row = adjudicate(replace(pair, scope=scope, candidate_id=str(scope.tmdb_id)),
                                 replace(idx, scope=scope, signal=left),
                                 replace(fp, scope=scope, signal=right, second_review=True))
                self.assertEqual(row.outcome, cells[i][j])
                rows.append(row)
        counts = summarize(rows)
        self.assertEqual(counts["advertised"], {"yes": 3, "no": 3, "unknown": 3})
        self.assertEqual(counts["agreement"].denominator, 4)
        for key in ("agreement", "precision", "conflict", "conditional_recall",
                    "false_positive_rate", "false_negative_rate"):
            self.assertEqual(counts[key].value, 0.5)
            self.assertEqual(summarize([])[key].value, "UNDEFINED")
        self.assertEqual(counts["full_panel_recall"], "UNDEFINED")
        self.assertEqual(counts["price_agreement"].value, "UNDEFINED")
        with self.assertRaises(ValueError):
            summarize(rows * 2)
        with self.assertRaises(ValueError):
            summarize([rows[0], replace(rows[1], index_name="application")])

    def test_retrieval_is_conditional_on_read_scope_contract_and_independent_positive(self):
        pair, _, fp = fixture()
        def read(p=pair, f=fp, **changes):
            args = dict(surface="offers", read=f.window, typed_scope=("movie", 27205, "NL"),
                        status=200, present=True, contributions=(f.window,))
            return display_retrieval(p, f, **{**args, **changes})
        shown = read()
        miss = read(p=replace(pair, candidate_id="missing"), status=404, present=False, contributions=())
        hidden = read(p=replace(pair, candidate_id="hidden"), in_scope=False)
        self.assertEqual(display_rate([shown, miss, hidden]).value, 0.5)
        rent, _, native = fixture("prime", "rent")
        self.assertEqual(read(p=rent, f=native).state, "excluded")
        for r in (hidden, read(f=replace(fp, disputed=True)), read(status=403), read(present=None),
                  read(surface="last_known_offers"), read(contributions=()), read(status=404),
                  read(typed_scope=("series", 27205, "NL")), read(typed_scope=("movie", 27205, "BE")),
                  read(contributions=(replace(fp.window, original=SLOT - MINUTE),))):
            self.assertEqual(display_rate([r]).value, "UNDEFINED")
        with self.assertRaises(ValueError):
            display_rate([shown, shown])
