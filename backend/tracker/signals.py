"""Deletion mirror for the legacy scraper ingestion bridge.

``ScrapedContentMapping`` rows are projected onto the canonical availability
tables (``availability.services.legacy_ingest``). Every write path mirrors
its write through the bridge, but *deletions* arrive from many directions —
Django admin, the scraper dedupe cleanup, provider cascade — so a single
``post_delete`` receiver keeps the two tables in lockstep instead of
scattering the mirror across every caller. Without it, deleting a legacy row
would leave an orphaned ``legacy-<pk>`` catalogue row that the unified
endpoint keeps serving forever.
"""
from __future__ import annotations

from django.db.models.signals import post_delete
from django.dispatch import receiver

from availability.services.legacy_ingest import delete_scraped_mapping

from .models import ScrapedContentMapping


@receiver(post_delete, sender=ScrapedContentMapping)
def _remove_canonical_projection(sender, instance, **kwargs):
    delete_scraped_mapping(instance)
