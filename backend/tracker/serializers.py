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