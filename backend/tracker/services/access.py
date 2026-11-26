"""Shared offer and title access projections for API reads."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from tracker.models import Provider, ProviderCategory, Subscription
from tracker.services.entitlement import Entitlement, project_title
from tracker.services.entitlement_reads import EntitlementContext
from tracker.services.offers import (
    Offer,
    TerritorialScope,
    effective_territorial_scope,
)
from tracker.services.presentation import present_offer
from tracker.services.score import score_offer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
class AccessType:
    """The access model a single offer represents (distinct from its brand)."""

    SUBSCRIPTION = "subscription"
    ADDON = "addon"
    FREE = "free"
    FREE_WITH_ADS = "free_with_ads"
    RENT = "rent"
    BUY = "buy"
    UNKNOWN = "unknown"


OFFER_TYPE_TO_ACCESS: dict[str, str] = {
    "subscription": AccessType.SUBSCRIPTION,
    "addon": AccessType.ADDON,
    "free": AccessType.FREE,
    "free_with_ads": AccessType.FREE_WITH_ADS,
    "rent": AccessType.RENT,
    "buy": AccessType.BUY,
}

SUBSCRIPTION_ACCESS_TYPES = frozenset({AccessType.SUBSCRIPTION, AccessType.ADDON})
FREE_ACCESS_TYPES = frozenset({AccessType.FREE, AccessType.FREE_WITH_ADS})
PAYMENT_ACCESS_TYPES = frozenset({AccessType.RENT, AccessType.BUY})
#: Access types that can make a title watchable without extra payment.
INCLUDED_ACCESS_TYPES = SUBSCRIPTION_ACCESS_TYPES | FREE_ACCESS_TYPES

class SubscriptionAccessMode:
    """How the user holds a service — stored on ``Subscription.access_mode``."""

    INCLUDED = "included"
    ADDON = "addon"
    FREE = "free"
    RENT_BUY = "rent_buy"
    NONE = "none"

    CHOICES = (
        (INCLUDED, "Included subscription"),
        (ADDON, "Add-on subscription"),
        (FREE, "Free access"),
        (RENT_BUY, "Account for rental / purchase"),
        (NONE, "Not owned / not available"),
    )


class Locality:
    """Whether the user can reach a given country's catalogue."""

    LOCAL = "local"
    VPN = "vpn"
    OUT_OF_REACH = "out_of_reach"
    ANYWHERE = "anywhere"  # explicit source assertion says country independent
    UNKNOWN = "unknown"  # no territorial assertion was supplied


class AccessVerdict:
    AVAILABLE_HERE = "available_here"
    AVAILABLE_VPN = "available_vpn"
    PURCHASE_ONLY = "purchase_only"
    AVAILABLE_ELSEWHERE = "available_elsewhere"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    CHECKING = "checking"
    NEEDS_RECHECK = "needs_recheck"


VERDICT_LABELS: dict[str, tuple[str, str]] = {
    AccessVerdict.AVAILABLE_HERE: ("Watchable here", "🟢"),
    AccessVerdict.AVAILABLE_VPN: ("Watchable with VPN", "🟡"),
    AccessVerdict.PURCHASE_ONLY: ("Available — rental or purchase", "🟣"),
    AccessVerdict.AVAILABLE_ELSEWHERE: ("Available — not with your subscriptions", "🟠"),
    AccessVerdict.UNAVAILABLE: ("Not available", "⚫"),
    AccessVerdict.UNKNOWN: ("Unknown", "⚪"),
    AccessVerdict.CHECKING: ("Checking availability…", "🔵"),
    AccessVerdict.NEEDS_RECHECK: ("Needs recheck", "🕓"),
}


class RefreshState:
    """Per-title refresh-queue state, derived from ``RefreshRequest`` rows.

    The vocabulary the read path uses to tell "no offers because never
    checked" apart from "no offers because a check found none" (and to show
    queued/failed checks) — produced by
    ``tracker.services.refresh_requests.refresh_states_for``.
    """

    PENDING = "pending"
    CHECKED = "checked"
    FAILED = "failed"
    NEVER_CHECKED = "never_checked"


# ---------------------------------------------------------------------------
# Provider aliases and normalization
# ---------------------------------------------------------------------------
#: Explicit, verified alias mappings from external, technical, or legacy
#: representations to canonical provider slugs.
#:
#: Only verified 1:1 synonyms for the exact same entitlement and access type
#: are permitted. Distinct tiers, add-ons, channels, or rental storefronts
#: (e.g. viaplay vs viaplay_sports, prime vs amazon channels, apple vs apple_tv_store)
#: are separate providers and must NEVER be aliased.
SAFE_PROVIDER_ALIASES: dict[str, str] = {
    # Curated profile / identical entitlement aliases
    "netflixkids": "netflix",
    "netflix_kids": "netflix",
    "netflix-kids": "netflix",
    "netflix kids": "netflix",
    # Amazon Prime Video variants
    "amazonprimevideo": "prime",
    "amazonprime": "prime",
    "prime-video": "prime",
    "prime_video": "prime",
    "prime video": "prime",
    # Apple TV+ subscription variants (distinct from apple_tv_store / apple_tv_amazon)
    "appletvplus": "apple",
    "appletv": "apple",
    "apple-tv-plus": "apple",
    "apple tv+": "apple",
    # Disney+ variants
    "disneyplus": "disney",
    "disney-plus": "disney",
    "disney_plus": "disney",
    "disney+": "disney",
    # Max / HBO variants (sa_id="hbo", canonical slug="max"; distinct from max_amazon)
    "hbo": "max",
    "hbomax": "max",
    "hbo-max": "max",
    "hbo_max": "max",
    "hbo max": "max",
    # Paramount+ variants
    "paramountplus": "paramount",
    "paramount-plus": "paramount",
    "paramount_plus": "paramount",
    "paramount+": "paramount",
    # Peacock variants
    "peacocktv": "peacock",
    "peacock-tv": "peacock",
    "peacock_tv": "peacock",
    "peacock tv": "peacock",
    # Tubi variants
    "tubitv": "tubi",
    "tubi-tv": "tubi",
    "tubi_tv": "tubi",
    # Pluto TV variants
    "plutotv": "pluto_tv",
    "pluto-tv": "pluto_tv",
    "pluto tv": "pluto_tv",
    # Mediaset Infinity variants
    "mediasetplay": "mediaset_infinity",
    "mediaset-infinity": "mediaset_infinity",
    "mediaset infinity": "mediaset_infinity",
    # BBC iPlayer variants (migration 0033 merged legacy alias "bbc")
    "bbc": "bbc_iplayer",
    "bbc-iplayer": "bbc_iplayer",
    "bbc iplayer": "bbc_iplayer",
    # RaiPlay variants (migration 0033 merged legacy alias "rai-play")
    "rai-play": "raiplay",
    "rai_play": "raiplay",
    "rai play": "raiplay",
    # NHK World variants (migration 0033 merged legacy alias "nhk")
    "nhk": "nhk_world",
    # Viaplay Sports variants (distinct from base viaplay)
    "viaplaysports": "viaplay_sports",
    "viaplay sports": "viaplay_sports",
    # Rakuten TV (JustWatch "wuaki")
    "wuaki": "rakuten_tv",
    # YouTube Free variants (distinct from paid youtubetv)
    "youtubefree": "youtube",
    "youtube free": "youtube",
    # YouTube TV
    "youtube tv": "youtubetv",
    # Canal+ variants
    "canalplus": "canal_plus",
    "canal-plus": "canal_plus",
    "canal+": "canal_plus",
    # Film1 variants
    "film1nl": "film1",
    # NPO Plus variants
    "npoplus": "npo_plus",
    "npo plus": "npo_plus",
    # Pathé Thuis variants
    "pathe": "pathe_thuis",
    # Curiosity Stream variants
    "curiositystream": "curiosity_stream",
    "curiosity": "curiosity_stream",
    # JustWatch TV / South Park / WOW / IFFR / Magellan
    "justwatchtv": "justwatch_tv",
    "southpark": "south_park",
    "wowpresentsplus": "wow_presents_plus",
    "iffrunleashed": "iffr_unleashed",
    "magellantv": "magellan_tv",
}


def normalize_service_slug(slug: str) -> str:
    """Normalize a provider or service slug to its canonical identifier.

    Applies exact lowercase trimming and explicit, verified 1:1 alias mappings.
    Never uses substring containment or heuristic fuzzy matching.
    """
    s = (slug or "").lower().strip()
    if not s:
        return ""
    return SAFE_PROVIDER_ALIASES.get(s, s)


# ---------------------------------------------------------------------------
# Entitlement: what the user's selected services grant
# ---------------------------------------------------------------------------
@dataclass
class UserAccess:
    """The user's declared services, grouped by access mode."""

    included: set[str] = field(default_factory=set)
    addon: set[str] = field(default_factory=set)
    free: set[str] = field(default_factory=set)
    rent_buy: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)
    #: True when synthesised from an explicit slug set (tests / callers that
    #: pass ``user_subscriptions`` directly) rather than DB rows.
    synthetic: bool = False
    entitlement: EntitlementContext = field(default_factory=EntitlementContext)

    def _match(self, slug: str, bucket: set[str]) -> bool:
        s = normalize_service_slug(slug)
        if not s or not bucket:
            return False
        if s in bucket:
            return True
        return any(normalize_service_slug(b) == s for b in bucket)

    def entitles(self, slug: str, access_type: str) -> bool:
        """Does the user's declared access cover this offer's access type?"""
        if access_type == AccessType.SUBSCRIPTION:
            return self._match(slug, self.included)
        if access_type == AccessType.ADDON:
            return self._match(slug, self.addon)
        if access_type in FREE_ACCESS_TYPES:
            return (
                self._match(slug, self.free)
                or self._match(slug, self.included)
                or self._match(slug, self.addon)
            )
        if access_type in PAYMENT_ACCESS_TYPES:
            return self._match(slug, self.rent_buy)
        return False

    def modes_for(self, slug: str) -> list[str]:
        modes = []
        if self._match(slug, self.included):
            modes.append(SubscriptionAccessMode.INCLUDED)
        if self._match(slug, self.addon):
            modes.append(SubscriptionAccessMode.ADDON)
        if self._match(slug, self.free):
            modes.append(SubscriptionAccessMode.FREE)
        if self._match(slug, self.rent_buy):
            modes.append(SubscriptionAccessMode.RENT_BUY)
        return modes


def build_explicit_access(slugs: Iterable[str]) -> UserAccess:
    """Access from an explicit slug/name set (used by callers that pass one)."""
    values = {normalize_service_slug(s) for s in slugs or () if s}
    values.discard("")
    return UserAccess(
        included=set(values), addon=set(), free=set(values),
        rent_buy=set(), names=set(values), synthetic=True,
    )


def build_user_access(user_id: int = 1, *, offers=(), as_of=None) -> UserAccess:
    """Build the user's access map from ``Subscription`` rows.

    ``access_mode`` decides the bucket; ``none`` means "explicitly not owned"
    and grants nothing. Providers the app treats as always-available
    (broadcaster VOD, scraper sources) still auto-count. Name-only rows
    preserve their declared ``access_mode``; unknown modes are ignored.
    """
    access = UserAccess()
    targets = {
        SubscriptionAccessMode.INCLUDED: access.included,
        SubscriptionAccessMode.ADDON: access.addon,
        SubscriptionAccessMode.FREE: access.free,
        SubscriptionAccessMode.RENT_BUY: access.rent_buy,
    }
    rows = Subscription.objects.filter(user_id=user_id).select_related("provider")
    legacy_holdings = set()
    for sub in rows:
        raw_mode = getattr(sub, "access_mode", None)
        mode = str(raw_mode).lower().strip() if raw_mode is not None else SubscriptionAccessMode.INCLUDED
        if mode == SubscriptionAccessMode.NONE:
            continue
        target = targets.get(mode)
        if target is None:
            # Unknown access mode must never silently grant included entitlement
            continue
        slug = (sub.provider.slug.lower().strip() if sub.provider_id else "")
        name = (sub.name or "").lower().strip()
        if slug:
            canon_slug = normalize_service_slug(slug)
            if canon_slug:
                target.add(canon_slug)
                if mode == SubscriptionAccessMode.INCLUDED:
                    legacy_holdings.add(canon_slug)
        else:
            # Name-only rows preserve their declared access_mode.
            if name:
                access.names.add(name)
                canon_name = normalize_service_slug(name)
                if canon_name:
                    target.add(canon_name)
        if name and slug:
            access.names.add(name)

    auto = Provider.objects.filter(
        is_enabled=True,
        category__in=[
            ProviderCategory.BVOD,
            ProviderCategory.AVOD,
            ProviderCategory.FAST,
            ProviderCategory.SCRAPER,
        ],
    ).values_list("slug", "category")
    for slug, category in auto:
        s = normalize_service_slug(slug)
        if not s:
            continue
        # Ad-supported and public-service catalogues are free by definition
        # (BVOD = broadcaster VOD, AVOD = ad-supported, FAST = free ad-supported
        # TV). A TVOD storefront is *not* free — it stays out of every bucket
        # until the user declares a rent/buy account, so a rent offer never
        # silently becomes an entitlement.
        if category in (
            ProviderCategory.BVOD,
            ProviderCategory.AVOD,
            ProviderCategory.FAST,
        ):
            access.free.add(s)
        else:
            access.included.add(s)
    access.entitlement = EntitlementContext(as_of=as_of, legacy_holdings=legacy_holdings).load(user_id, list(offers))
    return access


# ---------------------------------------------------------------------------
# Country reach
# ---------------------------------------------------------------------------
@dataclass
class CountryReach:
    current_country: str = ""
    vpn_active: bool = False
    vpn_regions: set[str] = field(default_factory=set)

    def locality(self, country: str, territorial_scope: str = "") -> str:
        """Determine reach from territory, independently of source provenance.

        Only an explicit ``worldwide`` scope grants country-independent reach.
        A known country is evaluated against the user's market/VPN, and a
        missing country without worldwide evidence remains unknown.
        """
        if (territorial_scope or "").lower().strip() == TerritorialScope.WORLDWIDE:
            return Locality.ANYWHERE
        c = (country or "").upper().strip()
        if not c:
            # Missing geography is not a global-rights assertion. Only the
            # explicit worldwide territorial assertion may be treated as global.
            return Locality.UNKNOWN
        if self.current_country and c == self.current_country:
            return Locality.LOCAL
        if self.vpn_active and (not self.vpn_regions or c in self.vpn_regions):
            return Locality.VPN
        return Locality.OUT_OF_REACH

    def reachable(self, locality: str) -> bool:
        return locality in (Locality.LOCAL, Locality.VPN, Locality.ANYWHERE)


def build_reach(preference: Any, current_country: Optional[str] = None) -> CountryReach:
    """Country reach from the user's own setting — never from IP/locale."""
    curr = (current_country or getattr(preference, "current_country", "") or "")
    curr = curr.upper().strip()
    regions = {
        (c or "").upper().strip()
        for c in (getattr(preference, "candidate_countries", None) or [])
    }
    regions.discard("")
    vpn_enabled = bool(getattr(preference, "vpn_enabled", False))
    # Candidate countries are a region limit, not consent to use a VPN. The
    # explicit switch must be on before any foreign offer is VPN-reachable.
    vpn_active = vpn_enabled
    return CountryReach(current_country=curr, vpn_active=vpn_active, vpn_regions=regions)


# ---------------------------------------------------------------------------
# Per-offer classification
# ---------------------------------------------------------------------------
@dataclass(eq=False)
class ClassifiedOffer:
    country: str
    service: str
    service_display: str
    source_indicator: str
    offer_type: str
    access_type: str
    access_label: str
    quality: str
    url: str
    audio_languages: list[str] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)
    is_scraped_source: bool = False
    source_category: str = ""
    territorial_scope: str = TerritorialScope.UNKNOWN
    is_entitled: bool = False
    is_language_match: bool = True
    has_language_info: bool = False
    locality: str = Locality.LOCAL
    is_accessible: bool = False
    requires_payment: bool = False
    #: True for an offer delivered as an add-on channel (its provider has a
    #: base platform). ``required_base``/``required_base_display`` name the
    #: platform the user must also hold; both are empty for standalone offers.
    is_addon: bool = False
    required_base: str = ""
    required_base_display: str = ""
    rejection_reasons: list[str] = field(default_factory=list)
    ranking_score: tuple = field(default_factory=tuple)
    availability_evidence: str = "verified"
    entitlement: Entitlement | None = None
    lineage_scope: dict = field(default_factory=dict)
    #: Display price (structured amount + currency) or None. Price never
    #: affects ``is_entitled``/``is_accessible`` — it only tells the reader what
    #: a rent/buy offer would cost, which is orthogonal to access.
    price: Optional[dict] = None
    prices: list[dict] = field(default_factory=list)
    available_from: Optional[str] = None
    available_until: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None

    @property
    def is_qualifying_offer_type(self) -> bool:
        return self.access_type in INCLUDED_ACCESS_TYPES

    @property
    def is_current_country(self) -> bool:
        return self.locality in (Locality.LOCAL, Locality.ANYWHERE)

    @property
    def is_candidate_country(self) -> bool:
        return self.locality == Locality.VPN

    def entitlement_fields(self) -> dict[str, Any]:
        return {
            "access_type": self.access_type,
            "access_label": self.access_label,
            "locality": self.locality,
            "is_accessible": self.is_accessible,
            "is_entitled": self.is_entitled,
            "requires_payment": self.requires_payment,
            "playback_verified": False,
            "is_addon": self.is_addon,
            "required_base": self.required_base,
            "required_base_display": self.required_base_display,
            "rejection_reasons": self.rejection_reasons,
            "entitlement": self.entitlement.to_dict() if self.entitlement else None,
            **self.lineage_scope,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "country": self.country,
            "service": self.service,
            "service_display": self.service_display,
            "source_indicator": self.source_indicator,
            "offer_type": self.offer_type,
            "quality": self.quality,
            "url": self.url,
            "audio_languages": self.audio_languages,
            "subtitle_languages": self.subtitle_languages,
            "is_scraped_source": self.is_scraped_source,
            "source_category": self.source_category,
            "territorial_scope": self.territorial_scope,
            "is_language_match": self.is_language_match,
            "availability_evidence": self.availability_evidence,
            **self.entitlement_fields(),
            "price": self.price,
            "prices": self.prices,
            "available_from": self.available_from,
            "available_until": self.available_until,
            "season_number": self.season_number,
            "episode_number": self.episode_number,
        }


def _coerce_offer(raw: Any) -> Offer:
    """Normalize any offer-like object (Offer, RankedOffer, dict) to ``Offer``.

    Ranking returns ``RankedOffer`` (which carries ``audio``/``subs``) and some
    legacy paths hand over dicts; the shared classifier only needs the
    canonical fields, so it coerces rather than making every caller convert.
    """
    if isinstance(raw, Offer):
        return raw
    if isinstance(raw, dict):
        return Offer.from_dict(raw)
    return Offer(
        country=(getattr(raw, "country", "") or ""),
        service=(getattr(raw, "service", "") or ""),
        offer_type=(getattr(raw, "offer_type", "") or ""),
        quality=(getattr(raw, "quality", "") or ""),
        url=(getattr(raw, "url", "") or ""),
        audio_languages=list(
            getattr(raw, "audio_languages", None) or getattr(raw, "audio", None) or []
        ),
        subtitle_languages=list(
            getattr(raw, "subtitle_languages", None) or getattr(raw, "subs", None) or []
        ),
        is_scraped_source=bool(getattr(raw, "is_scraped_source", False)),
        source_category=getattr(raw, "source_category", ""),
        territorial_scope=getattr(raw, "territorial_scope", ""),
        price=getattr(raw, "price", None),
        prices=list(getattr(raw, "prices", None) or []),
        availability_evidence=getattr(raw, "availability_evidence", "verified"),
        available_from=getattr(raw, "available_from", None),
        available_until=getattr(raw, "available_until", None),
        season_number=getattr(raw, "season_number", None),
        episode_number=getattr(raw, "episode_number", None),
        lineage=getattr(raw, "lineage", None),
    )


def classify_offer(
    raw: Offer,
    *,
    access: UserAccess,
    preference: Any,
    reach: CountryReach,
    provider_map: dict[str, Provider],
) -> ClassifiedOffer:
    """Classify one offer into its access model, reachability and eligibility.

    ``is_accessible`` is what both the watchlist verdict and the detail page
    read: it is true only when the user is entitled to the offer, the language
    rule passes, and the offer's country is reachable. It never depends on
    mere presence.
    """
    raw = _coerce_offer(raw)
    country = (raw.country or "").upper().strip()
    service = raw.service or ""
    offer_type = (raw.offer_type or "").lower().strip()
    access_type = OFFER_TYPE_TO_ACCESS.get(offer_type, AccessType.UNKNOWN)
    is_scraped = bool(getattr(raw, "is_scraped_source", False))
    territorial_scope = effective_territorial_scope(
        country, getattr(raw, "territorial_scope", "")
    )
    audio = list(raw.audio_languages or [])
    subs = list(raw.subtitle_languages or [])
    has_lang_info = bool(audio or subs)

    presented = present_offer(raw, provider_map)
    scored = score_offer(raw, preference)
    is_lang_match = scored.is_match

    # Worldwide distribution does not extend paid plan terms to another market.
    reach_scope = TerritorialScope.COUNTRY if access_type in SUBSCRIPTION_ACCESS_TYPES else territorial_scope
    locality = reach.locality(country, reach_scope)
    requires_payment = access_type in PAYMENT_ACCESS_TYPES

    entitlement = access.entitlement.evaluate(
        raw, normalize_service_slug(service), country, access_type, reach.current_country,
    )
    entitled = entitlement.positive
    is_accessible = bool(entitled and is_lang_match and reach.reachable(locality))
    is_addon = bool(entitlement.fact.host) or access_type == AccessType.ADDON
    if is_addon:
        access_type = AccessType.ADDON
    base_slug = entitlement.fact.host
    base_display = getattr(provider_map.get(base_slug), "name", "") or base_slug
    labels = {
        "PLAN_COVERS_TITLE": "Included with your selected plan",
        "CHANNEL_AND_BASE_COVER_TITLE": "Included with your channel",
        "CHANNEL_NOT_HELD": "Requires add-on channel",
        "PUBLIC_FREE": "Free",
        "PUBLIC_AD_FUNDED": "Free with ads",
        "RENTAL": "Rent",
        "PURCHASE": "Buy",
    }
    access_label = labels.get(entitlement.reason)
    if not access_label:
        access_label = "Plan access unconfirmed" if access_type in SUBSCRIPTION_ACCESS_TYPES else "Access unconfirmed"
        if entitlement.verdict == "BLOCKED":
            access_label = "Access blocked"
    reasons: list[str] = []
    reason_text = {
        "BASE_NOT_HELD": "The required base subscription is marked as not held",
        "CHANNEL_NOT_HELD": "The required add-on channel is marked as not held",
        "PLAN_EXCLUDED": "The selected plan excludes this offer",
        "REGION_EXCLUDED": "The selected product excludes this market",
        "PLAN_UNSPECIFIED": "Confirm your plan",
        "PLAN_UNSUPPORTED": "The declared plan has no supported product mapping",
        "HOLDING_UNCONFIRMED": "The required holding is unconfirmed",
        "NO_APPLICABLE_RULE": "Title coverage under the selected plan is unconfirmed",
        "PORTABILITY_UNVERIFIED": "Plan coverage in this market is unconfirmed",
        "SOURCE_REPORTED": "A source reported this offer",
        "STALE_EVIDENCE": "This offer was last seen in an earlier check",
        "EVIDENCE_CONFLICT": "Applicable offer evidence conflicts",
    }
    if not entitled:
        reasons.append(reason_text.get(entitlement.reason, access_label))
    if is_lang_match is None:
        reasons.append("Language information unknown")
    elif is_lang_match is False:
        if not has_lang_info:
            reasons.append("Language information unknown")
        else:
            reasons.append("Does not satisfy language rules")
    if locality == Locality.OUT_OF_REACH:
        cc = country or "?"
        if not reach.vpn_active:
            reasons.append(f"Available in {cc}, but VPN access is disabled")
        elif reach.vpn_regions:
            reasons.append(
                f"Available in {cc}, but {cc} is not in your configured VPN regions"
            )
        else:
            reasons.append(f"Available in {cc}, but no VPN regions are configured")
    elif locality == Locality.UNKNOWN:
        reasons.append("Territory information unknown")

    return ClassifiedOffer(
        country=presented.country or country,
        service=service,
        service_display=presented.service_display,
        source_indicator=presented.source_indicator,
        offer_type=offer_type,
        access_type=access_type,
        access_label=access_label,
        quality=raw.quality or "",
        url=raw.url or "",
        audio_languages=audio,
        subtitle_languages=subs,
        is_scraped_source=is_scraped,
        source_category=(
            getattr(raw, "source_category", "")
            or ("unofficial" if is_scraped else "official")
        ),
        territorial_scope=territorial_scope,
        is_entitled=entitled,
        is_language_match=is_lang_match,
        has_language_info=has_lang_info,
        locality=locality,
        is_accessible=is_accessible,
        requires_payment=requires_payment,
        is_addon=is_addon,
        required_base=base_slug,
        required_base_display=base_display,
        rejection_reasons=reasons,
        ranking_score=scored.score_tuple,
        availability_evidence=raw.availability_evidence,
        entitlement=entitlement,
        lineage_scope=raw.lineage.public_scope() if raw.lineage else {},
        price=raw.price,
        prices=list(raw.prices or []),
        available_from=raw.available_from,
        available_until=raw.available_until,
        season_number=raw.season_number,
        episode_number=raw.episode_number,
    )


# ---------------------------------------------------------------------------
# Per-title resolution
# ---------------------------------------------------------------------------
@dataclass
class AccessResult:
    movie_id: int
    verdict: str
    status_label: str
    status_symbol: str
    offers: list[ClassifiedOffer] = field(default_factory=list)
    accessible_offers: list[ClassifiedOffer] = field(default_factory=list)
    vpn_offers: list[ClassifiedOffer] = field(default_factory=list)
    purchase_offers: list[ClassifiedOffer] = field(default_factory=list)
    excluded_offers: list[ClassifiedOffer] = field(default_factory=list)
    best_offer: Optional[ClassifiedOffer] = None
    explanation: list[str] = field(default_factory=list)
    refresh_state: Optional[str] = None
    access_flags: dict[str, Any] = field(default_factory=dict)

    def to_watchability_dict(self) -> dict[str, Any]:
        """Shape consumed by the detail page's ``watchability`` field."""
        return {
            "verdict": self.verdict,
            "status": self.verdict,
            "status_label": self.status_label,
            "status_symbol": self.status_symbol,
            "explanation": self.explanation,
            "refresh_state": self.refresh_state,
            "access_flags": self.access_flags,
            "best_offer": self.best_offer.to_dict() if self.best_offer else None,
            "purchase_offers": [offer.to_dict() for offer in self.purchase_offers],
        }


def _empty_result(movie, refresh_state: Optional[str]) -> AccessResult:
    label, symbol = VERDICT_LABELS[AccessVerdict.UNKNOWN]
    if refresh_state == RefreshState.CHECKED:
        verdict = AccessVerdict.UNKNOWN
        label, symbol = "Unknown — no offers verified", "⚪"
        explanation = [
            "A source returned no offers, but source coverage does not establish "
            "that the title is unavailable in this country."
        ]
    elif refresh_state == RefreshState.PENDING:
        verdict, label, symbol = (
            AccessVerdict.CHECKING,
            *VERDICT_LABELS[AccessVerdict.CHECKING],
        )
        explanation = ["A refresh for this title is queued — results appear once the worker gets to it."]
    elif refresh_state == RefreshState.FAILED:
        verdict = AccessVerdict.UNKNOWN
        label, symbol = "Unknown — last check failed", "⚪"
        explanation = ["The last availability check failed — it is retried on the next refresh."]
    else:
        verdict = AccessVerdict.UNKNOWN
        explanation = ["No availability data found for this title."]
    return AccessResult(
        movie_id=getattr(movie, "id", 0),
        verdict=verdict,
        status_label=label,
        status_symbol=symbol,
        explanation=explanation,
        refresh_state=refresh_state,
        access_flags={},
    )


def resolve_title(
    movie,
    offers: Iterable[Offer],
    preference: Any,
    *,
    current_country: Optional[str] = None,
    user_subscriptions: Optional[set[str]] = None,
    refresh_state: Optional[str] = None,
    access: Optional[UserAccess] = None,
    provider_map: Optional[dict[str, Provider]] = None,
) -> AccessResult:
    """Resolve one title's overall access verdict from its canonical offers.

    This is the single resolver: the watchlist badge and the detail page both
    read its :class:`AccessResult`. When ``user_subscriptions`` is passed it is
    treated as the complete set of services the user holds (legacy callers);
    otherwise the map is built from ``Subscription`` rows.
    """
    offer_list = [o for o in offers or ()]
    for raw in offer_list:
        if not isinstance(raw, Offer):
            raise TypeError(f"Expected Offer, got {type(raw).__name__}")

    if not offer_list:
        return _empty_result(movie, refresh_state)

    if access is not None:
        pass
    elif user_subscriptions is not None:
        access = build_explicit_access(user_subscriptions)
    else:
        access = build_user_access(getattr(preference, "user_id", 1) or 1, offers=offer_list)

    reach = build_reach(preference, current_country)
    if provider_map is None:
        provider_map = {p.slug: p for p in Provider.objects.all()}

    classified = [
        classify_offer(
            o, access=access, preference=preference, reach=reach, provider_map=provider_map
        )
        for o in offer_list
    ]

    local = [
        c for c in classified
        if c.is_accessible and c.locality in (Locality.LOCAL, Locality.ANYWHERE)
    ]
    vpn = [c for c in classified if c.is_accessible and c.locality == Locality.VPN]
    purchase = [
        c for c in classified
        if c.entitlement.verdict == "RENT_OR_BUY" and c.is_language_match and reach.reachable(c.locality)
    ]

    raw_ranking_pref = getattr(preference, "ranking_preferences", None)
    if isinstance(raw_ranking_pref, dict) and raw_ranking_pref:
        ranking_prefs = raw_ranking_pref
    else:
        from tracker.services.preferences import get_default_ranking_preferences
        ranking_prefs = get_default_ranking_preferences()

    source_priority = ranking_prefs.get("source_priority") or ["official", "local", "unofficial"]
    prefer_official = ranking_prefs.get("prefer_official", True)
    prefer_local = ranking_prefs.get("prefer_local", True)
    prefer_entitled = ranking_prefs.get("prefer_entitled", True)
    provider_priority = [p.lower() for p in (ranking_prefs.get("provider_priority") or [])]

    from tracker.services.ranking import build_offer_sort_key

    def _offer_sort_key(c: ClassifiedOffer):
        # Convert access-classification fields through the same precedence key
        # used by rank_offers so both APIs select the same eligible best offer.
        return build_offer_sort_key(
            c,
            source_priority=source_priority,
            prefer_official=prefer_official,
            prefer_local=prefer_local,
            prefer_entitled=prefer_entitled,
            local_country=reach.current_country,
            provider_priority=provider_priority,
        )

    accessible = sorted(local, key=_offer_sort_key) + sorted(vpn, key=_offer_sort_key)
    excluded = [c for c in classified if c not in accessible]
    projected = project_title([(c.entitlement, c.locality, c.is_language_match) for c in classified])
    verdict = {
        "watchable_here": AccessVerdict.AVAILABLE_HERE,
        "watchable_vpn": AccessVerdict.AVAILABLE_VPN,
    }.get(projected, projected)
    explanation = list(dict.fromkeys(
        reason for c in excluded for reason in c.rejection_reasons
    )) if not accessible else []
    if verdict == AccessVerdict.PURCHASE_ONLY:
        explanation = ["Available for rental or purchase."]
    if verdict == AccessVerdict.AVAILABLE_ELSEWHERE:
        label, symbol = _elsewhere_label(excluded)
    else:
        label, symbol = VERDICT_LABELS[verdict]

    present_types = {c.access_type for c in classified}
    available_types = {c.access_type for c in accessible}
    access_flags = {
        "present_access_types": sorted(present_types),
        "available_access_types": sorted(available_types),
        "has_subscription": AccessType.SUBSCRIPTION in available_types,
        "has_addon": AccessType.ADDON in available_types,
        "has_free": AccessType.FREE in available_types,
        "has_free_with_ads": AccessType.FREE_WITH_ADS in available_types,
        "has_rent": any(c.access_type == AccessType.RENT for c in purchase),
        "has_buy": any(c.access_type == AccessType.BUY for c in purchase),
        "requires_payment_only": verdict == AccessVerdict.PURCHASE_ONLY,
    }

    return AccessResult(
        movie_id=getattr(movie, "id", 0),
        verdict=verdict,
        status_label=label,
        status_symbol=symbol,
        offers=classified,
        accessible_offers=accessible,
        vpn_offers=vpn,
        purchase_offers=purchase,
        excluded_offers=excluded,
        best_offer=accessible[0] if accessible else None,
        explanation=explanation,
        refresh_state=refresh_state,
        access_flags=access_flags,
    )


def _elsewhere_label(excluded: list[ClassifiedOffer]) -> tuple[str, str]:
    """Pick the most informative 🟠 label from the excluded offers."""
    lang_excluded = [
        c for c in excluded
        if c.is_qualifying_offer_type and c.is_entitled and not c.is_language_match
    ]
    if lang_excluded:
        if any(not c.has_language_info for c in lang_excluded):
            return ("Available — language data unknown", "🟠")
        return ("Available — not matching language filter", "🟠")
    return ("Available — not with your subscriptions", "🟠")
