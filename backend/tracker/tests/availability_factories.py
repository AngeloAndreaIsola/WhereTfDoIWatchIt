"""Canonical availability fixtures for HTTP read tests."""
from availability.domain.enums import OfferType, SourceType
from availability.models import Country, Offer, Source
from availability.sources import DEFAULT_SOURCE_SLUG
from tracker.models import Provider, ProviderCategory


def local_offer(movie, *, country="US", service="netflix", offer_type="subscription",
                quality="", url="", audio_languages=None, subtitle_languages=None,
                season_number=None, episode_number=None, provider_content_id=""):
    provider, _ = Provider.objects.get_or_create(
        slug=service.lower(),
        defaults={"name": service.replace("_", " ").title(), "category": ProviderCategory.OFFICIAL},
    )
    availability_country, _ = Country.objects.get_or_create(
        code=country.upper(), defaults={"name": country.upper()}
    )
    # Canonical offers belong to the default title-targeted scope, so the
    # per-source freshness policy sees them where a real refresh writes them.
    source, _ = Source.objects.get_or_create(
        slug=DEFAULT_SOURCE_SLUG,
        defaults={"name": "Streaming Availability API", "type": SourceType.API},
    )
    kwargs = {}
    if movie.content_type == "series":
        kwargs["season_number"] = 1 if season_number is None else season_number
        kwargs["episode_number"] = 1 if episode_number is None else episode_number
    return Offer.objects.create(
        provider=provider,
        country=availability_country,
        source=source,
        content_type=movie.content_type,
        tmdb_id=movie.tmdb_id,
        offer_type=offer_type,
        quality=quality,
        provider_url=url,
        audio_languages=audio_languages,
        subtitle_languages=subtitle_languages,
        provider_content_id=provider_content_id,
        **kwargs,
    )


def reviewed_test_sources(now=None, *, slugs=None):
    """Synthetic permission records for tests that mock every outbound transport."""
    from datetime import timedelta

    from django.utils import timezone

    from availability.services.source_permissions import ENDPOINTS, PERMISSION_KEY

    now = now or timezone.now()
    operations = {
        "justwatch": ["search", "seasons", "catalogue"],
        "watchmode": ["search", "title_sources"],
        "streaming_availability": ["show_availability"],
    }
    for slug in slugs or ENDPOINTS:
        source, _ = Source.objects.get_or_create(slug=slug, defaults={"name": slug})
        source.configuration = {
            **source.configuration,
            PERMISSION_KEY: {
                "status": "authorized",
                "endpoint": ENDPOINTS[slug],
                "evidence_reference": f"synthetic-test-{slug}",
                "reviewed_by": "test fixture",
                "countries": ["AU", "CA", "GB", "IT", "JP", "NL", "US"],
                "operations": operations[slug],
                "valid_from": (now - timedelta(days=30)).isoformat(),
                "valid_until": (now + timedelta(days=30)).isoformat(),
                "maximum_requests": 100,
                "minimum_interval_seconds": 1,
            },
        }
        source.save(update_fields=["configuration"])
