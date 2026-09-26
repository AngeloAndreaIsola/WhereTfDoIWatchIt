"""DRF serializers for Phase 2 endpoints."""
from __future__ import annotations

from rest_framework import serializers

from .languages import CANONICAL_LANGUAGE_MAP, normalize_language_code
from django.db import models
from .models import (
    # ExternalProviderMapping,
    Movie,
    MovieWatchEvent,
    Preference,
    Provider,
    ScrapedContentMapping,
    Subscription,
    UserMovieData,
    WatchlistItem,
)


class MovieSerializer(serializers.ModelSerializer):
    class Meta:
        model = Movie
        fields = [
            "id",
            "tmdb_id",
            "title",
            "year",
            "content_type",
            "poster_path",
            "overview",
            "genres",
            "genre_ids",
            "cast",
            "director",
            "seasons_data",
            "runtime_minutes",
            "tmdb_rating",
            "first_air_date",
            "release_date",
        ]


class MovieDetailSerializer(MovieSerializer):
    pass


class ProviderSerializer(serializers.ModelSerializer):
    source_indicator = serializers.SerializerMethodField()
    has_scraper = serializers.SerializerMethodField()
    is_subscribed = serializers.SerializerMethodField()
    scraped_content_count = serializers.SerializerMethodField()

    class Meta:
        model = Provider
        fields = [
            "id",
            "slug",
            "name",
            "category",
            "is_enabled",
            "is_major",
            "source_indicator",
            "has_scraper",
            "is_subscribed",
            "scraped_content_count",
            # "external_mappings",
        ]

    def get_source_indicator(self, obj: Provider) -> str:
        return obj.source_indicator

    def get_is_subscribed(self, obj: Provider) -> bool:
        user_subscribed_ids = self.context.get("user_subscribed_ids")
        if user_subscribed_ids is not None:
            return obj.id in user_subscribed_ids
        return Subscription.objects.filter(user_id=1, provider__id=obj.id).exists()

    def get_scraped_content_count(self, obj: Provider) -> int:
        return obj.scraped_mappings.count()

    def get_has_scraper(self, obj: Provider) -> bool:
        from tracker.scrapers import resolve_scraper_class
        return resolve_scraper_class(obj.slug) is not None


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
            provider = Provider.objects.filter(id=provider_id).first()
        if not provider and provider_slug:
            provider = Provider.objects.filter(slug=provider_slug).first()
        if not provider and name:
            provider = Provider.objects.filter(name=name).first()

        if not provider and not name:
            raise serializers.ValidationError(
                "Either provider, provider_id, provider_slug, or name must be provided."
            )

        attrs["resolved_provider"] = provider
        return attrs


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
    class Meta:
        model = Preference
        fields = [
            "id",
            "user_id",
            "audio_languages",
            "subtitle_languages",
            "candidate_countries",
            "current_country",
            "language_rule",
        ]


class WatchlistItemSerializer(serializers.ModelSerializer):
    movie = MovieSerializer(read_only=True)

    class Meta:
        model = WatchlistItem
        fields = [
            "id",
            "movie",
            "user_id",
            "source",
            "added_at",
            "watched",
            "watched_at",
            "rating",
        ]


class LanguageInfoSerializer(serializers.Serializer):
    region = serializers.CharField(source="region.provider")


class RankedOfferSerializer(serializers.Serializer):
    provider_slug = serializers.CharField()
    provider_name = serializers.CharField()
    country = serializers.CharField()
    offer_type = serializers.CharField()
    quality = serializers.CharField(allow_null=True, allow_blank=True)
    price_info = serializers.JSONField(allow_null=True)
    audio_languages = serializers.ListField(child=serializers.CharField())
    subtitle_languages = serializers.ListField(child=serializers.CharField())
    is_available = serializers.BooleanField()
    last_scraped_at = serializers.DateTimeField(allow_null=True)
    score = serializers.FloatField()
    reasons = serializers.ListField(child=serializers.CharField())


class CountrySummarySerializer(serializers.Serializer):
    country = serializers.CharField()
    provider_count = serializers.IntegerField()
    total_score = serializers.FloatField()


class ServiceSummarySerializer(serializers.Serializer):
    service = serializers.CharField()
    provider_count = serializers.IntegerField()
    titles = serializers.IntegerField()


class WatchlistReportSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    watchable = serializers.IntegerField()
    only_rent_buy = serializers.IntegerField()
    unknown = serializers.IntegerField()
    not_in_db = serializers.IntegerField()


class WatchlistReportRowSerializer(serializers.Serializer):
    movie_id = serializers.IntegerField()
    title = serializers.CharField()
    year = serializers.IntegerField(allow_null=True)
    content_type = serializers.CharField()
    source = serializers.CharField()
    added_at = serializers.DateTimeField()
    best_country = serializers.CharField(allow_null=True)
    best_provider = serializers.CharField(allow_null=True)
    best_offer_type = serializers.CharField(allow_null=True)
    best_quality = serializers.CharField(allow_null=True)
    watchable = serializers.BooleanField()
    audio_languages = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    subtitle_languages = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    score = serializers.FloatField()
    reasons = serializers.ListField(child=serializers.CharField())


class UserMovieDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserMovieData
        fields = ["id", "movie", "is_watched", "rating", "review"]


class MovieWatchEventSerializer(serializers.ModelSerializer):
    movie = MovieSerializer(read_only=True)

    class Meta:
        model = MovieWatchEvent
        fields = [
            "id",
            "movie",
            "user_id",
            "watched_date",
            "rating",
            "review",
            "rewatch",
            "letterboxd_uri",
            "created_at",
        ]
