import django.db.models.deletion
from django.db import migrations, models


def snapshot_movie_pks(apps, schema_editor):
    audit = apps.get_model("tracker", "MovieIdentityAudit")
    audit.objects.using(schema_editor.connection.alias).update(movie_pk=models.F("movie_id"))


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0059_movie_canonical_title_movie_title_is_override_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="movieidentityaudit",
            name="movie_pk",
            field=models.BigIntegerField(null=True),
        ),
        migrations.RunPython(snapshot_movie_pks, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="movieidentityaudit",
            name="movie_pk",
            field=models.BigIntegerField(db_index=True),
        ),
        migrations.AlterField(
            model_name="movieidentityaudit",
            name="movie",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="identity_audits",
                to="tracker.movie",
            ),
        ),
    ]
