from django.urls import path, re_path
from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("search/", views.search, name="search"),
    path("movie/<int:tmdb_id>/availability/", views.movie_availability, name="movie-availability"),
    path("movie/<int:tmdb_id>/language-info/", views.movie_language_info, name="movie-language-info"),
    path("providers/", views.providers_list, name="providers-list"),
    path("providers/<str:slug>/toggle/", views.provider_toggle, name="provider-toggle"),
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
    path("watchlist/report/", views.watchlist_report, name="watchlist-report"),
    path("watchlist/resolve/", views.watchlist_resolve, name="watchlist-resolve"),
    path("prime/popular/", views.prime_popular, name="prime-popular"),
    path("prime/movie/<int:tmdb_id>/", views.prime_movie, name="prime-movie"),
]