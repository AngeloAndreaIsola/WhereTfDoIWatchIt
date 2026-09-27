from django.contrib import admin
from django.contrib.postgres.fields import ArrayField
from django import forms
from django.urls import path
from django.utils.html import format_html
from django.shortcuts import render

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
    list_display = ("id", "tmdb_id", "title", "year", "content_type", "tmdb_rating", "has_poster")
    list_filter = ("content_type",)
    search_fields = ("title", "tmdb_id")
    readonly_fields = ("id",)
    fieldsets = (
        ("Basic", {"fields": ("tmdb_id", "title", "year", "content_type")}),
        ("Media", {"fields": ("poster_path", "overview")}),
        ("Metadata", {"fields": ("genres", "genre_ids", "cast", "seasons_data", "runtime_minutes", "tmdb_rating")}),
    )

    def has_poster(self, obj):
        return bool(obj.poster_path)
    has_poster.boolean = True
    has_poster.short_description = "poster"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:lookup>/link-tmdb/",
                self.admin_site.admin_view(self.link_tmdb_view),
                name="movie-link-tmdb",
            ),
        ]
        return custom_urls + urls

    def link_tmdb_view(self, request, lookup):
        """Admin view: search TMDB and link a movie to an existing DB record."""
        from django.conf import settings
        from .services.enrichment import resolve_or_create_movie
        from .services.tmdb import TMDbClient

        context = dict(self.admin_site.each_context(request))
        movie = None
        results = []
        linked = False
        error = None

        if "link_btn" in request.POST:
            tmdb_id = request.POST.get("tmdb_id", "").strip()
            title_override = request.POST.get("title_override", "").strip()
            movie_id = request.POST.get("movie_id", "").strip()
            if movie_id and tmdb_id and tmdb_id.isdigit():
                try:
                    movie = Movie.objects.get(pk=int(movie_id))
                except Movie.DoesNotExist:
                    error = "Movie not found."
                else:
                    try:
                        # resolve_or_create_movie already fetches details and
                        # syncs every field (including content_type).
                        movie = resolve_or_create_movie(int(tmdb_id))
                        linked = True
                    except Exception as exc:
                        error = f"TMDb fetch failed: {exc}"
            else:
                error = "Please enter a valid TMDB ID and select a movie."

        if "search_btn" in request.POST:
            query = request.POST.get("q", "").strip()
            movie_id = request.POST.get("movie_id", "").strip()
            if query and movie_id:
                try:
                    movie = Movie.objects.get(pk=int(movie_id))
                except Movie.DoesNotExist:
                    error = "Movie not found."
                else:
                    client = TMDbClient(settings.TMDB_API_KEY)
                    try:
                        results = client.search(query)
                    except Exception as exc:
                        error = f"TMDb search failed: {exc}"

        context.update({
            "title": "Link TMDB Movie" if not movie else f"Link TMDB for '{movie}'",
            "movie": movie,
            "results": results,
            "linked": linked,
            "error": error,
            "opts": self.model._meta,
        })
        return render(request, "admin/tracker/movie/link_tmdb.html", context)


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
