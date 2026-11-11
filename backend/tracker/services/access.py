"""Single source of truth for "how can this user access this title?".

The watchlist badge and the title-detail offer list must never disagree about
whether a title is watchable. They used to run two different classifiers
(``watchability.evaluate_watchability`` for the watchlist,
``offer_pipeline.OfficialOffersPipeline._qualify_offers`` for the detail page)
with different rules — one applied country + language + strict subscription
matching, the other applied none of them — so a title could read 🟠 "Available
elsewhere" in the watchlist while its detail page listed the same offers as
included. This module is the one resolver both paths call.

It distinguishes, per offer, the access model the SPEC requires:

* **included subscription** — a service the user selected with
  ``access_mode="included"``;
* **add-on channel** — a separate subscription bolted onto a base service
  (``access_mode="addon"``);
* **free** vs **free with ads** — genuinely free streams;
* **rent** / **buy** — require an additional payment (and an account the user
  selected with ``access_mode="rent_buy"``);
* **a selected service in another country (VPN)** — reachable only through the
  user's configured VPN regions.

and the title verdict: available here / available via VPN / purchase-only /
available elsewhere / unavailable / unknown / checking.

VPN reach is driven by the user's own setting (``Preference.vpn_enabled`` and
``Preference.candidate_countries`` as the regions) and their actual country
(``current_country``). It is never inferred from IP or locale, and a
cross-country offer is reported as *potentially* reachable (🟡), never as a
certainty.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from tracker.models import Provider, ProviderCategory, Subscription
from tracker.services.offers import Offer
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

ACCESS_TYPE_LABELS: dict[str, str] = {
    AccessType.SUBSCRIPTION: "Included with your subscription",
    AccessType.ADDON: "Add-on channel",
    AccessType.FREE: "Free",
    AccessType.FREE_WITH_ADS: "Free with ads",
    AccessType.RENT: "Rent",
    AccessType.BUY: "Buy",
    AccessType.UNKNOWN: "Unknown access",
}


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
    ANYWHERE = "anywhere"  # scraped sources ignore geography


class AccessVerdict:
    AVAILABLE_HERE = "available_here"
    AVAILABLE_VPN = "available_vpn"
    PURCHASE_ONLY = "purchase_only"
    AVAILABLE_ELSEWHERE = "available_elsewhere"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    CHECKING = "checking"


VERDICT_LABELS: dict[str, tuple[str, str]] = {
    AccessVerdict.AVAILABLE_HERE: ("Watchable here", "🟢"),
    AccessVerdict.AVAILABLE_VPN: ("Watchable with VPN", "🟡"),
    AccessVerdict.PURCHASE_ONLY: ("Available — rental or purchase", "🟣"),
    AccessVerdict.AVAILABLE_ELSEWHERE: ("Available — not with your subscriptions", "🟠"),
    AccessVerdict.UNAVAILABLE: ("Not available", "⚫"),
    AccessVerdict.UNKNOWN: ("Unknown", "⚪"),
    AccessVerdict.CHECKING: ("Checking availability…", "🔵"),
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


def build_user_access(user_id: int = 1) -> UserAccess:
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
    return access


# ---------------------------------------------------------------------------
# Country reach
# ---------------------------------------------------------------------------
@dataclass
class CountryReach:
    current_country: str = ""
    vpn_active: bool = False
    vpn_regions: set[str] = field(default_factory=set)

    def locality(self, country: str, is_scraped: bool = False) -> str:
        if is_scraped:
            return Locality.ANYWHERE
        c = (country or "").upper().strip()
        if not c:
            # No geography recorded: cannot claim it is out of reach.
            return Locality.ANYWHERE
        if self.current_country and c == self.current_country:
            return Locality.LOCAL
        if self.vpn_active and (not self.vpn_regions or c in self.vpn_regions):
            return Locality.VPN
        return Locality.OUT_OF_REACH

    def reachable(self, locality: str) -> bool:
        return locality != Locality.OUT_OF_REACH


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
    # A user who has configured explicit VPN regions has, by definition,
    # enabled VPN reach even before the flag exists on their row.
    vpn_active = vpn_enabled or bool(regions)
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
    #: Display price (structured amount + currency) or None. Price never
    #: affects ``is_entitled``/``is_accessible`` — it only tells the reader what
    #: a rent/buy offer would cost, which is orthogonal to access.
    price: Optional[dict] = None
    prices: list[dict] = field(default_factory=list)

    @property
    def is_qualifying_offer_type(self) -> bool:
        return self.access_type in INCLUDED_ACCESS_TYPES

    @property
    def is_current_country(self) -> bool:
        return self.locality in (Locality.LOCAL, Locality.ANYWHERE)

    @property
    def is_candidate_country(self) -> bool:
        return self.locality == Locality.VPN

    def to_dict(self) -> dict[str, Any]:
        return {
            "country": self.country,
            "service": self.service,
            "service_display": self.service_display,
            "source_indicator": self.source_indicator,
            "offer_type": self.offer_type,
            "access_type": self.access_type,
            "access_label": self.access_label,
            "quality": self.quality,
            "url": self.url,
            "audio_languages": self.audio_languages,
            "subtitle_languages": self.subtitle_languages,
            "is_scraped_source": self.is_scraped_source,
            "is_entitled": self.is_entitled,
            "is_language_match": self.is_language_match,
            "locality": self.locality,
            "is_accessible": self.is_accessible,
            "requires_payment": self.requires_payment,
            "is_addon": self.is_addon,
            "required_base": self.required_base,
            "required_base_display": self.required_base_display,
            "rejection_reasons": self.rejection_reasons,
            "price": self.price,
            "prices": self.prices,
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
        price=getattr(raw, "price", None),
        prices=list(getattr(raw, "prices", None) or []),
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
    audio = list(raw.audio_languages or [])
    subs = list(raw.subtitle_languages or [])
    has_lang_info = bool(audio or subs)

    presented = present_offer(raw, provider_map)
    scored = score_offer(raw, preference)
    is_lang_match = scored.is_match

    locality = reach.locality(country, is_scraped)
    requires_payment = access_type in PAYMENT_ACCESS_TYPES

    # An offer whose provider has a base platform is delivered as an add-on
    # channel. It is only entitled when the user holds *both* the channel and
    # that base platform: selecting the channel never grants the base, and
    # holding the base never grants every channel. The requirement is surfaced
    # on the offer itself (``required_base``) so the UI can say what is missing.
    clean_service = (service or "").lower().strip()
    provider = provider_map.get(clean_service) or provider_map.get(normalize_service_slug(clean_service))
    is_addon = False
    base_slug = ""
    base_display = ""
    if provider is not None:
        channel_base = getattr(provider, "addon_parent_slug", "") or ""
        if channel_base:
            base_provider = provider_map.get(channel_base) or provider_map.get(normalize_service_slug(channel_base))
            is_addon = True
            base_slug = channel_base
            base_display = (
                getattr(base_provider, "name", "") or channel_base
                if base_provider is not None
                else channel_base
            )
            if access_type == AccessType.SUBSCRIPTION:
                access_type = AccessType.ADDON

    if is_addon:
        base_entitled = access.entitles(base_slug, AccessType.SUBSCRIPTION)
        channel_entitled = access.entitles(service, AccessType.ADDON)
        entitled = base_entitled and channel_entitled
    else:
        base_entitled = True
        channel_entitled = True
        entitled = access.entitles(service, access_type)

    is_accessible = bool(
        entitled
        and is_lang_match
        and reach.reachable(locality)
        and access_type in INCLUDED_ACCESS_TYPES
    )

    reasons: list[str] = []
    if is_addon and not base_entitled:
        reasons.append(f"Requires {base_display or base_slug} (add-on channel)")
    if not entitled:
        if requires_payment:
            reasons.append(
                f"Only available to {offer_type} (not included in subscription)"
            )
        elif is_addon and not channel_entitled:
            reasons.append(
                f"No active subscription for {presented.service_display or service} "
                "(add-on channel)"
            )
        elif not is_addon:
            reasons.append(
                f"No active subscription for {presented.service_display or service}"
            )
    if is_lang_match is None:
        reasons.append("Language information unknown")
    elif is_lang_match is False:
        if not has_lang_info:
            reasons.append("Language information unknown")
        else:
            reasons.append("Does not satisfy language rules")
    if locality == Locality.OUT_OF_REACH:
        cc = country or "?"
        if reach.vpn_regions:
            reasons.append(
                f"Available in {cc}, but {cc} is not in your configured VPN regions"
            )
        else:
            reasons.append(f"Available in {cc}, but no VPN regions are configured")

    return ClassifiedOffer(
        country=presented.country or country,
        service=service,
        service_display=presented.service_display,
        source_indicator=presented.source_indicator,
        offer_type=offer_type,
        access_type=access_type,
        access_label=ACCESS_TYPE_LABELS.get(access_type, ACCESS_TYPE_LABELS[AccessType.UNKNOWN]),
        quality=raw.quality or "",
        url=raw.url or "",
        audio_languages=audio,
        subtitle_languages=subs,
        is_scraped_source=is_scraped,
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
        price=raw.price,
        prices=list(raw.prices or []),
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
        }


def _empty_result(movie, refresh_state: Optional[str]) -> AccessResult:
    label, symbol = VERDICT_LABELS[AccessVerdict.UNKNOWN]
    if refresh_state == RefreshState.CHECKED:
        verdict, label, symbol = (
            AccessVerdict.UNAVAILABLE,
            *VERDICT_LABELS[AccessVerdict.UNAVAILABLE],
        )
        explanation = ["Availability was checked — no qualifying offers found for this title."]
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
        access = build_user_access(getattr(preference, "user_id", 1) or 1)

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
        if c.requires_payment and c.is_entitled and reach.reachable(c.locality)
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

    from tracker.services.ranking import compute_source_rank

    def _offer_sort_key(c: ClassifiedOffer):
        entitled_tier = 0
        if prefer_entitled and not c.is_entitled:
            entitled_tier = 1

        source_rank = compute_source_rank(source_priority, c.is_scraped_source, c.is_current_country)

        official_penalty = 0
        if prefer_official and c.is_scraped_source:
            official_penalty = 1

        local_penalty = 0
        if prefer_local and not c.is_current_country:
            local_penalty = 1

        clean_service = (c.service or "").lower().strip()
        provider_rank = 99
        if clean_service in provider_priority:
            provider_rank = provider_priority.index(clean_service)

        return (entitled_tier, source_rank, official_penalty, local_penalty, provider_rank, c.ranking_score)

    local.sort(key=_offer_sort_key)
    vpn.sort(key=_offer_sort_key)

    accessible = local + vpn
    excluded = [c for c in classified if c not in accessible]

    explanation: list[str] = []
    if local:
        verdict = AccessVerdict.AVAILABLE_HERE
    elif vpn:
        verdict = AccessVerdict.AVAILABLE_VPN
    elif purchase:
        verdict = AccessVerdict.PURCHASE_ONLY
        explanation = [
            "Only available for an additional payment (rent or buy) — no "
            "subscription or free option is accessible with your services."
        ]
    else:
        verdict = AccessVerdict.AVAILABLE_ELSEWHERE
        seen: set[str] = set()
        for c in excluded:
            for r in c.rejection_reasons:
                if r not in seen:
                    seen.add(r)
                    explanation.append(r)

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
