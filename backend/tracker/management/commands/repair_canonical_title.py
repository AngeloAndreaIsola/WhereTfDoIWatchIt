"""Guarded, dry-run-first repair of a corrupted canonical title (#112).

Repairs ONLY the display/canonical title of one explicitly identified Movie
row whose ``tmdb_id``/``content_type`` identity is trusted but whose title
diverges from the trusted TMDb record (the PK-830 pattern). It never re-keys
identities, never touches offers, mappings, refresh history or user state,
and refuses rows flagged as deliberate user overrides.

Default mode is a read-only dry run. ``--apply`` additionally requires the
exact ``<database>@<host>`` confirmation token (same contract as
``guarded_migrate``) and performs an independent live TMDb identity check
before writing. Every applied repair is recorded in MovieIdentityAudit.
"""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from tracker.models import Movie, MovieIdentityAudit
from tracker.services.tmdb import TMDbClient
from django.conf import settings


class Command(BaseCommand):
    help = "Dry-run or guarded repair of one movie's corrupted canonical title (#112)."

    def add_arguments(self, parser):
        parser.add_argument("--movie-id", type=int, required=True)
        parser.add_argument("--apply", action="store_true", help="Actually write (default: dry run).")
        parser.add_argument("--confirm-target", default="", help="Required with --apply: <database>@<host>.")
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        alias = options["database"]
        if alias not in connections:
            raise CommandError(f"Unknown database alias: {alias}")
        actual = connections[alias].settings_dict
        actual_name = str(actual.get("NAME") or "")
        actual_host = str(actual.get("HOST") or "")
        token = f"{actual_name}@{actual_host}"

        movie = Movie.objects.filter(pk=options["movie_id"]).first()
        if movie is None:
            raise CommandError(f"Movie pk={options['movie_id']} not found")

        refs = {
            "watchlist_items": movie.watchlist_items.count(),
            "user_data": movie.user_data.count(),
            "watch_events": movie.watch_events.count(),
            "refresh_requests": movie.refresh_requests.count(),
            "availabilities": movie.availabilities.count(),
        }
        before = {
            "id": movie.pk,
            "tmdb_id": movie.tmdb_id,
            "content_type": movie.content_type,
            "title": movie.title,
            "canonical_title": movie.canonical_title,
            "title_is_override": movie.title_is_override,
            "year": movie.year,
            "references": refs,
        }

        if movie.title_is_override:
            self.stdout.write(json.dumps({"status": "refused", "reason": "title_is_override", "before": before}))
            raise CommandError("Row is a deliberate user override — not a corruption repair candidate.")

        if not options["apply"]:
            self.stdout.write(json.dumps({
                "status": "dry_run",
                "target": token,
                "before": before,
                "intended_action": "fetch trusted TMDb details for (tmdb_id, content_type); "
                                   "set title and canonical_title to the verified TMDb title; audit the repair",
                "requires": "--apply --confirm-target " + token,
            }, default=str))
            return

        if options["confirm_target"] != token:
            raise CommandError(f"Confirmation must equal the exact target token {token!r}")

        # Independent identity check against the trusted metadata source.
        client = TMDbClient(settings.TMDB_API_KEY)
        details = client.details(movie.tmdb_id, content_type=movie.content_type)
        if details.tmdb_id != movie.tmdb_id or (details.content_type or movie.content_type) != movie.content_type:
            raise CommandError("TMDb identity check failed: returned identity does not match the row")
        if not details.title:
            raise CommandError("TMDb returned no title; refusing to repair")

        if movie.title == details.title and movie.canonical_title == details.title:
            self.stdout.write(json.dumps({"status": "already_correct", "before": before}, default=str))
            return

        with transaction.atomic():
            changes = {}
            if movie.title != details.title:
                changes["title"] = {"old": movie.title, "new": details.title}
            if movie.canonical_title != details.title:
                changes["canonical_title"] = {"old": movie.canonical_title, "new": details.title}
            movie.title = details.title
            movie.canonical_title = details.title
            movie.save(update_fields=["title", "canonical_title"])
            MovieIdentityAudit.objects.create(
                movie=movie,
                operation="repair",
                changes=changes,
                provenance="guarded_repair",
            )
        after = {**before, "title": movie.title, "canonical_title": movie.canonical_title}
        self.stdout.write(json.dumps({"status": "repaired", "target": token, "before": before, "after": after}, default=str))
