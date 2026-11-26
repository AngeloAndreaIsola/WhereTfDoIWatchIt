"""Market product catalogue and atomic viewer declarations."""
import re
from collections import defaultdict

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework import serializers

from availability.models import Country
from tracker.models import EntitlementChannel, EntitlementPlan, ViewerChannelDeclaration, ViewerMarketSelection

from .entitlement import in_window


def viewer_id(request):
    """Use the authenticated account or the existing single-viewer default."""
    user = request.user
    if user.is_authenticated and user.pk is not None:
        return user.pk
    configured = getattr(settings, "WTDW_LEGACY_OWNER_ID", None)
    return configured if configured is not None else 1


def normalize_market(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z]{2}", value):
        raise serializers.ValidationError({"billing_country": "Expected a two-letter market."})
    country = value.upper()
    if not Country.objects.filter(code=country).exists():
        raise serializers.ValidationError({"billing_country": "Unknown billing market."})
    return country


def active_at(queryset, as_of):
    return queryset.filter(starts_at__lte=as_of).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=as_of))


def effective_plan_state(profile, plan, as_of):
    if profile.plan_state == "selected" and (
        plan is None or not in_window(as_of, plan.starts_at, plan.ends_at)
    ):
        return "unsupported"
    return profile.plan_state


def selection_data(profile, plan, as_of, legacy_holding="unspecified"):
    if profile is None:
        return {
            "holding": legacy_holding, "plan_state": "unspecified", "plan_id": None,
            "plan_display_name": None, "channels": [], "revision": 0,
        }
    return {
        "holding": profile.holding,
        "plan_state": effective_plan_state(profile, plan, as_of),
        "plan_id": profile.plan_id,
        "plan_display_name": plan.display_name if plan else None,
        "channels": [
            {"package_id": child.package_id, "holding": child.holding}
            for child in sorted(profile.channels.all(), key=lambda child: child.package_id)
        ],
        "revision": profile.revision,
    }


def selection_catalogue(providers, country, user_id, legacy_ids, as_of):
    plans = defaultdict(list)
    channels = defaultdict(list)
    slugs = [provider.slug for provider in providers]
    products = {
        (plan.provider_slug, plan.plan_id): plan for plan in EntitlementPlan.objects.filter(
            billing_country=country, provider_slug__in=slugs,
        ).order_by("plan_id")
    }
    for plan in products.values():
        if in_window(as_of, plan.starts_at, plan.ends_at):
            plans[plan.provider_slug].append({
                "plan_id": plan.plan_id, "display_name": plan.display_name, "class": plan.plan_class,
                "billing_country": country, "caveat": plan.caveat,
            })
    channel_options = active_at(
        EntitlementChannel.objects.filter(country=country, parent_provider__in=slugs), as_of,
    ).order_by("package_id")
    for channel in channel_options:
        channels[channel.parent_provider].append({
            "package_id": channel.package_id, "parent_provider": channel.parent_provider,
            "country": country, "display_name": channel.display_name, "caveat": channel.caveat,
        })
    profiles = {
        p.provider_slug: p for p in ViewerMarketSelection.objects.filter(
            user_id=user_id, billing_country=country, provider_slug__in=slugs,
        ).prefetch_related("channels")
    }
    data = []
    for provider in providers:
        slug = provider.slug
        profile = profiles.get(slug)
        data.append({
            "billing_country": country,
            "plan_options": plans[slug],
            "channel_options": channels[slug],
            "unlisted_plan_option": {
                "plan_id": None, "plan_state": "unsupported", "display_name": "I don't know / my plan isn't listed",
            },
            "selection": selection_data(
                profile, products.get((slug, profile.plan_id)) if profile else None, as_of,
                "yes" if provider.pk in legacy_ids else "unspecified",
            ),
        })
    return data


class ExactId(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str) or not data or data != data.strip():
            self.fail("invalid")
        return super().to_internal_value(data)


class ChannelInput(serializers.Serializer):
    package_id = ExactId(max_length=100)
    holding = serializers.ChoiceField(choices=["yes", "no"])


class SelectionInput(serializers.Serializer):
    holding = serializers.ChoiceField(choices=["yes", "no", "unspecified"])
    plan_state = serializers.ChoiceField(choices=["selected", "unspecified", "unsupported"])
    plan_id = ExactId(max_length=100, allow_null=True)
    channels = ChannelInput(many=True)
    revision = serializers.IntegerField(min_value=0, max_value=2**63 - 2)

    def validate_revision(self, value):
        if type(self.initial_data.get("revision")) is not int:
            raise serializers.ValidationError("An integer revision is required.")
        return value

    def validate(self, data):
        if data["plan_state"] == "selected":
            if data["holding"] != "yes" or data["plan_id"] is None:
                raise serializers.ValidationError("A selected plan requires a held base and an exact plan ID.")
        elif data["plan_id"] is not None:
            raise serializers.ValidationError("Use a null plan ID for an unlisted or unspecified plan.")
        ids = [row["package_id"] for row in data["channels"]]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError({"channels": "Duplicate channel declarations."})
        return data


@transaction.atomic
def replace_selection(provider, country, user, data):
    # Also used by legacy removal. The provider row exists even on first write,
    # so concurrent creations and legacy changes share a serialization point.
    type(provider).objects.select_for_update().get(pk=provider.pk)
    as_of = timezone.now()
    profile = ViewerMarketSelection.objects.filter(
        provider_slug=provider.slug, billing_country=country, user_id=user,
    ).prefetch_related("channels").first()
    state = {key: data[key] for key in ("holding", "plan_state", "plan_id")}
    children = {row["package_id"]: row["holding"] for row in data["channels"]}
    revision = profile.revision if profile else 0
    existing_plan = EntitlementPlan.objects.filter(
        provider_slug=provider.slug, billing_country=country, plan_id=profile.plan_id,
    ).first() if profile and profile.plan_id else None
    if profile and data["revision"] in {revision, revision - 1} and (
        state == {key: getattr(profile, key) for key in state}
        and children == {child.package_id: child.holding for child in profile.channels.all()}
    ):
        return selection_data(profile, existing_plan, as_of), False

    plan = None
    if state["plan_id"]:
        plan = active_at(EntitlementPlan.objects.filter(
            provider_slug=provider.slug, billing_country=country, plan_id=state["plan_id"],
        ), as_of).first()
        if plan is None:
            raise serializers.ValidationError({"plan_id": "Plan is unavailable for this provider and market."})
    if children:
        allowed = set(active_at(EntitlementChannel.objects.filter(
            parent_provider=provider.slug, country=country, package_id__in=children,
        ), as_of).values_list("package_id", flat=True))
        if children.keys() - allowed:
            raise serializers.ValidationError({"channels": "Channel is unavailable for this host and market."})
    if data["revision"] != revision:
        return selection_data(profile, existing_plan, as_of), True

    if profile is None:
        profile = ViewerMarketSelection.objects.create(
            provider_slug=provider.slug, billing_country=country, user_id=user, revision=1, **state,
        )
    else:
        for key, value in state.items():
            setattr(profile, key, value)
        profile.revision += 1
        profile.save(update_fields=[*state, "revision"])
        profile.channels.all().delete()
    ViewerChannelDeclaration.objects.bulk_create([
        ViewerChannelDeclaration(selection=profile, package_id=package_id, holding=holding)
        for package_id, holding in children.items()
    ])
    profile.refresh_from_db()
    return selection_data(profile, plan, as_of), False


def revoke_selections(provider_slug, user):
    """Caller holds the provider lock in the same transaction as legacy removal."""
    ViewerMarketSelection.objects.filter(
        provider_slug=provider_slug, user_id=user,
    ).exclude(holding="no", plan_state="unspecified", plan_id__isnull=True).update(
        holding="no", plan_state="unspecified", plan_id=None, revision=F("revision") + 1,
    )
