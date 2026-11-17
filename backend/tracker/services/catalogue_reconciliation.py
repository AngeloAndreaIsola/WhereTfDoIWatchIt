"""Conservative, auditable reconciliation for unofficial catalogue observations.

The default path is read-only.  It classifies only on source-specific evidence,
proposes canonical matches only from identity-grade evidence, and never treats
catalogue presence as an availability offer.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict

from django.db import transaction

from availability.models import ContentMapping, ProviderContent, Source
from tracker.models import DiscoveredCatalogueRecord, Movie, ScrapedContentMapping


def _normal_title(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _type_evidence(record):
    metadata = record.original_metadata if isinstance(record.original_metadata, dict) else {}
    provider = record.provider.slug
    partition = str(metadata.get("catalogue_partition") or "").casefold()
    listing_hint = str(metadata.get("listing_content_type_hint") or "").casefold()
    if provider == "hydrahd" and partition in {"movies", "tv-shows"}:
        value = "movie" if partition == "movies" else "series"
        if listing_hint in {"movie", "series"} and listing_hint != value:
            return None, 0.0, [f"HydraHD partition={partition}", f"conflicting listing hint={listing_hint}"]
        return value, 0.99, [f"HydraHD partition={partition}"]
    if provider == "animeworld" and metadata.get("source_kind") == "official_rss":
        hint = str(metadata.get("anime_type") or "").strip().casefold()
        if hint == "anime":
            if listing_hint in {"movie", "series"} and listing_hint != "series":
                return None, 0.0, ["AnimeWorld official RSS type=anime", f"conflicting listing hint={listing_hint}"]
            return "series", 0.98, ["AnimeWorld official RSS type=anime"]
        if hint in {"movie", "film"}:
            if listing_hint in {"movie", "series"} and listing_hint != "movie":
                return None, 0.0, [f"AnimeWorld official RSS type={hint}", f"conflicting listing hint={listing_hint}"]
            return "movie", 0.98, [f"AnimeWorld official RSS type={hint}"]
    # A legacy listing hint can be a parser default.  Keep it visible as weak
    # evidence, but do not promote it to a classification.
    hint = listing_hint
    evidence = [f"unverified listing hint={hint}"] if hint in {"movie", "series"} else []
    return None, 0.0, evidence


def _extract_year(record):
    metadata = record.original_metadata if isinstance(record.original_metadata, dict) else {}
    candidates = [metadata.get("year"), metadata.get("release_year")]
    for value in candidates:
        if isinstance(value, bool) or not re.fullmatch(r"\d{4}", str(value or "").strip()):
            continue
        year = int(value)
        if 1870 <= year <= 2100:
            return year, "explicit listing year"
    return None, "year absent"


def _external_tmdb_id(record):
    metadata = record.original_metadata if isinstance(record.original_metadata, dict) else {}
    external = metadata.get("external_ids")
    if not isinstance(external, dict):
        return None
    value = external.get("tmdb") or external.get("tmdb_id")
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    value = str(value).strip()
    return value if value.isdigit() else None


def _match(record, classified_type, year, titles_by_norm, movies_by_tmdb):
    if not classified_type:
        return {"status": "UNMATCHED", "confidence": 0.0, "evidence": ["media type unresolved"], "candidates": []}
    tmdb_id = _external_tmdb_id(record)
    if tmdb_id:
        target = movies_by_tmdb.get(int(tmdb_id))
        if target and target.content_type == classified_type:
            return {"status": "MATCH_HIGH", "confidence": 1.0, "evidence": ["source metadata contains matching TMDb ID", "media type agrees"], "candidates": [_movie_candidate(target)]}
        return {"status": "POSSIBLE_REVIEW", "confidence": 0.0, "evidence": ["explicit TMDb ID is absent from canonical catalogue or has a type conflict"], "candidates": []}

    title = _normal_title(record.title)
    candidates = [m for m in titles_by_norm.get(title, []) if m.content_type == classified_type]
    if not candidates:
        conflicting = titles_by_norm.get(title, [])
        if conflicting:
            return {"status": "POSSIBLE_REVIEW", "confidence": 0.0, "evidence": ["exact title exists only under another media type"], "candidates": [_movie_candidate(m) for m in conflicting]}
        return {"status": "UNMATCHED", "confidence": 0.0, "evidence": ["no exact normalized title in canonical catalogue"], "candidates": []}
    if year is not None:
        same_year = [m for m in candidates if m.year == year]
        if len(same_year) == 1:
            return {"status": "MATCH_HIGH", "confidence": 0.99, "evidence": ["exact normalized title", "exact release year", "media type agrees"], "candidates": [_movie_candidate(same_year[0])]}
        if len(same_year) > 1:
            candidates = same_year
        else:
            return {"status": "POSSIBLE_REVIEW", "confidence": 0.0, "evidence": ["exact title but canonical release year conflicts"], "candidates": [_movie_candidate(m) for m in candidates]}
    if len(candidates) == 1:
        return {"status": "POSSIBLE_REVIEW", "confidence": 0.75, "evidence": ["exact normalized title", "release year unavailable"], "candidates": [_movie_candidate(candidates[0])]}
    return {"status": "POSSIBLE_REVIEW", "confidence": 0.0, "evidence": ["multiple exact-title candidates"], "candidates": [_movie_candidate(m) for m in candidates]}


def _movie_candidate(movie):
    return {"movie_id": movie.pk, "tmdb_id": movie.tmdb_id, "title": movie.title, "year": movie.year, "content_type": movie.content_type}


def _provider_content_id(record):
    native_id = str(record.provider_native_id or "").strip()
    if native_id and len(native_id) <= 100:
        return native_id
    source_identity = record.identity_url or record.source_url
    digest = hashlib.sha256(source_identity.encode("utf-8")).hexdigest()
    return f"url:{digest}"


def reconcile_catalogue(*, provider_slugs=None, limit=None, apply=False):
    """Return per-record proposals; apply only high-confidence classifications.

    Application creates provider catalogue identity rows for confidently
    classified discoveries and links only high-confidence canonical matches.
    It does not create offers or infer that an item is available.
    """
    if apply:
        # Apply remains explicit at the management-command boundary.  The
        # caller must supply a reviewable proposal set before passing apply.
        pass
    qs = DiscoveredCatalogueRecord.objects.select_related("provider").order_by("provider__slug", "pk")
    if provider_slugs:
        qs = qs.filter(provider__slug__in=provider_slugs)
    if limit is not None:
        qs = qs[:max(0, int(limit))]

    movies = list(Movie.objects.all().only("id", "tmdb_id", "title", "year", "content_type"))
    titles_by_norm = defaultdict(list)
    movies_by_tmdb = {}
    for movie in movies:
        titles_by_norm[_normal_title(movie.title)].append(movie)
        movies_by_tmdb[movie.tmdb_id] = movie

    legacy_qs = ScrapedContentMapping.objects.select_related("provider").only(
        "id", "provider_id", "provider__slug", "scraped_title", "scraped_year",
        "scraped_content_type", "scraped_url", "movie_id", "is_confirmed",
    )
    if provider_slugs:
        legacy_qs = legacy_qs.filter(provider__slug__in=provider_slugs)
    legacy_by_url = defaultdict(list)
    legacy_by_title = defaultdict(list)
    legacy_by_id = {}
    for legacy in legacy_qs.iterator(chunk_size=500):
        legacy_by_id[legacy.pk] = legacy
        if legacy.scraped_url:
            legacy_by_url[(legacy.provider.slug, legacy.scraped_url)].append(legacy)
        legacy_by_title[(legacy.provider.slug, _normal_title(legacy.scraped_title))].append(legacy)

    rows = []
    counters = Counter()
    provider_counters = defaultdict(Counter)
    legacy_provider_counters = defaultdict(Counter)
    duplicate_title_groups = defaultdict(list)
    for record in qs.iterator(chunk_size=500):
        media_type, type_confidence, type_evidence = _type_evidence(record)
        year, year_evidence = _extract_year(record)
        title = str(record.title or "").strip()
        extracted = bool(title)
        match = _match(record, media_type, year, titles_by_norm, movies_by_tmdb)
        legacy_candidates = []
        seen_legacy_ids = set()
        for legacy in legacy_by_url.get((record.provider.slug, record.source_url), []):
            seen_legacy_ids.add(legacy.pk)
        for legacy in legacy_by_title.get((record.provider.slug, _normal_title(title)), []):
            seen_legacy_ids.add(legacy.pk)
        for legacy_id in seen_legacy_ids:
            legacy = legacy_by_id.get(legacy_id)
            if legacy is None:
                continue
            exact_url = bool(record.source_url and legacy.scraped_url == record.source_url)
            exact_title = _normal_title(record.title) == _normal_title(legacy.scraped_title)
            exact_year = year is not None and legacy.scraped_year is not None and year == legacy.scraped_year
            type_agrees = bool(media_type and legacy.scraped_content_type == media_type)
            legacy_candidates.append({
                "legacy_mapping_id": legacy.pk,
                "title": legacy.scraped_title,
                "year": legacy.scraped_year,
                "content_type": legacy.scraped_content_type,
                "source_url": legacy.scraped_url,
                "exact_url": exact_url,
                "exact_title": exact_title,
                "exact_year": exact_year,
                "type_agrees": type_agrees,
                "canonical_movie_id": legacy.movie_id,
                "is_confirmed": legacy.is_confirmed,
                "identity_status": "SAME_SOURCE_URL" if exact_url and exact_title else "TITLE_YEAR_TYPE" if exact_title and exact_year and type_agrees else "REVIEW",
            })
        if not extracted:
            counters["extraction_incomplete"] += 1
        else:
            counters["titles_extracted"] += 1
            provider_counters[record.provider.slug]["titles_extracted"] += 1
        if year is not None:
            counters["years_extracted"] += 1
            provider_counters[record.provider.slug]["years_extracted"] += 1
        else:
            counters["years_missing"] += 1
            provider_counters[record.provider.slug]["years_missing"] += 1
        counters["evaluated"] += 1
        provider_counters[record.provider.slug]["evaluated"] += 1
        if media_type == "movie": counters["movies_confident"] += 1
        elif media_type == "series": counters["series_confident"] += 1
        else: counters["ambiguous_classification"] += 1
        if media_type is None:
            counters["match_not_evaluated_ambiguous_type"] += 1
            provider_counters[record.provider.slug]["ambiguous_classification"] += 1
        else:
            counters[match["status"].lower()] += 1
            provider_counters[record.provider.slug][match["status"].lower()] += 1
            provider_counters[record.provider.slug][f"classified_{media_type}"] += 1
        if media_type == "movie": provider_counters[record.provider.slug]["movies_confident"] += 1
        elif media_type == "series": provider_counters[record.provider.slug]["series_confident"] += 1
        if title and media_type:
            duplicate_title_groups[(record.provider.slug, media_type, _normal_title(title), year)].append(record.pk)
        if any(item["exact_url"] for item in legacy_candidates):
            legacy_provider_counters[record.provider.slug]["exact_source_url_records"] += 1
        if any(item["exact_title"] and item["exact_year"] and item["type_agrees"] for item in legacy_candidates):
            legacy_provider_counters[record.provider.slug]["exact_title_year_type_records"] += 1
        if any(item["identity_status"] == "REVIEW" for item in legacy_candidates):
            legacy_provider_counters[record.provider.slug]["review_candidates"] += 1
        proposal = {
            "record_id": record.pk,
            "provider": record.provider.slug,
            "provider_native_id": record.provider_native_id,
            "title": title,
            "year": year,
            "content_type": media_type,
            "classification_confidence": type_confidence,
            "classification_evidence": type_evidence + [year_evidence],
            "match": match,
            "legacy_mapping_candidates": legacy_candidates,
            "source_url": record.source_url,
            "identity_url": record.identity_url,
            "provenance_state": record.provenance_state,
            "access_policy_state": record.access_policy_state,
            "action": "PENDING_REVIEW",
        }
        if apply and media_type and extracted:
            try:
                proposal["action"] = _apply_classification(record, media_type, year, match)
                counters[f"applied_{proposal['action'].lower()}"] += 1
            except Exception as exc:  # one damaged source row must not block other records
                proposal["action"] = "APPLY_FAILED"
                proposal["apply_error"] = f"{type(exc).__name__}: {exc}"
                counters["apply_failed"] += 1
        rows.append(proposal)

    duplicate_title_groups_report = []
    for (provider, content_type, title, year), ids in duplicate_title_groups.items():
        if len(ids) > 1:
            duplicate_title_groups_report.append({"provider": provider, "content_type": content_type, "normalized_title": title, "year": year, "record_ids": ids, "kind": "same_title_candidate; not merged"})
    duplicate_identity_groups = 0  # model uniqueness constraints prevent persisted key collisions
    counters["duplicate_identity_groups"] = duplicate_identity_groups
    false_positive_findings = [
        row for row in rows
        if any("conflict" in evidence for evidence in row["classification_evidence"])
        or any("conflict" in evidence or "another media type" in evidence for evidence in row["match"]["evidence"])
    ]
    return {
        "mode": "APPLY" if apply else "DRY_RUN",
        "records_evaluated": len(rows),
        "statistics": dict(counters),
        "legacy_mapping_overlaps": {
            "discovered_records_with_exact_source_url_match": sum(1 for row in rows if any(item["exact_url"] for item in row["legacy_mapping_candidates"])),
            "discovered_records_with_exact_title_year_type_match": sum(1 for row in rows if any(item["exact_title"] and item["exact_year"] and item["type_agrees"] for item in row["legacy_mapping_candidates"])),
            "discovered_records_with_title_only_or_conflicting_match": sum(1 for row in rows if any(item["identity_status"] == "REVIEW" for item in row["legacy_mapping_candidates"])),
            "by_provider": {provider: dict(values) for provider, values in sorted(legacy_provider_counters.items())},
            "legacy_mappings_are_not_canonical_associations": True,
        },
        "by_provider": {provider: dict(values) for provider, values in sorted(provider_counters.items())},
        "duplicate_identities": {"count": duplicate_identity_groups, "title_candidate_groups": duplicate_title_groups_report},
        "false_positive_findings": false_positive_findings,
        "false_positive_validation": "not measurable when no canonical matches are proposed",
        "requested_metrics": {
            "movies_confidently_classified": counters["movies_confident"],
            "series_confidently_classified": counters["series_confident"],
            "ambiguous_classifications": counters["ambiguous_classification"],
            "high_confidence_canonical_matches": counters["match_high"],
            "possible_matches_requiring_review": counters["possible_review"],
            "unmatched_records": counters["unmatched"],
            "duplicate_identity_groups": duplicate_identity_groups,
            "duplicate_title_candidate_groups": len(duplicate_title_groups_report),
        },
        "records": rows,
        "offers_created": 0,
    }


@transaction.atomic
def _apply_classification(record, content_type, year, match):
    """Persist one safely classified source identity, without offer creation."""
    provider = record.provider
    native_id = _provider_content_id(record)
    metadata = dict(record.original_metadata) if isinstance(record.original_metadata, dict) else {}
    metadata.update({
        "discovered_catalogue_record_id": record.pk,
        "provider_native_id_original": record.provider_native_id,
        "identity_url": record.identity_url,
        "source_urls_seen": list(record.source_urls_seen or []),
        "classification_evidence": _type_evidence(record)[2],
        "classification_confidence": _type_evidence(record)[1],
        "unverified_discovery": True,
    })
    from datetime import date
    content, created = ProviderContent.objects.get_or_create(
        provider=provider,
        provider_content_id=native_id,
        content_type=content_type,
        season_number=None,
        episode_number=None,
        defaults={
            "title": record.title,
            "release_date": date(year, 1, 1) if year else None,
            "provider_url": record.source_url,
            "metadata": metadata,
            "currently_available": False,
        },
    )
    record.classification_state = "CLASSIFIED"
    record.classified_content_type = content_type
    record.save(update_fields=["classification_state", "classified_content_type", "updated_at"])
    if match["status"] != "MATCH_HIGH":
        return "provider_content_created" if created else "provider_content_exists"

    candidate = match["candidates"][0]
    source = Source.objects.filter(slug=provider.slug).first()
    if source is None:
        return "classified_canonical_match_requires_source"
    existing_mapping = ContentMapping.objects.filter(
        provider=provider, provider_content_id=native_id
    ).first()
    if existing_mapping:
        # Reconciliation never replaces an existing operator/provider mapping.
        return "existing_canonical_mapping_preserved"
    ContentMapping.objects.create(
        provider=provider,
        provider_content_id=native_id,
        source=source,
        content_type=content_type,
        tmdb_id=candidate["tmdb_id"],
        content=content,
        confidence=match["confidence"],
        match_method="external_id" if "TMDb ID" in " ".join(match["evidence"]) else "title_year",
    )
    return "canonical_mapping_created"
