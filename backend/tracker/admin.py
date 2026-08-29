from django.contrib import admin
from django.contrib.postgres.fields import ArrayField

from .models import (
    ApiCallCounter,
    Availability,
    LanguageEntry,
    LanguageInfo,
    Movie,
    Preference,
    Provider,
    RefreshRun,
    Region,
    ScrapedContentMapping,
    Subscription,
    WatchlistItem,
)


class LanguageEntryInline(admin.TabularInline):
    model = LanguageEntry
    extra = 1
    help_text = "Language code must be a canonical 2-letter uppercase ISO code (e.g. 'EN', 'JA', 'ES')."


@admin.register(LanguageInfo)
class LanguageInfoAdmin(admin.ModelAdmin):
    list_display = ("movie", "region", "get_audio_count", "get_subtitle_count")
    list_filter = ("region__provider", "region__country")
    search_fields = ("movie__title", "region__provider", "region__country")
    inlines = [LanguageEntryInline]

    @admin.display(description="Audio Languages")
    def get_audio_count(self, obj):
        count = obj.entries.filter(kind="audio").count()
        return f"{count} language(s)"

    @admin.display(description="Subtitle Languages")
    def get_subtitle_count(self, obj):
        count = obj.entries.filter(kind="subtitle").count()
        return f"{count} language(s)"


@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ("tmdb_id", "title", "year")
    search_fields = ("title",)


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ("provider", "country")
    list_filter = ("provider",)
    search_fields = ("provider",)


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "category", "is_enabled", "is_major", "source_indicator", "last_scraped_at")
    list_filter = ("category", "is_enabled", "is_major")
    search_fields = ("name", "slug", "domain")


@admin.register(Availability)
class AvailabilityAdmin(admin.ModelAdmin):
    list_display = ("movie", "region", "is_available", "offer_type", "last_checked_at")
    list_filter = ("offer_type", "is_available")
    search_fields = ("movie__title",)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("name", "user_id")
    search_fields = ("name",)


@admin.register(Preference)
class PreferenceAdmin(admin.ModelAdmin):
    list_display = ("user_id", "audio_languages", "subtitle_languages", "candidate_countries")

    def formfield_for_dbfield(self, db_field, **kwargs):
        field = super().formfield_for_dbfield(db_field, **kwargs)
        if isinstance(db_field, ArrayField):
            field.help_text = "Comma-separated canonical 2-letter uppercase codes (e.g. EN, DE, FR), first = highest priority."
        return field


@admin.register(WatchlistItem)
class WatchlistItemAdmin(admin.ModelAdmin):
    list_display = ("movie", "source", "added_at")
    search_fields = ("movie__title",)


@admin.register(ApiCallCounter)
class ApiCallCounterAdmin(admin.ModelAdmin):
    list_display = ("service", "call_date", "count")
    list_filter = ("service", "call_date")


@admin.register(RefreshRun)
class RefreshRunAdmin(admin.ModelAdmin):
    list_display = (
        "service",
        "started_at",
        "finished_at",
        "rows_refreshed",
        "rows_skipped",
        "rate_limited",
    )
    list_filter = ("service", "rate_limited")
    readonly_fields = (
        "started_at",
        "finished_at",
        "rows_refreshed",
        "rows_skipped",
        "rate_limited",
    )


@admin.register(ScrapedContentMapping)
class ScrapedContentMappingAdmin(admin.ModelAdmin):
    list_display = ("scraped_title", "scraped_year", "provider", "movie", "is_confirmed", "confidence_score", "is_available")
    list_filter = ("provider__category", "provider__slug", "is_confirmed", "is_available")
    search_fields = ("scraped_title", "movie__title")
