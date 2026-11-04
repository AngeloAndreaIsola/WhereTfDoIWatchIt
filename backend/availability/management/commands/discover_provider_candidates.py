"""CLI command for budgeted, durable TMDb candidate discovery for unmapped ProviderContent entries."""
from __future__ import annotations

import json
import logging
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Exists, OuterRef

from availability.domain.enums import CandidateResolutionCategory, ContentType
from availability.models import ContentMapping, ProviderContent
from availability.services.candidate_gateway import (
    CandidateDiscoveryBudget,
    CandidateDiscoveryBudgetExceeded,
    TMDbCandidateGateway,
)
from availability.services.candidate_scoring import (
    categorize_candidates,
    persist_candidates,
    score_candidate,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Discover TMDb candidates for unmapped ProviderContent entries under strict finite budgets, "
        "supporting offline preview and authorized network lookups without creating ContentMapping or Movie rows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--preview",
            action="store_true",
            default=False,
            help="Offline no-write preview: inspect unmapped entries, check cache, output proposed queries without making network requests.",
        )
        parser.add_argument(
            "--authorize-network",
            action="store_true",
            default=False,
            help="Explicitly authorize external TMDb network requests. When absent, runs strictly offline/from cache.",
        )
        parser.add_argument(
            "--budget",
            type=int,
            default=None,
            help="Maximum finite TMDb HTTP requests allowed for this run. Required when --authorize-network is set.",
        )
        parser.add_argument(
            "--provider",
            type=str,
            default=None,
            help="Filter unmapped ProviderContent by provider slug.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of unmapped ProviderContent entries to process.",
        )
        parser.add_argument(
            "--write",
            action="store_true",
            default=False,
            help="Persist discovered candidates to ProviderContentCandidate table. If omitted, no candidate rows are written.",
        )

    def handle(self, *args, **options):
        is_preview = options["preview"]
        authorize_network = options["authorize_network"]
        budget_limit = options["budget"]
        provider_slug = options["provider"]
        limit = options["limit"]
        write_enabled = options["write"]

        # Validate CLI execution mode combinations:
        # 1. Contradictory flag check: --preview with --write or --authorize-network is invalid
        if is_preview and (authorize_network or write_enabled):
            raise CommandError("--preview cannot be combined with --authorize-network or --write.")

        # 2. Candidate persistence requires explicit authorization
        if write_enabled and not authorize_network:
            raise CommandError("--write requires --authorize-network. Dry-run/offline runs cannot write to database.")

        # 3. Network lookup requires explicit authorization AND a positive finite budget
        if authorize_network:
            if budget_limit is None or budget_limit <= 0:
                raise CommandError("--authorize-network requires an explicit positive integer --budget <N>.")
        elif budget_limit is not None:
            # Providing budget without authorize-network is contradictory
            raise CommandError("--budget was supplied without --authorize-network.")

        effective_budget = budget_limit or 0
        budget = (
            CandidateDiscoveryBudget(limit=effective_budget)
            if authorize_network
            else None
        )

        # Allow cache writes only when --write is explicitly enabled
        gateway = TMDbCandidateGateway(budget=budget, allow_cache_writes=write_enabled)

        # Query unmapped ProviderContent entries
        # Preserve manual mapping precedence: exclude any content that already has an existing mapping
        mapping_exists_subquery = ContentMapping.objects.filter(
            provider=OuterRef("provider"),
            provider_content_id=OuterRef("provider_content_id"),
        )

        unmapped_qs = (
            ProviderContent.objects.filter(
                content_type__in=[ContentType.MOVIE, ContentType.SERIES],
                currently_available=True,
            )
            .annotate(has_mapping=Exists(mapping_exists_subquery))
            .filter(has_mapping=False)
            .order_by("provider__slug", "provider_content_id")
        )

        if provider_slug:
            unmapped_qs = unmapped_qs.filter(provider__slug=provider_slug)

        total_unmapped_count = unmapped_qs.count()
        if limit:
            unmapped_qs = unmapped_qs[:limit]

        items_to_process = list(unmapped_qs)

        self.stdout.write(
            f"Candidate discovery run started: mode={'preview' if is_preview or not authorize_network else 'authorized'} "
            f"write={'enabled' if write_enabled else 'disabled'} unmapped_available={total_unmapped_count} "
            f"selected={len(items_to_process)}"
        )

        stats: dict[str, Any] = {
            "processed": 0,
            "cache_hits": 0,
            "network_requests": 0,
            "unique_candidates": 0,
            "ambiguous": 0,
            "conflict": 0,
            "no_results": 0,
            "candidates_persisted": 0,
            "budget_exhausted": False,
        }

        preview_records: list[dict[str, Any]] = []

        for content in items_to_process:
            query = content.title
            year = content.release_date.year if content.release_date else None
            content_type = content.content_type

            cache_status = "cache_unchecked"

            if is_preview or not authorize_network:
                # Offline / preview mode: 0 network, 0 writes. Do not inspect
                # cache internals here; the gateway owns cache identity/TTL.
                preview_records.append({
                    "provider": content.provider.slug,
                    "content_id": content.provider_content_id,
                    "title": content.title,
                    "year": year,
                    "content_type": content_type,
                    "cache_status": cache_status,
                })
                stats["processed"] += 1
                continue

            # Authorized network / cached lookup
            try:
                raw_results = gateway.search(
                    query=query,
                    year=year,
                    content_type=content_type,
                    authorize_network=authorize_network,
                )
            except CandidateDiscoveryBudgetExceeded as exc:
                self.stdout.write(self.style.WARNING(str(exc)))
                stats["budget_exhausted"] = True
                break

            # Score candidates
            scored_candidates = []
            for item in raw_results:
                cand_score, evidence = score_candidate(content, item)
                scored_candidates.append({
                    "tmdb_id": item["tmdb_id"],
                    "title": item["title"],
                    "year": item["year"],
                    "content_type": item["content_type"],
                    "confidence_score": cand_score,
                    "scoring_evidence": evidence,
                })

            category = categorize_candidates(content, scored_candidates)
            if category == CandidateResolutionCategory.UNIQUE_CANDIDATE:
                stats["unique_candidates"] += 1
            elif category == CandidateResolutionCategory.AMBIGUOUS:
                stats["ambiguous"] += 1
            elif category == CandidateResolutionCategory.CONFLICT:
                stats["conflict"] += 1
            elif category == CandidateResolutionCategory.NO_RESULTS:
                stats["no_results"] += 1

            if write_enabled:
                persisted = persist_candidates(content, scored_candidates, category)
                stats["candidates_persisted"] += len(persisted)

            stats["processed"] += 1

        if budget:
            budget_stats = budget.to_dict()
            stats["network_requests"] = budget_stats["attempted"]
            stats["budget_remaining"] = budget_stats["remaining"]

        output_summary = {
            "mode": "preview" if is_preview or not authorize_network else "authorized",
            "write": write_enabled,
            "stats": stats,
        }
        if is_preview or not authorize_network:
            output_summary["preview_sample"] = preview_records[:20]

        self.stdout.write(json.dumps(output_summary, indent=2))
        self.stdout.write(self.style.SUCCESS("Candidate discovery command finished."))
