"""Read-only audit of provider-native mapping coverage.

Usage::

    python manage.py audit_provider_mapping --provider raiplay --format json
    python manage.py audit_provider_mapping --provider raiplay --snapshot

The command performs no writes, no live requests, and no TMDb calls. It only
reads rows and reports counts:

* ProviderContent / ContentMapping / Offer / ScrapedContentMapping /
  SyncCursor / OfferEvidence / OfferPrice (snapshot mode only for the last two)

``locally_matchable_without_stored_mapping`` counts unmapped ProviderContent
rows that ``match_local_catalog`` would resolve today (exact title/year, or
unique title). ``local_catalog_match_analysis`` splits every other unmapped
row into mutually exclusive reasons (eligible-but-ambiguous, conflicting
year, media-type mismatch, no local candidate, ineligible content type), so
the seven buckets always sum to the unmapped total.
"""
from __future__ import annotations

import hashlib
import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from availability.domain.enums import ContentType
from availability.models import (
    ContentMapping,
    Offer,
    OfferEvidence,
    OfferPrice,
    ProviderContent,
    SyncCursor,
)
from availability.services.matching import (
    MATCHABLE_CONTENT_TYPES,
    TITLE_ONLY,
    TITLE_YEAR_EXACT,
    match_local_catalog,
)
from tracker.models import Movie, Provider, ScrapedContentMapping


class Command(BaseCommand):
    help = "Read-only audit of provider-native mapping coverage."

    def add_arguments(self, parser):
        parser.add_argument("--provider", required=True)
        parser.add_argument("--format", choices=["json", "text"], default="text")
        parser.add_argument("--snapshot", action="store_true",
                            help="Emit deterministic hashes of stored identities/fields.")

    def handle(self, *args, **options):
        provider_slug = options["provider"]
        try:
            provider = Provider.objects.get(slug=provider_slug)
        except Provider.DoesNotExist:
            raise CommandError(f"Unknown provider: {provider_slug}")

        pcs = ProviderContent.objects.filter(provider=provider)
        mappings = ContentMapping.objects.filter(provider=provider)
        total = pcs.count()
        mapped_pc_ids = set(mappings.values_list("provider_content_id", flat=True))
        pcs_with_mapping = pcs.filter(provider_content_id__in=mapped_pc_ids).count()

        mapping_by_method_confidence = {}
        for row in mappings.values("match_method", "confidence").annotate(count=Count("id")):
            method = row["match_method"]
            confidence = str(row["confidence"])
            mapping_by_method_confidence.setdefault(method, {})[confidence] = row["count"]

        offers = Offer.objects.filter(provider=provider)
        active_offers = offers.filter(currently_available=True).count()
        canonical_offers = offers.count()
        offered_pc_ids = set(offers.values_list("provider_content_id", flat=True))
        pc_with_mapping_no_offer = pcs.filter(
            provider_content_id__in=mapped_pc_ids
        ).exclude(provider_content_id__in=offered_pc_ids).count()

        unmapped_pcs = pcs.exclude(provider_content_id__in=mapped_pc_ids)
        local_match_analysis = {
            "unique_exact_title_year_matches": 0,
            "unique_title_only_matches": 0,
            "ambiguous_titles": 0,
            "conflicting_years": 0,
            "media_type_mismatches": 0,
            "no_candidate_in_local_tracker_movie": 0,
            "ineligible_for_local_mapping": 0,
        }
        locally_matchable_unmapped = 0
        for pc in unmapped_pcs:
            candidate = match_local_catalog(pc)
            if candidate is not None:
                locally_matchable_unmapped += 1
                if candidate.confidence == TITLE_YEAR_EXACT:
                    local_match_analysis["unique_exact_title_year_matches"] += 1
                elif candidate.confidence == TITLE_ONLY:
                    local_match_analysis["unique_title_only_matches"] += 1
                else:
                    raise RuntimeError("Unexpected local matcher confidence")
                continue

            title = (pc.title or "").strip()
            if not title or pc.content_type not in MATCHABLE_CONTENT_TYPES:
                local_match_analysis["ineligible_for_local_mapping"] += 1
                continue

            same_type_candidates = Movie.objects.filter(
                content_type=pc.content_type, title__iexact=title
            )
            other_type = (
                ContentType.SERIES if pc.content_type == ContentType.MOVIE
                else ContentType.MOVIE
            )
            if Movie.objects.filter(content_type=other_type, title__iexact=title).exists():
                local_match_analysis["media_type_mismatches"] += 1
                continue

            if pc.content_type == ContentType.MOVIE and pc.release_date is not None:
                candidate_years = set(same_type_candidates.values_list("year", flat=True))
                if candidate_years and pc.release_date.year not in candidate_years:
                    local_match_analysis["conflicting_years"] += 1
                    continue

            if same_type_candidates.values("tmdb_id").distinct().count() > 1:
                local_match_analysis["ambiguous_titles"] += 1
            else:
                local_match_analysis["no_candidate_in_local_tracker_movie"] += 1

        # Representative unresolved examples
        unresolved_examples = list(
            unmapped_pcs.values(
                "provider_content_id", "title", "content_type", "release_date"
            )[:20]
        )

        # Mismatch detection: a mapping is a *candidate* mismatch when the
        # stored mapping contradicts the provider record it points at —
        # content_type disagrees, or the local catalog title for the mapped
        # tmdb_id does not case-insensitively match the provider title.
        mismatches = []
        for m in mappings.select_related("content"):
            pc = m.content
            if pc is None or not pc.title:
                continue
            reasons = []
            if pc.content_type != m.content_type:
                reasons.append("content_type")
            local_titles = set(
                Movie.objects.filter(tmdb_id=m.tmdb_id).values_list("title", flat=True)
            )
            if local_titles and pc.title.strip().lower() not in {
                t.strip().lower() for t in local_titles
            }:
                reasons.append("title")
            if reasons:
                mismatches.append({
                    "provider_content_id": m.provider_content_id,
                    "tmdb_id": m.tmdb_id,
                    "provider_title": pc.title,
                    "provider_type": pc.content_type,
                    "mapping_type": m.content_type,
                    "mismatch_reasons": reasons,
                })

        report = {
            "provider": provider_slug,
            "total_provider_native_records": total,
            "records_with_content_mapping": pcs_with_mapping,
            "records_without_content_mapping": total - pcs_with_mapping,
            "content_mappings_total": ContentMapping.objects.filter(provider=provider).count(),
            "content_mappings_by_method_and_confidence": mapping_by_method_confidence,
            "canonical_offers": canonical_offers,
            "active_offers": active_offers,
            "records_with_mapping_but_no_offer": pc_with_mapping_no_offer,
            "locally_matchable_without_stored_mapping": locally_matchable_unmapped,
            "local_catalog_match_analysis": local_match_analysis,
            "mismatch_candidates": mismatches[:20],
            "representative_unresolved_examples": unresolved_examples,
            "legacy_scraped_content_mappings": ScrapedContentMapping.objects.filter(
                provider=provider
            ).count() if hasattr(ScrapedContentMapping, "provider") else ScrapedContentMapping.objects.count(),
            "sync_cursors": SyncCursor.objects.filter(provider=provider).count(),
        }

        if options["snapshot"]:
            report["snapshot"] = self._snapshot(provider)

        if options["format"] == "json":
            self.stdout.write(json.dumps(report, indent=2, default=str))
        else:
            for k, v in report.items():
                self.stdout.write(f"{k}: {v}")

    def _snapshot(self, provider):
        def h(rows):
            return hashlib.sha256(
                json.dumps(rows, sort_keys=True, default=str).encode()
            ).hexdigest()

        pcs = list(
            ProviderContent.objects.filter(provider=provider).values(
                "identity", "provider_content_id", "content_type", "title",
                "release_date", "currently_available", "metadata",
            ).order_by("identity")
        )
        cms = list(
            ContentMapping.objects.filter(provider=provider).values(
                "provider", "provider_content_id", "content_type", "tmdb_id",
                "confidence", "match_method",
            ).order_by("provider_content_id")
        )
        offers = list(
            Offer.objects.filter(provider=provider).values(
                "identity", "content_type", "tmdb_id", "offer_type",
                "currently_available", "provider_content_id",
            ).order_by("identity")
        )
        legacy = []
        try:
            legacy = list(
                ScrapedContentMapping.objects.filter(provider=provider).values().order_by("pk")
            )
        except Exception:
            legacy = list(ScrapedContentMapping.objects.values().order_by("pk"))
        evidence = list(
            OfferEvidence.objects.filter(provider_slug=provider.slug).values().order_by("pk")
        )
        prices = list(
            OfferPrice.objects.filter(provider_slug=provider.slug).values().order_by("pk")
        )
        cursors = list(
            SyncCursor.objects.filter(provider=provider).values().order_by("pk")
        )
        return {
            "provider_content": {"count": len(pcs), "hash": h(pcs)},
            "content_mappings": {"count": len(cms), "hash": h(cms)},
            "offers": {"count": len(offers), "hash": h(offers)},
            "offer_evidence": {"count": len(evidence), "hash": h(evidence)},
            "offer_prices": {"count": len(prices), "hash": h(prices)},
            "legacy_scraped_content_mappings": {"count": len(legacy), "hash": h(legacy)},
            "sync_cursors": {"count": len(cursors), "hash": h(cursors)},
        }
