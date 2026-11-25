from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


LEDGER_TABLES = ("availability_availabilitystagedrun", "availability_availabilitystagedattemptevent")


def protect_stage_d_ledger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        for table in LEDGER_TABLES:
            schema_editor.execute(
                f"CREATE TRIGGER stage_d_ledger_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION availability_experiment_ledger_immutable()"
            )


def unprotect_stage_d_ledger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        for table in LEDGER_TABLES:
            schema_editor.execute(f"DROP TRIGGER stage_d_ledger_immutable ON {table}")


class Migration(migrations.Migration):

    dependencies = [
        ("availability", "0017_justwatch_request_budget"),
    ]

    operations = [
        migrations.CreateModel(
            name="AvailabilityStageDRun",
            fields=[
                (
                    "experiment",
                    models.OneToOneField(
                        primary_key=True,
                        serialize=False,
                        to="availability.availabilityexperiment",
                        on_delete=django.db.models.deletion.PROTECT,
                    ),
                ),
                (
                    "grant",
                    models.ForeignKey(
                        to="availability.justwatchrequestbudget",
                        on_delete=django.db.models.deletion.PROTECT,
                    ),
                ),
                ("initial_grant_attempts", models.PositiveIntegerField()),
            ],
        ),
        migrations.CreateModel(
            name="AvailabilityStageDAttemptEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("reservation", "Reservation"),
                            ("handoff", "Handoff"),
                            ("response", "Response"),
                            ("checkpoint", "Checkpoint"),
                        ],
                        max_length=11,
                    ),
                ),
                (
                    "occurred_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "evidence",
                    models.JSONField(default=dict),
                ),
                (
                    "attempt",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stage_d_events",
                        to="availability.availabilityexperimentattempt",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("attempt", "kind"),
                        name="uniq_stage_d_attempt_event",
                    ),
                ],
            },
        ),
        migrations.RunPython(protect_stage_d_ledger, unprotect_stage_d_ledger),
    ]
