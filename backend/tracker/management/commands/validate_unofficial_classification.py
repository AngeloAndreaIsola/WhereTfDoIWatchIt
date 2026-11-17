"""Stratified, read-only classification audit against TMDb title/type evidence."""
from __future__ import annotations

import json
import random
import re
from collections import defaultdict

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from availability.domain.enums import ContentType
from availability.services.candidate_gateway import CandidateDiscoveryBudget, TMDbCandidateGateway
from tracker.models import DiscoveredCatalogueRecord
from tracker.services.catalogue_reconciliation import _extract_year, _normal_title, _type_evidence


def _audit_title(value):
    """Drop the known display-only Italian marker for external title lookup."""
    return re.sub(r"\s*\((?:ITA|Italiano)\)\s*$", "", str(value or ""), flags=re.IGNORECASE).strip()


class Command(BaseCommand):
    help = "Audit a stratified sample of proposed types using exact TMDb title/year/type evidence."

    def add_arguments(self, parser):
        parser.add_argument("--per-stratum", type=int, default=5)
        parser.add_argument("--seed", type=int, default=20261117)
        parser.add_argument("--request-budget", type=int, default=160)
        parser.add_argument("--interval", type=float, default=0.25)
        parser.add_argument("--allow-network", action="store_true")
        parser.add_argument("--summary-only", action="store_true")

    def handle(self, *args, **options):
        if options["per_stratum"] < 1 or options["request_budget"] < 1 or options["interval"] < 0:
            raise CommandError("per-stratum and request-budget must be positive; interval cannot be negative")
        if options["allow_network"] and not settings.TMDB_API_KEY:
            raise CommandError("--allow-network requires TMDB_API_KEY")
        rng = random.Random(options["seed"])
        all_records = list(DiscoveredCatalogueRecord.objects.select_related("provider").order_by("pk"))
        strata = defaultdict(list)
        duplicate_groups = defaultdict(list)
        classification = {}
        for record in all_records:
            media_type, _, evidence = _type_evidence(record)
            year, _ = _extract_year(record)
            classification[record.pk] = (media_type, year)
            if record.provider.slug == "hydrahd" and media_type == "movie":
                strata["hydrahd_movies"].append(record)
            if record.provider.slug == "hydrahd" and media_type == "series":
                strata["hydrahd_series"].append(record)
            if record.provider.slug == "animeworld" and media_type:
                strata["animeworld_classified"].append(record)
            if year is None:
                strata["missing_year"].append(record)
            if media_type is None:
                strata["ambiguous"].append(record)
            if any("conflict" in item for item in evidence):
                strata["conflicting_metadata"].append(record)
            if media_type and record.title:
                duplicate_groups[(record.provider.slug, media_type, _normal_title(record.title), year)].append(record)
        for group in duplicate_groups.values():
            if len(group) > 1:
                strata["same_title_releases"].extend(group)

        sample = {}
        selected_ids = set()
        for name, records in sorted(strata.items()):
            unique = {record.pk: record for record in records}
            candidates = list(unique.values())
            rng.shuffle(candidates)
            chosen = [row for row in candidates if row.pk not in selected_ids][:options["per_stratum"]]
            for row in chosen:
                selected_ids.add(row.pk)
            sample[name] = chosen

        budget = CandidateDiscoveryBudget(limit=options["request_budget"])
        gateway = TMDbCandidateGateway(budget=budget,
                                       min_request_interval_seconds=options["interval"])
        observed = []
        correct = incorrect = unresolved = strong_type_evidence_pending = 0
        for stratum, records in sample.items():
            for record in records:
                proposed_type, year = classification[record.pk]
                row = {"record_id": record.pk, "provider": record.provider.slug,
                       "title": record.title, "year": year, "proposed_type": proposed_type,
                       "stratum": stratum, "status": "UNVERIFIABLE", "tmdb_type": None,
                       "evidence": []}
                found = []
                for tmdb_type in (ContentType.MOVIE, ContentType.SERIES):
                    query_title = _audit_title(record.title)
                    results = gateway.search(query_title, year=year, content_type=tmdb_type,
                                             authorize_network=options["allow_network"])
                    for result in results:
                        if ((year is None or result.get("year") == year)
                                and _normal_title(result.get("title")) == _normal_title(query_title)):
                            found.append((tmdb_type, result))
                # Multiple TMDb identities are not a ground-truth label: same-title
                # remakes/adaptations need a human or stronger identifier.
                unique = {(kind, int(item["tmdb_id"])): item for kind, item in found if item.get("tmdb_id")}
                if len(unique) != 1 or len({kind for kind, _ in unique}) != 1:
                    row["status"] = "AMBIGUOUS_EVIDENCE" if unique else "UNVERIFIABLE"
                    row["evidence"] = ["TMDb exact title evidence is absent or has multiple identities",
                                       *( ["release year unavailable; title-only evidence cannot establish canonical identity"] if year is None else [] )]
                    unresolved += 1
                    observed.append(row)
                    continue
                tmdb_type, tmdb_id = next(iter(unique))
                details = gateway.get_details(tmdb_id, content_type=tmdb_type,
                                              authorize_network=options["allow_network"])
                if not details or int(details.get("tmdb_id") or 0) != tmdb_id:
                    row["evidence"] = ["TMDb details did not verify the candidate identifier"]
                    unresolved += 1
                    observed.append(row)
                    continue
                valid = (details.get("content_type") == tmdb_type
                         and (year is None or details.get("year") == year)
                         and _normal_title(details.get("title")) == _normal_title(_audit_title(record.title)))
                if not valid:
                    row["status"] = "CONFLICTING_EVIDENCE"
                    row["evidence"] = ["TMDb details disagree with exact title/year/type search result"]
                    unresolved += 1
                    observed.append(row)
                    continue
                row.update({"tmdb_type": tmdb_type, "tmdb_id": tmdb_id,
                            "tmdb_title": details.get("title"), "tmdb_year": details.get("year"),
                            "status": ("STRONG_TYPE_EVIDENCE_REVIEW" if proposed_type is None else
                                       "CORRECT" if proposed_type == tmdb_type else "INCORRECT"),
                            "evidence": ["exact normalized title",
                                         *( ["exact release year"] if year is not None else ["title-only type evidence; canonical match remains prohibited"] ),
                                         "TMDb details verified identifier and media type"]})
                if proposed_type is None:
                    strong_type_evidence_pending += 1
                    observed.append(row)
                    continue
                if proposed_type == tmdb_type:
                    correct += 1
                else:
                    incorrect += 1
                observed.append(row)

        total_evaluable = correct + incorrect
        by_stratum = defaultdict(lambda: {"sampled": 0, "correct": 0, "incorrect": 0,
                                          "unverifiable_or_ambiguous": 0,
                                          "strong_type_evidence_pending_review": 0})
        for row in observed:
            bucket = by_stratum[row["stratum"]]
            bucket["sampled"] += 1
            if row["status"] == "CORRECT":
                bucket["correct"] += 1
            elif row["status"] == "INCORRECT":
                bucket["incorrect"] += 1
            elif row["status"] == "STRONG_TYPE_EVIDENCE_REVIEW":
                bucket["strong_type_evidence_pending_review"] += 1
            else:
                bucket["unverifiable_or_ambiguous"] += 1
        output = {
            "mode": "READ_ONLY_AUDIT",
            "population": len(all_records),
            "sample_size": len(observed),
            "population_by_stratum": {name: len(rows) for name, rows in sorted(strata.items())},
            "sample_by_stratum": {name: len(sample.get(name, [])) for name in (
                "hydrahd_movies", "hydrahd_series", "animeworld_classified", "missing_year",
                "same_title_releases", "ambiguous", "conflicting_metadata",
            )},
            "results_by_stratum": dict(sorted(by_stratum.items())),
            "observed_accuracy": correct / total_evaluable if total_evaluable else None,
            "correct": correct, "incorrect": incorrect,
            "unverifiable_or_ambiguous": unresolved,
            "strong_type_evidence_pending_review": strong_type_evidence_pending,
            "accuracy_denominator": total_evaluable,
            "tmdb_budget": budget.to_dict(),
            "records": observed,
        }
        if options["summary_only"]:
            output.pop("records")
        self.stdout.write(json.dumps(output, indent=2, ensure_ascii=False, default=str))
