from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracker", "0049_scraped_content_type_identity")]

    operations = [
        migrations.AddField(
            model_name="scrapedcontentmapping",
            name="provider_content_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Stable native ID supplied by the provider, when available.",
                max_length=255,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="scrapedcontentmapping",
            name="uniq_scraped_content_mapping",
        ),
        migrations.AddConstraint(
            model_name="scrapedcontentmapping",
            constraint=models.UniqueConstraint(
                condition=models.Q(provider_content_id=""),
                fields=("provider", "scraped_title", "scraped_year", "scraped_content_type"),
                name="uniq_scraped_content_mapping_legacy",
            ),
        ),
        migrations.AddConstraint(
            model_name="scrapedcontentmapping",
            constraint=models.UniqueConstraint(
                condition=~models.Q(provider_content_id=""),
                fields=("provider", "provider_content_id", "scraped_content_type"),
                name="uniq_scraped_content_mapping_native",
            ),
        ),
    ]
