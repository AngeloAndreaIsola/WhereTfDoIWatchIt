"""Read-only audit of provider-native mapping coverage.

Usage::

    python manage.py audit_provider_mapping --provider raiplay --format json

The command performs no writes, no live requests, and no TMDb calls. It only
reads ProviderContent / ContentMapping / Offer / ScrapedContentMapping /
SyncCursor rows and reports counts.
"""
from __future__ import annotations

import hashlib
import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q

from availability.models import (
    ContentMapping,
    Offer,
    ProviderContent,
    SyncCursor,
)
from tracker.models import Provider, ScrapedContentMapping


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
        total = pcs.count()
        mapped_ids = set(
            ContentMapping.objects.filter(provider=provider).values_list(
                "provider_content_id", flat=True
            )
        )
        with_mapping = pcs.filter(provider_content_id__in=mapped_ids).count() \
            if mapped_ids else 0
        # more accurate: join via content FK / provider_content_id
        mapped_pc_count = 0
        mapping_by_method = {}
        for m in ContentMapping.objects.filter(provider=provider):
            mapping_by_method.setdefault(m.match_method, {}).setdefault(
                str(m.confidence), 0
            )
            mapping_by_method[m.match_method][str(m.confidence)] += 0  # placeholder

        # Recompute properly
        mapping_by_method = {}
        for m in ContentMapping.objects.filter(provider=provider):
            key = m.match_method
            mapping_by_method.setdefault(key, {"count": 0, "confidences": {}})
            mapping_by_method[key]["count"] += 1
            mapping_by_method[key]["confidences"][str(m.confidence)] = (
                mapping_by_method[key]["confidences"].get(str(m.confidence), 0) + 1
            )

        # ProviderContent rows that have a ContentMapping (by provider_content_id)
        mapped_pc_ids = set(
            ContentMapping.objects.filter(provider=provider).values_list(
                "provider_content_id", flat=True
            )
        )
        pcs_with_mapping = pcs.filter(provider_content_id__in=mapped_pc_ids).count()

        offers = Offer.objects.filter(provider=provider)
        active_offers = offers.filter(currently_available=True).count()
        canonical_offers = offers.count()

        # ProviderContent with mapping but no offer
        pc_with_mapping_no_offer = 0
        for pc in pcs.filter(provider_content_id__in=mapped_pc_ids):
            has_offer = offers.filter(provider_content_id=pc.provider_content_id).exists()
            if not has_offer:
                pc_with_mapping_no_offer += 1

        # Locally matchable without stored mapping: ProviderContent rows whose
        # title/year would match a Movie? We can't do TMDb here; report count of
        # unmapped ProviderContent rows as candidates.
        unmapped_pcs = pcs.exclude(provider_content_id__in=mapped_pc_ids)
        locally_matchable_unmapped = unmapped_pcs.count()

        # Representative unresolved examples
        unresolved_examples = list(
            unmapped_pcs.values(
                "provider_content_id", "title", "content_type", "release_date"
            )[:20]
        )

        # Mismatch detection: title/year/media-type/alias mismatches between
        # ContentMapping target (tmdb_id) and ProviderContent
        mismatches = []
        for m in ContentMapping.objects.filter(provider=provider).select_related("content"):
            pc = m.content
            if pc is not None and pc.title:
                mismatches.append({
                    "provider_content_id": m.provider_content_id,
                    "tmdb_id": m.tmdb_id,
                    "provider_title": pc.title,
                    "provider_type": pc.content_type,
                    "mapping_type": m.content_type,
                })

        report = {
            "provider": provider_slug,
            "total_provider_native_records": total,
            "records_with_content_mapping": pcs_with_mapping,
            "records_without_content_mapping": total - pcs_with_mapping,
            "content_mappings_total": ContentMapping.objects.filter(provider=provider).count(),
            "valid_mappings_by_match_method": mapping_by_method,
            "canonical_offers": canonical_offers,
            "active_offers": active_offers,
            "records_with_mapping_but_no_offer": pc_with_mapping_no_offer,
            "locally_matchable_without_stored_mapping": locally_matchable_unmapped,
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
        cursors = list(
            SyncCursor.objects.filter(provider=provider).values().order_by("pk")
        )
        return {
            "provider_content": {"count": len(pcs), "hash": h(pcs)},
            "content_mappings": {"count": len(cms), "hash": h(cms)},
            "offers": {"count": len(offers), "hash": h(offers)},
            "legacy_scraped_content_mappings": {"count": len(legacy), "hash": h(legacy)},
            "sync_cursors": {"count": len(cursors), "hash": h(cursors)},
        }
