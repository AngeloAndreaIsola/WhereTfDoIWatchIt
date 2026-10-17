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
    # Rakuten TV was Wuaki.tv; JustWatch still uses the pre-rebrand id. Its
    # NL offers are ad-supported (``ADS`` -> ``free_with_ads``), so mapping it
    # is what lets a free-with-ads title surface instead of disappearing.
    "wuaki": "rakuten_tv",
}

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