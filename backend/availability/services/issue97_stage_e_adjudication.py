"""Offline Stage E adjudication of sealed, sanitized reviews.

The caller supplies a separately reviewed panel, including the exact document
and action bindings for each role. An observation cannot supply its own mapping.
There is no transport, scheduler, database access or live manifest here.
"""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import re


def _known(value):
    return isinstance(value, str) and value.strip().casefold() not in {"", "unknown", "unspecified", "unmapped"}


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


@dataclass(frozen=True)
class Scope:
    media_type: str
    tmdb_id: int
    canonical_title: str
    year: int
    edition: str
    market: str
    provider: str
    offer_class: str
    parent: str | None = None


@dataclass(frozen=True)
class Binding:
    # The issuer of the actual contributing document, including for app projections.
    issuer: str
    document_ref: str  # Digest of effective document locator / reviewed operation
    native_id: str
    native_title: str  # May be a separately reviewed alias.
    package_id: str
    manifest_digest: str

    def complete(self):
        return (all(_known(v) for v in (self.issuer, self.native_id, self.native_title, self.package_id))
                and all(_digest(v) for v in (self.document_ref, self.manifest_digest)))


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    uncertainty: timedelta
    original: datetime  # Event or original underlying assertion, never import/read time.


def time_state(slot, *windows):
    def utc(t):
        return isinstance(t, datetime) and t.utcoffset() == timedelta(0)

    if not utc(slot) or not windows:
        return "unknown"
    starts, ends = [], []
    for w in windows:
        if (w is None or not all(utc(t) for t in (w.start, w.end, w.original))
                or not isinstance(w.uncertainty, timedelta)
                or not timedelta() <= w.uncertainty <= timedelta(seconds=5)
                or max(w.start, w.original) > w.end):
            return "unknown"
        starts.append(min(w.start, w.original) - w.uncertainty)
        ends.append(w.end + w.uncertainty)
    if min(starts) < slot or max(ends) >= slot + timedelta(minutes=30):
        return "stale"
    if max(ends) - min(starts) > timedelta(minutes=10):
        return "stale"
    return "eligible"


def _read_time_state(slot, native, read, contributions):
    state = time_state(slot, native, read, *contributions)
    if state == "eligible" and any(w.end > read.end for w in contributions):
        return "unknown"
    return state


@dataclass(frozen=True)
class CaptureReceipt:
    """Reviewed digest and original bounds from the sealed evidence inventory."""
    payload_digest: str
    window: Window


@dataclass(frozen=True)
class ReviewedDisplayTrace:
    """Binding and receipt of the original capture traced to the saved app row."""

    binding: Binding
    receipt: CaptureReceipt


@dataclass(frozen=True)
class ConflictReview:
    index_name: str
    availability: str | None = None  # Confirmed FP or FN
    price_conflict: bool = False


@dataclass(frozen=True)
class ReviewedPair:
    candidate_id: str
    scope: Scope
    index: Binding
    first_party: Binding
    slot: datetime
    index_receipt: CaptureReceipt
    native_receipt: CaptureReceipt
    # Second review of this sealed comparison, separate from reviewing either extraction.
    conflict_review: ConflictReview | None = None
    # Exact child row independently reviewed as covering this series-level scope.
    title_display_mapping: tuple[int, int | None] | None = None
    display_trace: ReviewedDisplayTrace | None = None


@dataclass(frozen=True)
class Variant:
    quality: str
    currency: str
    basis: str
    promotion: str
    package_conditions: str
    amount_minor: int

    def key(self):
        return self.quality, self.currency, self.basis, self.promotion, self.package_conditions


@dataclass(frozen=True)
class Observation:
    scope: Scope
    binding: Binding
    window: Window
    status: int
    document_kind: str  # title, login_wall, redirect, denial, shell, unsupported, ...
    market_basis: str
    freshness: str
    # Reviewed title-specific action, reviewed class-wide negative, or no assertion.
    signal: str
    producer: str
    payload_digest: str  # Sealed original behind this assertion (also for app echoes).
    recorded_market: str | None = None
    second_review: bool = False
    disputed: bool = False
    variants_complete: bool = False
    variants: tuple[Variant, ...] = ()


# These are the document owners for the frozen Stage E panel.
_OWNERS = {"netflix": "netflix", "prime": "prime", "rakuten_tv": "rakuten_tv",
           "max_amazon": "prime"}

# A sealed review classifies the title-specific action against the evidence protocol.
_ACTION = {"subscription": "subscription_action", "addon": "channel_benefit",
           "rent": "rental_action", "buy": "purchase_action",
           "free": "full_feature_free_without_ads",
           "free_with_ads": "full_feature_free_with_ads"}


def _valid_scope(scope):
    return (isinstance(scope, Scope) and scope.provider in _OWNERS and scope.market == "NL"
            and scope.media_type in {"movie", "series"}
            and type(scope.tmdb_id) is int and scope.tmdb_id > 0
            and type(scope.year) is int and scope.year > 0
            and _known(scope.canonical_title) and _known(scope.edition)
            and scope.offer_class in _ACTION
            and (scope.offer_class == "addon") == (scope.provider == "max_amazon")
            and scope.parent == ("prime" if scope.provider == "max_amazon" else None))


def _verdict(pair, obs, *, native, index_name="justwatch"):
    expected = pair.first_party if native else pair.index
    receipt = pair.native_receipt if native else pair.index_receipt
    scope = pair.scope
    if obs.scope != scope or not _valid_scope(scope):
        return "unsupported"
    owner = _OWNERS[scope.provider]
    if (not expected or obs.binding != expected or not expected.complete()
            or obs.producer != (owner if native else index_name)
            or not _digest(obs.payload_digest)
            or not receipt or receipt.payload_digest != obs.payload_digest or receipt.window != obs.window
            or (native and expected.issuer != owner)
            or (not native and index_name == "justwatch" and expected.issuer != "justwatch")
            or expected.issuer in {"application", "unknown", "untraceable"}):
        return "unsupported"
    if (obs.status != 200
            or obs.recorded_market not in {None, scope.market}
            or obs.market_basis not in ({"explicit", "reviewed_storefront"} if native
                                        else {"explicit", "request_scoped"})):
        return "unsupported"
    if not native and index_name == "application":
        if obs.document_kind == "last_known_offers":
            return "stale"
        if obs.document_kind == "source_reported_offers":
            return "unknown"  # A displayed source report makes no current app assertion.
        expected_surface = "purchase_offers" if scope.offer_class in {"rent", "buy"} else "offers"
        if obs.document_kind != expected_surface:
            return "unsupported"
    elif obs.document_kind != "title":
        return "unsupported"
    if obs.freshness != "fresh":
        return "stale" if obs.freshness == "last_known" else "unknown"
    state = time_state(pair.slot, obs.window)
    if state != "eligible":
        return state
    if obs.disputed:
        return "needs_recheck"
    if obs.signal == "explicit_class_negative":
        return "no" if obs.second_review is True else "needs_recheck"
    if obs.signal == _ACTION[scope.offer_class]:
        return "yes"
    return "unknown"


def _price_map(obs):
    if not obs.variants_complete or not obs.variants:
        return None
    amounts = {}
    for v in obs.variants:
        key = v.key()
        if (v.currency != "EUR" or v.basis not in {"gross", "net"}
                or not all(_known(term) for term in key)
                or type(v.amount_minor) is not int or v.amount_minor < 0 or key in amounts):
            return None
        amounts[key] = v.amount_minor
    return amounts


def _prices(scope, outcome, index, first_party, review):
    if scope.offer_class not in {"rent", "buy"} or outcome != "TP":
        return "unscored"
    a, b = _price_map(index), _price_map(first_party)
    if a is None or b is None or a.keys() != b.keys():
        return "unknown"
    if a == b:
        return "agreement"
    return "conflict" if review and review.price_conflict is True else "needs_recheck"


@dataclass(frozen=True)
class Decision:
    candidate_id: str
    index_name: str
    scope: Scope
    index: str
    advertised: str
    eligibility: str
    outcome: str
    price: str


_CELLS = {("yes", "yes"): "TP", ("no", "yes"): "FN",
          ("yes", "no"): "FP", ("no", "no"): "TN"}


def _same_source(left, left_digest, right, right_digest):
    return bool((left and right and (left.issuer == right.issuer or left.document_ref == right.document_ref))
                or (left_digest and left_digest == right_digest))


def _review_for_index(pair, index_name):
    review = pair.conflict_review
    return review if review and review.index_name == index_name else None


def adjudicate(pair, index, first_party, *, index_name="justwatch", app_read=None):
    """For app comparisons preserve the contributing index capture and add its local read."""
    idx = _verdict(pair, index, native=False, index_name=index_name)
    fp = _verdict(pair, first_party, native=True)
    if index_name == "application":
        state = _read_time_state(pair.slot, first_party.window, app_read, (index.window,))
    elif index_name != "justwatch" or app_read is not None:
        raise ValueError("use a named index and an application read only for application")
    else:
        state = time_state(pair.slot, index.window, first_party.window)
    cell = _CELLS.get((idx, fp))
    review = _review_for_index(pair, index_name)
    if _same_source(index.binding, index.payload_digest, first_party.binding, first_party.payload_digest):
        state = "same_source_echo"
    elif "unsupported" in (idx, fp):
        state = "unsupported"
    elif "stale" in (idx, fp):
        state = "stale"
    elif "needs_recheck" in (idx, fp):
        state = "needs_recheck"
    elif state == "eligible" and cell in {"FP", "FN"} and (not review or review.availability != cell):
        state = "needs_recheck"
    if state != "eligible":
        outcome = state
    elif cell:
        outcome = cell
    elif idx in {"yes", "no"}:
        outcome = "unresolved_positive" if idx == "yes" else "unresolved_negative"
    elif fp in {"yes", "no"}:
        outcome = "abstention"
    else:
        outcome = "unresolved"
    return Decision(pair.candidate_id, index_name, pair.scope, idx, fp, state, outcome,
                    _prices(pair.scope, outcome, index, first_party, review))


@dataclass(frozen=True)
class Rate:
    numerator: int
    denominator: int

    @property
    def value(self):
        return self.numerator / self.denominator if self.denominator else "UNDEFINED"


def _candidate_key(scope):
    return scope.media_type, scope.tmdb_id, scope.market, scope.provider, scope.offer_class


def summarize(decisions, *, expected_manifest=None):
    """Match submitted decisions against independently supplied (candidate_id, Scope) records."""
    rows = list(decisions)
    submitted = {d.candidate_id for d in rows}
    if (len({d.index_name for d in rows}) > 1 or len(submitted) != len(rows)
            or any(not _known(key) for key in submitted)):
        raise ValueError("mixed indices or duplicate candidate")
    planned = None
    completeness = "unknown"
    missing, extra, mismatched = [], [], []
    if expected_manifest is not None:
        manifest = list(expected_manifest)
        expected = dict(manifest)
        if (not manifest or len(expected) != len(manifest)
                or any(not _known(key) or not _valid_scope(scope) for key, scope in manifest)
                or len({_candidate_key(scope) for scope in expected.values()}) != len(expected)):
            raise ValueError("invalid or duplicate frozen candidate")
        planned = len(expected)
        missing = sorted(expected.keys() - submitted)
        extra = sorted(submitted - expected.keys())
        mismatched = sorted(d.candidate_id for d in rows if d.candidate_id in expected
                            and (not _valid_scope(d.scope) or d.scope != expected[d.candidate_id]))
        matched_ids = (submitted & expected.keys()) - set(mismatched)
        rows = [d for d in rows if d.candidate_id in matched_ids]
        completeness = "incomplete" if missing or extra or mismatched else "complete"
    if len({_candidate_key(d.scope) for d in rows}) != len(rows):
        raise ValueError("invalid or duplicate candidate")
    cells = Counter(d.outcome for d in rows)
    prices = Counter(d.price for d in rows)
    tp, fp, fn, tn = (cells[k] for k in ("TP", "FP", "FN", "TN"))
    n = tp + fp + fn + tn
    recall = Rate(tp, tp + fn)
    precision = Rate(tp, tp + fp)
    eligible_panel = completeness == "complete" and all(d.eligibility == "eligible" for d in rows)
    return {
        "planned": planned, "submitted": len(submitted), "matched": len(rows) if planned else None,
        "panel_completeness": completeness, "missing": missing, "extra": extra,
        "scope_mismatches": mismatched, "outcomes": cells,
        "advertised": Counter(d.advertised for d in rows),
        "index": Counter(d.index for d in rows),
        "eligibility": Counter(d.eligibility for d in rows),
        "agreement": Rate(tp + tn, n), "conflict": Rate(fp + fn, n),
        "precision": precision, "conditional_precision": precision, "conditional_recall": recall,
        "full_panel_precision": (precision.value if eligible_panel and all(
            d.index == "no" or d.outcome in {"TP", "FP"} for d in rows) else "UNDEFINED"),
        "full_panel_recall": (recall.value if eligible_panel and all(
            d.advertised == "no" or d.outcome in {"TP", "FN"} for d in rows) else "UNDEFINED"),
        "false_positive_rate": Rate(fp, fp + tn), "false_negative_rate": Rate(fn, fn + tp),
        "prices": prices, "price_agreement": Rate(prices["agreement"],
                                                 prices["agreement"] + prices["conflict"]),
    }


@dataclass(frozen=True)
class DisplayDecision:
    candidate_id: str
    surface: str
    state: str
    projection: str = "unscored"  # Fidelity to the sealed source report alone.


def display_retrieval(pair, first_party, *, surface, read, typed_scope, status, present,
                      contributions=(), in_scope=True):
    """Score a reviewed typed local read separately from assertion accuracy.

    typed_scope is the independently resolved route's (media type, TMDb ID, market).
    present is True/False only for a completed and classified read on the named surface.
    contributions are original source windows of the matched display row.
    """
    scope = pair.scope
    if surface not in {"offers", "source_reported_offers", "last_known_offers", "purchase_offers"}:
        raise ValueError("unknown application surface")
    transactional = scope.offer_class in {"rent", "buy"}
    if (not in_scope or surface == "last_known_offers" or (surface == "offers" and transactional)
            or (surface == "purchase_offers" and not transactional)):
        state = "excluded"
    elif _verdict(pair, first_party, native=True) != "yes":
        state = "unscored"
    elif not (pair.index and pair.index.complete() and pair.index_receipt
              and _digest(pair.index_receipt.payload_digest)
              and pair.index.issuer not in {"application", "untraceable"}):
        state = "unscored"
    elif _same_source(pair.index, pair.index_receipt.payload_digest,
                      first_party.binding, first_party.payload_digest):
        state = "same_source_echo"
    elif (typed_scope != (scope.media_type, scope.tmdb_id, scope.market)
          or status not in {200, 404} or type(present) is not bool
          or (present and (contributions != (pair.index_receipt.window,) or status != 200))):
        state = "unresolved"
    elif _read_time_state(pair.slot, first_party.window, read, contributions) != "eligible":
        state = "unscored"
    else:
        state = "displayed" if present else "miss"
    return DisplayDecision(pair.candidate_id, surface, state)


def _title_display_row(pair, row):
    if "season_number" not in row or "episode_number" not in row:
        return False
    season, episode = row["season_number"], row["episode_number"]
    if season is None and episode is None:
        return True
    return (pair.scope.media_type == "series" and type(season) is int and season >= 0
            and (episode is None or (type(episode) is int and episode >= 0))
            and (season, episode) == pair.title_display_mapping)


def _source_report_present(pair, contributor, status, body, service_label):
    scope = pair.scope
    title = body.get(scope.media_type) if isinstance(body, dict) else None
    if (not isinstance(title, dict) or type(title.get("tmdb_id")) is not int
            or title["tmdb_id"] != scope.tmdb_id or not _known(service_label)):
        return None
    if status == 404:
        if (body.get("message") == f"{scope.media_type.title()} not tracked."
                and title.get("content_type", scope.media_type) == scope.media_type
                and body.get("offers", []) == [] and body.get("source_reported_offers", []) == []):
            return False
        return None
    if (status != 200 or title.get("content_type") != scope.media_type
            or title.get("title") != scope.canonical_title or title.get("year") != scope.year
            or body.get("selected_season") is not None):
        return None

    rows = body.get("source_reported_offers")
    if not isinstance(rows, list):
        return None
    matches = []
    for row in rows:
        if (not isinstance(row, dict) or row.get("country") != scope.market
                or not all(_known(row.get(k)) for k in ("service", "offer_type"))):
            return None
        if (row["service"], row["offer_type"]) == (service_label, scope.offer_class):
            matches.append(row)
    if not matches:
        return False
    matches = [row for row in matches if _title_display_row(pair, row)]
    if len(matches) != 1:
        return None

    row = matches[0]
    try:
        original = datetime.fromisoformat(row["observed_at"])
    except (KeyError, ValueError, TypeError):
        return None
    if (original.utcoffset() != timedelta(0) or original != contributor.window.original
            or row.get("reporting_source") != contributor.binding.issuer
            or row.get("availability_evidence") != "source_reported"):
        return None
    return True


def source_report_retrieval(pair, native, index, *, requested_scope, read, status, body, service_label,
                           report_source=None):
    """Read the typed Prime detail contract against reviewed source evidence.

    service_label is the catalogue display name resolved from the reviewed
    provider slug. When the index does not affirm the class, report_source is
    the separate original observation linked to the saved row in display_trace.
    The typed HTTP response alone cannot establish that link.
    """
    source = _verdict(pair, index, native=False)
    comparable = index.freshness == "fresh" and source in {"yes", "no", "unknown"}
    if source == "no":
        review = _review_for_index(pair, "justwatch")
        comparable = review is not None and review.availability == "FN"

    contributor, display_pair = index, pair
    trace = pair.display_trace
    if (comparable and source != "yes" and trace and report_source
            and report_source.payload_digest != index.payload_digest
            and not _same_source(index.binding, index.payload_digest, native.binding, native.payload_digest)):
        traced_pair = replace(pair, index=trace.binding, index_receipt=trace.receipt)
        if (_verdict(traced_pair, report_source, native=False) == "yes"
                and _read_time_state(pair.slot, native.window, read,
                                     (index.window, report_source.window)) == "eligible"):
            contributor, display_pair = report_source, traced_pair

    present = _source_report_present(pair, contributor, status, body, service_label) if comparable else None
    if present and source != "yes" and display_pair is pair:
        present = None
    verified = display_retrieval(display_pair if present else pair, native,
                                 surface="source_reported_offers", read=read,
                                 typed_scope=requested_scope, status=status, present=present,
                                 contributions=((contributor if present else index).window,))
    projection = "unscored"
    if (source == "yes" and present is not None
            and requested_scope == (pair.scope.media_type, pair.scope.tmdb_id, pair.scope.market)
            and _read_time_state(pair.slot, index.window, read, (index.window,)) == "eligible"):
        projection = "displayed" if present else "miss"
    return replace(verified, projection=projection)


def display_rate(rows):
    rows = list(rows)
    if (len({r.surface for r in rows}) > 1
            or len({r.candidate_id for r in rows}) != len(rows)):
        raise ValueError("mixed surfaces or duplicate candidate")
    counts = Counter(r.state for r in rows)
    return Rate(counts["displayed"], counts["displayed"] + counts["miss"])
