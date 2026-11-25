"""Offline Stage E adjudication for issue #97.

This module is pure, deterministic and transport-free. It consumes *sanitized,
structured* observations -- never raw provider bytes, headers, credentials or
signed links -- and applies the gates and denominator rules frozen in
``docs/audits/issue97/stage-e-evidence-protocol.md``.

Nothing here performs I/O, writes a database or issues a provider request. It
exists so the adjudication rules can be rehearsed offline against fixtures
before any exact-scope authorization is considered.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation


YES = "yes"
NO = "no"
UNKNOWN = "unknown"
NEEDS_RECHECK = "needs_recheck"

TP = "TP"
FP = "FP"
FN = "FN"
TN = "TN"
UNRESOLVED_POSITIVE = "unresolved_positive"
UNRESOLVED_NEGATIVE = "unresolved_negative"
ABSTENTION = "abstention"
UNRESOLVED = "unresolved"

INDEPENDENT = "independent"
SAME_SOURCE_ECHO = "same_source_echo"
NOT_PROVEN = "not_proven"

FRESH = "fresh"
LAST_KNOWN = "last_known"

UNTESTED = "untested"

UNDEFINED = "UNDEFINED"

SLOT_LENGTH = timedelta(minutes=30)
MAX_SPAN = timedelta(minutes=10)
MAX_UNCERTAINTY = timedelta(seconds=5)

POSITIVE_RULES = frozenset(
    {
        "netflix_subscription",
        "prime_subscription",
        "prime_transaction",
        "prime_channel",
        "rakuten_transaction",
        "free_class_control",
    }
)


@dataclass(frozen=True)
class Identity:
    media_type: str
    tmdb_id: int
    year: int | None
    canonical_title: str = ""
    native_id: str = ""
    edition: str = ""


@dataclass(frozen=True)
class Price:
    amount_minor: int | None
    currency: str
    basis: str = ""
    variant_id: str = ""
    comparable: bool = True


@dataclass(frozen=True)
class Signals:
    """Structured, scoped first-party signals extracted offline from a document."""

    typed_title_native_id: bool = False
    verified_nl_context: bool = False
    title_specific_positive: bool = False
    class_specific_action: bool = False
    zero_payment: bool = False
    ad_funded: bool = False
    explicit_class_negative: bool = False
    change_boundary_straddle: bool = False
    conflicting_scoped_signals: bool = False
    incomplete_or_login_wall: bool = False


@dataclass(frozen=True)
class Observation:
    source: str
    identity: Identity
    market: str
    market_basis: str
    provider_slug: str
    provider_id: str
    offer_class: str
    observed_start: datetime | None
    observed_end: datetime | None
    uncertainty: timedelta | None
    freshness: str
    claim: str = UNKNOWN
    signals: Signals = field(default_factory=Signals)
    prices: tuple[Price, ...] = ()
    title_404: bool = False


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    batch: str
    media_type: str
    tmdb_id: int
    year: int
    market: str
    provider_slug: str
    offer_class: str
    evidence_rule: str


@dataclass(frozen=True)
class Gate:
    name: str
    passed: bool
    reason: str = ""


@dataclass(frozen=True)
class Adjudication:
    candidate_id: str
    batch: str
    eligibility: tuple[Gate, ...]
    first_party_verdict: str
    index_verdict: str
    independence: str
    outcome: str
    price_verdict: str
    entitlement: str = "untested"
    playback: str = "untested"

    @property
    def eligible(self) -> bool:
        return all(gate.passed for gate in self.eligibility)


@dataclass(frozen=True)
class Metrics:
    counts: dict[str, int]
    rates: dict[str, object]
    denominators: dict[str, tuple[int, int] | None]


def _naive_check(observation: Observation) -> None:
    if not isinstance(observation, Observation):
        raise TypeError("expected an Observation")


def identity_gate(observation: Observation, manifest: Identity) -> Gate:
    """Typed-title binding: exact (media_type, tmdb_id), year and canonical title.

    A native-id binding must be present when the observation itself does not
    carry a typed id. Duplicate, conflicting or unresolved identities never pass.
    """
    _naive_check(observation)
    own = observation.identity
    if not own.tmdb_id or (manifest.year is None and own.year is None):
        return Gate("identity", False, "unresolved identity")
    if own.media_type != manifest.media_type or own.tmdb_id != manifest.tmdb_id:
        return Gate("identity", False, "typed id mismatch")
    if manifest.year is not None and own.year != manifest.year:
        return Gate("identity", False, "year mismatch")
    if own.canonical_title and manifest.canonical_title and own.canonical_title != manifest.canonical_title:
        return Gate("identity", False, "title mismatch")
    if own.native_id and manifest.native_id and own.native_id != manifest.native_id:
        return Gate("identity", False, "native id mismatch")
    return Gate("identity", True)


def market_gate(observation: Observation, *, require_explicit: bool = False) -> Gate:
    """Exact NL territory; request_scoped is admitted only for the reviewed index."""
    if observation.market != "NL":
        return Gate("market", False, "non-NL territory")
    if observation.market_basis in ("unknown", "conflict", ""):
        return Gate("market", False, "unestablished territory")
    if require_explicit and observation.market_basis != "explicit":
        return Gate("market", False, "first-party market must be explicit")
    if observation.market_basis not in ("explicit", "request_scoped"):
        return Gate("market", False, "unsupported territory basis")
    return Gate("market", True)


def service_class_gate(observation: Observation, candidate: Candidate) -> Gate:
    """Exact canonical slug plus a provenance-bound provider id and offer class."""
    if observation.provider_slug != candidate.provider_slug:
        return Gate("service_class", False, "provider slug mismatch")
    if not observation.provider_id:
        return Gate("service_class", False, "missing provider-bound id")
    if observation.offer_class != candidate.offer_class:
        return Gate("service_class", False, "offer class mismatch")
    if candidate.evidence_rule == "prime_channel" and not observation.identity.edition:
        return Gate("service_class", False, "channel parent/package not retained")
    return Gate("service_class", True)


def time_gate(observations: tuple[Observation, ...], slot_start: datetime) -> Gate:
    """Half-open 30-minute slot, <=5s uncertainty and <=10-minute expanded span."""
    slot_end = slot_start + SLOT_LENGTH
    starts: list[datetime] = []
    ends: list[datetime] = []
    for observation in observations:
        if observation.observed_start is None or observation.observed_end is None:
            return Gate("time", False, "unknown observation time")
        if observation.uncertainty is None or observation.uncertainty > MAX_UNCERTAINTY:
            return Gate("time", False, "clock uncertainty")
        if observation.observed_end < observation.observed_start:
            return Gate("time", False, "unordered timestamps")
        outer_start = observation.observed_start - observation.uncertainty
        outer_end = observation.observed_end + observation.uncertainty
        if not (slot_start <= outer_start and outer_end <= slot_end):
            return Gate("time", False, "outside title slot")
        starts.append(outer_start)
        ends.append(outer_end)
    if max(ends) - min(starts) > MAX_SPAN:
        return Gate("time", False, "combined span exceeds ten minutes")
    return Gate("time", True)


def freshness_gate(observation: Observation) -> Gate:
    """Only a fresh observation may support a current-claim comparison."""
    if observation.freshness not in (FRESH, LAST_KNOWN):
        return Gate("freshness", False, "unknown freshness")
    if observation.freshness == LAST_KNOWN:
        return Gate("freshness", False, "last-known evidence only")
    return Gate("freshness", True)


_FIRST_PARTY_FAMILIES = frozenset({"netflix", "prime", "rakuten", "max", "first-party"})


def _source_family(source: str) -> str:
    """Normalize a sanitized source label to its family name."""
    return source.split(":", 1)[0].split("_", 1)[0]


def source_independence(index: Observation, first_party: Observation) -> Gate:
    """A JustWatch index and an app row sourced from JustWatch are an echo."""
    index_family = _source_family(index.source)
    first_party_family = _source_family(first_party.source)
    if index_family == first_party_family == "justwatch":
        return Gate("independence", False, SAME_SOURCE_ECHO)
    if first_party_family == "app" and ":justwatch" in first_party.source:
        return Gate("independence", False, SAME_SOURCE_ECHO)
    if index_family == "justwatch" and first_party_family in _FIRST_PARTY_FAMILIES:
        return Gate("independence", True, INDEPENDENT)
    return Gate("independence", False, NOT_PROVEN)


_REQUIRED_POSITIVE_SIGNALS = {
    "netflix_subscription": ("typed_title_native_id", "verified_nl_context",
                             "title_specific_positive", "class_specific_action"),
    "prime_subscription": ("typed_title_native_id", "verified_nl_context",
                           "title_specific_positive", "class_specific_action"),
    "prime_transaction": ("typed_title_native_id", "verified_nl_context",
                          "title_specific_positive", "class_specific_action"),
    "prime_channel": ("typed_title_native_id", "verified_nl_context",
                      "title_specific_positive", "class_specific_action"),
    "rakuten_transaction": ("typed_title_native_id", "verified_nl_context",
                            "title_specific_positive", "class_specific_action"),
    "free_class_control": ("verified_nl_context", "title_specific_positive",
                           "class_specific_action", "zero_payment"),
}


def first_party_verdict(observation: Observation, rule: str) -> str:
    """Derive a scoped advertised verdict from structured first-party signals."""
    signals = observation.signals
    if signals.change_boundary_straddle or signals.conflicting_scoped_signals:
        return NEEDS_RECHECK
    if signals.incomplete_or_login_wall:
        return UNKNOWN
    if signals.explicit_class_negative:
        return NO
    if rule not in POSITIVE_RULES:
        return UNKNOWN
    required = _REQUIRED_POSITIVE_SIGNALS[rule]
    if not all(getattr(signals, name) for name in required):
        return UNKNOWN
    if rule == "free_class_control" and observation.offer_class == "free_with_ads" and not signals.ad_funded:
        return UNKNOWN
    return YES


def index_verdict(observation: Observation) -> str:
    """A current, explicitly scoped index claim; absence is not a negative."""
    if observation.title_404 or observation.freshness != FRESH:
        return UNKNOWN
    if observation.claim not in (YES, NO):
        return UNKNOWN
    if not observation.provider_id:
        return UNKNOWN
    return observation.claim


def parse_eur_minor(raw: str) -> int | None:
    """Parse a localized EUR amount to cents; None means unscorable."""
    if not raw or not isinstance(raw, str):
        return None
    value = raw.strip()
    if any(sep in value for sep in ("-", "\u2013", "\u2014", "range")) and value.count(",") + value.count(".") > 1:
        return None
    cleaned = value.replace("\u00a0", "").replace(" ", "").replace("EUR", "").replace("\u20ac", "")
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") == 1:
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(".") == 1:
        integer, _, fraction = cleaned.partition(".")
        if len(fraction) != 2:
            return None
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    else:
        return None
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if amount < 0 or amount.as_tuple().exponent < -2:
        return None
    return int((amount * 100).to_integral_value())


def compare_prices(index_prices: tuple[Price, ...], first_party_prices: tuple[Price, ...]) -> str:
    """Exact EUR-cents equality on comparable variants; otherwise unknown/conflict."""
    comparable_index = [p for p in index_prices if p.comparable and p.currency == "EUR" and p.amount_minor is not None]
    comparable_first = [p for p in first_party_prices if p.comparable and p.currency == "EUR" and p.amount_minor is not None]
    if not comparable_index or not comparable_first:
        return UNKNOWN
    by_variant = {p.variant_id: p.amount_minor for p in comparable_index if p.variant_id}
    if len(by_variant) != len(comparable_index) or not by_variant:
        return UNKNOWN
    for price in comparable_first:
        if price.variant_id not in by_variant:
            return UNKNOWN
        if price.basis and comparable_index[0].basis and price.basis != comparable_index[0].basis:
            return UNKNOWN
    amounts = {price.amount_minor for price in comparable_first}
    if len(amounts) != 1:
        return NEEDS_RECHECK
    first_amount = amounts.pop()
    if len(set(by_variant.values())) != 1:
        return NEEDS_RECHECK
    if first_amount != next(iter(by_variant.values())):
        return NEEDS_RECHECK
    return YES


def adjudicate_pair(
    candidate: Candidate,
    index: Observation,
    first_party: Observation,
    manifest: Identity,
    slot_start: datetime,
) -> Adjudication:
    """Apply every gate and produce one outcome for a frozen matrix row."""
    gates = (
        identity_gate(index, manifest),
        identity_gate(first_party, manifest),
        market_gate(index),
        market_gate(first_party, require_explicit=True),
        service_class_gate(index, candidate),
        service_class_gate(first_party, candidate),
        time_gate((index, first_party), slot_start),
        freshness_gate(index),
        freshness_gate(first_party),
        source_independence(index, first_party),
    )
    fp_verdict = first_party_verdict(first_party, candidate.evidence_rule)
    idx_verdict = index_verdict(index)
    price_verdict = compare_prices(index.prices, first_party.prices)
    eligible = all(gate.passed for gate in gates)
    if fp_verdict == NEEDS_RECHECK:
        outcome = NEEDS_RECHECK
    elif not eligible or fp_verdict == UNKNOWN or idx_verdict == UNKNOWN:
        outcome = ABSTENTION if idx_verdict == UNKNOWN else UNRESOLVED
    elif fp_verdict == YES and idx_verdict == YES:
        outcome = TP
    elif fp_verdict == YES and idx_verdict == NO:
        outcome = FN
    elif fp_verdict == NO and idx_verdict == YES:
        outcome = FP
    elif fp_verdict == NO and idx_verdict == NO:
        outcome = TN
    else:
        outcome = UNRESOLVED
    return Adjudication(
        candidate_id=candidate.candidate_id,
        batch=candidate.batch,
        eligibility=gates,
        first_party_verdict=fp_verdict,
        index_verdict=idx_verdict,
        independence=INDEPENDENT if gates[-1].passed else gates[-1].reason,
        outcome=outcome,
        price_verdict=price_verdict,
    )


def _rate(numerator: int, denominator: int) -> object:
    if denominator == 0:
        return UNDEFINED
    return numerator / denominator


def metrics(adjudications: tuple[Adjudication, ...]) -> Metrics:
    """Counts and conditional rates; zero or unknowable denominators are UNDEFINED."""
    counts = Counter(item.outcome for item in adjudications)
    tp, fp, fn, tn = (counts[key] for key in (TP, FP, FN, TN))
    adjudicated = tp + fp + fn + tn
    unresolved_positives = counts[UNRESOLVED_POSITIVE] + sum(
        1 for item in adjudications
        if item.first_party_verdict == YES and item.outcome in (ABSTENTION, UNRESOLVED)
    )
    rates = {
        "agreement_rate": _rate(tp + tn, adjudicated),
        "conflict_rate": _rate(fp + fn, adjudicated),
        "precision": _rate(tp, tp + fp),
        "recall": _rate(tp, tp + fn),
        "false_positive_rate": _rate(fp, fp + tn),
        "false_negative_rate": _rate(fn, fn + tp),
    }
    if unresolved_positives:
        rates["recall"] = UNDEFINED
    denominators = {
        "agreement_rate": (tp + tn, adjudicated) if adjudicated else None,
        "conflict_rate": (fp + fn, adjudicated) if adjudicated else None,
        "precision": (tp, tp + fp) if tp + fp else None,
        "recall": (tp, tp + fn) if tp + fn else None,
        "false_positive_rate": (fp, fp + tn) if fp + tn else None,
        "false_negative_rate": (fn, fn + tp) if fn + tp else None,
    }
    return Metrics(dict(counts), rates, denominators)


@dataclass(frozen=True)
class DisplayRetrieval:
    """Display-retrieval accounting, deliberately separate from source accuracy."""

    eligible_positive_pairs: int
    displayed_pairs: int
    retrieval_misses: tuple[str, ...]
    excluded_transactional: tuple[str, ...]
    unresolved_reads: tuple[str, ...]

    @property
    def rate(self) -> object:
        return _rate(self.displayed_pairs, self.eligible_positive_pairs)


def app_display_retrieval(
    eligible_positive_ids: tuple[str, ...],
    displayed_ids: tuple[str, ...],
    *,
    unresolved_read_ids: tuple[str, ...] = (),
    transactional_ids: tuple[str, ...] = (),
    surface_exposes_transactions: bool = False,
) -> DisplayRetrieval:
    """Separate 'did the UI show it' from 'was the claim accurate'.

    Transactional pairs are excluded only when the named surface intentionally
    does not expose them; they are reported rather than counted as misses.
    """
    eligible = set(eligible_positive_ids)
    displayed = set(displayed_ids) & eligible
    unresolved = set(unresolved_read_ids) & eligible
    excluded = tuple(sorted(eligible & set(transactional_ids))) if not surface_exposes_transactions else ()
    scored = eligible - set(excluded) - unresolved
    misses = tuple(sorted(scored - displayed))
    return DisplayRetrieval(
        eligible_positive_pairs=len(eligible),
        displayed_pairs=len(displayed),
        retrieval_misses=misses,
        excluded_transactional=excluded,
        unresolved_reads=tuple(sorted(unresolved)),
    )
