"""Add YouTube as a free streaming provider (Phase E).

Seeds the ``youtube`` provider row the YouTube availability source resolves
its offers against (``availability.services.refresh`` looks providers up by
slug): YouTube's free ad-supported movie program, global domain,
``country_code`` left empty like other global free providers (tubi) — the
per-offer country is decided by the collector's observed egress region.

Category follows the established convention: every free provider today
(tubi, pluto_tv, cbc_gem, ...) is stored as ``bvod`` after the 0020 refactor,
and the subscriptions page keys its free-provider section off that value.
"""
from django.db import migrations

FREE_PROVIDERS = [
    {
        "slug": "youtube",
        "name": "YouTube",
        "domain": "youtube.com",
        "country_code": "",
        "icon_name": "youtube",
    },
]


def seed_youtube_provider(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    has_config_field = any(field.name == "config" for field in Provider._meta.fields)

    for item in FREE_PROVIDERS:
        defaults = {
            "name": item["name"],
            "category": "bvod",
            "is_major": False,
            "is_enabled": True,
            "icon_name": item["icon_name"],
            "domain": item["domain"],
            "country_code": item["country_code"],
        }
        if has_config_field:
            defaults["config"] = {}

        provider, created = Provider.objects.get_or_create(
            slug=item["slug"],
            defaults=defaults,
        )
        if not created:
            provider.name = item["name"]
            provider.category = "bvod"
            provider.domain = item["domain"]
            provider.country_code = item["country_code"]
            provider.icon_name = item["icon_name"]
            provider.save()


def unseed_youtube_provider(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    slugs = [item["slug"] for item in FREE_PROVIDERS]
    # Only remove rows this migration is known to own.
    Provider.objects.filter(slug__in=slugs, name="YouTube", category="bvod").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0031_refreshrequest_priority_remap"),
    ]

    operations = [
        migrations.RunPython(seed_youtube_provider, unseed_youtube_provider),
    ]
