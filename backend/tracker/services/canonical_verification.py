"""Shared record-specific TMDb verifier used by bootstrap and incremental jobs."""
import hashlib
import json
from datetime import timedelta

from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from tracker.models import CanonicalBootstrapEvidence, DiscoveredCatalogueRecord, Movie
from tracker.services.catalogue_reconciliation_inputs import fingerprint_v1
from tracker.services.catalogue_reconciliation import (
    _extract_year, _normal_title, _trusted_external_tmdb_id, _type_evidence,
)

EVIDENCE_TTL = timedelta(days=30)


def source_snapshot(record):
    return {"source_title": record.title, "source_year": _extract_year(record)[0],
            "provider": record.provider.slug, "provider_native_id": record.provider_native_id,
            "source_url": record.source_url, "identity_url": record.identity_url,
            "content_type": _type_evidence(record)[0]}


def fingerprint(record):
    """Preserve the existing bootstrap file-checkpoint format."""
    return hashlib.sha256(json.dumps(source_snapshot(record), sort_keys=True).encode()).hexdigest()


def evidence_agrees(record, evidence):
    stored = evidence.evidence
    if not isinstance(stored, dict):
        return False
    return (
        evidence.verified_at > timezone.now() - EVIDENCE_TTL
        and all(stored.get(k) == v for k, v in source_snapshot(record).items())
        and stored.get("input_fingerprint", fingerprint_v1(record)) == fingerprint_v1(record)
        and stored.get("tmdb_id") == evidence.movie.tmdb_id
        and evidence.movie.content_type == _type_evidence(record)[0]
        and evidence.movie.year == _extract_year(record)[0]
        and (not _trusted_external_tmdb_id(record)
             or int(_trusted_external_tmdb_id(record)) == evidence.movie.tmdb_id)
    )


class CanonicalVerifier:
    def evaluate(self, record, media, year, gateway, options, totals, result):
        trusted = _trusted_external_tmdb_id(record)
        if trusted:
            candidates = [{"tmdb_id": int(trusted), "content_type": media}]
            exact = candidates
        elif options.get("cached_candidates"):
            candidates = exact = options["cached_candidates"]
        else:
            before = gateway.budget.cache_hits if hasattr(gateway, "budget") else None
            candidates = gateway.search(record.title, year=year, content_type=media,
                                        authorize_network=options["allow_network"])
            if (not candidates and not options["allow_network"] and before is not None
                    and gateway.budget.cache_hits == before):
                result.update(status="CACHE_MISS", evidence=["no fresh search cache; network not authorized"])
                return
            exact = list({int(c["tmdb_id"]): c for c in candidates
                          if c.get("content_type") == media and c.get("year") == year
                          and _normal_title(c.get("title")) == _normal_title(record.title)
                          and _normal_title(record.title)}.values())
        totals["rejected_candidates"] += len(candidates) - len(exact)
        result["candidates"] = [{k: c.get(k) for k in ("tmdb_id", "title", "year", "content_type")} for c in candidates]
        if (not trusted and (any(c.get("search_truncated") for c in candidates)
                or self.title_groups[(record.provider_id, media, year, _normal_title(record.title))] > 1)):
            result.update(status="AMBIGUOUS", evidence=["multiple source releases or incomplete search; record-specific distinction required"])
            totals["exact_proposed"] += int(len(exact) == 1)
            totals["ambiguous"] += 1
            return
        if len(exact) != 1:
            result.update(status="AMBIGUOUS" if len(exact) > 1 else "UNMATCHED",
                          evidence=["search did not produce one exact title/year/type candidate"])
            totals["ambiguous" if len(exact) > 1 else "unmatched"] += 1
            return
        if not trusted:
            totals["exact_proposed"] += 1
        candidate = exact[0]
        before = gateway.budget.cache_hits if hasattr(gateway, "budget") else None
        details = gateway.get_details(int(candidate["tmdb_id"]), content_type=media,
                                      authorize_network=options["allow_network"])
        if (details is None and not options["allow_network"] and before is not None
                and gateway.budget.cache_hits == before):
            result.update(status="CACHE_MISS", evidence=["no usable details cache; not verified"])
            return
        if (not details or details.get("tmdb_id") != int(candidate["tmdb_id"])
                or details.get("content_type") != media or details.get("year") != year
                or not _normal_title(record.title)
                or _normal_title(record.title) not in {
                    _normal_title(details.get("title")), _normal_title(details.get("original_title"))}):
            result.update(status="CONFLICTING", evidence=["details rejected identifier/title/year/type proposal"])
            totals["conflicting"] += 1
            totals["rejected_candidates"] += 1
            return
        movie = Movie.objects.filter(tmdb_id=int(candidate["tmdb_id"]), content_type=media).first()
        if movie and movie.year != year:
            result.update(status="CONFLICTING", evidence=["existing canonical release year conflicts"])
            totals["conflicting"] += 1
            return
        totals["verified"] += 1
        totals["already_present" if movie else "would_create"] += 1
        result.update(status="VERIFIED", tmdb_id=int(candidate["tmdb_id"]),
                      canonical_title=details["title"], canonical_year=year,
                      verification_source=("tmdb_trusted_id_details" if trusted else
                                           "tmdb_cached_identity_details" if options.get("cached_candidates") else
                                           "tmdb_search_and_details"),
                      evidence=["record-specific details verified identifier, title, year and media type"],
                      action="PENDING_APPLY" if options["apply"] else "DRY_RUN")
        if options.get("return_details"):
            result["verified_details"] = details
        if not options["apply"]:
            return
        self.persist_verified(record, media, year, details, trusted, totals, result)

    def persist_verified(self, record, media, year, details, trusted, totals, result, *, refresh_evidence=False):
        with transaction.atomic():
            locked = DiscoveredCatalogueRecord.objects.select_for_update().select_related("provider").get(pk=record.pk)
            if fingerprint_v1(locked) != fingerprint_v1(record):
                raise CommandError("Source changed during verification; rerun for review")
            movie, created = Movie.objects.get_or_create(
                tmdb_id=int(details["tmdb_id"]), content_type=media,
                defaults={"title": details["title"], "year": year,
                          **{k: details.get(k) or fallback for k, fallback in (
                              ("overview", ""), ("poster_path", ""), ("genres", []),
                              ("genre_ids", []), ("cast", []), ("director", []), ("seasons_data", []))},
                          "runtime_minutes": details.get("runtime_minutes"),
                          "tmdb_rating": details.get("tmdb_rating")})
            if movie.year != year:
                raise CommandError("Canonical year changed during verification; review before retry")
            proof = {**source_snapshot(record), "tmdb_id": movie.tmdb_id,
                     "tmdb_title": details["title"], "tmdb_year": year,
                     "input_fingerprint": fingerprint_v1(record)}
            evidence, evidence_created = CanonicalBootstrapEvidence.objects.get_or_create(
                discovered_record=record, defaults={"movie": movie,
                    "source_kind": result.get("verification_source", "tmdb_trusted_id_details" if trusted else "tmdb_search_and_details"),
                    "verification_method": "exact_title_year_type",
                    "evidence": proof})
            if evidence.movie_id != movie.pk:
                raise CommandError("Existing evidence has another canonical identity; operator review required")
            if (not evidence_created and refresh_evidence
                    and (evidence.evidence != proof or evidence.verified_at <= timezone.now() - EVIDENCE_TTL)):
                evidence.evidence = proof
                evidence.verified_at = timezone.now()
                evidence.source_kind = result.get("verification_source", evidence.source_kind)
                evidence.save(update_fields=["evidence", "verified_at", "source_kind"])
            totals["created"] += int(created)
            totals["applied"] += 1
            result["action"] = "created" if created else "existing_identity_evidence_recorded"
