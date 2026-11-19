"""Bounded, resumable canonical bootstrap using record-specific TMDb evidence."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections import Counter
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from availability.services.candidate_gateway import (
    CandidateDiscoveryBudget, CandidateDiscoveryBudgetExceeded, TMDbCandidateGateway,
)
from tracker.models import CanonicalBootstrapEvidence, DiscoveredCatalogueRecord, Movie
from tracker.services.catalogue_reconciliation import (
    _extract_year, _normal_title, _trusted_external_tmdb_id, _type_evidence,
)
from tracker.services.canonical_verification import CanonicalVerifier, source_snapshot, fingerprint, evidence_agrees


def write_checkpoint(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(state, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Command(CanonicalVerifier, BaseCommand):
    help = "Bootstrap at most 250 discoveries per frozen batch; dry-run by default."

    def add_arguments(self, parser):
        parser.add_argument("--provider", action="append", dest="providers")
        parser.add_argument("--record-id", action="append", type=int, dest="record_ids")
        parser.add_argument("--limit", type=int, default=25)
        parser.add_argument("--request-budget", type=int, default=50)
        parser.add_argument("--interval", type=float, default=0.25)
        parser.add_argument("--allow-network", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--retries", type=int, default=2)
        parser.add_argument("--checkpoint-file", help="Atomic batch manifest and completed result journal.")

    def handle(self, *args, **options):
        if (not 0 <= options["limit"] <= 250 or options["request_budget"] <= 0
                or not 0 <= options["retries"] <= 5 or not math.isfinite(options["interval"])
                or options["interval"] < 0):
            raise CommandError("Require limit 0..250, positive request-budget, retries 0..5 and finite non-negative interval")
        if options["allow_network"] and not settings.TMDB_API_KEY:
            raise CommandError("--allow-network requires TMDB_API_KEY")
        ids = sorted(set(options.get("record_ids") or []))
        if len(ids) > options["limit"]:
            raise CommandError("--record-id count cannot exceed --limit")
        scope = hashlib.sha256(json.dumps({
            "database": [connection.settings_dict["HOST"], connection.settings_dict["NAME"]],
            "providers": sorted(set(options.get("providers") or [])), "record_ids": ids,
            "apply": options["apply"], "limit": options["limit"], "version": 2,
        }, sort_keys=True).encode()).hexdigest()
        path = Path(options["checkpoint_file"]) if options.get("checkpoint_file") else None
        qs = DiscoveredCatalogueRecord.objects.select_related("provider").order_by("pk")
        if options.get("providers"):
            qs = qs.filter(provider__slug__in=options["providers"])
        if ids:
            qs = qs.filter(pk__in=ids)
        if path and path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                if (state["scope"] != scope or not isinstance(state["records"], list)
                        or not isinstance(state["selection"], dict)):
                    raise ValueError("checkpoint scope or structure differs")
                records = list(qs.filter(pk__in=state["selection"]))
                if {str(r.pk): fingerprint(r) for r in records} != state["selection"]:
                    raise ValueError("source identity changed or disappeared; review and start a new batch")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise CommandError(f"Unable to resume checkpoint: {exc}") from exc
        else:
            records = list(qs[:options["limit"]])
            if ids and len(records) != len(ids):
                raise CommandError("Requested records are missing or excluded by provider selection")
            state = {"scope": scope, "selection": {str(r.pk): fingerprint(r) for r in records},
                     "records": [], "last_completed_record_id": 0}
            if path:
                write_checkpoint(path, state)
        self.title_groups = Counter(
            (r.provider_id, _type_evidence(r)[0], _extract_year(r)[0], _normal_title(r.title))
            for r in DiscoveredCatalogueRecord.objects.select_related("provider").iterator()
        )
        completed = {row["record_id"] for row in state["records"]}
        prior = {row.discovered_record_id: row for row in
                 CanonicalBootstrapEvidence.objects.select_related("movie").filter(
                     discovered_record_id__in=[r.pk for r in records])}
        budget = CandidateDiscoveryBudget(limit=options["request_budget"])
        gateway = TMDbCandidateGateway(budget=budget, min_request_interval_seconds=options["interval"])
        gateway.max_retries = options["retries"]
        gateway.stop_on_rate_limit = True
        totals = dict.fromkeys(("evaluated", "exact_proposed", "verified", "ambiguous", "unmatched",
                               "conflicting", "created", "would_create", "already_present", "applied",
                               "rejected_candidates"), 0)
        rows = []
        stopped = False
        for record in records:
            if record.pk in completed:
                continue
            usage_before = budget.to_dict()
            totals_before = dict(totals)
            totals["evaluated"] += 1
            media, _, type_evidence = _type_evidence(record)
            year, year_evidence = _extract_year(record)
            result = {"record_id": record.pk, "provider": record.provider.slug,
                      "provider_native_id": record.provider_native_id, "title": record.title,
                      "year": year, "content_type": media, "evidence": [],
                      "status": "UNMATCHED", "tmdb_id": None, "action": "DRY_RUN"}
            complete = True
            try:
                evidence = prior.get(record.pk)
                if evidence:
                    if evidence_agrees(record, evidence):
                        result.update(status="VERIFIED", tmdb_id=evidence.movie.tmdb_id,
                                      action="already_verified", evidence=["record-specific verified evidence reused"])
                        totals["verified"] += 1
                        totals["already_present"] += 1
                    else:
                        result.update(status="CONFLICTING", evidence=["stored evidence conflicts with current identity"])
                        totals["conflicting"] += 1
                elif media is None or year is None:
                    result.update(status="AMBIGUOUS" if media is None else "UNMATCHED",
                                  evidence=type_evidence + [year_evidence])
                    totals["ambiguous" if media is None else "unmatched"] += 1
                else:
                    self.evaluate(record, media, year, gateway, options, totals, result)
                    if result["status"] == "CACHE_MISS":
                        complete = False
            except CandidateDiscoveryBudgetExceeded:
                result.update(status="BUDGET_EXHAUSTED", evidence=["budget exhausted; unfinished record remains resumable"])
                complete = False
            except requests.RequestException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                stopped = status in (401, 403, 429)
                result.update(status="SERVICE_RESTRICTED" if stopped else "REQUEST_FAILED",
                              evidence=[f"TMDb HTTP {status or 'transport/response error'}; resume after service recovery"])
                complete = False
            result["metrics"] = {
                "tmdb_requests": budget.attempted - usage_before["attempted"],
                "cache_hits": budget.cache_hits - usage_before["cache_hits"],
                "exact_candidates": totals["exact_proposed"] - totals_before["exact_proposed"],
                "rejected_candidates": totals["rejected_candidates"] - totals_before["rejected_candidates"],
            }
            rows.append(result)
            if not complete:
                break
            state["records"].append(result)
            state["last_completed_record_id"] = record.pk
            if path:
                write_checkpoint(path, state)
        self.stdout.write(json.dumps({
            "mode": "APPLY" if options["apply"] else "DRY_RUN", "statistics": totals,
            "tmdb_budget": budget.to_dict(), "stopped_for_service_restriction": stopped,
            "batch_selected": len(records), "batch_completed": len(state["records"]),
            "batch_remaining": len(records) - len(state["records"]),
            "checkpoint_file": str(path) if path else None, "records": rows,
        }, indent=2, ensure_ascii=False, default=str))
