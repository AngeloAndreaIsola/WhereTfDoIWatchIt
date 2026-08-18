from django.urls import path, re_path
from . import views

urlpatterns = [
    re_path(r"^health/?$", views.health, name="health"),
    re_path(r"^search/?$", views.search, name="search"),
    re_path(r"^movie/(?P<tmdb_id>\d+)/availability/?$", views.movie_availability, name="movie-availability"),
    re_path(r"^preferences/?$", views.preferences, name="preferences"),
    re_path(r"^watchlist/?$", views.watchlist, name="watchlist"),
    re_path(r"^watchlist/report/?$", views.watchlist_report, name="watchlist-report"),
    re_path(r"^watchlist/resolve/?$", views.watchlist_resolve, name="watchlist-resolve"),
]