from django.urls import path
from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("search/", views.search, name="search"),
    path("movie/<int:tmdb_id>/availability/", views.movie_availability, name="movie-availability"),
    path("preferences/", views.preferences, name="preferences"),
    path("watchlist/", views.watchlist, name="watchlist"),
    path("watchlist/refresh/", views.watchlist_refresh, name="watchlist-refresh"),
    path("watchlist/report/", views.watchlist_report, name="watchlist-report"),
    path("watchlist/resolve/", views.watchlist_resolve, name="watchlist-resolve"),
]