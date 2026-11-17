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


_NATIVE_ID_KEYS = (
    "provider_content_id", "content_id", "contentId", "dramaId", "dramaID",
    "movieId", "movieID", "animeId", "animeID", "title_id", "id",
)


def provider_content_id_for_item(item):
    """Return an explicit, stable provider identifier when the adapter has one."""
    payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    for key in _NATIVE_ID_KEYS:
        value = payload.get(key)
        if value is None or isinstance(value, (dict, list, bool)):
            continue
        value = str(value).strip()
        if value:
            return value[:255]
    return ""


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
    native_id = provider_content_id_for_item(item)
    defaults = {
        "scraped_url": item.source_url,
        "scraped_content_type": item.content_type if item.content_type in {"movie", "series"} else "movie",
        "raw_payload": payload,
        "is_available": item.availability,
    }
    if apply_match:
        defaults.update(
            movie=movie,
            is_confirmed=False,
            confidence_score=confidence,
        )
    identity = {"provider": provider, "scraped_content_type": defaults["scraped_content_type"]}
    if native_id:
        mapping = ScrapedContentMapping.objects.filter(
            **identity, provider_content_id=native_id
        ).first()
        if mapping is None:
            # Promote a prior title-keyed row in place when a later observation
            # supplies the provider's native ID. This preserves its manual match.
            mapping = ScrapedContentMapping.objects.filter(
                **identity, provider_content_id="", scraped_title=item.title,
                scraped_year=item.year,
            ).first()
    else:
        mapping = ScrapedContentMapping.objects.filter(
            **identity, provider_content_id="", scraped_title=item.title,
            scraped_year=item.year,
        ).first()
    if mapping is None:
        mapping = ScrapedContentMapping(provider=provider)
    mapping.scraped_title = item.title
    mapping.scraped_year = item.year
    mapping.scraped_content_type = defaults["scraped_content_type"]
    if native_id:
        mapping.provider_content_id = native_id
    for field, value in defaults.items():
        setattr(mapping, field, value)
    mapping.save()
    ingest_scraped_mapping(mapping)
    return mapping
