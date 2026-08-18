from django.urls import path
from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("search/", views.search, name="search"),
    path("movie/<int:tmdb_id>/availability/", views.movie_availability, name="movie-availability"),
    path("movie/<int:tmdb_id>/language-info/", views.movie_language_info, name="movie-language-info"),
    path("preferences/", views.preferences, name="preferences"),
    path("watchlist/", views.watchlist, name="watchlist"),
    path("watchlist/report/", views.watchlist_report, name="watchlist-report"),
    path("watchlist/resolve/", views.watchlist_resolve, name="watchlist-resolve"),
]