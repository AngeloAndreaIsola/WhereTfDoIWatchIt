"""Bootstrap existing canonical Movie identities from verified TMDb metadata."""
from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from availability.services.candidate_gateway import (
    CandidateDiscoveryBudget,
    CandidateDiscoveryBudgetExceeded,
    TMDbCandidateGateway,
)
from tracker.models import CanonicalBootstrapEvidence, DiscoveredCatalogueRecord, Movie
from tracker.services.catalogue_reconciliation import _extract_year, _normal_title, _type_evidence


class Command(BaseCommand):
    help = "Bootstrap canonical Movie rows from bounded, verified TMDb responses (dry-run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--provider", action="append", dest="providers")
        parser.add_argument("--record-id", action="append", type=int, dest="record_ids")
        parser.add_argument("--limit", type=int, default=25, help="Maximum discovered records evaluated.")
        parser.add_argument("--request-budget", type=int, default=50, help="Maximum TMDb HTTP attempts, including retries.")
        parser.add_argument("--interval", type=float, default=0.25, help="Minimum seconds between TMDb requests.")
        parser.add_argument("--allow-network", action="store_true", help="Permit cache misses to use TMDb.")
        parser.add_argument("--apply", action="store_true", help="Persist verified canonical rows and provenance.")

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 0 or options["request_budget"] <= 0 or options["interval"] < 0:
            raise CommandError("limit and interval must be non-negative; request-budget must be positive")
        if options["allow_network"] and not settings.TMDB_API_KEY:
            raise CommandError("--allow-network requires TMDB_API_KEY")
        record_ids = options.get("record_ids") or []
        qs = DiscoveredCatalogueRecord.objects.select_related("provider").order_by("provider__slug", "pk")
        if options.get("providers"):
            qs = qs.filter(provider__slug__in=options["providers"])
        if record_ids:
            qs = qs.filter(pk__in=record_ids)
            if len(set(record_ids)) > limit:
                raise CommandError("--record-id count cannot exceed --limit")
        qs = qs[:limit]

        budget = CandidateDiscoveryBudget(limit=options["request_budget"])
        gateway = TMDbCandidateGateway(
            budget=budget,
            min_request_interval_seconds=options["interval"],
        )
        rows = []
        totals = {"evaluated": 0, "verified": 0, "ambiguous": 0, "unmatched": 0,
                  "conflicting": 0, "created": 0, "already_present": 0, "applied": 0}
        for record in qs:
            totals["evaluated"] += 1
            content_type, _, type_evidence = _type_evidence(record)
            year, year_evidence = _extract_year(record)
            result = {"record_id": record.pk, "provider": record.provider.slug,
                      "provider_native_id": record.provider_native_id, "title": record.title,
                      "year": year, "content_type": content_type, "evidence": [],
                      "status": "UNMATCHED", "tmdb_id": None, "action": "DRY_RUN"}
            if content_type is None or year is None:
                result["status"] = "AMBIGUOUS" if content_type is None else "UNMATCHED"
                result["evidence"] = type_evidence + [year_evidence]
                totals["ambiguous" if content_type is None else "unmatched"] += 1
                rows.append(result)
                continue

            try:
                candidates = gateway.search(
                    record.title, year=year, content_type=content_type,
                    authorize_network=options["allow_network"],
                )
            except CandidateDiscoveryBudgetExceeded:
                result["status"] = "BUDGET_EXHAUSTED"
                result["evidence"] = ["TMDb request budget exhausted; rerun resumes from cached results"]
                totals["unmatched"] += 1
                rows.append(result)
                break
            exact = [c for c in candidates if c.get("year") == year and
                     _normal_title(c.get("title")) == _normal_title(record.title)]
            if len(exact) != 1:
                conflicting = [c for c in candidates if c.get("year") == year]
                result["status"] = "AMBIGUOUS" if len(exact) > 1 or conflicting else "UNMATCHED"
                result["candidates"] = [{"tmdb_id": c.get("tmdb_id"), "title": c.get("title"),
                                         "year": c.get("year")} for c in candidates]
                result["evidence"] = ["TMDb search title/year did not produce one exact candidate", *type_evidence]
                totals["ambiguous" if result["status"] == "AMBIGUOUS" else "unmatched"] += 1
                rows.append(result)
                continue

            candidate = exact[0]
            try:
                details = gateway.get_details(candidate["tmdb_id"], content_type=content_type,
                                              authorize_network=options["allow_network"])
            except CandidateDiscoveryBudgetExceeded:
                result["status"] = "BUDGET_EXHAUSTED"
                result["evidence"] = ["TMDb request budget exhausted; rerun resumes from cached results"]
                totals["unmatched"] += 1
                rows.append(result)
                break
            if not details or int(details.get("tmdb_id") or 0) != int(candidate["tmdb_id"]):
                result["status"] = "UNMATCHED"
                result["evidence"] = ["TMDb details could not verify the searched identifier"]
                totals["unmatched"] += 1
                rows.append(result)
                continue
            verified_title = details.get("title") or ""
            verified_year = details.get("year")
            if (_normal_title(verified_title) != _normal_title(record.title)
                    or verified_year != year or details.get("content_type") != content_type):
                result["status"] = "CONFLICTING"
                result["evidence"] = ["TMDb details conflict with exact source title/year/type",
                                      f"TMDb title={verified_title!r}", f"TMDb year={verified_year!r}",
                                      f"TMDb type={details.get('content_type')!r}"]
                totals["conflicting"] += 1
                rows.append(result)
                continue

            movie = Movie.objects.filter(tmdb_id=int(candidate["tmdb_id"])).first()
            if movie and movie.content_type != content_type:
                result["status"] = "CONFLICTING"
                result["evidence"] = ["existing canonical identity has a different media type"]
                totals["conflicting"] += 1
                rows.append(result)
                continue
            totals["verified"] += 1
            if movie:
                totals["already_present"] += 1
            else:
                totals["created"] += 1
            result.update({"status": "VERIFIED", "tmdb_id": int(candidate["tmdb_id"]),
                           "canonical_title": verified_title, "canonical_year": verified_year,
                           "evidence": ["exact normalized source title", "exact release year",
                                        "TMDb details verified identifier and media type", *type_evidence],
                           "action": "PENDING_APPLY" if options["apply"] else "DRY_RUN"})
            if options["apply"]:
                with transaction.atomic():
                    movie, created = Movie.objects.get_or_create(
                        tmdb_id=int(candidate["tmdb_id"]),
                        defaults={
                            "title": verified_title, "year": verified_year,
                            "content_type": content_type,
                            "overview": details.get("overview") or "",
                            "poster_path": details.get("poster_path") or "",
                            "genres": details.get("genres") or [],
                            "genre_ids": details.get("genre_ids") or [],
                            "cast": details.get("cast") or [],
                            "director": details.get("director") or [],
                            "seasons_data": details.get("seasons_data") or [],
                            "runtime_minutes": details.get("runtime_minutes"),
                            "tmdb_rating": details.get("tmdb_rating"),
                        },
                    )
                    if movie.content_type != content_type:
                        raise CommandError(f"TMDb {movie.tmdb_id} conflicts with existing canonical media type")
                    evidence_row, evidence_created = CanonicalBootstrapEvidence.objects.get_or_create(
                        discovered_record=record,
                        defaults={
                            "movie": movie,
                            "source_kind": "tmdb_search_and_details",
                            "verification_method": "exact_title_year_type",
                            "evidence": {"source_title": record.title, "source_year": year,
                                         "provider": record.provider.slug,
                                         "provider_native_id": record.provider_native_id,
                                         "tmdb_id": int(candidate["tmdb_id"]),
                                         "tmdb_title": verified_title, "tmdb_year": verified_year,
                                         "content_type": content_type},
                        },
                    )
                    if not evidence_created and evidence_row.movie_id != movie.pk:
                        raise CommandError(
                            f"Discovered record {record.pk} already has canonical evidence for "
                            f"TMDb {evidence_row.movie.tmdb_id}; review and correct it before remapping"
                        )
                    totals["applied"] += 1
                    result["action"] = "created" if created else "existing_identity_evidence_recorded"
            rows.append(result)

        self.stdout.write(json.dumps({"mode": "APPLY" if options["apply"] else "DRY_RUN",
                                      "network_authorized": options["allow_network"],
                                      "statistics": totals, "tmdb_budget": budget.to_dict(),
                                      "records": rows}, indent=2, ensure_ascii=False, default=str))
