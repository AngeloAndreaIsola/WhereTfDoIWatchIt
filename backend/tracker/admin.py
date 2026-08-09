from django.contrib import admin
from django.contrib.postgres.fields import ArrayField

from .models import (
    Availability,
    LanguageEntry,
    LanguageInfo,
    Movie,
    Preference,
    Region,
    Subscription,
    WatchlistItem,
)


class LanguageEntryInline(admin.TabularInline):
    model = LanguageEntry
    extra = 1


@admin.register(LanguageInfo)
class LanguageInfoAdmin(admin.ModelAdmin):
    list_display = ("movie", "region")
    list_filter = ("region__provider",)
    search_fields = ("movie__title",)
    inlines = [LanguageEntryInline]


@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ("tmdb_id", "title", "year")
    search_fields = ("title",)


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ("provider", "country")
    list_filter = ("provider",)
    search_fields = ("provider",)


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
            field.help_text = "Comma-separated ISO 639-1 codes, first = highest priority."
        return field


@admin.register(WatchlistItem)
class WatchlistItemAdmin(admin.ModelAdmin):
    list_display = ("movie", "source", "added_at")
    search_fields = ("movie__title",)
