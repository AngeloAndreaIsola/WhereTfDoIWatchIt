"""Single logical watchlist membership per (user, movie) — issue #106.

``WatchlistItem.source`` is provenance (how the title entered the watchlist),
not membership identity. Manual add, ambiguous-import resolution and the
Letterboxd/MyAnimeList importer must all reuse an existing row for the same
(user, movie) instead of creating a second row under a different source.

The database-level uniqueness decision belongs to the #96 ownership/auth
migration (``(movie, source)`` is the current constraint). Until that lands,
the movie row lock taken here serializes membership creation under PostgreSQL
READ COMMITTED, so concurrent callers cannot both insert: the second caller
blocks on the lock, then observes the first caller's committed row.
"""

from django.db import transaction

from ..models import Movie, WatchlistItem


def get_or_create_watchlist_membership(
    movie: Movie, *, user_id: int = 1, source: str = "manual"
) -> tuple[WatchlistItem, bool]:
    """Return ``(item, created)`` for the user's single membership of *movie*.

    Any existing row for ``(user_id, movie)`` is reused regardless of its
    source; the original provenance is preserved unchanged. ``source`` is only
    used when a new membership is actually created.
    """
    with transaction.atomic():
        # Serialize concurrent membership creation for this title without a
        # schema change (see module docstring / #96).
        Movie.objects.select_for_update().filter(pk=movie.pk).first()
        item = WatchlistItem.objects.filter(user_id=user_id, movie=movie).first()
        if item is not None:
            return item, False
        item = WatchlistItem.objects.create(
            user_id=user_id, movie=movie, source=source
        )
        return item, True
