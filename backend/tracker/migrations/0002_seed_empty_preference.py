from django.db import migrations


def create_empty_preference(apps, schema_editor):
    Preference = apps.get_model("tracker", "Preference")
    if not Preference.objects.filter(user_id=1).exists():
        Preference.objects.create(user_id=1)


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_empty_preference, migrations.RunPython.noop),
    ]
