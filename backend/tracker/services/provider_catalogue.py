"""Repeatable JustWatch → ``tracker.Provider`` catalogue synchronization (task 5).

What this is for
----------------
The service-access manager and the discovery filters both present providers
that the app can *actually* cover: a service is only useful if our data sources
recognise it. Rather than hard-code that list in the frontend — which drifts
every time JustWatch renames a package — this walks JustWatch's own provider
catalogue (one ``GetProviders`` call per country) and records, per market, which
JustWatch ``technicalName`` denotes which canonical ``tracker.Provider``.

It writes exactly two things and only ever *additively*:

1. **Provider rows** — created only for a mapped package that has no row yet,
   and created **disabled** (``is_enabled=False``). Enabling a newly discovered
   service stays an explicit operator decision: sync must never silently widen
   what the app offers. Existing rows are never mutated (name, category, icon,
   enabled flag and every user-facing field are left untouched).
2. **``ExternalProviderMapping`` rows** — the country-scoped
   ``("justwatch", <technicalName>, <country>)`` identity the availability
   refresh service already resolves offers through
   (:meth:`availability.services.refresh.…​._provider`).

Idempotency & user-selection safety
-----------------------------------
``get_or_create`` on both models makes a re-run a no-op. ``Subscription`` is
never read or written here, so re-syncing cannot drop, re-order or re-mode a
user's services — the whole point of preserving selections across refreshes.

Observability for unmapped packages
-----------------------------------
A package whose ``technicalName`` is not in :data:`PACKAGE_TO_PROVIDER_SLUG` is
**skipped and reported**, not guessed at: fuzzy display-name matching is how an
aggregator confidently attaches offers to the wrong service (see the note in
``justwatch_providers``). The returned report groups those by technical name so
an operator can extend the identity table deliberately.
"""
from __future__ import annotations

import logging

from django.db import transaction

from availability.models import Country
from availability.sources.justwatch_client import JustWatchClient
from availability.sources.justwatch_providers import provider_slug_for_package

from tracker.models import (
    ExternalProviderMapping,
    Provider,
    ProviderCategory,
)

logger = logging.getLogger(__name__)

#: JustWatch's source slug; the ``integration_name`` its mappings live under.
JUSTWATCH_INTEGRATION = "justwatch"


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


def _upsert_provider(slug: str, package) -> tuple[Provider, bool]:
    """Return ``(provider, created)``; never mutates an existing row."""
    return Provider.objects.get_or_create(
        slug=slug,
        defaults={
            "name": (getattr(package, "clear_name", "") or slug).strip() or slug,
            "category": _category_for_package(package),
            "is_enabled": False,
            "is_major": False,
            "icon_name": slug,
        },
    )


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
    unmapped packages (grouped by technical name, with the markets each was
    seen in), the providers created, and any fetch errors — a failed fetch is
    reported as an error, never as an empty catalogue.
    """
    justwatch = client or JustWatchClient()
    country_codes = _target_countries(countries, enabled_only=enabled_only)

    report: dict = {
        "integration": JUSTWATCH_INTEGRATION,
        "countries": [],
        "packages_seen": 0,
        "mapped": [],
        "created_providers": [],
        "mappings_created": [],
        "errors": [],
        "unmapped": {},
    }
    touched_slugs: set[str] = set()

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

            if not slug:
                entry = report["unmapped"].setdefault(
                    technical_name or "<unknown>",
                    {
                        "technical_name": technical_name,
                        "clear_name": getattr(package, "clear_name", "") or "",
                        "countries": [],
                    },
                )
                if code not in entry["countries"]:
                    entry["countries"].append(code)
                logger.warning(
                    "justwatch provider catalogue: unmapped package %r (%r) in %s",
                    technical_name,
                    getattr(package, "clear_name", ""),
                    code,
                )
                continue

            provider, created = _upsert_provider(slug, package)
            if created:
                report["created_providers"].append(slug)
            touched_slugs.add(slug)

            if _upsert_mapping(provider, technical_name, code):
                report["mappings_created"].append(f"{slug}:{code}")

            report["mapped"].append(
                {
                    "country": code,
                    "slug": slug,
                    "technical_name": technical_name,
                }
            )

    report["providers_touched"] = sorted(touched_slugs)
    report["unmapped"] = list(report["unmapped"].values())
    report["unmapped_count"] = len(report["unmapped"])
    return report


def run_sync_provider_catalogue(countries=None, *, client=None) -> dict:
    """Transactional wrapper: the whole reconciliation commits or rolls back."""
    with transaction.atomic():
        return sync_provider_catalogue(countries, client=client)
