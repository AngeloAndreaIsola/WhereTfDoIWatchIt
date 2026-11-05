from django.urls import path, re_path
from . import views, api_v2

urlpatterns = [
    path("health/", views.health, name="health"),
    path("geo/ip/", views.geo_ip, name="geo-ip"),
    path("search/", views.search, name="search"),
    path("search/person/", views.search_person, name="search-person"),
    path("genres/", views.genres_list, name="genres-list"),
    path("homepage/", views.homepage_feed, name="homepage-feed"),
    path("catalog/", views.catalog_list, name="catalog-list"),
    path("catalog/unified/", views.unified_catalog, name="catalog-unified"),
    path("movie/<int:tmdb_id>/availability/", views.movie_availability, name="movie-availability"),
    path("movie/<int:tmdb_id>/refresh/", views.refresh_availability, name="movie-refresh"),
    path("movie/<int:tmdb_id>/language-info/", views.movie_language_info, name="movie-language-info"),
    path("providers/", views.providers_list, name="providers-list"),
    path("providers/sync/", views.providers_sync, name="providers-sync"),
    path("providers/packages/", views.provider_packages, name="providers-packages"),
    path("providers/<str:slug>/toggle/", views.provider_toggle, name="provider-toggle"),
    path("providers/<str:slug>/refresh/", views.provider_refresh, name="provider-refresh"),
    path("providers/<str:slug>/", views.provider_delete, name="provider-delete"),
    path("subscriptions/", views.subscriptions_list_create, name="subscriptions-list-create"),
    path("subscriptions", views.subscriptions_list_create),
    path("subscriptions/<int:pk>/", views.subscription_detail, name="subscription-detail"),
    path("subscriptions/<int:pk>", views.subscription_detail),
    path("scrapers/import-url/", views.scraper_import_url, name="scraper-import-url"),
    path("scrapers/run/", views.scraper_run, name="scraper-run"),
    path("scrapers/unresolved/", views.scraper_unresolved, name="scraper-unresolved"),
    path("scrapers/confirm-match/", views.scraper_confirm_match, name="scraper-confirm-match"),
    path("preferences/", views.preferences, name="preferences"),
    path("watchlist/", views.watchlist, name="watchlist"),
    path("watchlist/refresh/", views.watchlist_refresh, name="watchlist-refresh"),
    path(
        "watchlist/refresh-explicit/",
        views.watchlist_refresh_explicit,
        name="watchlist-refresh-explicit",
    ),
    path(
        "watchlist/refresh-status/",
        views.watchlist_refresh_status,
        name="watchlist-refresh-status",
    ),
    path("watchlist/report/", views.watchlist_report, name="watchlist-report"),
    path("watchlist/resolve/", views.watchlist_resolve, name="watchlist-resolve"),
    # --- Source-first availability observability (Phase 1/2) ---
    # The report and the persisted run records are what let a page reload
    # reconstruct a long-running refresh instead of losing it.
    path("availability/report/", views.availability_report, name="availability-report"),
    path(
        "availability/metrics/",
        views.availability_metrics,
        name="availability-metrics",
    ),
    path(
        "availability/report/<int:tmdb_id>/",
        views.availability_title_report,
        name="availability-title-report",
    ),
    path("availability/refresh/", views.availability_refresh_start, name="availability-refresh-start"),
    path(
        "availability/refreshes/",
        views.availability_refresh_list,
        name="availability-refresh-list",
    ),
    path(
        "availability/refreshes/<int:pk>/",
        views.availability_refresh_detail,
        name="availability-refresh-detail",
    ),
    # --- Watchlist export (canonical round-trip CSV backup) ---
    path("export/watchlist/", views.export_watchlist, name="export-watchlist"),
    path("export/watched/", views.export_watched, name="export-watched"),
    path("export/", views.export_combined, name="export-combined"),
    path("prime/popular/", views.prime_popular, name="prime-popular"),
    path("prime/popular-series/", views.prime_popular_series, name="prime-popular-series"),
    path("prime/movie/<int:tmdb_id>/", views.prime_movie, name="prime-movie"),
    path("prime/series/<int:tmdb_id>/", views.prime_series, name="prime-series"),
    path("movie/<int:pk>/", views.movie_detail, name="movie-detail"),
    path("movie/<int:pk>/link-tmdb/", views.link_tmdb, name="link-tmdb"),
    path("movie/<int:tmdb_id>/", views.update_movie, name="update-movie"),
    path("movie/<int:tmdb_id>/search-matches/", views.search_movie_matches, name="search-movie-matches"),
    path("watch/<int:tmdb_id>/", views.watch_redirect, name="watch-redirect"),
    path("watch/<int:tmdb_id>", views.watch_redirect),
    # --- Manual watchlist management ---
    path("watchlist/add/", views.add_to_watchlist, name="add-to-watchlist"),
    path("watchlist/remove/<int:tmdb_id>/", views.remove_from_watchlist, name="remove-from-watchlist"),
    path("watched/", views.watched_list, name="watched-list"),
    path("watched/<int:tmdb_id>/", views.mark_movie_watched, name="mark-movie-watched"),
    path("unwatched/<int:tmdb_id>/", views.unmark_movie_watched, name="unmark-movie-watched"),
    # --- API v2: Unofficial sources (Phase C) ---
    path("v2/unofficial/", api_v2.unofficial_catalog, name="v2-unofficial-catalog"),
    path("v2/unofficial/scrape/", api_v2.unofficial_scrape, name="v2-unofficial-scrape"),
    path("v2/unofficial/movie/<int:tmdb_id>/offers/", api_v2.unofficial_movie_offers, name="v2-unofficial-movie-offers"),
    path("v2/unofficial/providers/", api_v2.unofficial_providers, name="v2-unofficial-providers"),
    # --- Google Interests importer ---
    path("importers/google-interests/", views.google_interests_import, name="google-interests-import"),
    # --- Letterboxd public-profile URL importer ---
    path("importers/letterboxd-url/", views.letterboxd_url_import, name="letterboxd-url-import"),
]