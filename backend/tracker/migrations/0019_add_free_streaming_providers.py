"""Add PBS, ABC iview, Mediaset Infinity, and CBC Gem as Free Streaming Providers.

Seeds:
- PBS (pbs, domain: pbs.org, country_code: US)
- ABC iview (abc_iview, domain: iview.abc.net.au, country_code: AU)
- Mediaset Infinity (mediaset_infinity, domain: mediasetinfinity.mediaset.it, country_code: IT)
- CBC Gem (cbc_gem, domain: gem.cbc.ca, country_code: CA)
"""

from django.db import migrations

FREE_PROVIDERS = [
    {
        "slug": "pbs",
        "name": "PBS",
        "domain": "pbs.org",
        "country_code": "US",
        "icon_name": "pbs",
    },
    {
        "slug": "abc_iview",
        "name": "ABC iview",
        "domain": "iview.abc.net.au",
        "country_code": "AU",
        "icon_name": "abc_iview",
    },
    {
        "slug": "mediaset_infinity",
        "name": "Mediaset Infinity",
        "domain": "mediasetinfinity.mediaset.it",
        "country_code": "IT",
        "icon_name": "mediaset_infinity",
    },
    {
        "slug": "cbc_gem",
        "name": "CBC Gem",
        "domain": "gem.cbc.ca",
        "country_code": "CA",
        "icon_name": "cbc_gem",
    },
]


def seed_new_free_providers(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    has_config_field = any(field.name == "config" for field in Provider._meta.fields)

    for item in FREE_PROVIDERS:
        defaults = {
            "name": item["name"],
            "category": "free",
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
            provider.category = "free"
            provider.domain = item["domain"]
            provider.country_code = item["country_code"]
            provider.icon_name = item["icon_name"]
            provider.save()


def reverse_seed_new_free_providers(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    slugs = [item["slug"] for item in FREE_PROVIDERS if item["slug"] != "cbc_gem"]
    Provider.objects.filter(slug__in=slugs, category="free").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0018_nhk_to_free_streaming_provider"),
    ]

    operations = [
        migrations.RunPython(seed_new_free_providers, reverse_seed_new_free_providers),
    ]
