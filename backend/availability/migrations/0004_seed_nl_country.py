"""Seed the NL country row required by the Prime Video catalogue source.

Prime Video serves the catalogue of the client's egress region and Phase 11
scoped the source to the territory it was researched against (``NL``), so
every offer it reports carries ``country="NL"``. The refresh service only
persists offers for enabled ``availability.Country`` rows and never creates
countries itself — without this row every live prime_video refresh would
silently skip its offers with ``reason=unsupported_country``.

Same rationale as ``0002_seed_ca_country`` (CBC Gem): seed exactly the country
one source declares, leave wider seeding an explicit operational decision.
"""
from django.db import migrations


def seed_netherlands(apps, schema_editor):
    Country = apps.get_model("availability", "Country")
    Country.objects.update_or_create(
        code="NL", defaults={"name": "Netherlands", "enabled": True}
    )


def unseed_netherlands(apps, schema_editor):
    # Only remove the row this migration is known to own (name untouched by
    # anything else in this migration's lifetime).
    Country = apps.get_model("availability", "Country")
    Country.objects.filter(code="NL", name="Netherlands").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("availability", "0003_providercontent_contentmapping_content_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_netherlands, unseed_netherlands),
    ]
