"""Add Rakuten TV as an ad-supported provider (JustWatch ``wuaki``).

JustWatch reports Rakuten TV (formerly Wuaki.tv) under the ``technicalName``
``wuaki``. Until now that package was unmapped, so every Rakuten TV offer —
including the free-with-ads offer it carries for titles such as *Natural Born
Killers* in NL — was silently dropped before it could become an ``Offer``.

The provider row is what ``availability.services.refresh`` resolves a
``ProviderOffer.provider_id`` ("rakuten_tv") against, so it must exist for the
mapping in ``availability.sources.justwatch_providers`` to have any effect.

Category ``avod`` (ad-supported video on demand): the JustWatch offers this
mapping unlocks are ``ADS`` -> ``free_with_ads``. Rakuten TV also runs a
transactional store, but those ``BUY``/``RENT`` offers are excluded from
ranking by design, so the provider is classified by the access type that
actually counts. ``country_code`` is left empty: Rakuten TV operates across
many European markets, not one.
"""
from django.db import migrations

PROVIDERS = [
    {
        "slug": "rakuten_tv",
        "name": "Rakuten TV",
        "domain": "rakuten.tv",
        "country_code": "",
        "icon_name": "rakuten_tv",
        "category": "avod",
    },
]


def seed_rakuten_tv(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    has_config_field = any(field.name == "config" for field in Provider._meta.fields)

    for item in PROVIDERS:
        defaults = {
            "name": item["name"],
            "category": item["category"],
            "is_major": False,
            "is_enabled": True,
            "icon_name": item["icon_name"],
            "domain": item["domain"],
            "country_code": item["country_code"],
        }
        if has_config_field:
            defaults["config"] = {}

        provider, created = Provider.objects.get_or_create(
            slug=item["slug"], defaults=defaults
        )
        if not created:
            provider.name = item["name"]
            provider.category = item["category"]
            provider.domain = item["domain"]
            provider.country_code = item["country_code"]
            provider.icon_name = item["icon_name"]
            provider.save()


def unseed_rakuten_tv(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    slugs = [item["slug"] for item in PROVIDERS]
    # Only remove the row this migration owns.
    Provider.objects.filter(slug__in=slugs, name="Rakuten TV").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0039_refreshrun_abandoned_at"),
    ]

    operations = [
        migrations.RunPython(seed_rakuten_tv, unseed_rakuten_tv),
    ]
