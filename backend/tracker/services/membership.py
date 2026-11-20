"""Single logical watchlist membership per (user, movie) — issue #106.

``WatchlistItem.source`` is provenance (how the title entered the watchlist),
not membership identity. Manual add, ambiguous resolution and file and URL imports reuse the same
(user, movie) membership across sources.

The database-level uniqueness decision belongs to the #96 ownership/auth
migration (``(movie, source)`` is the current constraint). Until that lands,
the movie row lock taken here serializes membership creation under PostgreSQL
READ COMMITTED, so concurrent callers cannot both insert: the second caller
blocks on the lock, then observes the first caller's committed row.
"""

from django.db import router, transaction

from ..models import Movie, WatchlistItem


def locked_watchlist_memberships(movie: Movie, *, user_id: int):
    """Return memberships after locking the movie for the caller’s transaction."""
    alias = router.db_for_write(WatchlistItem)
    Movie.objects.using(alias).select_for_update().only("pk").get(pk=movie.pk)
    return WatchlistItem.objects.using(alias).filter(user_id=user_id, movie_id=movie.pk)


def get_or_create_watchlist_membership(
    movie: Movie, *, user_id: int = 1, source: str = "manual"
) -> tuple[WatchlistItem, bool]:
    """Return ``(item, created)`` for the user's single membership of *movie*.

    Any existing row for ``(user_id, movie)`` is reused regardless of its
    source; the original provenance is preserved unchanged. ``source`` is only
    used when a new membership is actually created.
    """
    with transaction.atomic(using=router.db_for_write(WatchlistItem)):
        memberships = locked_watchlist_memberships(movie, user_id=user_id)
        item = memberships.order_by("pk").first()
        if item is not None:
            return item, False
        item = memberships.create(user_id=user_id, movie_id=movie.pk, source=source)
        return item, True
