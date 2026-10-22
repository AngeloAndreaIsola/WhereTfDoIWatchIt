from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0042_providercataloguepackage"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApiCallLedger",
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
                ("service", models.CharField(max_length=50)),
                ("day", models.DateField()),
                ("count", models.IntegerField(default=0)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=["service", "day"],
                        name="uniq_apicallledger_service_day",
                    ),
                ],
            },
        ),
    ]
