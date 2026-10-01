from django.contrib import admin

from .models import (
    ContentMapping,
    Country,
    Offer,
    ProviderContent,
    Source,
    SyncCursor,
    SyncRun,
)


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "enabled")
    list_filter = ("enabled",)
    search_fields = ("code", "name")


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "type", "enabled")
    list_filter = ("type", "enabled")
    search_fields = ("slug", "name")


@admin.register(SyncRun)
class SyncRunAdmin(admin.ModelAdmin):
    list_display = (
        "provider",
        "source",
        "country",
        "mode",
        "status",
        "authoritative",
        "started_at",
        "completed_at",
    )
    list_filter = ("mode", "status", "authoritative")
    search_fields = ("provider__slug", "last_error")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ProviderContent)
class ProviderContentAdmin(admin.ModelAdmin):
    list_display = (
        "provider_content_id",
        "provider",
        "content_type",
        "title",
        "release_date",
        "currently_available",
        "last_seen",
    )
    list_filter = ("content_type", "currently_available", "provider")
    search_fields = ("provider_content_id", "title", "identity")
    readonly_fields = ("identity", "first_seen", "last_seen", "created_at", "updated_at")


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = (
        "tmdb_id",
        "content_type",
        "provider",
        "country",
        "offer_type",
        "currently_available",
        "last_seen",
    )
    list_filter = ("content_type", "offer_type", "currently_available", "country")
    search_fields = ("tmdb_id", "provider_content_id", "identity")
    readonly_fields = ("identity", "first_seen", "last_seen", "created_at", "updated_at")


@admin.register(ContentMapping)
class ContentMappingAdmin(admin.ModelAdmin):
    list_display = (
        "provider",
        "provider_content_id",
        "content_type",
        "tmdb_id",
        "confidence",
        "match_method",
        "last_verified",
    )
    list_filter = ("match_method",)
    search_fields = ("provider__slug", "provider_content_id")


@admin.register(SyncCursor)
class SyncCursorAdmin(admin.ModelAdmin):
    list_display = ("provider", "source", "country", "last_successful_sync")
    search_fields = ("provider__slug",)
