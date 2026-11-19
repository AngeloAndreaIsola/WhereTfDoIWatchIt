"""Temporary ingestion bridge: legacy scraper store -> canonical models.

This module is the *write* side of the read-path migration. The legacy
scrapers still persist their findings in ``tracker.ScrapedContentMapping``
(their review/confirm UI depends on that table), but every read path — the
unified catalogue included — now goes through the canonical availability
models. The bridge projects one legacy row into:

- ``ProviderContent`` — the provider-native catalogue entry, carrying the
  provider's own title/year/url plus the scrape metadata the unified
  catalogue response needs (poster, languages, country);
- ``ContentMapping`` — only when the legacy row is linked to a ``Movie``,
  preserving the legacy confidence/confirmation semantics through
  ``MatchMethod.MANUAL`` (confirmed) or ``MatchMethod.TITLE_YEAR``.

Provenance is kept intact: everything written here is attributed to the
``legacy_scraper`` ``Source``, never to a live collector, and no ``Offer``
row is invented — a legacy scrape is a catalogue observation, not a
commercial offer, so watchability/ranking semantics are untouched.

Deliberately local-only: no HTTP, no refresh, no TMDb. It is called from the
legacy scraper write paths (after each legacy row is written) and from the
``ingest_legacy_scraper_catalogue`` management command for backfilling rows
that existed before this bridge.

``tracker.ScrapedContentMapping`` stays in place as an ingestion-era table;
this bridge is what makes it stop being a read path.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from django.db import transaction

from ..domain.enums import ContentType, MatchMethod, SourceType
from ..models import LEGACY_SCRAPER_SOURCE_SLUG, ContentMapping, ProviderContent, Source
from ..persistence.repositories import MappingRepository, ProviderContentRepository
from .write_barrier import writer_barrier

_SERIES_VALUES = {"series", "show", "tv"}


def legacy_scraper_source() -> Source:
    """The provenance row every bridged observation is attributed to."""
    source, _ = Source.objects.get_or_create(
        slug=LEGACY_SCRAPER_SOURCE_SLUG,
        defaults={"name": "Legacy scraper (ingested)", "type": SourceType.WEB},
    )
    return source


class LegacyProjectionTypeConflict(ValueError):
    """Existing identity needs separately authorized investigation/correction."""


def _normalized_type(value):
    value = str(value or "").strip().lower()
    if value in _SERIES_VALUES:
        return ContentType.SERIES
    if value == ContentType.MOVIE:
        return ContentType.MOVIE
    return None


def _content_type(mapping) -> str:
    """Canonical content type for one legacy row.

    A valid stored typed field is authoritative, including when payload
    metadata disagrees. Only old/invalid typed fields use payload, linked Movie,
    then the historical movie default. Identity conflicts are checked before
    any writes by ``legacy_type_report``; inference never retypes saved data.
    """
    if mapping.scraped_content_type in {ContentType.MOVIE, ContentType.SERIES}:
        return mapping.scraped_content_type
    payload = mapping.raw_payload if isinstance(mapping.raw_payload, dict) else {}
    return (_normalized_type(payload.get("content_type"))
            or _normalized_type(getattr(mapping.movie, "content_type", None))
            or ContentType.MOVIE)


def legacy_type_report(mapping, *, for_update=False) -> dict:
    """Read-only diagnosis used by ingestion and the dry-run audit command.

    Existing wrong-type projections are not automatically deleted, retyped or
    duplicated: candidates, audits and evidence may depend on their identity.
    Even unverified drift requires separately authorized repair.
    Ingestion requests row locks inside its transaction; the audit command
    only performs ordinary SELECTs. Neither mode changes any row.
    """
    desired = _content_type(mapping)
    payload = mapping.raw_payload if isinstance(mapping.raw_payload, dict) else {}
    payload_type = _normalized_type(payload.get("content_type"))
    projections = ProviderContent.objects.filter(
        provider_id=mapping.provider_id, provider_content_id=_provider_content_id(mapping),
    ).order_by("pk")
    canonical = ContentMapping.objects.filter(
        provider_id=mapping.provider_id, provider_content_id=_provider_content_id(mapping),
    ).order_by("pk")
    if for_update:
        projections = projections.select_for_update()
        canonical = canonical.select_for_update()
    # Lock content before mappings, matching the canonical review services.
    projections = list(projections.values("id", "content_type"))
    canonical = list(canonical.values("id", "content_type", "tmdb_id", "match_method"))
    conflicts = []
    if mapping.movie_id and mapping.movie.content_type != desired:
        conflicts.append("linked_movie_type_conflict")
    if any(row["content_type"] != desired for row in projections):
        conflicts.append("projection_type_drift")
    if any(row["content_type"] != desired for row in canonical):
        conflicts.append("canonical_mapping_type_conflict")
    if mapping.movie_id and any(
        row["match_method"] == MatchMethod.MANUAL
        and (row["tmdb_id"], row["content_type"]) != (mapping.movie.tmdb_id, desired)
        for row in canonical
    ):
        conflicts.append("verified_mapping_identity_conflict")
    return {"legacy_id": mapping.pk, "provider_id": mapping.provider_id,
            "stored_type": mapping.scraped_content_type, "payload_type": payload_type,
            "proposed_type": desired,
            "linked_movie": ({"id": mapping.movie_id, "tmdb_id": mapping.movie.tmdb_id,
                              "content_type": mapping.movie.content_type} if mapping.movie_id else None),
            "projections": projections, "canonical_mappings": canonical,
            "conflicts": conflicts,
            "payload_type_disagrees": bool(payload_type and payload_type != desired),
            "proposal": ("Separate authorization required to investigate and repair identity drift"
                         if conflicts else "Use stored type; ignore conflicting payload metadata")}


def _languages(values) -> list[str]:
    """Upper-cased, de-duplicated language codes (legacy read semantics)."""
    normalized = []
    for value in values or []:
        code = str(value).strip().upper()
        if code and code not in normalized:
            normalized.append(code)
    return normalized


def _release_date(year: Optional[int]) -> Optional[date]:
    """Year-precision release date, matching the canonical storage convention."""
    if not year:
        return None
    try:
        return date(int(year), 1, 1)
    except (TypeError, ValueError):
        return None


def _poster(payload: dict, movie) -> str:
    if movie is not None and getattr(movie, "poster_path", ""):
        return movie.poster_path
    for key in ("poster_path", "poster", "image"):
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def _metadata(payload: dict, mapping, movie) -> dict:
    """Scrape metadata with no first-class column; empty values are omitted
    so the repository's shallow merge never erases an earlier observation."""
    metadata = {}
    poster = _poster(payload, movie)
    if poster:
        metadata["poster"] = poster
    audio = _languages(payload.get("audio_languages") or payload.get("audio"))
    if audio:
        metadata["audio_languages"] = audio
    subtitles = _languages(payload.get("subtitle_languages") or payload.get("subtitles"))
    if subtitles:
        metadata["subtitle_languages"] = subtitles
    country = str(mapping.country_code or "").strip().upper()
    if country:
        metadata["country_code"] = country
    return metadata


def _provider_content_id(mapping) -> str:
    """Stable canonical identity for one legacy row.

    Derived from the legacy primary key: title/year and native-ID uniqueness
    are scoped by ``scraped_content_type``. A movie and series can share their
    title or numeric ID while retaining separate legacy/canonical identities.
    Scraped URLs are not unique (an import can stamp one URL on a whole run).
    """
    return f"legacy-{mapping.pk}"


def ingest_scraped_mapping(mapping) -> ProviderContent:
    """Project one legacy scraper row onto the canonical availability models.

    Idempotent: re-ingesting the same row (after a re-scrape or a manual
    confirmation) upserts the same ``ProviderContent``/``ContentMapping``
    identities. Returns the canonical ``ProviderContent`` row.

    Every canonical mutation (the ``Source`` provenance row, the
    ``ProviderContent`` upsert and the optional ``ContentMapping``) runs in one
    transaction behind the shared writer barrier.  The barrier is acquired
    *before* the transaction opens and released after it commits or rolls back;
    if it cannot be acquired nothing is written.  ``writer_barrier`` is
    re-entrant, so a caller that already holds it (provider deletion) does not
    take the session advisory lock twice.
    """
    with writer_barrier("legacy scraper ingestion"):
        with transaction.atomic():
            # Do not ingest stale caller state after an explicit confirmation.
            mapping = type(mapping).objects.select_for_update().get(pk=mapping.pk)
            return _ingest_scraped_mapping_locked(mapping)


def _ingest_scraped_mapping_locked(mapping) -> ProviderContent:
    """The mutating body of :func:`ingest_scraped_mapping`, barrier held."""
    payload = mapping.raw_payload if isinstance(mapping.raw_payload, dict) else {}
    movie = mapping.movie
    report = legacy_type_report(mapping, for_update=True)
    if report["conflicts"]:
        raise LegacyProjectionTypeConflict(
            f"Legacy row {mapping.pk}: {', '.join(report['conflicts'])}; "
            "run audit_legacy_projection_types (read-only); correction requires separate authorization"
        )
    content_type = report["proposed_type"]

    year = None
    if movie is not None and getattr(movie, "year", None):
        year = movie.year
    elif mapping.scraped_year:
        year = mapping.scraped_year

    provider = mapping.provider
    source = legacy_scraper_source()
    provider_content_id = _provider_content_id(mapping)

    content, _created = ProviderContentRepository().upsert(
        provider=provider,
        source=source,
        provider_content_id=provider_content_id,
        content_type=content_type,
        title=mapping.scraped_title or "",
        release_date=_release_date(year),
        provider_url=mapping.scraped_url or "",
        metadata=_metadata(payload, mapping, movie),
    )

    # ``upsert`` records the observation as available; a legacy row that has
    # been marked unavailable must not read as currently available.
    if not mapping.is_available and content.currently_available:
        content.currently_available = False
        content.save(update_fields=["currently_available", "updated_at"])

    # A listing card is a title discovery, not proof of playable access. Keep
    # it in ProviderContent for catalogue review, but withhold an automatic
    # TMDb relation so it cannot feed the per-title offer path. Manual
    # confirmation remains the explicit route to a canonical mapping.
    unverified_discovery = bool(payload.get("unverified_discovery"))
    if (
        movie is not None
        and getattr(movie, "tmdb_id", 0)
        and (not unverified_discovery or mapping.is_confirmed)
    ):
        match_method = (
            MatchMethod.MANUAL if mapping.is_confirmed else MatchMethod.TITLE_YEAR
        )
        MappingRepository().record(
            provider=provider,
            source=source,
            provider_content_id=provider_content_id,
            content_type=content_type,
            tmdb_id=movie.tmdb_id,
            confidence=float(mapping.confidence_score or 0.0),
            match_method=match_method,
            content=content,
        )
    elif unverified_discovery and not mapping.is_confirmed:
        # If an earlier version attached an automatic candidate, revoke that
        # candidate projection while retaining any explicit manual decision.
        ContentMapping.objects.filter(
            provider=provider,
            provider_content_id=provider_content_id,
        ).exclude(match_method=MatchMethod.MANUAL).delete()

    return content


def delete_scraped_mapping(mapping) -> None:
    """Remove the canonical projection of one legacy row (deletion mirror).

    The identity is derived from the legacy primary key, so this can only
    ever remove rows this bridge wrote. Called from the ``post_delete``
    receiver on ``ScrapedContentMapping`` (admin deletions, dedupe cleanups)
    so no orphaned ``legacy-<pk>`` catalogue row survives its source row.

    Both deletions run in one transaction behind the shared writer barrier,
    acquired before the transaction.  If the barrier is unavailable the
    deletion is refused instead of leaving the canonical tables unprotected.
    The barrier is re-entrant: when the receiver fires inside provider
    deletion, the endpoint's existing acquisition covers this work too.
    """
    with writer_barrier("legacy scraper projection deletion"):
        with transaction.atomic():
            _delete_scraped_mapping_locked(mapping)


def _delete_scraped_mapping_locked(mapping) -> None:
    """The deleting body of :func:`delete_scraped_mapping`, barrier held."""
    provider_content_id = _provider_content_id(mapping)
    ContentMapping.objects.filter(
        provider=mapping.provider, provider_content_id=provider_content_id
    ).delete()
    ProviderContent.objects.filter(
        provider=mapping.provider, provider_content_id=provider_content_id
    ).delete()


def ingest_all() -> int:
    """Backfill: project every legacy row onto the canonical models.

    Used once when a deployment switches its read paths to the canonical
    models (and after the fact for rows created before this bridge existed).
    Returns the number of legacy rows ingested.

    The barrier is acquired **per item** by :func:`ingest_scraped_mapping`, not
    around the whole batch.  Each item commits independently (the historical
    behaviour), so a failing row rolls back only its own canonical projection
    and the already-ingested rows stay committed.  A batch-wide lock would hold
    the shared advisory lock for the whole backfill and would change those
    failure semantics for no correctness gain.
    """
    from tracker.models import ScrapedContentMapping

    count = 0
    rows = ScrapedContentMapping.objects.select_related("provider", "movie").iterator(
        chunk_size=500
    )
    for mapping in rows:
        ingest_scraped_mapping(mapping)
        count += 1
    return count
