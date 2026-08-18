"""DRF serializers for Phase 2 endpoints."""
from __future__ import annotations

from rest_framework import serializers

from .models import Movie, Preference, WatchlistItem


class MovieSerializer(serializers.ModelSerializer):
    class Meta:
        model = Movie
        fields = ["id", "tmdb_id", "title", "year"]


class PreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Preference
        fields = ["audio_languages", "subtitle_languages", "candidate_countries"]


class WatchlistItemSerializer(serializers.ModelSerializer):
    movie = MovieSerializer(read_only=True)

    class Meta:
        model = WatchlistItem
        fields = ["id", "movie", "source", "added_at"]


def validate_language_codes(codes: list) -> list[str]:
    cleaned = []
    seen = set()
    for raw in codes:
        if not isinstance(raw, str):
            raise serializers.ValidationError("Language codes must be strings.")
        code = raw.strip().lower()
        if not code:
            continue
        if len(code) != 2 or not code.isalpha():
            raise serializers.ValidationError(
                f"Invalid ISO 639-1 language code '{raw}'. Codes must be 2 letters."
            )
        if code not in seen:
            seen.add(code)
            cleaned.append(code)
    return cleaned


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


class WatchlistReportRowSerializer(serializers.Serializer):
    movie = MovieSerializer(read_only=True)
    available_countries = serializers.ListField(child=serializers.CharField())
    best_country = serializers.CharField(allow_null=True)
    best_service = serializers.CharField(allow_null=True)