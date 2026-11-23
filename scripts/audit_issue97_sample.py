"""Replay the bounded #97 sample on an authorized disposable PostgreSQL clone."""
import argparse
import csv
import json
import os
import sys
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs/audits/stage2b"


def sample():
    with (AUDIT / "nl_pilot_cohort_20261122_v2.csv").open() as handle:
        pilot = {r["tmdb_id"]: r for r in csv.DictReader(handle)}
    with (AUDIT / "live_sample.csv").open() as handle:
        original = list(csv.DictReader(handle))
    rows = []
    for row in original:
        identifier = "220289" if row["tmdb_id"] == "300602" else row["tmdb_id"]
        identity = pilot.get(identifier, row)
        rows.append({**{k: identity[k] for k in ("tmdb_id", "title", "media_type", "year")},
                     "country": row["country"]})
    for identifier, country in [("2316", "GB"), ("565310", "CA"), ("438631", "AU"), ("2316", "US")]:
        rows.append({**pilot[identifier], "country": country})
    return rows


def measure(rows):
    from django.db import connection, transaction
    from rest_framework.test import APIRequestFactory
    from tracker.models import Movie, MovieWatchEvent, Preference, Provider, UserMovieData
    from tracker.services.access import build_explicit_access
    from tracker.services.availability_reads import get_offer_evidence
    from tracker.services.watchability import describe_last_known, evaluate_watchability, qualify_watchability
    from tracker.views import movie_availability

    results = []
    with transaction.atomic(), ExitStack() as scope:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        # The legacy response also reads preferences and personal history.
        # Provide empty querysets before evaluating the real view.
        for model in (MovieWatchEvent, UserMovieData):
            scope.enter_context(patch.object(model.objects, "filter", return_value=model.objects.none()))
        providers = {p.slug: p for p in Provider.objects.all()}
        factory = APIRequestFactory()
        for row in rows:
            country = row["country"]
            pref = Preference(current_country=country, candidate_countries=[country], ranking_preferences={})
            key = int(row["tmdb_id"])
            movie = Movie.objects.visible().filter(tmdb_id=key, content_type=row["media_type"]).first()
            with patch.object(Preference.objects, "filter") as pref_query:
                pref_query.return_value.first.return_value = pref
                response = movie_availability(factory.get(f"/api/movie/{key}/availability/"), key)
            projected = {}
            if response.status_code == 200:
                if movie is None:
                    raise ValueError(f"Untyped view resolved a different title at {key}")
                for field in ("offers", "last_known_offers"):
                    projected[field] = [[o["service"], o["offer_type"], o["availability_evidence"]]
                                        for o in response.data[field] if o["country"] == country]
            elif response.status_code != 404:
                raise ValueError(f"Unexpected HTTP {response.status_code} for {key}")
            status = "NO_VISIBLE_TYPED_MOVIE"
            if movie:
                current, history = get_offer_evidence(movie, country=country)
                profile = dict(current_country=country, access=build_explicit_access(set()), provider_map=providers)
                result = evaluate_watchability(movie, current, pref, **profile)
                qualify_watchability(result, describe_last_known(movie, history, pref, **profile))
                status = str(result.status)
            results.append({**row, "read_utc": datetime.now(timezone.utc).isoformat(),
                            "http": response.status_code, "catalogue_status": status, **projected})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True,
                        help="Name of the disposable clone authorized for this run")
    parser.add_argument("--audit-code-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "backend"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connection
    run(args, connection, call_command, parser)


def run(args, connection, call_command, parser):
    if connection.vendor != "postgresql":
        parser.error("Use the authorized disposable PostgreSQL clone.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        actual = cursor.fetchone()[0]
        if actual != args.expected_database:
            parser.error(f"Connected database {actual!r} differs from expected clone {args.expected_database!r}.")
        cursor.execute("SHOW default_transaction_read_only")
        if cursor.fetchone()[0] != "on":
            parser.error("Set PGOPTIONS='-c default_transaction_read_only=on'.")
    rows = sample()
    args.output.mkdir(parents=True, exist_ok=False)
    cohort = args.output / "cohort.csv"
    with cohort.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    call_command("audit_availability_accuracy", offline=True, cohort=cohort, audit_code_sha=args.audit_code_sha,
                 identity_manifest=AUDIT / "identity_manifest_20261122.csv", output=args.output)
    (args.output / "display.json").write_text(json.dumps(measure(rows), indent=2) + "\n")


if __name__ == "__main__":
    main()
