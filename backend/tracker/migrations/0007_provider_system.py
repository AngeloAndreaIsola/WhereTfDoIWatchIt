# Generated migration for Provider system and seeding

from django.db import migrations, models
import django.db.models.deletion


MAJOR_PROVIDERS = [
    {
        "slug": "prime",
        "name": "Prime Video",
        "is_major": True,
        "icon_name": "prime",
        "streaming_availability_id": "prime",
    },
    {
        "slug": "netflix",
        "name": "Netflix",
        "is_major": True,
        "icon_name": "netflix",
        "streaming_availability_id": "netflix",
    },
    {
        "slug": "disney",
        "name": "Disney+",
        "is_major": True,
        "icon_name": "disney",
        "streaming_availability_id": "disney",
    },
    {
        "slug": "hulu",
        "name": "Hulu",
        "is_major": True,
        "icon_name": "hulu",
        "streaming_availability_id": "hulu",
    },
    {
        "slug": "max",
        "name": "Max",
        "is_major": True,
        "icon_name": "max",
        "streaming_availability_id": "hbo",
    },
    {
        "slug": "apple",
        "name": "Apple TV+",
        "is_major": True,
        "icon_name": "apple",
        "streaming_availability_id": "apple",
    },
    {
        "slug": "paramount",
        "name": "Paramount+",
        "is_major": True,
        "icon_name": "paramount",
        "streaming_availability_id": "paramount",
    },
    {
        "slug": "peacock",
        "name": "Peacock",
        "is_major": True,
        "icon_name": "peacock",
        "streaming_availability_id": "peacock",
    },
    {
        "slug": "youtubetv",
        "name": "YouTube TV",
        "is_major": True,
        "icon_name": "youtubetv",
        "streaming_availability_id": "youtubetv",
    },
    {
        "slug": "crunchyroll",
        "name": "Crunchyroll",
        "is_major": True,
        "icon_name": "crunchyroll",
        "streaming_availability_id": "crunchyroll",
    },
]

SCRAPER_PROVIDERS = [
    {
        "slug": "kisskh",
        "name": "KissKH",
        "category": "scraper",
        "is_major": False,
        "icon_name": "kisskh",
        "domain": "kisskh.co",
    },
    {
        "slug": "hydrahd",
        "name": "HydraHD",
        "category": "scraper",
        "is_major": False,
        "icon_name": "hydrahd",
        "domain": "hydrahd.ru",
    },
    {
        "slug": "animeworld",
        "name": "AnimeWorld",
        "category": "scraper",
        "is_major": False,
        "icon_name": "animeworld",
        "domain": "animeworld.ac",
    },
]


def seed_providers(apps, schema_editor):
    Provider = apps.get_model("tracker", "Provider")
    ExternalProviderMapping = apps.get_model("tracker", "ExternalProviderMapping")
    Subscription = apps.get_model("tracker", "Subscription")

    # Seed official major providers
    for item in MAJOR_PROVIDERS:
        provider, _ = Provider.objects.get_or_create(
            slug=item["slug"],
            defaults={
                "name": item["name"],
                "category": "official",
                "is_major": True,
                "is_enabled": True,
                "icon_name": item["icon_name"],
            },
        )
        if "streaming_availability_id" in item:
            ExternalProviderMapping.objects.get_or_create(
                provider=provider,
                integration_name="streaming_availability",
                defaults={"external_id": item["streaming_availability_id"]},
            )

    # Seed scraper providers
    for item in SCRAPER_PROVIDERS:
        Provider.objects.get_or_create(
            slug=item["slug"],
            defaults={
                "name": item["name"],
                "category": "scraper",
                "is_major": False,
                "is_enabled": True,
                "icon_name": item["icon_name"],
                "domain": item.get("domain", ""),
            },
        )

    # Migrate existing subscriptions by matching name or slug
    for sub in Subscription.objects.all():
        if not sub.provider and sub.name:
            matched_p = Provider.objects.filter(
                models.Q(name__iexact=sub.name.strip()) | models.Q(slug__iexact=sub.name.strip())
            ).first()
            if matched_p:
                sub.provider = matched_p
                sub.save()


def reverse_seed_providers(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0006_standardize_canonical_languages"),
    ]

    operations = [
        migrations.CreateModel(
            name="Provider",
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
                ("slug", models.SlugField(unique=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("official", "Official / Major Provider"),
                            ("scraper", "Scraper / Small Source"),
                        ],
                        default="official",
                        max_length=20,
                    ),
                ),
                ("is_major", models.BooleanField(default=False)),
                ("is_enabled", models.BooleanField(default=True)),
                ("icon_name", models.CharField(blank=True, default="", max_length=50)),
                ("domain", models.CharField(blank=True, default="", max_length=100)),
                ("last_scraped_at", models.DateTimeField(blank=True, null=True)),
            ],
        ),
        migrations.CreateModel(
            name="ExternalProviderMapping",
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
                ("integration_name", models.CharField(max_length=50)),
                ("external_id", models.CharField(max_length=100)),
                (
                    "provider",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="external_mappings",
                        to="tracker.provider",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="externalprovidermapping",
            constraint=models.UniqueConstraint(
                fields=("provider", "integration_name"),
                name="uniq_external_provider_mapping",
            ),
        ),
        migrations.CreateModel(
            name="ScrapedContentMapping",
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
                ("scraped_title", models.CharField(max_length=255)),
                ("scraped_year", models.IntegerField(blank=True, null=True)),
                (
                    "scraped_url",
                    models.URLField(blank=True, default="", max_length=500),
                ),
                ("is_confirmed", models.BooleanField(default=False)),
                ("confidence_score", models.FloatField(default=0.0)),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "movie",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="scraped_mappings",
                        to="tracker.movie",
                    ),
                ),
                (
                    "provider",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="scraped_mappings",
                        to="tracker.provider",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="scrapedcontentmapping",
            constraint=models.UniqueConstraint(
                fields=("provider", "scraped_title", "scraped_year"),
                name="uniq_scraped_content_mapping",
            ),
        ),
        migrations.AddField(
            model_name="subscription",
            name="provider",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="subscriptions",
                to="tracker.provider",
            ),
        ),
        migrations.AlterField(
            model_name="subscription",
            name="name",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AddConstraint(
            model_name="subscription",
            constraint=models.UniqueConstraint(
                fields=("user_id", "provider"),
                name="uniq_user_provider_subscription",
            ),
        ),
        migrations.RunPython(seed_providers, reverse_seed_providers),
    ]
