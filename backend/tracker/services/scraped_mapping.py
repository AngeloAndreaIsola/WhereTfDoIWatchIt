"""One persistence seam for legacy scraper observations.

Scraper runs and TMDb matching used to maintain nearly identical writes to
``ScrapedContentMapping``.  Keeping them separate let a raw run skip changed
URLs/payloads while a matcher updated them, and made it easy for their
canonical projections to diverge.  This module owns the common observation
write and immediately projects it through the established ingestion bridge.
"""
from __future__ import annotations

from availability.services.legacy_ingest import ingest_scraped_mapping

from tracker.models import ScrapedContentMapping


def persist_scraped_item(
    *,
    provider,
    item,
    movie=None,
    confidence: float = 0.0,
    apply_match: bool = False,
):
    """Persist one scraper observation and return its legacy mapping.

    A raw scraper run refreshes observation-owned fields only; it must not
    erase a manual or previously confirmed association.  The matcher opts in
    to changing the association with ``apply_match=True``.  Both routes then
    project the exact same row into the canonical catalogue.
    """
    payload = dict(item.raw_payload) if isinstance(item.raw_payload, dict) else {}
    payload.setdefault("content_type", item.content_type)
    defaults = {
        "scraped_url": item.source_url,
        "raw_payload": payload,
        "is_available": item.availability,
    }
    if apply_match:
        defaults.update(
            movie=movie,
            is_confirmed=False,
            confidence_score=confidence,
        )
    mapping, _created = ScrapedContentMapping.objects.update_or_create(
        provider=provider,
        scraped_title=item.title,
        scraped_year=item.year,
        defaults=defaults,
    )
    ingest_scraped_mapping(mapping)
    return mapping
