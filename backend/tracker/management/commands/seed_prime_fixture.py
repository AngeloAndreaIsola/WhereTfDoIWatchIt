"""Seed cached Prime availability from a saved fixture — fully offline.

Lets the UI render the movie-detail page (real ranked Prime offers) without a
live Streaming Availability call, so we never spend the 100 req/day free-tier
quota just to demo. Parses the same v4 fixture the parser tests use and writes
Availability / LanguageInfo / LanguageEntry rows through the cache's write path.

    python manage.py seed_prime_fixture
    python manage.py seed_prime_fixture --fixture path/to/other.json
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ...models import Movie
from ...services.cache import AvailabilityCache
from ...services.streaming_api import StreamingAvailabilityClient, StreamingResult

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "streaming_movie_sample.json"
)


def _tmdb_id_from_payload(payload: dict) -> int:
    """v4 reports tmdbId as e.g. "movie/588228"; extract the trailing int."""
    raw = str(payload.get("tmdbId") or payload.get("id") or "")
    digits = raw.rsplit("/", 1)[-1]
    if not digits.isdigit():
        raise CommandError(f"Could not parse a numeric TMDb id from {raw!r}")
    return int(digits)


class Command(BaseCommand):
    help = "Seed cached Prime availability from a saved v4 fixture (offline, no API call)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fixture",
            type=str,
            default=str(DEFAULT_FIXTURE),
            help=f"Path to a v4 Show JSON fixture (default: {DEFAULT_FIXTURE.name}).",
        )

    def handle(self, *args, **options):
        fixture_path = Path(options["fixture"])
        if not fixture_path.exists():
            raise CommandError(f"Fixture not found: {fixture_path}")

        with fixture_path.open() as fh:
            payload = json.load(fh)

        tmdb_id = _tmdb_id_from_payload(payload)
        movie, created = Movie.objects.get_or_create(
            tmdb_id=tmdb_id,
            defaults={
                "title": payload.get("title") or f"Movie {tmdb_id}",
                "year": payload.get("releaseYear"),
            },
        )

        offers = StreamingAvailabilityClient._parse_offers(payload)
        result = StreamingResult(tmdb_id=tmdb_id, offers=offers)

        # Write directly through the cache — this bypasses the live client and
        # the rate limiter entirely, so it is safe to run any number of times.
        written = AvailabilityCache()._write_result(movie, result)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {written} offer(s) for '{movie.title}' (tmdb {tmdb_id}, "
                f"{'created' if created else 'existing'} movie) from {fixture_path.name}"
            )
        )
