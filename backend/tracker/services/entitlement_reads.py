"""Bulk-load exact package mappings, viewer products and first-party assertions."""
from collections import defaultdict
from datetime import datetime

from django.utils import timezone

from tracker.models import EntitlementChannel, EntitlementCoverage, EntitlementPlan, ReviewedEntitlementPackage, ViewerMarketSelection

from .entitlement import CoverageRule, OfferFact, OfferKey, Selection, evaluate_entitlement, in_window
from .entitlement_selections import active_at, effective_plan_state


def _timestamp(value):
    if value:
        return datetime.fromisoformat(value)
    return None


class EntitlementContext:
    def __init__(self, *, as_of=None, legacy_holdings=()):
        self.as_of = as_of or timezone.now()
        self.legacy_holdings = frozenset(legacy_holdings)
        self.selections = defaultdict(dict)
        self.channels = set()
        self.packages = {}
        self.rules = defaultdict(list)

    def load(self, user_id, offers):
        lineages = [row.lineage for row in offers if row.lineage and row.lineage.package_id]
        if not lineages:
            return self
        # Use the whole request's bounded key set. The package key and profile
        # match below still compare exact tuples.
        packages = active_at(ReviewedEntitlementPackage.objects.filter(
            source__in={row.source for row in lineages},
            source_package_id__in={row.package_id for row in lineages},
            country__in={row.country for row in offers if row.country},
        ), self.as_of).select_related("channel")
        for package in packages:
            self.packages[package.source, package.country, package.source_package_id, package.offer_type] = package
        if not self.packages:
            return self

        providers = {
            package.channel.parent_provider if package.channel else package.provider_slug
            for package in self.packages.values() if package.offer_type in {"subscription", "addon"}
        }
        if not providers:
            return self
        products = {
            (p.provider_slug, p.billing_country, p.plan_id): p
            for p in EntitlementPlan.objects.filter(provider_slug__in=providers)
        }
        for profile in ViewerMarketSelection.objects.filter(
            user_id=user_id, provider_slug__in=providers,
        ).prefetch_related("channels"):
            slug, country = profile.provider_slug, profile.billing_country
            plan = products.get((slug, country, profile.plan_id))
            self.selections[slug][country] = Selection(
                slug, country, profile.holding, effective_plan_state(profile, plan, self.as_of),
                profile.plan_id, {row.package_id: row.holding for row in profile.channels.all()},
            )
        if not self.selections:
            return self

        self.channels = set(active_at(
            EntitlementChannel.objects.filter(parent_provider__in=providers), self.as_of,
        ).values_list("parent_provider", "country", "package_id"))
        rows = active_at(EntitlementCoverage.objects.filter(
            offer_identity__in={row.identity for row in lineages},
            offer_country__in={row.country for row in offers if row.country},
            package_id__in={p.pk for p in self.packages.values()},
            product_id__in={p.pk for p in products.values()},
            observed_at__lte=self.as_of, fresh_until__gt=self.as_of,
        ).exclude(provenance_reference=""), self.as_of).select_related("product")
        for row in rows:
            self.rules[row.offer_identity, row.package_id, row.variant_id, row.offer_country].append(row)
        return self

    def _selection(self, provider, country, current_country):
        profiles = self.selections.get(provider, {})
        # Prefer a declaration in the current country, then a sole declared
        # billing market. With several foreign profiles, the exact offer market
        # is unambiguous if the viewer declared that product there.
        chosen = profiles.get(current_country)
        if chosen is None:
            if len(profiles) == 1:
                chosen = next(iter(profiles.values()))
            else:
                chosen = profiles.get(country)
        if chosen is None and not profiles and provider in self.legacy_holdings:
            return Selection(provider, holding="yes")
        return chosen

    def evaluate(self, offer, provider, country, offer_type, current_country):
        lineage = offer.lineage
        package = self.packages.get((
            lineage.source, country, lineage.package_id, offer_type,
        )) if lineage else None
        if package and package.provider_slug != provider:
            package = None
        host = ""
        channel_id = ""
        if package and package.channel and offer_type in {"subscription", "addon"}:
            channel = package.channel
            if channel.country == country and in_window(self.as_of, channel.starts_at, channel.ends_at):
                host, channel_id = channel.parent_provider, channel.package_id
            else:
                package = None
        key = OfferKey(
            provider, country, offer_type, lineage.content_type if lineage else "",
            lineage.title_id if lineage else 0, lineage.source if lineage else "",
            lineage.package_id if lineage else "", lineage.variant_id if lineage else "",
            offer.season_number, offer.episode_number,
        )
        fact = OfferFact(
            key, offer.availability_evidence,
            package_resolved=bool(lineage and lineage.package_id and lineage.variant_id),
            source_supported=package is not None,
            observed_at=lineage.observed_at if lineage else None,
            source_label=package.source_label if package else "",
            starts_at=_timestamp(offer.available_from), ends_at=_timestamp(offer.available_until),
            host=host, channel=channel_id,
            conflicted=bool(lineage and lineage.conflicted),
            public_conditions_met=bool(package and package.public_conditions_met) if offer_type in {"free", "free_with_ads"} else True,
        )
        selection = self._selection(host or provider, country, current_country) if offer_type in {"subscription", "addon"} else None
        rules = []
        if package and selection and lineage:
            candidates = self.rules.get((lineage.identity, package.pk, lineage.variant_id, country), ())
            rules = [
                CoverageRule(key, row.product.billing_country, row.product.plan_id, row.effect,
                             row.observed_at, row.fresh_until, row.starts_at, row.ends_at,
                             row.source_label, row.portability)
                for row in candidates
                if row.product.provider_slug == (host or provider)
                and row.product.billing_country == selection.billing_country
                and row.product.plan_id == selection.plan_id
            ]
        if channel_id and selection and (host, selection.billing_country, channel_id) not in self.channels:
            selection = Selection(selection.provider, selection.billing_country, selection.holding,
                                  selection.plan_state, selection.plan_id)
        return evaluate_entitlement(fact, rules, selection, self.as_of)
