"""Make ``ExternalProviderMapping`` country-aware.

Why this change
---------------
``ExternalProviderMapping`` originally held one external id per
``(provider, integration_name)``. That is fine for an integration whose ids
are country-independent (Streaming Availability), but it is *factually wrong*
for an aggregator that renames providers per market. Measured against the live
JustWatch API:

    Amazon Prime Video  ->  amazonprimevideo / "prv"   in NL, IT, CA, AU
    Amazon Prime Video  ->  amazonprime      / "amp"   in US, GB, DE

A single global row can therefore only ever be right for some markets and
wrong for the rest, and a lookup for ``amazonprime`` in NL silently resolves
to nothing (or, worse, to the wrong provider).

Schema change
-------------
- new ``country`` field, 2-letter uppercase market code, ``blank=True,
  default=""``. Empty means "global/unscoped", which is exactly how every
  existing row is interpreted — so the change is additive and no existing
  integration loses its mapping.
- uniqueness moves from ``(provider, integration_name)`` to
  ``(provider, integration_name, country)``: one id per provider, per
  integration, per market. An unscoped row and a per-country row coexist,
  which is what lets a country-independent id (Streaming Availability's
  ``prime``) sit alongside country-specific ones.
- lookup index ``(integration_name, country, external_id)``, in the order the
  availability refresh service resolves an observed provider id
  ("which provider does *this* integration call *that* external id *in this
  market*?").

Deliberately **not** done: widening uniqueness to
``(provider, integration_name, external_id)``. That would permit two
different external ids for the same provider+market+integration — exactly the
ambiguity a lookup must never face — and it would still not say which id is
the market-specific one.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0034_refreshrequest_batch_cataloguerefreshrequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="externalprovidermapping",
            name="country",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "ISO 3166-1 alpha-2 market this external id belongs to. "
                    "Empty = global/unscoped."
                ),
                max_length=2,
            ),
        ),
        # Django 5.2's ``AlterConstraint.database_forwards`` is an empty
        # ``pass`` stub: it updates migration *state* only and never emits DDL.
        # Using it alone left the real table on the old
        # ``UNIQUE(provider_id, integration_name)`` uniqueness while Django
        # believed the change had been applied — which then made the
        # country-specific JustWatch seed rows in availability.0006 fail with
        # a duplicate-key error on a clean database. The operation is kept so
        # autodetected state still matches the model, and the two RunSQL
        # operations below perform the actual schema change.
        migrations.AlterConstraint(
            model_name="externalprovidermapping",
            name="uniq_external_provider_mapping",
            constraint=models.UniqueConstraint(
                fields=("provider", "integration_name", "country"),
                name="uniq_external_provider_mapping",
            ),
        ),
        # Forward: drop the 2-column constraint, then create the 3-column one.
        # Django 5.2's AlterConstraint emits no DDL, so these two RunSQL steps
        # are what actually change the table.
        migrations.RunSQL(
            sql=(
                "ALTER TABLE tracker_externalprovidermapping "
                "DROP CONSTRAINT IF EXISTS uniq_external_provider_mapping; "
                "ALTER TABLE tracker_externalprovidermapping "
                "ADD CONSTRAINT uniq_external_provider_mapping "
                "UNIQUE (provider_id, integration_name, country);"
            ),
            # Reversing restores the previous 2-column semantics in one step.
            # It cannot run before ``country`` is dropped while the 3-column
            # constraint still exists, so the drop of that constraint is part
            # of this same statement.
            reverse_sql=(
                "ALTER TABLE tracker_externalprovidermapping "
                "DROP CONSTRAINT IF EXISTS uniq_external_provider_mapping; "
                "ALTER TABLE tracker_externalprovidermapping "
                "ADD CONSTRAINT uniq_external_provider_mapping "
                "UNIQUE (provider_id, integration_name);"
            ),
        ),
        migrations.AddIndex(
            model_name="externalprovidermapping",
            index=models.Index(
                fields=["integration_name", "country", "external_id"],
                name="idx_extprov_integration_lookup",
            ),
        ),
    ]