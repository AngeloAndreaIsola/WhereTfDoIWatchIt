"""Repeatable JustWatch → ``tracker.Provider`` catalogue synchronization (task 5).

What this is for
----------------
The service-access manager and the discovery filters both present providers
that the app can *actually* cover: a service is only useful if our data sources
recognise it. Rather than hard-code that list in the frontend — which drifts
every time JustWatch renames a package — this walks JustWatch's own provider
catalogue (one ``GetProviders`` call per country) and records, per market, which
JustWatch ``technicalName`` denotes which canonical ``tracker.Provider``.

It writes exactly three things and only ever *additively*:

1. **Provider rows** — created only for a mapped package that has no row yet,
   and created **disabled** (``is_enabled=False``). Enabling a newly discovered
   service stays an explicit operator decision: sync must never silently widen
   what the app offers. Existing rows are never mutated (name, category, icon,
   enabled flag and every user-facing field are left untouched). A verified
   add-on channel records its base platform in ``config["addon_parent"]``, so
   the access resolver can require *both* entitlements.
2. **``ExternalProviderMapping`` rows** — the country-scoped
   ``("justwatch", <technicalName>, <country>)`` identity the availability
   refresh service already resolves offers through
   (:meth:`availability.services.refresh.…​._provider`).
3. **``ProviderCataloguePackage`` rows** — one per ``(country, technicalName)``,
   *including the ones this app does not map*. An unresolved package is kept
   with an explicit reason code instead of being dropped, so "81 unmapped"
   becomes an inspectable list of decisions rather than a count.

Idempotency & user-selection safety
-----------------------------------
``get_or_create``/``update_or_create`` make a re-run a no-op on the fields that
matter. ``Subscription`` is never read or written here, so re-syncing cannot
drop, re-order or re-mode a user's services — the whole point of preserving
selections across refreshes. Newly discovered providers default to disabled.

Add-on channels
---------------
A package in :data:`~availability.sources.justwatch_providers.ADDON_PACKAGE_PARENT`
is an add-on to a base platform (Amazon Channels require Prime Video). Its
canonical provider is a *distinct* row whose ``parent`` is that base, so a
channel and its platform are separately selectable and separately required.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from availability.models import Country
from availability.sources.justwatch_client import JustWatchClient
from availability.sources.justwatch_providers import (
    KNOWN_ALIASES,
    addon_parent_slug_for_package,
    provider_slug_for_package,
)

from tracker.models import (
    ExternalProviderMapping,
    PackageReasonCode,
    PackageResolutionStatus,
    Provider,
    ProviderCataloguePackage,
    ProviderCategory,
)

logger = logging.getLogger(__name__)

#: JustWatch's source slug; the ``integration_name`` its mappings live under.
JUSTWATCH_INTEGRATION = "justwatch"

#: Monetization types that mean "this is not a streamable catalogue service".
#: A cinema listing is a real JustWatch package but outside the availability
#: domains this app tracks, so it is unresolved *by category*, not by mistake.
_NON_STREAMING_TYPES = frozenset({"CINEMA"})


def _category_for_package(package) -> str:
    """Best-effort ``ProviderCategory`` from a package's monetization types.

    Deliberately coarse: only the *strongest* signal is honoured, because a
    wrong category is worse than a conservative default. ``FLATRATE`` (all-you-
    can-watch) → OFFICIAL; otherwise ``ADS``/``FREE`` → AVOD; otherwise
    ``RENT``/``BUY`` → TVOD. Anything unrecognised falls back to OFFICIAL, the
    category that requires an explicit subscription and is therefore the safest
    to be wrong about.
    """
    types = {str(t).upper() for t in (getattr(package, "monetization_types", None) or [])}
    if "FLATRATE" in types or "FLATRATE_AND_BUY" in types:
        return ProviderCategory.OFFICIAL
    if "ADS" in types or "FREE" in types:
        return ProviderCategory.AVOD
    if "RENT" in types or "BUY" in types:
        return ProviderCategory.TVOD
    return ProviderCategory.OFFICIAL


def _upsert_provider(slug: str, package, parent_slug: str = "") -> tuple[Provider, bool]:
    """Return ``(provider, created)``; never mutates a user-facing field.

    ``parent_slug`` is recorded in ``config["addon_parent"]`` for a verified
    add-on channel (and only ever filled in when absent), so a re-sync cannot
    re-home a channel under a different platform. An existing provider keeps
    its name, category, icon and enabled flag untouched.
    """
    provider, created = Provider.objects.get_or_create(
        slug=slug,
        defaults={
            "name": (getattr(package, "clear_name", "") or slug).strip() or slug,
            "category": _category_for_package(package),
            "is_enabled": False,
            "is_major": False,
            "icon_name": slug,
            "config": {"addon_parent": parent_slug} if parent_slug else {},
        },
    )
    if parent_slug and not provider.addon_parent_slug:
        config = dict(provider.config or {})
        config["addon_parent"] = parent_slug
        provider.config = config
        provider.save(update_fields=["config"])
    return provider, created


def _upsert_mapping(provider: Provider, technical_name: str, country: str) -> bool:
    """Upsert the ``(provider, justwatch, country) -> technicalName`` mapping.

    Returns ``True`` when a new mapping row was created, ``False`` when it
    already existed (or was corrected in place). The unique constraint is on
    ``(provider, integration_name, country)``, so the market slot identifies the
    row and the technical name is the value we keep current.
    """
    mapping, created = ExternalProviderMapping.objects.get_or_create(
        provider=provider,
        integration_name=JUSTWATCH_INTEGRATION,
        country=country,
        defaults={"external_id": technical_name},
    )
    if not created and mapping.external_id != technical_name:
        mapping.external_id = technical_name
        mapping.save(update_fields=["external_id"])
    return created


def classify_unresolved_package(package) -> tuple[str, str]:
    """Return ``(reason_code, reason)`` for a package this app does not map.

    Pure and deterministic so the decision is testable without a database. The
    vocabulary is closed (``PackageReasonCode``) and the default is
    ``needs_manual_review`` — the honest answer when the source gives no signal
    that identifies the package, never a silent skip.
    """
    technical = str(getattr(package, "technical_name", "") or "").strip().lower()
    clear_name = str(getattr(package, "clear_name", "") or "").strip()
    types = {str(t).upper() for t in (getattr(package, "monetization_types", None) or [])}

    if not technical:
        return (
            PackageReasonCode.MISSING_IDENTITY,
            "The package response carried no technicalName, so it cannot be identified.",
        )
    if technical in KNOWN_ALIASES:
        return (
            PackageReasonCode.KNOWN_ALIAS,
            "Known alias that should resolve to an existing provider.",
        )
    if types and types <= _NON_STREAMING_TYPES:
        return (
            PackageReasonCode.UNSUPPORTED_OFFER_TYPE,
            "Cinema-only package: outside the streaming/rental domains this app tracks.",
        )
    # The source declares an Amazon Channel add-on explicitly in its display
    # name. Match on that marker only: a bare ``amazon`` prefix (the plain
    # storefront) or a generic "… Channel" (Tennis Channel) is not evidence of
    # a base platform, so those fall through to manual review.
    clear_lower = clear_name.lower()
    if "amazon channel" in clear_lower:
        return (
            PackageReasonCode.ADDON_PARENT_UNRESOLVED,
            "Source declares an add-on channel, but its base platform is not in the verified table.",
        )
    return (
        PackageReasonCode.NEEDS_MANUAL_REVIEW,
        "No source-declared identity: needs a manual decision before mapping.",
    )


def _upsert_package(
    package,
    *,
    country: str,
    provider: Provider | None,
    status: str,
    reason_code: str,
    reason: str,
) -> ProviderCataloguePackage:
    """Upsert the persistent observation of one package.

    ``first_seen`` never moves; ``last_seen`` advances on every sync that sees
    the package again. Resolution fields are refreshed because a package may be
    mapped by a later code change — the point of keeping the row is that the
    decision can be revisited.
    """
    technical = str(getattr(package, "technical_name", "") or "").strip().lower()
    resolved_slug = provider.slug if provider is not None else ""
    now = timezone.now()
    row, created = ProviderCataloguePackage.objects.get_or_create(
        country=country,
        technical_name=technical,
        defaults={
            "package_id": getattr(package, "package_id", None),
            "external_id": str(getattr(package, "id", "") or ""),
            "clear_name": getattr(package, "clear_name", "") or "",
            "short_name": getattr(package, "short_name", "") or "",
            "slug": getattr(package, "slug", "") or "",
            "monetization_types": list(getattr(package, "monetization_types", None) or []),
            "resolved_provider_slug": resolved_slug,
            "resolution_status": status,
            "reason_code": reason_code,
            "reason": reason,
            "first_seen": now,
            "last_seen": now,
        },
    )
    row.package_id = getattr(package, "package_id", None)
    row.external_id = str(getattr(package, "id", "") or "")
    row.clear_name = getattr(package, "clear_name", "") or ""
    row.short_name = getattr(package, "short_name", "") or ""
    row.slug = getattr(package, "slug", "") or ""
    row.monetization_types = list(getattr(package, "monetization_types", None) or [])
    row.resolved_provider_slug = resolved_slug
    row.resolution_status = status
    row.reason_code = reason_code
    row.reason = reason
    row.last_seen = now
    row.save(
        update_fields=[
            "package_id",
            "external_id",
            "clear_name",
            "short_name",
            "slug",
            "monetization_types",
            "resolved_provider_slug",
            "resolution_status",
            "reason_code",
            "reason",
            "last_seen",
            "updated_at",
        ]
    )
    return row


def _target_countries(countries=None, *, enabled_only: bool = True) -> list[str]:
    if countries:
        return [str(c).strip().upper() for c in countries if str(c).strip()]
    qs = Country.objects.all()
    if enabled_only:
        qs = qs.filter(enabled=True)
    return list(qs.order_by("code").values_list("code", flat=True))


def sync_provider_catalogue(
    countries=None,
    *,
    client=None,
    enabled_only: bool = True,
) -> dict:
    """Walk JustWatch's provider catalogue and reconcile the local one.

    ``countries`` limits the markets walked; when omitted every enabled
    ``availability.Country`` is used. ``client`` is an injection seam for tests
    (any object with ``providers(country) -> list[JustWatchPackage]``); it never
    touches the network by default.

    Returns a JSON-serializable report. Per country it records every mapped
    package and every mapping it created; across the run it aggregates the
    unresolved packages with an explicit reason code, the providers created
    (including add-on relationships), and any fetch errors — a failed fetch is
    reported as an error, never as an empty catalogue.
    """
    justwatch = client or JustWatchClient()
    country_codes = _target_countries(countries, enabled_only=enabled_only)

    report: dict = {
        "integration": JUSTWATCH_INTEGRATION,
        "countries": [],
        "packages_seen": 0,
        "mapped": [],
        "addons": [],
        "created_providers": [],
        "mappings_created": [],
        "errors": [],
        "unmapped": {},
        "resolution_counts": {
            PackageResolutionStatus.MAPPED: 0,
            PackageResolutionStatus.ALIAS: 0,
            PackageResolutionStatus.ADDON: 0,
            PackageResolutionStatus.UNRESOLVED: 0,
        },
    }
    touched_slugs: set[str] = set()
    unresolved_by_reason: dict[str, int] = {}

    for code in country_codes:
        try:
            packages = justwatch.providers(code)
        except Exception as exc:  # noqa: BLE001 - isolate one market's failure
            logger.warning(
                "justwatch provider catalogue: fetch failed for %s: %s", code, exc
            )
            report["errors"].append({"country": code, "error": str(exc)})
            continue

        report["countries"].append(code)

        for package in packages:
            report["packages_seen"] += 1
            slug = provider_slug_for_package(package)
            technical_name = str(getattr(package, "technical_name", "") or "").strip().lower()
            parent_slug = addon_parent_slug_for_package(package)

            if not slug:
                reason_code, reason = classify_unresolved_package(package)
                _upsert_package(
                    package,
                    country=code,
                    provider=None,
                    status=PackageResolutionStatus.UNRESOLVED,
                    reason_code=reason_code,
                    reason=reason,
                )
                report["resolution_counts"][PackageResolutionStatus.UNRESOLVED] += 1
                unresolved_by_reason[reason_code] = unresolved_by_reason.get(reason_code, 0) + 1
                entry = report["unmapped"].setdefault(
                    technical_name or "<unknown>",
                    {
                        "technical_name": technical_name,
                        "clear_name": getattr(package, "clear_name", "") or "",
                        "short_name": getattr(package, "short_name", "") or "",
                        "monetization_types": list(
                            getattr(package, "monetization_types", None) or []
                        ),
                        "reason_code": reason_code,
                        "reason": reason,
                        "countries": [],
                    },
                )
                if code not in entry["countries"]:
                    entry["countries"].append(code)
                logger.warning(
                    "justwatch provider catalogue: unresolved package %r (%r) in %s "
                    "reason=%s",
                    technical_name,
                    getattr(package, "clear_name", ""),
                    code,
                    reason_code,
                )
                continue

            provider, created = _upsert_provider(slug, package, parent_slug)
            if created:
                report["created_providers"].append(slug)
            touched_slugs.add(slug)
            if parent_slug:
                report["addons"].append(
                    {
                        "country": code,
                        "slug": slug,
                        "technical_name": technical_name,
                        "parent": parent_slug,
                    }
                )

            if _upsert_mapping(provider, technical_name, code):
                report["mappings_created"].append(f"{slug}:{code}")

            if technical_name in KNOWN_ALIASES:
                status = PackageResolutionStatus.ALIAS
                reason_code = PackageReasonCode.KNOWN_ALIAS
                reason = "Curated profile/alias: grants the canonical provider's entitlement."
            elif parent_slug:
                status = PackageResolutionStatus.ADDON
                reason_code = ""
                reason = f"Add-on channel of {parent_slug}."
            else:
                status = PackageResolutionStatus.MAPPED
                reason_code = ""
                reason = ""
            _upsert_package(
                package,
                country=code,
                provider=provider,
                status=status,
                reason_code=reason_code,
                reason=reason,
            )
            report["resolution_counts"][status] += 1

            report["mapped"].append(
                {
                    "country": code,
                    "slug": slug,
                    "technical_name": technical_name,
                    "parent": parent_slug or None,
                    "status": status,
                }
            )

    report["providers_touched"] = sorted(touched_slugs)
    report["unmapped"] = list(report["unmapped"].values())
    report["unmapped_count"] = len(report["unmapped"])
    report["unresolved_count"] = report["unmapped_count"]
    report["resolved_count"] = sum(
        report["resolution_counts"][s]
        for s in (
            PackageResolutionStatus.MAPPED,
            PackageResolutionStatus.ALIAS,
            PackageResolutionStatus.ADDON,
        )
    )
    report["unresolved_by_reason"] = dict(sorted(unresolved_by_reason.items()))
    return report


def run_sync_provider_catalogue(countries=None, *, client=None) -> dict:
    """Transactional wrapper: the whole reconciliation commits or rolls back."""
    with transaction.atomic():
        return sync_provider_catalogue(countries, client=client)
