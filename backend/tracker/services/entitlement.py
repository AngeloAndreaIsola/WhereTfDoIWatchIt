"""Pure evaluation of one title offer under a viewer's declared selection."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True)
class OfferKey:
    provider: str
    country: str
    offer_type: str
    content_type: str
    title_id: int
    source: str
    package_id: str
    variant: str
    season: int | None = None
    episode: int | None = None


@dataclass(frozen=True)
class OfferFact:
    key: OfferKey
    evidence_state: str
    package_resolved: bool = True
    source_supported: bool = True
    observed_at: datetime | None = None
    source_label: str = ""
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    host: str = ""
    channel: str = ""
    conflicted: bool = False
    public_conditions_met: bool = True


@dataclass(frozen=True)
class Selection:
    provider: str
    billing_country: str = ""
    holding: str = "unspecified"
    plan_state: str = "unspecified"
    plan_id: str | None = None
    channels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CoverageRule:
    key: OfferKey
    billing_country: str
    plan_id: str
    effect: str
    observed_at: datetime
    fresh_until: datetime
    starts_at: datetime
    ends_at: datetime | None
    source_label: str
    portability: str = "unspecified"

    def applies(self, key, selection, as_of):
        return (
            self.key == key
            and self.billing_country == selection.billing_country
            and self.plan_id == selection.plan_id
            and self.observed_at <= as_of < self.fresh_until
            and in_window(as_of, self.starts_at, self.ends_at)
        )


def in_window(as_of, starts_at, ends_at):
    return (starts_at is None or starts_at <= as_of) and (ends_at is None or as_of < ends_at)


@dataclass(frozen=True)
class Entitlement:
    verdict: str
    reason: str
    fact: OfferFact
    selection: Selection | None
    rule: CoverageRule | None = None

    @property
    def positive(self):
        return self.verdict in {"INCLUDED", "FREE_WITH_ADS"}

    def to_dict(self):
        key = self.fact.key
        data = {
            "verdict": self.verdict,
            "reason": self.reason,
            "provider": key.provider,
            "offer_type": key.offer_type,
            "offer_country": key.country or None,
            "evidence_state": self.fact.evidence_state,
            "caveats": [],
        }
        if self.selection and self.selection.billing_country:
            data["billing_country"] = self.selection.billing_country
        if self.selection and self.selection.plan_id:
            data["plan_id"] = self.selection.plan_id
        if self.fact.host:
            data["required_base"] = self.fact.host
        if self.fact.channel:
            data["required_channel"] = self.fact.channel
        label = self.rule.source_label if self.rule else self.fact.source_label
        observed = self.rule.observed_at if self.rule else self.fact.observed_at
        if label:
            data["source_label"] = label
        if observed:
            data["observed_at"] = observed.isoformat()
        if self.positive and self.reason in {"PLAN_COVERS_TITLE", "CHANNEL_AND_BASE_COVER_TITLE"}:
            data["caveats"].append("Coverage applies to your declared selection. Account status and playback are unverified.")
        return data


PUBLIC_ACTIONS = {
    "free": ("INCLUDED", "PUBLIC_FREE"),
    "free_with_ads": ("FREE_WITH_ADS", "PUBLIC_AD_FUNDED"),
    "rent": ("RENT_OR_BUY", "RENTAL"),
    "buy": ("RENT_OR_BUY", "PURCHASE"),
}


def evaluate_entitlement(fact, rules, selection, as_of):
    """Apply exact title/package/variant rules. The caller supplies reviewed facts."""
    def result(verdict, reason, rule=None):
        return Entitlement(verdict, reason, fact, selection, rule)

    key = fact.key
    if fact.evidence_state == "source_reported":
        return result("UNKNOWN", "SOURCE_REPORTED")
    if fact.evidence_state == "stale" or not in_window(as_of, fact.starts_at, fact.ends_at):
        return result("UNKNOWN", "STALE_EVIDENCE")
    if fact.conflicted:
        return result("UNKNOWN", "EVIDENCE_CONFLICT")
    if not key.country:
        return result("UNKNOWN", "TERRITORY_UNKNOWN")
    if key.offer_type not in PUBLIC_ACTIONS and key.offer_type not in {"subscription", "addon"}:
        return result("UNKNOWN", "OFFER_CLASS_UNKNOWN")
    if not fact.package_resolved or (key.offer_type == "addon" and not (fact.host and fact.channel)):
        return result("UNKNOWN", "PACKAGE_UNRESOLVED")
    if fact.evidence_state != "verified" or fact.observed_at is None or fact.observed_at > as_of:
        return result("UNKNOWN", "EVIDENCE_UNVERIFIED")
    if not fact.source_supported:
        return result("UNKNOWN", "SOURCE_UNSUPPORTED")
    if key.offer_type in PUBLIC_ACTIONS:
        if not fact.public_conditions_met:
            return result("UNKNOWN", "PUBLIC_CONDITIONS_UNCONFIRMED")
        return result(*PUBLIC_ACTIONS[key.offer_type])
    if selection is None or selection.provider != (fact.host or key.provider):
        return result("UNKNOWN", "HOLDING_UNCONFIRMED")

    applicable = [rule for rule in rules if rule.applies(key, selection, as_of)]
    if selection.billing_country and selection.billing_country != key.country:
        allowed = [rule for rule in applicable if rule.portability == "allowed"]
        denied = [rule for rule in applicable if rule.portability == "denied"]
        if allowed and denied:
            return result("UNKNOWN", "EVIDENCE_CONFLICT")
        if denied:
            return result("BLOCKED", "REGION_EXCLUDED", latest_rule(denied))
        if not allowed:
            return result("UNKNOWN", "PORTABILITY_UNVERIFIED")
        applicable = allowed
    if selection.holding == "no":
        return result("BLOCKED", "BASE_NOT_HELD")
    if selection.holding != "yes":
        return result("UNKNOWN", "HOLDING_UNCONFIRMED")
    if selection.plan_state == "unsupported":
        return result("UNKNOWN", "PLAN_UNSUPPORTED")
    if selection.plan_state != "selected" or not selection.plan_id:
        return result("UNKNOWN", "PLAN_UNSPECIFIED")
    if not selection.billing_country or not applicable:
        return result("UNKNOWN", "NO_APPLICABLE_RULE")
    effects = {rule.effect for rule in applicable}
    if effects == {"allow", "deny"}:
        return result("UNKNOWN", "EVIDENCE_CONFLICT")
    rule = latest_rule(applicable)
    if effects == {"deny"}:
        return result("BLOCKED", "PLAN_EXCLUDED", rule)
    if effects != {"allow"}:
        return result("UNKNOWN", "NO_APPLICABLE_RULE")
    if not fact.channel:
        return result("INCLUDED", "PLAN_COVERS_TITLE", rule)
    holding = selection.channels.get(fact.channel)
    if holding == "yes":
        return result("INCLUDED", "CHANNEL_AND_BASE_COVER_TITLE", rule)
    if holding == "no":
        return result("REQUIRES_ADDON", "CHANNEL_NOT_HELD", rule)
    return result("UNKNOWN", "HOLDING_UNCONFIRMED", rule)


def latest_rule(rules):
    return max(rules, key=lambda rule: (rule.observed_at, rule.source_label))


def project_title(rows):
    """Return a title status from (entitlement, locality, language match) rows."""
    if not rows:
        return None
    current = [(entitlement, locality) for entitlement, locality, language in rows
               if language and entitlement.fact.evidence_state == "verified"]
    for locality, status in (("local", "watchable_here"), ("vpn", "watchable_vpn")):
        if any(e.positive and (place == locality or locality == "local" and place == "anywhere")
               for e, place in current):
            return status
    if any(e.verdict == "UNKNOWN" and place != "out_of_reach"
           for e, place in current):
        return "unknown"
    if any(e.fact.evidence_state == "verified" and language is None and place != "out_of_reach"
           for e, place, language in rows):
        return "unknown"
    if any(e.verdict == "RENT_OR_BUY" and place in {"local", "vpn", "anywhere"} for e, place in current):
        return "purchase_only"
    if any(e.fact.evidence_state == "verified" for e, _, _ in rows):
        return "available_elsewhere"
    if any(e.reason == "SOURCE_REPORTED" for e, _, _ in rows):
        return "unknown"
    return "needs_recheck"
