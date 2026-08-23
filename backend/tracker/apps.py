from django.apps import AppConfig
from django.db.models.signals import post_migrate


def seed_providers_signal(sender, **kwargs):
    from tracker.models import Provider, ExternalProviderMapping, Preference
    Preference.objects.get_or_create(user_id=1)

    MAJOR_PROVIDERS = [
        {"slug": "prime", "name": "Prime Video", "is_major": True, "icon_name": "prime", "streaming_availability_id": "prime"},
        {"slug": "netflix", "name": "Netflix", "is_major": True, "icon_name": "netflix", "streaming_availability_id": "netflix"},
        {"slug": "disney", "name": "Disney+", "is_major": True, "icon_name": "disney", "streaming_availability_id": "disney"},
        {"slug": "hulu", "name": "Hulu", "is_major": True, "icon_name": "hulu", "streaming_availability_id": "hulu"},
        {"slug": "max", "name": "Max", "is_major": True, "icon_name": "max", "streaming_availability_id": "hbo"},
        {"slug": "apple", "name": "Apple TV+", "is_major": True, "icon_name": "apple", "streaming_availability_id": "apple"},
        {"slug": "paramount", "name": "Paramount+", "is_major": True, "icon_name": "paramount", "streaming_availability_id": "paramount"},
        {"slug": "peacock", "name": "Peacock", "is_major": True, "icon_name": "peacock", "streaming_availability_id": "peacock"},
        {"slug": "youtubetv", "name": "YouTube TV", "is_major": True, "icon_name": "youtubetv", "streaming_availability_id": "youtubetv"},
        {"slug": "crunchyroll", "name": "Crunchyroll", "is_major": True, "icon_name": "crunchyroll", "streaming_availability_id": "crunchyroll"},
    ]
    SCRAPER_PROVIDERS = [
        {"slug": "kisskh", "name": "KissKH", "category": "scraper", "is_major": False, "icon_name": "kisskh"},
        {"slug": "hydrahd", "name": "HydraHD", "category": "scraper", "is_major": False, "icon_name": "hydrahd"},
        {"slug": "animeworld", "name": "AnimeWorld", "category": "scraper", "is_major": False, "icon_name": "animeworld"},
    ]

    for item in MAJOR_PROVIDERS:
        p, _ = Provider.objects.get_or_create(
            slug=item["slug"],
            defaults={"name": item["name"], "category": "official", "is_major": True, "is_enabled": True, "icon_name": item["icon_name"]},
        )
        if "streaming_availability_id" in item:
            ExternalProviderMapping.objects.get_or_create(
                provider=p, integration_name="streaming_availability", defaults={"external_id": item["streaming_availability_id"]}
            )

    for item in SCRAPER_PROVIDERS:
        Provider.objects.get_or_create(
            slug=item["slug"],
            defaults={"name": item["name"], "category": "scraper", "is_major": False, "is_enabled": True, "icon_name": item["icon_name"]},
        )


class TrackerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tracker"

    def ready(self):
        post_migrate.connect(seed_providers_signal, sender=self)
