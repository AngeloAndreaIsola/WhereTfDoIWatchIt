"""Seed the CA country row required by the CBC Gem catalogue source.

The refresh service only persists offers for enabled ``availability.Country``
rows and never creates countries itself, but nothing before this migration
populated that table — live CBC Gem refreshes would silently skip every offer
with ``reason=unsupported_country``. CBC Gem is a Canada-only source, so this
seeds exactly ``CA``; seeding further countries remains an explicit
operational decision (the streaming path's wider gap is documented, not
papered over here).
"""
from django.db import migrations


def seed_canada(apps, schema_editor):
    Country = apps.get_model("availability", "Country")
    Country.objects.update_or_create(
        code="CA", defaults={"name": "Canada", "enabled": True}
    )


def unseed_canada(apps, schema_editor):
    # Only remove the row this migration is known to own (name untouched by
    # anything else in this migration's lifetime).
    Country = apps.get_model("availability", "Country")
    Country.objects.filter(code="CA", name="Canada").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("availability", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_canada, unseed_canada),
    ]
