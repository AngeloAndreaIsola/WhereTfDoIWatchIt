import datetime as dt
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0002_seed_empty_preference"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApiCallCounter",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=True, verbose_name="ID")),
                ("service", models.CharField(max_length=50, unique=True)),
                ("call_date", models.DateField(default=dt.date.today)),
                ("count", models.IntegerField(default=0)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=["service"], name="uniq_apicallcounter_service"
                    ),
                ],
            },
        ),
    ]