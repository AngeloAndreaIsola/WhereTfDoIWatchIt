"""Backfill sparse TMDb metadata (cast, genre_ids, genres, director, ...) for movies.

Catalog/Homepage/Watchlist search-by-actor and search-by-category rely on
``Movie.cast`` and ``Movie.genre_ids`` being populated. Those fields are only
    filled when a movie's detail page is opened (``sync_movie_fields``), so
movies created via bulk catalog seeding or watchlist import stay mostly empty,
which makes actor/genre filtering silently match nothing.

This command walks every Movie row missing the relevant fields and re-fetches
TMDb details, reusing the same enrichment logic the detail endpoint uses.

Respects a daily-ish budget: each ``details`` call is 1 TMDb request. Use
``--limit`` to cap how many rows are processed in one run (e.g. to stay under
free-tier rate limits) and ``--only-missing`` (default) to skip already-complete
rows.
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db.models import Q

from ...models import Movie
from ...services.enrichment import sync_movie_fields
from ...services.tmdb import TMDbClient, TMDbItem


class Command(BaseCommand):
    help = (
        "Backfill cast/genre_ids and other TMDb metadata for Movie rows that are "
        "missing it, so catalog/wishlist actor & genre filters work."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Max number of movies to process this run (0 = all missing). "
            "Use a small limit to stay under TMDb rate limits.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="process_all",
            help="Re-fetch metadata even for movies that already have cast/genre_ids.",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0.25,
            help="Seconds to sleep between TMDb requests (be gentle with the API).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count how many rows would be processed without calling TMDb.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        process_all = options["process_all"]
        delay = options["delay"]
        dry_run = options["dry_run"]

        if not TMDbClient.is_configured():
            self.stderr.write(
                self.style.ERROR(
                    "TMDB_API_KEY is not configured; cannot backfill metadata."
                )
            )
            return

        qs = Movie.objects.visible().order_by("id")
        if not process_all:
            # Empty JSON arrays are stored as exactly `[]`; match that plus NULLs
            # (the JSONField `__len` lookup misbehaves on Postgres for this case).
            qs = qs.filter(
                Q(cast=[]) | Q(genre_ids=[]) | Q(cast__isnull=True) | Q(genre_ids__isnull=True)
            )

        total_missing = qs.count()
        self.stdout.write(f"Movies needing metadata: {total_missing}")

        if dry_run:
            return

        if limit > 0:
            qs = qs[:limit]

        client = TMDbClient()
        processed = 0
        updated = 0
        failed = 0
        for movie in qs.iterator():
            processed += 1
            try:
                details = client.details(movie.tmdb_id, content_type=movie.content_type)
            except Exception as exc:
                failed += 1
                self.stderr.write(
                    self.style.WARNING(
                        f"  skip tmdb_id={movie.tmdb_id} ({movie.title}): {exc}"
                    )
                )
                continue
            if isinstance(details, TMDbItem) and sync_movie_fields(movie, details):
                updated += 1
                if (processed % 25) == 0:
                    self.stdout.write(f"  ...{processed} processed, {updated} updated")
            if delay:
                time.sleep(delay)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. processed={processed} updated={updated} failed={failed}"
            )
        )
