"""DRF serializers for Phase 2 endpoints."""
from __future__ import annotations

from rest_framework import serializers

from .languages import CANONICAL_LANGUAGE_MAP, normalize_language_code
from django.db import models
from .models import (
    ExternalProviderMapping,
    Movie,
    Preference,
    Provider,
    ScrapedContentMapping,
    Subscription,
    WatchlistItem,
)


class MovieSerializer(serializers.ModelSerializer):
    class Meta:
        model = Movie
        fields = ["id", "tmdb_id", "title", "year"]


class ExternalProviderMappingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExternalProviderMapping
        fields = ["integration_name", "external_id"]


class ProviderSerializer(serializers.ModelSerializer):
    external_mappings = ExternalProviderMappingSerializer(many=True, read_only=True)
    is_subscribed = serializers.SerializerMethodField()
    scraped_content_count = serializers.SerializerMethodField()

    class Meta:
        model = Provider
        fields = [
            "id",
            "slug",
            "name",
            "category",
            "is_major",
            "is_enabled",
            "icon_name",
            "domain",
            "last_scraped_at",
            "is_subscribed",
            "scraped_content_count",
            "external_mappings",
        ]

    def get_is_subscribed(self, obj: Provider) -> bool:
        user_subscribed_ids = self.context.get("user_subscribed_ids")
        if user_subscribed_ids is not None:
            return obj.id in user_subscribed_ids
        return Subscription.objects.filter(user_id=1, provider=obj).exists()

    def get_scraped_content_count(self, obj: Provider) -> int:
        return obj.scraped_mappings.count()


class SubscriptionSerializer(serializers.ModelSerializer):
    provider = ProviderSerializer(read_only=True)
    provider_id = serializers.IntegerField(write_only=True, required=False)
    provider_slug = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = Subscription
        fields = ["id", "name", "provider", "provider_id", "provider_slug"]
        extra_kwargs = {
            "name": {"validators": [], "required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        provider_id = attrs.get("provider_id")
        provider_slug = attrs.get("provider_slug")
        name = attrs.get("name")

        provider = None
        if provider_id:
            try:
                provider = Provider.objects.get(id=provider_id)
            except Provider.DoesNotExist:
                raise serializers.ValidationError({"provider_id": "Provider not found."})
        elif provider_slug:
            try:
                provider = Provider.objects.get(slug__iexact=provider_slug.strip())
            except Provider.DoesNotExist:
                raise serializers.ValidationError({"provider_slug": "Provider not found."})
        elif name:
            trimmed = name.strip()
            provider = Provider.objects.filter(
                models.Q(name__iexact=trimmed) | models.Q(slug__iexact=trimmed)
            ).first()

        if not provider and not name:
            raise serializers.ValidationError("Either provider, provider_id, provider_slug, or name must be provided.")

        attrs["resolved_provider"] = provider
        return attrs


class ScrapedContentMappingSerializer(serializers.ModelSerializer):
    provider = ProviderSerializer(read_only=True)
    movie = MovieSerializer(read_only=True)

    class Meta:
        model = ScrapedContentMapping
        fields = [
            "id",
            "provider",
            "scraped_title",
            "scraped_year",
            "scraped_url",
            "movie",
            "is_confirmed",
            "confidence_score",
            "raw_payload",
            "created_at",
            "updated_at",
        ]


class MovieSerializer(serializers.ModelSerializer):
    class Meta:
        model = Movie
        fields = ["id", "tmdb_id", "title", "year"]


def validate_language_codes(codes: list) -> list[str]:
    cleaned = []
    seen = set()
    for raw in codes:
        if not isinstance(raw, str):
            raise serializers.ValidationError("Language codes must be strings.")
        raw_clean = raw.strip()
        if not raw_clean:
            continue
        canonical_code = normalize_language_code(raw_clean)
        if not canonical_code:
            valid_codes_str = ", ".join(sorted(CANONICAL_LANGUAGE_MAP.keys()))
            raise serializers.ValidationError(
                f"Invalid canonical language code or name '{raw}'. Must be one of: {valid_codes_str}."
            )
        if canonical_code not in seen:
            seen.add(canonical_code)
            cleaned.append(canonical_code)
    return cleaned


class PreferenceSerializer(serializers.ModelSerializer):
    audio_languages = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    subtitle_languages = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    candidate_countries = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )

    class Meta:
        model = Preference
        fields = ["audio_languages", "subtitle_languages", "candidate_countries"]

    def validate_audio_languages(self, value: list) -> list[str]:
        return validate_language_codes(value)

    def validate_subtitle_languages(self, value: list) -> list[str]:
        return validate_language_codes(value)

    def validate_candidate_countries(self, value: list) -> list[str]:
        cleaned = []
        seen = set()
        for raw in value:
            if not isinstance(raw, str):
                raise serializers.ValidationError("Country codes must be strings.")
            c = raw.strip().upper()
            if not c:
                continue
            if len(c) != 2 or not c.isalpha():
                raise serializers.ValidationError("Country must be a 2-letter ISO code.")
            if c not in seen:
                seen.add(c)
                cleaned.append(c)
        return cleaned


class WatchlistItemSerializer(serializers.ModelSerializer):
    movie = MovieSerializer(read_only=True)

    class Meta:
        model = WatchlistItem
        fields = ["id", "movie", "source", "added_at"]


class LanguageInfoSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=50)
    country = serializers.CharField(max_length=2)
    audio_languages = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    subtitle_languages = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )

    def validate_country(self, value: str) -> str:
        country = value.strip().upper()
        if len(country) != 2 or not country.isalpha():
            raise serializers.ValidationError("Country must be a 2-letter ISO code.")
        return country

    def validate_provider(self, value: str) -> str:
        provider = value.strip()
        if not provider:
            raise serializers.ValidationError("Provider is required.")
        return provider

    def validate_audio_languages(self, value: list) -> list[str]:
        return validate_language_codes(value)

    def validate_subtitle_languages(self, value: list) -> list[str]:
        return validate_language_codes(value)


class RankedOfferSerializer(serializers.Serializer):
    country = serializers.CharField()
    service = serializers.CharField()
    offer_type = serializers.CharField()
    quality = serializers.CharField(allow_blank=True)
    audio_languages = serializers.ListField(child=serializers.CharField())
    subtitle_languages = serializers.ListField(child=serializers.CharField())
    ranking = serializers.DictField()


class CountrySummarySerializer(serializers.Serializer):
    country = serializers.CharField()
    count = serializers.IntegerField()


class ServiceSummarySerializer(serializers.Serializer):
    service = serializers.CharField()
    count = serializers.IntegerField()


class WatchlistReportSummarySerializer(serializers.Serializer):
    total_movies = serializers.IntegerField()
    available_movies = serializers.IntegerField()
    unavailable_movies = serializers.IntegerField()
    countries = CountrySummarySerializer(many=True)
    services = ServiceSummarySerializer(many=True)


class WatchlistReportRowSerializer(serializers.Serializer):
    movie = MovieSerializer(read_only=True)
    available_countries = serializers.ListField(child=serializers.CharField())
    best_country = serializers.CharField(allow_null=True)
    best_service = serializers.CharField(allow_null=True)
