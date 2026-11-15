from django.db import migrations, models
import django.db.models.deletion


def install_immutable_ledger_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    tables = (
        "availability_availabilityexperiment",
        "availability_availabilityexperimentattempt",
        "availability_availabilityexperimentattemptoutcome",
        "availability_availabilityexperimentscope",
    )
    schema_editor.execute(
        """CREATE OR REPLACE FUNCTION availability_experiment_ledger_immutable()
        RETURNS trigger AS $$ BEGIN
          RAISE EXCEPTION 'availability experiment ledger is append-only';
        END; $$ LANGUAGE plpgsql"""
    )
    for index, table in enumerate(tables):
        schema_editor.execute(
            f"CREATE TRIGGER availability_experiment_ledger_immutable_{index} "
            f"BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION availability_experiment_ledger_immutable()"
        )


def remove_immutable_ledger_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    tables = (
        "availability_availabilityexperiment",
        "availability_availabilityexperimentattempt",
        "availability_availabilityexperimentattemptoutcome",
        "availability_availabilityexperimentscope",
    )
    for index, table in enumerate(tables):
        schema_editor.execute(
            f"DROP TRIGGER IF EXISTS availability_experiment_ledger_immutable_{index} ON {table}"
        )
    schema_editor.execute("DROP FUNCTION IF EXISTS availability_experiment_ledger_immutable()")


class Migration(migrations.Migration):
    dependencies = [("availability", "0014_wave2a_verified_mapping_acceptance")]

    operations = [
        migrations.CreateModel(
            name="AvailabilityExperiment",
            fields=[
                ("authorization_id", models.UUIDField(primary_key=True, serialize=False)),
                ("manifest_digest", models.CharField(max_length=64)),
                ("database_identity", models.CharField(max_length=255)),
                ("maximum_attempts", models.PositiveSmallIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="AvailabilityExperimentAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ordinal", models.PositiveSmallIntegerField()),
                ("scope_id", models.CharField(max_length=80)),
                ("source_slug", models.CharField(max_length=50)),
                ("operation", models.CharField(max_length=50)),
                ("method", models.CharField(max_length=8)),
                ("destination_host", models.CharField(max_length=255)),
                ("reserved_at", models.DateTimeField(auto_now_add=True)),
                ("experiment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attempts", to="availability.availabilityexperiment")),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("experiment", "ordinal"), name="uniq_avail_exp_attempt_ordinal")],
            },
        ),
        migrations.CreateModel(
            name="AvailabilityExperimentAttemptOutcome",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("outcome", models.CharField(choices=[("success", "Success"), ("failure", "Failure")], max_length=12)),
                ("http_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
                ("attempt", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="outcome", to="availability.availabilityexperimentattempt")),
            ],
        ),
        migrations.CreateModel(
            name="AvailabilityExperimentScope",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scope_id", models.CharField(max_length=80)),
                ("completed_at", models.DateTimeField(auto_now_add=True)),
                ("experiment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="scopes", to="availability.availabilityexperiment")),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("experiment", "scope_id"), name="uniq_avail_exp_scope")],
            },
        ),
        migrations.RunPython(install_immutable_ledger_guards, remove_immutable_ledger_guards),
    ]
