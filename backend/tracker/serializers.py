"""DRF serializers for Phase 2 endpoints."""
from __future__ import annotations

from rest_framework import serializers

from .languages import CANONICAL_LANGUAGE_MAP, normalize_language_code
from django.db import models
from .models import (
    ExternalProviderMapping,
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
        ]


class MovieDetailSerializer(serializers.ModelSerializer):
    user_data = serializers.SerializerMethodField()
    watch_events = serializers.SerializerMethodField()

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
            "user_data",
            "watch_events",
        ]

    def get_user_data(self, obj: Movie):
        user_data = UserMovieData.objects.filter(user_id=1, movie=obj).first()
        return UserMovieDataSerializer(user_data).data if user_data else None

    def get_watch_events(self, obj: Movie):
        events = MovieWatchEvent.objects.filter(user_id=1, movie=obj).order_by("-watched_date", "-created_at")
        return MovieWatchEventSerializer(events, many=True).data


class ExternalProviderMappingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExternalProviderMapping
        fields = ["integration_name", "external_id"]


class ProviderSerializer(serializers.ModelSerializer):
    external_mappings = ExternalProviderMappingSerializer(many=True, read_only=True)
    is_subscribed = serializers.SerializerMethodField()
    access_mode = serializers.SerializerMethodField()
    scraped_content_count = serializers.SerializerMethodField()
    source_indicator = serializers.SerializerMethodField()
    has_scraper = serializers.SerializerMethodField()
    # Square-card operational fields, batched by the subscriptions endpoint
    # (see tracker.services.provider_status): one aggregate pass for the whole
    # list instead of one query per card.
    refresh_kind = serializers.SerializerMethodField()
    source_slug = serializers.SerializerMethodField()
    last_run = serializers.SerializerMethodField()
    last_run_error = serializers.SerializerMethodField()
    available_items = serializers.SerializerMethodField()
    refreshing = serializers.SerializerMethodField()
    #: Add-on relationship: the channel's base platform (slug + display name)
    #: and whether this row is an add-on at all. A channel and its platform are
    #: separately selectable, so the UI needs both to group and to explain.
    is_addon = serializers.SerializerMethodField()
    parent = serializers.SerializerMethodField()
    parent_name = serializers.SerializerMethodField()

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
            "country_code",
            "last_scraped_at",
            "is_subscribed",
            "access_mode",
            "scraped_content_count",
            "source_indicator",
            "has_scraper",
            "refresh_kind",
            "source_slug",
            "last_run",
            "last_run_error",
            "available_items",
            "refreshing",
            "is_addon",
            "parent",
            "parent_name",
            "external_mappings",
        ]

    def _card_status(self, obj: Provider) -> dict:
        """Batched status from context; registry-only fallback without it.

        ``GET /api/providers/`` passes ``provider_status`` (every field, one
        fixed set of queries). Serializers built elsewhere — nested
        subscriptions, imports, the v2 catalogue — resolve only the
        registry-derived fields so they never issue per-object aggregates.
        """
        batched = self.context.get("provider_status")
        if batched is not None and obj.id in batched:
            return batched[obj.id]
        cache = self.context.setdefault("_card_status_fallback", {})
        if obj.id not in cache:
            from tracker.services.provider_status import minimal_card_status

            cache[obj.id] = minimal_card_status(obj)
        return cache[obj.id]

    def get_refresh_kind(self, obj: Provider):
        return self._card_status(obj)["refresh_kind"]

    def get_source_slug(self, obj: Provider):
        return self._card_status(obj)["source_slug"]

    def get_last_run(self, obj: Provider):
        return self._card_status(obj)["last_run"]

    def get_last_run_error(self, obj: Provider):
        return self._card_status(obj)["last_run_error"]

    def get_available_items(self, obj: Provider):
        return self._card_status(obj)["available_items"]

    def get_refreshing(self, obj: Provider):
        return self._card_status(obj)["refreshing"]

    def get_source_indicator(self, obj: Provider) -> str:
        return obj.source_indicator

    def get_is_subscribed(self, obj: Provider) -> bool:
        user_subscribed_ids = self.context.get("user_subscribed_ids")
        if user_subscribed_ids is not None:
            return obj.id in user_subscribed_ids
        return Subscription.objects.filter(user_id=1, provider__id=obj.id).exists()

    def get_access_mode(self, obj: Provider):
        """The user's declared access mode for this provider (or ``None``)."""
        modes = self.context.get("user_access_modes")
        if modes is not None:
            return modes.get(obj.id)
        sub = Subscription.objects.filter(user_id=1, provider=obj).only("access_mode").first()
        return sub.access_mode if sub else None

    def get_scraped_content_count(self, obj: Provider) -> int:
        annotated = getattr(obj, "scraped_total", None)
        if annotated is not None:
            return annotated
        return obj.scraped_mappings.count()

    def get_has_scraper(self, obj: Provider) -> bool:
        from tracker.scrapers import resolve_scraper_class
        return resolve_scraper_class(obj.slug) is not None

    def get_is_addon(self, obj: Provider) -> bool:
        return obj.is_addon

    def get_parent(self, obj: Provider):
        """Canonical slug of the base platform, or ``None`` for standalone."""
        return obj.addon_parent_slug or None

    def get_parent_name(self, obj: Provider):
        slug = obj.addon_parent_slug
        if not slug:
            return None
        names = self.context.get("provider_names")
        if names is not None:
            return names.get(slug, slug)
        return (
            Provider.objects.filter(slug=slug).values_list("name", flat=True).first()
            or slug
        )


class SubscriptionSerializer(serializers.ModelSerializer):
    provider = ProviderSerializer(read_only=True)
    provider_id = serializers.IntegerField(write_only=True, required=False)
    provider_slug = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = Subscription
        fields = ["id", "name", "provider", "provider_id", "provider_slug", "access_mode"]
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
            "country_code",
            "movie",
            "is_confirmed",
            "confidence_score",
            "raw_payload",
            "created_at",
            "updated_at",
            "last_scraped_at",
            "is_available",
        ]


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
    current_country = serializers.CharField(max_length=2, required=False, allow_blank=True)
    language_rule = serializers.JSONField(required=False, allow_null=True)
    vpn_enabled = serializers.BooleanField(required=False)

    class Meta:
        model = Preference
        fields = ["audio_languages", "subtitle_languages", "candidate_countries", "current_country", "language_rule", "vpn_enabled"]

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

    def validate_current_country(self, value: str) -> str:
        country = value.strip().upper()
        if country and (len(country) != 2 or not country.isalpha()):
            raise serializers.ValidationError("Country must be a 2-letter ISO code.")
        return country

    def validate_language_rule(self, value):
        if value is None:
            return value
        if not isinstance(value, dict):
            raise serializers.ValidationError("Language rule must be a JSON object.")

        rule_type = value.get("type")
        if rule_type == "viewers":
            viewers = value.get("viewers")
            if not isinstance(viewers, list):
                raise serializers.ValidationError("Viewers field must be a list.")
            for viewer in viewers:
                if not isinstance(viewer, dict):
                    raise serializers.ValidationError("Each viewer must be an object.")
                langs = viewer.get("languages")
                if langs is not None:
                    if not isinstance(langs, list):
                        raise serializers.ValidationError("Viewer languages must be a list.")
                    viewer["languages"] = validate_language_codes(langs)
        return value


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


class MovieWatchEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = MovieWatchEvent
        fields = ["id", "watched_date", "rating", "review", "rewatch", "created_at"]


class UserMovieDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserMovieData
        fields = ["is_watched", "rating", "review", "updated_at"]


class RankedOfferSerializer(serializers.Serializer):
    country = serializers.CharField()
    service = serializers.CharField()
    offer_type = serializers.CharField()
    quality = serializers.CharField(allow_blank=True)
    url = serializers.CharField(allow_blank=True, required=False, default="")
    audio_languages = serializers.ListField(child=serializers.CharField())
    subtitle_languages = serializers.ListField(child=serializers.CharField())
    ranking = serializers.DictField()
    #: Structured price (amount + currency + provenance) or null. Present so the
    #: legacy ranked-offers endpoint does not silently drop what the canonical
    #: read path carries.
    price = serializers.DictField(required=False, allow_null=True)
    prices = serializers.ListField(child=serializers.DictField(), required=False)


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
