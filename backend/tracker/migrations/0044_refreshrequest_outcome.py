from django.db import migrations, models


def backfill_title_not_found(apps, schema_editor):
    """Move the one historical definitive negative out of ``failed``.

    Other old ``done`` rows deliberately remain blank: older releases did not
    record whether a successful request found offers, so guessing would turn
    missing provenance into fabricated metadata. New writes always set it.
    """
    RefreshRequest = apps.get_model("tracker", "RefreshRequest")
    RefreshRequest.objects.filter(
        status="failed", last_error__startswith="UpstreamTitleNotFound"
    ).update(status="done", outcome="title_not_found")


class Migration(migrations.Migration):

    dependencies = [("tracker", "0043_apicallledger")]

    operations = [
        migrations.AddField(
            model_name="refreshrequest",
            name="outcome",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "Not recorded / legacy row"),
                    ("offers_found", "Offers found"),
                    ("no_offer", "Checked — no offer"),
                    ("title_not_found", "Upstream title not found"),
                ],
                db_index=True,
                default="",
                help_text="Definitive result of a successful source check; blank for failures or legacy rows.",
                max_length=20,
            ),
        ),
        migrations.RunPython(backfill_title_not_found, migrations.RunPython.noop),
    ]
