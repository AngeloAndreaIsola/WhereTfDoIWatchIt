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

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from tracker.models import Movie, MovieIdentityAudit
from tracker.services.tmdb import TMDbClient


def _snapshot(movie, alias):
    return {
        "id": movie.pk,
        "tmdb_id": movie.tmdb_id,
        "content_type": movie.content_type,
        "title": movie.title,
        "canonical_title": movie.canonical_title,
        "title_is_override": movie.title_is_override,
        "year": movie.year,
        "references": {
            "watchlist_items": movie.watchlist_items.using(alias).count(),
            "user_data": movie.user_data.using(alias).count(),
            "watch_events": movie.watch_events.using(alias).count(),
            "refresh_requests": movie.refresh_requests.using(alias).count(),
            "availabilities": movie.availabilities.using(alias).count(),
        },
    }


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
        if options["apply"] and options["confirm_target"] != token:
            raise CommandError(f"Confirmation must equal the exact target token {token!r}")

        target = Movie.objects.using(alias).filter(pk=options["movie_id"])
        movie = target.first()
        if movie is None:
            raise CommandError(f"Movie pk={options['movie_id']} not found")

        if movie.title_is_override:
            self.stdout.write(json.dumps({
                "status": "refused",
                "reason": "title_is_override",
                "before": _snapshot(movie, alias),
            }))
            raise CommandError("Row has a deliberate user title override.")

        if not options["apply"]:
            self.stdout.write(json.dumps({
                "status": "dry_run",
                "target": token,
                "before": _snapshot(movie, alias),
                "intended_action": "fetch trusted TMDb details for (tmdb_id, content_type); "
                                   "set title and canonical_title to the verified TMDb title; audit the repair",
                "requires": "--apply --confirm-target " + token,
            }))
            return

        identity = (movie.tmdb_id, movie.content_type)
        try:
            client = TMDbClient(settings.TMDB_API_KEY)
            details = client.details(movie.tmdb_id, content_type=movie.content_type)
        except (requests.RequestException, KeyError, TypeError, ValueError):
            raise CommandError("Could not verify the row's identity with TMDb.") from None
        if (details.tmdb_id, details.content_type) != identity:
            raise CommandError("TMDb identity check failed: returned identity does not match the row")
        if not isinstance(details.title, str) or not details.title.strip():
            raise CommandError("TMDb did not return a usable title.")

        # Verify the reviewed row again after the network call, while holding the write lock.
        with transaction.atomic(using=alias):
            current = target.select_for_update().first()
            if current is None or (current.tmdb_id, current.content_type) != identity:
                raise CommandError("Movie identity changed during verification. Run a new dry run.")
            if current.title_is_override:
                raise CommandError("A user title override was set during verification. Run a new dry run.")

            before = _snapshot(current, alias)
            title_fields = ("title", "canonical_title")
            changes = {
                field: {"old": before[field], "new": details.title}
                for field in title_fields
                if before[field] != details.title
            }
            if not changes:
                self.stdout.write(json.dumps({
                    "status": "already_correct", "target": token, "before": before,
                }))
                return
            if any(before[field] != getattr(movie, field) for field in (*title_fields, "year")):
                raise CommandError("Movie title or year changed during verification. Run a new dry run.")

            for field in changes:
                setattr(current, field, details.title)
            current.save(using=alias, update_fields=changes)
            MovieIdentityAudit.objects.using(alias).create(
                movie=current,
                operation="repair",
                changes=changes,
                provenance="guarded_repair",
            )
        after = {**before, "title": current.title, "canonical_title": current.canonical_title}
        self.stdout.write(json.dumps({
            "status": "repaired", "target": token, "before": before, "after": after,
        }))
