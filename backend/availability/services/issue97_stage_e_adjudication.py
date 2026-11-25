"""Offline Stage E adjudication of sealed, sanitized reviews.

The caller supplies a separately reviewed panel, including the exact document
and action bindings for each role. An observation cannot supply its own mapping.
There is no transport, scheduler, database access or live manifest here.
"""

from collections import Counter
from dataclasses import dataclass
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
class ReviewedPair:
    candidate_id: str
    scope: Scope
    index: Binding
    first_party: Binding
    slot: datetime
    index_receipt: CaptureReceipt
    native_receipt: CaptureReceipt


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


def _verdict(pair, obs, *, native, index_name="justwatch"):
    expected = pair.first_party if native else pair.index
    receipt = pair.native_receipt if native else pair.index_receipt
    scope = pair.scope
    owner = _OWNERS.get(scope.provider)
    if (obs.scope != scope or not owner or scope.market != "NL"
            or scope.media_type not in {"movie", "series"}
            or type(scope.tmdb_id) is not int or scope.tmdb_id <= 0
            or type(scope.year) is not int or scope.year <= 0
            or not _known(scope.canonical_title) or not _known(scope.edition)
            or scope.offer_class not in _ACTION
            or (scope.offer_class == "addon") != (scope.provider == "max_amazon")
            or scope.parent != ("prime" if scope.provider == "max_amazon" else None)):
        return "unsupported"
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
        return "no" if not native or obs.second_review else "needs_recheck"
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


def _prices(scope, outcome, index, first_party):
    if scope.offer_class not in {"rent", "buy"} or outcome != "TP":
        return "unscored"
    a, b = _price_map(index), _price_map(first_party)
    if a is None or b is None or a.keys() != b.keys():
        return "unknown"
    return "agreement" if a == b else "conflict"


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
    if ((index.binding and first_party.binding and
         (index.binding.issuer == first_party.binding.issuer
          or index.binding.document_ref == first_party.binding.document_ref))
            or (index.payload_digest and index.payload_digest == first_party.payload_digest)):
        state = "same_source_echo"
    elif "unsupported" in (idx, fp):
        state = "unsupported"
    elif "stale" in (idx, fp):
        state = "stale"
    elif "needs_recheck" in (idx, fp):
        state = "needs_recheck"
    if state != "eligible":
        outcome = state
    elif (idx, fp) in _CELLS:
        outcome = _CELLS[idx, fp]
    elif idx in {"yes", "no"}:
        outcome = "unresolved_positive" if idx == "yes" else "unresolved_negative"
    elif fp in {"yes", "no"}:
        outcome = "abstention"
    else:
        outcome = "unresolved"
    return Decision(pair.candidate_id, index_name, pair.scope, idx, fp, state, outcome,
                    _prices(pair.scope, outcome, index, first_party))


@dataclass(frozen=True)
class Rate:
    numerator: int
    denominator: int

    @property
    def value(self):
        return self.numerator / self.denominator if self.denominator else "UNDEFINED"


def summarize(decisions):
    """One named index, one decision per frozen pair. Keep marginal verdicts visible."""
    rows = list(decisions)
    if (len({d.index_name for d in rows}) > 1
            or len({d.candidate_id for d in rows}) != len(rows)
            or len({(d.scope.media_type, d.scope.tmdb_id, d.scope.market,
                     d.scope.provider, d.scope.offer_class) for d in rows}) != len(rows)):
        raise ValueError("mixed indices or duplicate candidate")
    cells = Counter(d.outcome for d in rows)
    prices = Counter(d.price for d in rows)
    tp, fp, fn, tn = (cells[k] for k in ("TP", "FP", "FN", "TN"))
    n = tp + fp + fn + tn
    recall = Rate(tp, tp + fn)
    return {
        "planned": len(rows), "outcomes": cells,
        "advertised": Counter(d.advertised for d in rows),
        "index": Counter(d.index for d in rows),
        "eligibility": Counter(d.eligibility for d in rows),
        "agreement": Rate(tp + tn, n), "conflict": Rate(fp + fn, n),
        "precision": Rate(tp, tp + fp), "conditional_recall": recall,
        "full_panel_recall": ("UNDEFINED" if any(
            d.advertised == "yes" and d.outcome not in {"TP", "FN"} for d in rows
        ) else recall.value),
        "false_positive_rate": Rate(fp, fp + tn), "false_negative_rate": Rate(fn, fn + tp),
        "prices": prices, "price_agreement": Rate(prices["agreement"],
                                                 prices["agreement"] + prices["conflict"]),
    }


@dataclass(frozen=True)
class DisplayDecision:
    candidate_id: str
    surface: str
    state: str


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
    elif (typed_scope != (scope.media_type, scope.tmdb_id, scope.market)
          or status not in {200, 404} or type(present) is not bool
          or (present and (not contributions or status != 200))):
        state = "unresolved"
    elif _read_time_state(pair.slot, first_party.window, read, contributions) != "eligible":
        state = "unscored"
    else:
        state = "displayed" if present else "miss"
    return DisplayDecision(pair.candidate_id, surface, state)


def _source_report_present(pair, native, index, status, body, service_label):
    scope = pair.scope
    title = body.get(scope.media_type) if isinstance(body, dict) else None
    if not isinstance(title, dict) or title.get("tmdb_id") != scope.tmdb_id:
        return None
    if status == 404 and body.get("message") == f"{scope.media_type.title()} not tracked.":
        return False
    if (status != 200 or title.get("content_type") != scope.media_type
            or title.get("title") != scope.canonical_title or title.get("year") != scope.year):
        return None

    rows = body.get("source_reported_offers")
    if not service_label or not isinstance(rows, list):
        return None
    matches = []
    for row in rows:
        if not isinstance(row, dict) or not all(row.get(k) for k in ("country", "service", "offer_type")):
            return None
        if (row["country"], row["service"], row["offer_type"]) == (scope.market, service_label, scope.offer_class):
            matches.append(row)
    if not matches:
        return False
    if (len(matches) != 1 or _verdict(pair, index, native=False) != "yes"
            or index.payload_digest == native.payload_digest):
        return None

    row = matches[0]
    try:
        original = datetime.fromisoformat(row["observed_at"])
    except (KeyError, ValueError, TypeError):
        return None
    if (original.utcoffset() != timedelta(0) or original != index.window.original
            or row.get("reporting_source") != index.binding.issuer
            or row.get("availability_evidence") != "source_reported"):
        return None
    return True


def source_report_retrieval(pair, native, index, *, requested_scope, read, status, body, service_label):
    """Read the typed Prime detail contract against the sealed source observation.

    service_label is the catalogue display name resolved from the reviewed
    provider slug. The response does not carry the native document ID.
    """
    present = _source_report_present(pair, native, index, status, body, service_label)
    return display_retrieval(pair, native, surface="source_reported_offers", read=read,
                             typed_scope=requested_scope, status=status, present=present,
                             contributions=(index.window,) if present else ())


def display_rate(rows):
    rows = list(rows)
    if (len({r.surface for r in rows}) > 1
            or len({r.candidate_id for r in rows}) != len(rows)):
        raise ValueError("mixed surfaces or duplicate candidate")
    counts = Counter(r.state for r in rows)
    return Rate(counts["displayed"], counts["displayed"] + counts["miss"])
