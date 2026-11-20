"""Inventory and reversibly quarantine the specific fixture-shaped rows from #113."""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.db.models import Count
from django.forms.models import model_to_dict

from availability.models import (
    CandidateReviewAudit, ContentMapping, Offer, OfferEvidence, OfferPrice, ProviderContentCandidate,
)
from tracker.models import Movie, MovieIdentityAudit, RefreshRequestStatus

# The observed local fingerprint is (TMDb ID, display title, year), all in the
# movie namespace. See docs/audits/remediation_113_plan.md for upstream evidence.
OBSERVED = {
    814: (999901, "X", 2020),
    818: (33, "BehindA404", 2000),
    819: (9001, "AllFail", 2020),
    820: (99001, "Trace", 2000),
    821: (99002, "T2", 2000),
    822: (99003, "T3", 2000),
    823: (99004, "T4", 2000),
    824: (875001, "Neg", 2020),
    825: (875002, "Neg", 2021),
    826: (875003, "Neg", 2020),
    827: (875004, "Neg", 2020),
    828: (876001, "Neg", 2020),
    829: (877001, "Case", 2020),
}
INVENTORY_ONLY = {
    814: "provenance and upstream identity unverified",
    825: "preserve genuine Evacuation metadata for separate identity review",
    827: "upstream identity unverified after a 502",
}
BLOCKING_RELATIONS = (
    "watchlist_items", "user_data", "watch_events", "availabilities",
    "language_infos", "scraped_mappings", "bootstrap_evidence",
)
TYPED_RELATIONS = {
    "content_mappings": ContentMapping,
    "offers": Offer,
    "offer_evidence": OfferEvidence,
    "offer_prices": OfferPrice,
}
METADATA_FIELDS = (
    "canonical_title", "title_is_override", "poster_path", "overview", "genres",
    "genre_ids", "cast", "director", "seasons_data",
)


def snapshot(movie, alias):
    identity = {"tmdb_id": movie.tmdb_id, "content_type": movie.content_type}
    mappings = ContentMapping.objects.using(alias).filter(**identity)
    references = {
        name: getattr(movie, name).using(alias).count() for name in BLOCKING_RELATIONS
    }
    references.update({
        name: model.objects.using(alias).filter(**identity).count()
        for name, model in TYPED_RELATIONS.items()
    })
    references.update(
        provider_contents=mappings.exclude(content_id=None).values("content_id").distinct().count(),
        provider_candidates=ProviderContentCandidate.objects.using(alias).filter(**identity).count(),
        verified_candidate_audits=CandidateReviewAudit.objects.using(alias).filter(
            verified_tmdb_id=movie.tmdb_id, verified_content_type=movie.content_type,
        ).count(),
    )
    refresh = dict.fromkeys(RefreshRequestStatus.values, 0)
    refresh.update(
        movie.refresh_requests.using(alias).order_by().values_list("status").annotate(total=Count("id"))
    )
    return {
        **model_to_dict(movie),
        "references": {
            **references,
            "refresh_requests": refresh,
            "identity_audits": movie.identity_audits.using(alias).count(),
        },
    }


def refusal(movie, before, *, restore, alias):
    if movie.pk in INVENTORY_ONLY:
        return INVENTORY_ONLY[movie.pk]
    if (movie.tmdb_id, movie.title, movie.year) != OBSERVED[movie.pk] or movie.content_type != "movie":
        return "local identity does not match the manifest"
    if restore:
        if movie.is_quarantined:
            last = movie.identity_audits.using(alias).filter(
                operation__in=("quarantine", "restore")
            ).first()
            if last is None or last.operation != "quarantine" or last.provenance != "issue_113":
                return "no matching issue #113 quarantine audit"
        return None

    protected = (*BLOCKING_RELATIONS, *TYPED_RELATIONS, "verified_candidate_audits")
    if any(before["references"][name] for name in protected):
        return "legitimate user state, availability or identity evidence is present"
    if any(before[field] for field in METADATA_FIELDS) or any(
        before[field] is not None for field in ("runtime_minutes", "tmdb_rating")
    ):
        return "row contains metadata or a deliberate title override"
    return None


class Command(BaseCommand):
    help = "Inventory #113 movies, or apply/reverse an exact allowlisted quarantine."

    def add_arguments(self, parser):
        parser.add_argument("--movie-id", type=int, action="append", required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--restore", action="store_true", help="Plan or apply a reversal.")
        parser.add_argument("--confirm-target", default="", help="Required for writes: database@host:port.")
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        alias = options["database"]
        if alias not in connections:
            raise CommandError(f"Unknown database alias: {alias}")
        connection = connections[alias]
        if connection.vendor != "postgresql":
            raise CommandError("The quarantine command requires PostgreSQL.")
        config = connection.settings_dict
        target = f"{config['NAME']}@{config['HOST'] or 'local-socket'}:{config['PORT'] or '5432'}"
        apply = options["apply"]
        restore = options["restore"]
        desired = not restore
        ids = sorted(set(options["movie_id"]))
        if unknown := set(ids) - OBSERVED.keys():
            raise CommandError(f"IDs outside the issue #113 inventory: {sorted(unknown)}")
        if apply and options["confirm_target"] != target:
            raise CommandError(f"Confirmation must equal the exact target token {target!r}")

        with transaction.atomic(using=alias):
            with connection.cursor() as cursor:
                if not apply:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                cursor.execute("SELECT current_database()")
                if cursor.fetchone()[0] != config["NAME"]:
                    raise CommandError("Connected database does not match the configured target.")
            query = Movie.objects.using(alias).filter(pk__in=ids).order_by("pk")
            if apply:
                query = query.select_for_update()
            movies = {movie.pk: movie for movie in query}
            results = []
            for pk in ids:
                movie = movies.get(pk)
                before = snapshot(movie, alias) if movie else None
                reason = (
                    refusal(movie, before, restore=restore, alias=alias)
                    if movie else "movie is missing"
                )
                changed = reason is None and movie.is_quarantined != desired
                after = before
                if changed:
                    after = {
                        **before,
                        "is_quarantined": desired,
                        "references": {
                            **before["references"],
                            "identity_audits": before["references"]["identity_audits"] + 1,
                        },
                    }
                results.append({
                    "id": pk, "status": "refused" if reason else "planned" if changed else "unchanged",
                    "reason": reason, "before": before, "after": after,
                })

            refused = any(row["status"] == "refused" for row in results)
            if apply and not refused:
                for row in results:
                    if row["status"] != "planned":
                        continue
                    movie = movies[row["id"]]
                    movie.is_quarantined = desired
                    movie.save(using=alias, update_fields=["is_quarantined"])
                    audit = MovieIdentityAudit.objects.using(alias).create(
                        movie=movie,
                        movie_pk=movie.pk,
                        operation="restore" if restore else "quarantine",
                        changes={
                            "is_quarantined": {"old": not desired, "new": desired},
                            "identity": {
                                name: row["before"][name] for name in ("tmdb_id", "title", "content_type", "year")
                            },
                            "references": row["before"]["references"],
                            "target": target,
                        },
                        provenance="issue_113",
                    )
                    row.update(status="applied", audit_id=audit.pk)

        self.stdout.write(json.dumps({
            "target": target,
            "status": ("refused" if refused else "committed") if apply else "dry_run",
            "operation": "restore" if restore else "quarantine",
            "movies": results,
        }, sort_keys=True))
        if apply and refused:
            raise CommandError("Quarantine batch refused. See the report for details.")
