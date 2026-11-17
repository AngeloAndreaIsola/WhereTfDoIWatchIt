"""Seed the countries the Phase E sources declare.

The refresh service only persists offers for enabled ``availability.Country``
rows and never creates countries itself. Catalogue sources get their declared
country scope initialized by ``_ensure_declared_countries`` at refresh time,
but seeding them here keeps proper display names (the runtime initializer
falls back to the bare code as the name) and makes the title-targeted path —
which has no such hook — work from the first run:

- ``youtube``'s five free-with-ads program countries (US/AU/JP/DE/GB): the
  collector only ever reports the observed egress country from that set, and
  a missing row would silently skip every offer with ``reason=unsupported_country``;
- ``pluto_tv``'s full web footprint from its own ``seoHreflang`` payload
  (32 countries; ``CA`` is already seeded by ``0002`` so it is left alone);
- ``mediaset_infinity``'s single-country scope (``IT``).

``pbs`` (``US``) and ``abc_iview`` (``AU``) are covered by the same rows, so
every Phase E declared country exists before any scheduled run.

Same rationale as ``0002_seed_ca_country``/``0004_seed_nl_country``: seed
exactly the countries these sources declare, leave wider seeding an explicit
operational decision.
"""
from django.db import migrations

PHASE_E_COUNTRIES = [
    ("AR", "Argentina"),
    ("AT", "Austria"),
    ("AU", "Australia"),
    ("BO", "Bolivia"),
    ("BR", "Brazil"),
    ("CH", "Switzerland"),
    ("CL", "Chile"),
    ("CO", "Colombia"),
    ("CR", "Costa Rica"),
    ("DE", "Germany"),
    ("DK", "Denmark"),
    ("DO", "Dominican Republic"),
    ("EC", "Ecuador"),
    ("FI", "Finland"),
    ("FR", "France"),
    ("GB", "United Kingdom"),
    ("GT", "Guatemala"),
    ("HN", "Honduras"),
    ("IE", "Ireland"),
    ("IT", "Italy"),
    ("JP", "Japan"),
    ("MX", "Mexico"),
    ("NI", "Nicaragua"),
    ("NO", "Norway"),
    ("PA", "Panama"),
    ("PE", "Peru"),
    ("PY", "Paraguay"),
    ("SE", "Sweden"),
    ("SV", "El Salvador"),
    ("US", "United States"),
    ("UY", "Uruguay"),
    ("VE", "Venezuela"),
]


def seed_phase_e_countries(apps, schema_editor):
    Country = apps.get_model("availability", "Country")
    for code, name in PHASE_E_COUNTRIES:
        Country.objects.update_or_create(
            code=code, defaults={"name": name, "enabled": True}
        )


def unseed_phase_e_countries(apps, schema_editor):
    # Only remove the rows this migration is known to own (name untouched by
    # anything else in this migration's lifetime).
    Country = apps.get_model("availability", "Country")
    for code, name in PHASE_E_COUNTRIES:
        Country.objects.filter(code=code, name=name).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("availability", "0004_seed_nl_country"),
    ]

    operations = [
        migrations.RunPython(seed_phase_e_countries, unseed_phase_e_countries),
    ]
