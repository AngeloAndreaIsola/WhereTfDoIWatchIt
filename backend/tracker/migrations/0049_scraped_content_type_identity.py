from django.db import migrations, models


def copy_content_type(apps, schema_editor):
    Mapping = apps.get_model("tracker", "ScrapedContentMapping")
    for row in Mapping.objects.all().iterator(chunk_size=500):
        payload = row.raw_payload if isinstance(row.raw_payload, dict) else {}
        value = str(payload.get("content_type") or "movie").strip().lower()
        row.scraped_content_type = "series" if value in {"series", "show", "tv"} else "movie"
        row.save(update_fields=["scraped_content_type"])


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0047_cataloguediscoverystate"),
        ("tracker", "0048_extensioncapability_extensionhandshakechallenge_and_more"),
    ]
    operations = [
        migrations.AddField(
            model_name="scrapedcontentmapping",
            name="scraped_content_type",
            field=models.CharField(choices=[("movie", "Movie"), ("series", "Series")], default="movie", max_length=12),
        ),
        migrations.RunPython(copy_content_type, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="scrapedcontentmapping", name="uniq_scraped_content_mapping",
        ),
        migrations.AddConstraint(
            model_name="scrapedcontentmapping",
            constraint=models.UniqueConstraint(
                fields=("provider", "scraped_title", "scraped_year", "scraped_content_type"),
                name="uniq_scraped_content_mapping",
            ),
        ),
    ]
