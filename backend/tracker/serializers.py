"""DRF serializers for Phase 2 endpoints."""
from __future__ import annotations

from rest_framework import serializers

from .languages import CANONICAL_LANGUAGE_MAP, normalize_language_code
from .models import Movie, Preference, Subscription, WatchlistItem


class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = ["id", "name"]
        extra_kwargs = {
            "name": {
                "validators": [],
            }
        }

    def validate_name(self, value: str) -> str:
        trimmed = value.strip() if value else ""
        if not trimmed:
            raise serializers.ValidationError("Subscription name cannot be empty.")

        qs = Subscription.objects.filter(user_id=1, name__iexact=trimmed)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Subscription with this name already exists.")
        return trimmed


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
