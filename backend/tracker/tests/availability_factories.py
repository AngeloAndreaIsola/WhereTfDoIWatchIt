"""Canonical availability fixtures for HTTP read tests."""
from availability.domain.enums import OfferType, SourceType
from availability.models import Country, Offer, Source
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
    source, _ = Source.objects.get_or_create(
        slug="test-local", defaults={"name": "Test local availability", "type": SourceType.API}
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
