"""Synthetic reviewed facts for ranking/reach tests that do not exercise ingestion."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace


from tracker.services.access import normalize_service_slug
from tracker.services.entitlement import Selection
from tracker.services.entitlement_reads import EntitlementContext
from tracker.services.offers import OfferLineage


def reviewed_access(offers, access, *, current_country, hosts=None, as_of=None):
    """Translate the test's declared memberships into explicit synthetic test products."""
    context = EntitlementContext(as_of=as_of)
    now = context.as_of
    start, end = now - timedelta(hours=1), now + timedelta(hours=1)
    hosts = hosts or {}
    holdings = {normalize_service_slug(slug) for slug in access.included}
    child_holdings = {normalize_service_slug(slug) for slug in access.addon}
    for index, offer in enumerate(offers, 1):
        provider = normalize_service_slug(offer.service)
        market = offer.country
        if not market:
            continue
        package_id, variant, identity = f"test-{index}", f"rendition-{index}", f"offer-{index}"
        offer.lineage = OfferLineage("movie", 910010, identity, "synthetic", package_id,
                                     variant_id=variant, observed_at=start)
        host = hosts.get(provider, "")
        channel = SimpleNamespace(
            parent_provider=host, country=market, package_id=provider, starts_at=start, ends_at=end,
        ) if host else None
        key = ("synthetic", market, package_id, offer.offer_type)
        context.packages[key] = SimpleNamespace(
            provider_slug=provider, channel=channel, source_label="Synthetic review",
            public_conditions_met=True, pk=index,
        )
        if offer.offer_type not in {"subscription", "addon"}:
            continue
        billing = current_country or market
        owner = host or provider
        holding = "yes" if owner in holdings else "no"
        context.selections[owner][billing] = Selection(
            owner, billing, holding, "selected", "fixture-plan",
            {provider: "yes" if provider in child_holdings else "no"} if host else {},
        )
        if host:
            context.channels.add((host, billing, provider))
        product = SimpleNamespace(provider_slug=owner, billing_country=billing, plan_id="fixture-plan")
        context.rules[identity, index, variant, market].append(SimpleNamespace(
            product=product, effect="allow", observed_at=start, fresh_until=end, starts_at=start,
            ends_at=end, source_label="Synthetic coverage", portability="allowed",
        ))
    return replace(access, entitlement=context)


def record_reviewed_offer(row, *, viewer=None, billing_country=None, parent=None, now=None):
    """Add an exact source snapshot and optional reviewed title-plan rule."""
    from availability.persistence.repositories import OfferPriceRepository
    from tracker.models import (
        EntitlementChannel, EntitlementCoverage, EntitlementPlan, ReviewedEntitlementPackage,
        ViewerChannelDeclaration, ViewerMarketSelection,
    )

    now = now or row.last_seen
    start = now - timedelta(days=10)
    provider, country = row.provider.slug, row.country.code
    billing_country = billing_country or country
    offer_type = row.offer_type
    source_package = f"fixture-{provider}-{offer_type}"
    variant = f"fixture-{row.pk}"
    url = row.provider_url
    snapshot = {
        "package_id": source_package,
        "source_offer_id": variant,
        "url": url,
        "presentation_type": row.quality,
        "available_from": row.available_from.isoformat() if row.available_from else None,
        "available_until": row.available_until.isoformat() if row.available_until else None,
    }
    OfferPriceRepository().record(
        offer=row, source=row.source, amount=None, currency="", now=now, variants=[snapshot],
    )
    channel = None
    if parent:
        channel, _ = EntitlementChannel.objects.get_or_create(
            parent_provider=parent, country=country, package_id=source_package,
            defaults={"starts_at": start, "display_name": "Synthetic channel"},
        )
    mapping, _ = ReviewedEntitlementPackage.objects.get_or_create(
        source=row.source.slug, country=country, source_package_id=source_package, offer_type=offer_type,
        defaults={"provider_slug": provider, "source_label": "Synthetic review",
                  "starts_at": start, "public_conditions_met": True, "channel": channel},
    )
    if channel and mapping.channel_id != channel.pk:
        mapping.channel = channel
        mapping.save(update_fields=["channel"])
    if viewer is None or offer_type not in {"subscription", "addon"}:
        return row
    owner = parent or provider
    plan, _ = EntitlementPlan.objects.get_or_create(
        provider_slug=owner, billing_country=billing_country, plan_id="fixture-plan",
        defaults={"starts_at": start, "display_name": "Synthetic plan", "plan_class": "base"},
    )
    selection, _ = ViewerMarketSelection.objects.get_or_create(
        user_id=viewer, provider_slug=owner, billing_country=billing_country,
        defaults={"holding": "yes", "plan_state": "selected", "plan_id": plan.plan_id, "revision": 1},
    )
    if channel:
        ViewerChannelDeclaration.objects.get_or_create(
            selection=selection, package_id=channel.package_id, defaults={"holding": "yes"},
        )
    EntitlementCoverage.objects.get_or_create(
        offer_identity=row.identity, package=mapping, product=plan, variant_id=variant,
        offer_country=country, effect="allow", starts_at=start, provenance_reference="synthetic fixture",
        defaults={"observed_at": now, "fresh_until": now + timedelta(days=1),
                  "source_label": "Synthetic title coverage", "portability": "allowed"},
    )
    return row
