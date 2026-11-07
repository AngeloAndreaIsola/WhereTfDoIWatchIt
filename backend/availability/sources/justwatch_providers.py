"""JustWatch package-id -> canonical ``tracker.Provider`` mapping.

Two responsibilities, deliberately separated from the source adapter:

1. **Static identity table** (:data:`PACKAGE_TO_PROVIDER_SLUG`) — application
   knowledge, in the same spirit as ``reseed_providers.MAJOR_PROVIDERS``: which
   canonical provider slug a JustWatch ``technicalName`` denotes. This is what
   lets ``DiscoveredContent`` (which carries no country) name a provider with a
   stable slug that the refresh service resolves to a ``Provider`` row.
2. **Country-scoped external ids** (:func:`country_package_map`) — the
   per-market 3-letter short names JustWatch accepts in a ``packages`` filter,
   measured on the live endpoint.

Why a table and not fuzzy display-name matching
-----------------------------------------------
JustWatch names are crowded with near-collisions that are *different
services*: ``amazonprimevideo`` (Prime Video) vs ``amazonprimevideowithads``
(Prime Video with Ads) vs ``amazonimdbtv`` (Prime Video Free with Ads) vs
``amazonhbomax`` (Max) vs ``max`` (Max) vs ``amazonmgm`` (MGM) vs
``appletvparamountplus`` (Paramount Plus on Apple TV). Matching on
``clearName`` similarity would confidently attach Max to ``amazonhbomax`` and
create wrong offers on the canonical model. Only ids confirmed on the live
endpoint are mapped; anything else is *unmapped and skipped*.

Per-market divergence is why this cannot be one flat table: Amazon Prime
Video is ``amazonprimevideo`` in NL/IT/CA/AU and ``amazonprime`` in US/GB/DE.
Both spellings therefore map to the same canonical ``prime`` slug, and the
*market-specific* short name for the ``packages`` filter is looked up per
country.
"""
from __future__ import annotations

#: Canonical provider slug for each JustWatch ``technicalName`` confirmed on
#: the live endpoint. Both Prime Video spellings are present on purpose: the
#: same brand, two ids, one provider row.
PACKAGE_TO_PROVIDER_SLUG: dict[str, str] = {
    # International majors
    "netflix": "netflix",
    "amazonprimevideo": "prime",
    "amazonprime": "prime",
    "disneyplus": "disney",
    "appletvplus": "apple",
    "max": "max",
    "paramountplus": "paramount",
    "peacocktv": "peacock",
    "hulu": "hulu",
    "crunchyroll": "crunchyroll",
    "youtubetv": "youtubetv",
    "youtubefree": "youtube",
    # Free / ad-supported
    "tubitv": "tubi",
    "plutotv": "pluto_tv",
    "pbs": "pbs",
    "iview": "abc_iview",
    "sbsondemand": "sbs_ondemand",
    "npostart": "npo_start",
    "raiplay": "raiplay",
    "mediasetplay": "mediaset_infinity",
    "bbc": "bbc_iplayer",
    # Netherlands — verified against the live ``GetProviders`` catalogue. Kept
    # per-service (never merged on display-name similarity): Videoland, Viaplay
    # and Ziggo are different services that merely share a market.
    "videoland": "videoland",
    "viaplay": "viaplay",
    # Viaplay Sports is its own package with its own FLATRATE entitlement, and
    # the source does not confirm that holding Viaplay grants it (or the
    # reverse). Kept as a *separate* canonical provider rather than an alias so
    # a Viaplay subscription never silently unlocks Viaplay Sports.
    "viaplaysports": "viaplay_sports",
    "skyshowtime": "skyshowtime",
    "canalplus": "canal_plus",
    "nlziet": "nlziet",
    "film1nl": "film1",
    "hayu": "hayu",
    "ziggo": "ziggo",
    "npoplus": "npo_plus",
    # Netherlands — niche / niche-adjacent VOD.
    "picl": "picl",
    "sooner": "sooner",
    "iffrunleashed": "iffr_unleashed",
    "pathe": "pathe_thuis",
    # Apple TV *Store* (rent/buy) is a different package from Apple TV+ (svod);
    # ``itunes`` is the storefront id, ``appletvplus`` the subscription above.
    "itunes": "apple_tv_store",
    # Specialist / documentary.
    "curiositystream": "curiosity_stream",
    "guidedoc": "guidedoc",
    "docsville": "docsville",
    "magellantv": "magellan_tv",
    "broadwayhd": "broadwayhd",
    "wowpresentsplus": "wow_presents_plus",
    # Free / ad-supported (NL).
    "justwatchtv": "justwatch_tv",
    "filmzie": "filmzie",
    "southpark": "south_park",
    # Netflix Kids is a *curated Netflix profile*, not a separate service or
    # entitlement: its titles are part of the Netflix subscription, so it maps
    # to the one canonical ``netflix`` provider rather than inventing a second.
    "netflixkids": "netflix",
    # Rakuten TV was Wuaki.tv; JustWatch still uses the pre-rebrand id. Its
    # NL offers are ad-supported (``ADS`` -> ``free_with_ads``), so mapping it
    # is what lets a free-with-ads title surface instead of disappearing.
    "wuaki": "rakuten_tv",
    # Add-on channels sold through a base platform. The technical names are
    # JustWatch's own (``<platform><channel>``) and the ``clearName`` ends in
    # "Amazon Channel" for every one of them, so the channel identity and its
    # parent are both source-declared, not guessed from a fuzzy name. Each is a
    # *distinct* canonical provider: holding the channel never grants the base
    # platform, and holding the base platform never grants the channel. The
    # parent is recorded separately in ``ADDON_PACKAGE_PARENT``.
    "amazonshorts": "shorts_tv_amazon",
    "amazonmgmplus": "mgm_plus_amazon",
    "amazonstingraykaraoke": "stingray_karaoke_amazon",
    "amazonciplay": "crime_investigation_play_amazon",
    "amazoncrunchyroll": "crunchyroll_amazon",
    "amazondizi": "dizi_amazon",
    "amazonhayu": "hayu_amazon",
    "amazonmubi": "mubi_amazon",
    "amazonlovenature": "love_nature_amazon",
    "amazonhopster": "hopster_amazon",
    "amazonacorntv": "acorn_tv_amazon",
    "amazondiscoveryplus": "discovery_plus_amazon",
    "amazonappletvplus": "apple_tv_amazon",
    "amazonnbalp": "nba_league_pass_amazon",
    "amazonviaplaysport": "viaplay_tv_amazon",
    "amazoncuriositystreamstandard": "curiosity_stream_amazon",
    "amazonhbomax": "max_amazon",
    "amazonlionsgateplus": "lionsgate_plus_amazon",
    "amazoncjenmselects": "cj_enm_selects_amazon",
    "amazonfear": "fear_amazon",
    "amazondocubay": "docubay_amazon",
}

#: ``technicalName`` -> canonical slug of the base platform a package is an
#: add-on *to*. Only ids confirmed on the live endpoint are listed; an add-on
#: whose parent is not source-declared stays unmapped (``addon_parent_unresolved``)
#: rather than being attached to a guessed base. Amazon Channels require Prime
#: Video (``prime``), which is the only delivery model this table asserts.
ADDON_PACKAGE_PARENT: dict[str, str] = {
    technical: "prime"
    for technical in (
        "amazonshorts",
        "amazonmgmplus",
        "amazonstingraykaraoke",
        "amazonciplay",
        "amazoncrunchyroll",
        "amazondizi",
        "amazonhayu",
        "amazonmubi",
        "amazonlovenature",
        "amazonhopster",
        "amazonacorntv",
        "amazondiscoveryplus",
        "amazonappletvplus",
        "amazonnbalp",
        "amazonviaplaysport",
        "amazoncuriositystreamstandard",
        "amazonhbomax",
        "amazonlionsgateplus",
        "amazoncjenmselects",
        "amazonfear",
        "amazondocubay",
    )
}

#: Technical names that are an *alias* of a mapped provider rather than a
#: separate service: JustWatch lists them as their own package, but they grant
#: the same entitlement as the canonical provider. Netflix Kids is a curated
#: profile inside the Netflix subscription, so it maps to ``netflix`` — it is
#: never a second subscription the user must buy.
KNOWN_ALIASES: frozenset[str] = frozenset({"netflixkids"})

#: JustWatch 3-letter ``shortName`` per (canonical slug, country), as measured.
#: Only the markets this project has mapped providers for are listed; the
#: ``packages`` filter is a *title* filter, so this only needs to be right for
#: the providers we intend to walk.
PACKAGE_SHORT_NAMES: dict[str, dict[str, str]] = {
    "netflix": {"US": "nfx", "GB": "nfx", "NL": "nfx", "IT": "nfx", "CA": "nfx",
                "AU": "nfx", "DE": "nfx", "FR": "nfx"},
    "prime": {"NL": "prv", "IT": "prv", "CA": "prv", "AU": "prv", "FR": "prv",
              "US": "amp", "GB": "amp", "DE": "amp"},
    "disney": {"US": "dnp", "GB": "dnp", "NL": "dnp", "IT": "dnp", "CA": "dnp",
               "AU": "dnp", "DE": "dnp", "FR": "dnp"},
    "apple": {"US": "atp", "GB": "atp", "NL": "atp", "IT": "atp", "CA": "atp",
              "AU": "atp", "DE": "atp", "FR": "atp"},
    "max": {"US": "mxx", "GB": "mxx", "NL": "mxx", "IT": "mxx", "AU": "mxx",
            "DE": "mxx", "FR": "mxx"},
    "paramount": {"US": "pmp", "GB": "pmp", "IT": "pmp", "CA": "pmp", "AU": "pmp",
                  "DE": "pmp", "FR": "pmp"},
    "peacock": {"US": "pct"},
    "hulu": {"US": "hlu"},
    "crunchyroll": {"US": "cru", "GB": "cru", "NL": "cru", "IT": "cru", "CA": "cru",
                    "AU": "cru", "DE": "cru", "FR": "cru"},
    "youtubetv": {"US": "ytt"},
    "youtube": {"US": "yfr", "GB": "yfr"},
    "tubi": {"US": "tbv", "GB": "tbv", "CA": "tbv"},
    "pluto_tv": {"US": "ptv", "IT": "ptv", "CA": "ptv"},
    "pbs": {"US": "pbs"},
    "abc_iview": {"AU": "ivw"},
    "sbs_ondemand": {"AU": "sbs"},
    "npo_start": {"NL": "npo"},
    "raiplay": {"IT": "rai"},
    "mediaset_infinity": {"IT": "msp"},
    "bbc_iplayer": {"GB": "bbc"},
    # Netherlands — measured on the live endpoint (task: complete the
    # subscriptions catalogue). Only NL is asserted here; other markets can be
    # added as they are measured, which keeps an unmeasured market meaning
    # "not walkable" rather than "walked with a guessed id".
    "videoland": {"NL": "vil"},
    "viaplay": {"NL": "vip"},
    "skyshowtime": {"NL": "sst"},
    "canal_plus": {"NL": "cpd"},
    "nlziet": {"NL": "nlz"},
    "film1": {"NL": "fm1"},
    "hayu": {"NL": "hay"},
    "ziggo": {"NL": "zig"},
    "npo_plus": {"NL": "npp"},
    "picl": {"NL": "pcl"},
    "sooner": {"NL": "snr"},
    "iffr_unleashed": {"NL": "ifr"},
    "pathe_thuis": {"NL": "pth"},
    "apple_tv_store": {"NL": "itu"},
    "curiosity_stream": {"NL": "cts"},
    "guidedoc": {"NL": "gdc"},
    "docsville": {"NL": "dsv"},
    "magellan_tv": {"NL": "mgl"},
    "broadwayhd": {"NL": "bhd"},
    "wow_presents_plus": {"NL": "wow"},
    "justwatch_tv": {"NL": "jwt"},
    "filmzie": {"NL": "fmz"},
    "south_park": {"NL": "spk"},
    "rakuten_tv": {"NL": "wki"},
    # Add-on channels (and the separated Viaplay Sports tier), measured on the
    # live NL catalogue. Recorded so these providers are walkable too — an
    # unmeasured market still means "not walkable", never a guessed id.
    "viaplay_sports": {"NL": "vps"},
    "shorts_tv_amazon": {"NL": "atv"},
    "mgm_plus_amazon": {"NL": "mgp"},
    "stingray_karaoke_amazon": {"NL": "skk"},
    "crime_investigation_play_amazon": {"NL": "cpa"},
    "crunchyroll_amazon": {"NL": "cra"},
    "dizi_amazon": {"NL": "dac"},
    "hayu_amazon": {"NL": "ahy"},
    "mubi_amazon": {"NL": "amu"},
    "love_nature_amazon": {"NL": "aln"},
    "hopster_amazon": {"NL": "ahs"},
    "acorn_tv_amazon": {"NL": "aac"},
    "discovery_plus_amazon": {"NL": "adp"},
    "apple_tv_amazon": {"NL": "avl"},
    "nba_league_pass_amazon": {"NL": "anb"},
    "viaplay_tv_amazon": {"NL": "vpa"},
    "curiosity_stream_amazon": {"NL": "acs"},
    "max_amazon": {"NL": "aho"},
    "lionsgate_plus_amazon": {"NL": "alg"},
    "cj_enm_selects_amazon": {"NL": "cje"},
    "fear_amazon": {"NL": "fer"},
    "docubay_amazon": {"NL": "ado"},
}


def provider_slug_for_package(package) -> str:
    """Canonical provider slug for a JustWatch package, or ``""`` if unmapped.

    Accepts a :class:`~availability.sources.justwatch_client.JustWatchPackage`
    or a bare technical-name string. An unmapped package answers ``""`` — the
    caller skips it and logs; it never falls back to a display name, because a
    near-miss name is how an aggregator attaches offers to the wrong service.
    """
    technical_name = getattr(package, "technical_name", package)
    return PACKAGE_TO_PROVIDER_SLUG.get(str(technical_name or "").strip().lower(), "")


def provider_slugs_for_packages(packages) -> set[str]:
    """Canonical slugs for a sequence of JustWatch packages (unmapped dropped)."""
    return {slug for slug in (provider_slug_for_package(p) for p in packages) if slug}


def addon_parent_slug_for_package(package) -> str:
    """Canonical slug of the base platform *package* is an add-on to, or ``""``.

    Empty means "not a source-declared add-on": either the package is
    standalone, or its parent is unknown. The two are deliberately not
    distinguished here — an add-on with no confirmed parent must not be
    attached to a guessed base, so callers treat ``""`` as "no requirement".
    """
    technical_name = getattr(package, "technical_name", package)
    return ADDON_PACKAGE_PARENT.get(str(technical_name or "").strip().lower(), "")


def is_addon_package(package) -> bool:
    """True when the package is a verified, source-declared add-on channel."""
    return bool(addon_parent_slug_for_package(package))


def is_alias_package(package) -> bool:
    """True when the package grants the same entitlement as another provider."""
    technical_name = getattr(package, "technical_name", package)
    return str(technical_name or "").strip().lower() in KNOWN_ALIASES


def package_short_name(provider_slug, country) -> str:
    """JustWatch ``shortName`` for *provider_slug* in *country*, or ``""``.

    Used to build the ``packages`` filter, which the endpoint accepts only as
    3-letter short names and silently ignores ids it does not recognise — so
    an unknown combination must yield ``""`` (no filter) rather than a guess.
    """
    market = str(country or "").strip().upper()
    return PACKAGE_SHORT_NAMES.get(str(provider_slug or "").strip().lower(), {}).get(market, "")


def country_package_map(provider_slugs, country) -> list[str]:
    """Short names for *provider_slugs* in *country*.

    Empty when none of the requested providers are mapped in that market, which
    the caller treats as "this partition is not walkable" rather than
    "this partition is empty".
    """
    names = []
    for slug in provider_slugs or ():
        name = package_short_name(slug, country)
        if name and name not in names:
            names.append(name)
    return names


def mapped_countries_for(provider_slug) -> list[str]:
    """Every market this provider has a measured JustWatch short name for."""
    return sorted(PACKAGE_SHORT_NAMES.get(str(provider_slug or "").lower(), {}))


def technical_names_for(provider_slug) -> list[str]:
    """Every JustWatch ``technicalName`` that denotes *provider_slug*.

    A slug can have more than one (Prime Video is ``amazonprimevideo`` *and*
    ``amazonprime``); both must map back to the same canonical provider, which
    is why the reverse direction matters as much as the forward one.
    """
    slug = str(provider_slug or "").strip().lower()
    return sorted(
        technical for technical, mapped in PACKAGE_TO_PROVIDER_SLUG.items()
        if mapped == slug
    )