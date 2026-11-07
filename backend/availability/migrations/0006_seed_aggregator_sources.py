"""Seed the two aggregator sources and their country-aware provider mappings.

Adds ``Source`` rows for the two aggregator integrations and the JustWatch
package-id mappings the availability domain needs to resolve an observed
offer onto a canonical ``tracker.Provider``.

Why explicit rows rather than code-only registration
----------------------------------------------------
The source registry (:mod:`availability.sources`) already connects a
``Source.slug`` to an implementation; the database row owns *configuration*
(type, enabled, per-source refresh interval). The refresh service creates the
row on first write (``get_or_create``), so seeding is not strictly required
for correctness — but seeding it means the row exists with the right
``type="aggregator"`` from the start, and an operator can disable a source
before it has ever written a row.

JustWatch is an ``aggregator``: it reports where a title streams but is not
the service that streams it. Watchmode likewise. This is the provenance /
identity split the availability model is built on — ``Source = JustWatch``
(where the information came from), ``Provider = Prime Video`` (where the user
watches) — and getting ``type`` wrong here would misreport it.

Country-scoped JustWatch package ids
------------------------------------
Every mapping below is written **per country** with the id measured on the
live endpoint, because the ids differ per market (``prv`` vs ``amp`` for Prime
Video). Only countries this project actually has enabled providers for are
seeded; an unmapped market simply resolves nothing rather than resolving
wrongly. ``external_id`` stores JustWatch's ``technicalName`` (the stable
``amazonprimevideo``) — the short 3-letter id (``prv``) is country-scoped in
the same way and is handled by the source's client-side filter, so the durable
mapping key is the technical name.
"""
from django.db import migrations

# JustWatch ``technicalName`` -> (canonical provider slug, countries measured
# on the live endpoint). Ids are per-market; a country absent here is not
# mapped rather than mapped wrongly.
JUSTWATCH_PACKAGE_MAPPINGS = [
    # International majors
    ("netflix", "netflix", ["US", "GB", "NL", "IT", "CA", "AU", "DE", "FR"]),
    ("amazonprimevideo", "prime", ["NL", "IT", "CA", "AU", "FR"]),
    ("amazonprime", "prime", ["US", "GB", "DE"]),
    ("disneyplus", "disney", ["US", "GB", "NL", "IT", "CA", "AU", "DE", "FR"]),
    ("appletvplus", "apple", ["US", "GB", "NL", "IT", "CA", "AU", "DE", "FR"]),
    ("max", "max", ["US", "GB", "NL", "IT", "AU", "DE", "FR"]),
    ("paramountplus", "paramount", ["US", "GB", "IT", "CA", "AU", "DE", "FR"]),
    ("peacocktv", "peacock", ["US"]),
    ("hulu", "hulu", ["US"]),
    ("crunchyroll", "crunchyroll", ["US", "GB", "NL", "IT", "CA", "AU", "DE", "FR"]),
    ("youtubetv", "youtubetv", ["US"]),
    ("youtubefree", "youtube", ["US", "GB"]),
    # Free/ad-supported services
    ("tubitv", "tubi", ["US", "GB", "CA"]),
    ("plutotv", "pluto_tv", ["US", "IT", "CA"]),
    ("pbs", "pbs", ["US"]),
    ("iview", "abc_iview", ["AU"]),
    ("sbsondemand", "sbs_ondemand", ["AU"]),
    ("npostart", "npo_start", ["NL"]),
    ("raiplay", "raiplay", ["IT"]),
    ("mediasetplay", "mediaset_infinity", ["IT"]),
    ("bbc", "bbc_iplayer", ["GB"]),
]

SOURCES = [
    ("justwatch", "JustWatch", "aggregator", 12 * 3600),
    ("watchmode", "Watchmode", "aggregator", 24 * 3600),
]


def seed_aggregator_sources(apps, schema_editor):
    Source = apps.get_model("availability", "Source")
    Provider = apps.get_model("tracker", "Provider")
    ExternalProviderMapping = apps.get_model("tracker", "ExternalProviderMapping")

    for slug, name, source_type, interval in SOURCES:
        Source.objects.update_or_create(
            slug=slug,
            defaults={
                "name": name,
                "type": source_type,
                "enabled": True,
                # Per-source freshness window, read by
                # ``availability.freshness.declared_freshness``; never a
                # single global TTL.
                "configuration": {"expected_refresh_interval_seconds": interval},
            },
        )

    providers = {
        provider.slug: provider
        for provider in Provider.objects.filter(
            slug__in={slug for _tech, slug, _countries in JUSTWATCH_PACKAGE_MAPPINGS}
        )
    }
    for technical_name, provider_slug, countries in JUSTWATCH_PACKAGE_MAPPINGS:
        provider = providers.get(provider_slug)
        if provider is None:
            # Never create a Provider here: an absent canonical provider means
            # the mapping is wrong, and inventing a row would duplicate a brand.
            continue
        for country in countries:
            ExternalProviderMapping.objects.get_or_create(
                provider=provider,
                integration_name="justwatch",
                country=country,
                defaults={"external_id": technical_name},
            )


def unseed_aggregator_sources(apps, schema_editor):
    Source = apps.get_model("availability", "Source")
    ExternalProviderMapping = apps.get_model("tracker", "ExternalProviderMapping")

    ExternalProviderMapping.objects.filter(integration_name="justwatch").delete()
    Source.objects.filter(
        slug__in=[slug for slug, _name, _type, _interval in SOURCES]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("availability", "0005_seed_phase_e_countries"),
        ("tracker", "0035_country_aware_external_provider_mapping"),
    ]

    operations = [
        migrations.RunPython(seed_aggregator_sources, unseed_aggregator_sources),
    ]