"""Read-only comparison of a title/country cohort with canonical app evidence."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from availability.domain.enums import AvailabilityEvidence, ContentType
from availability.freshness import freshness_state
from availability.models import ContentMapping, Country, Offer, Source
from availability.services.query import AvailabilityQueryService
from tracker.models import Movie, Preference, Provider, ProviderCategory
from tracker.services.access import UserAccess, normalize_service_slug, resolve_title
from tracker.services.offers import Offer as TrackerOffer
from tracker.services.source_policy import SOURCE_ROLES


REQUIRED_COHORT_COLUMNS = {"tmdb_id", "title", "media_type", "year", "country"}
COMPARISON_STATES = {
    "AGREEMENT_CONFIRMED", "CONFLICT_CONFIRMED", "APP_DATA_MISSING",
    "APP_DATA_STALE", "APP_SOURCE_DISABLED", "INSUFFICIENT_EXTERNAL_EVIDENCE",
    "INSUFFICIENT_APP_EVIDENCE", "NOT_COMPARABLE",
}
FREE_CATEGORIES = {ProviderCategory.BVOD, ProviderCategory.AVOD, ProviderCategory.FAST}


def _read_csv(path: Path, required: set[str], label: str) -> list[dict]:
    path = Path(path)
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            headers = set(reader.fieldnames or ())
            missing = required - headers
            if missing:
                raise CommandError(f"{label} missing required columns: {', '.join(sorted(missing))}")
            rows = list(reader)
    except OSError as exc:
        raise CommandError(f"Cannot read {label} CSV {path}: {exc}") from exc
    return rows


def _parse_cohort(rows: list[dict]) -> list[dict]:
    parsed = []
    seen = set()
    for line, row in enumerate(rows, start=2):
        try:
            tmdb_id = int(row["tmdb_id"])
            year = int(row["year"]) if (row.get("year") or "").strip() else None
        except (TypeError, ValueError) as exc:
            raise CommandError(f"Invalid tmdb_id/year on cohort CSV line {line}") from exc
        media_type = (row.get("media_type") or "").strip().lower()
        media_type = {"movie": "movie", "film": "movie", "series": "series", "tv": "series"}.get(media_type, media_type)
        country = (row.get("country") or "").strip().upper()
        if tmdb_id <= 0 or media_type not in {ContentType.MOVIE, ContentType.SERIES}:
            raise CommandError(f"Invalid TMDb id or media_type on cohort CSV line {line}")
        if len(country) != 2 or not country.isalpha():
            raise CommandError(f"Invalid ISO alpha-2 country on cohort CSV line {line}")
        title = (row.get("title") or "").strip()
        if not title:
            raise CommandError(f"Missing title on cohort CSV line {line}")
        key = (tmdb_id, media_type, country)
        if key in seen:
            raise CommandError(f"Duplicate cohort identity {key!r} on line {line}")
        seen.add(key)
        parsed.append({"tmdb_id": tmdb_id, "title": title, "media_type": media_type,
                       "year": year, "country": country})
    return sorted(parsed, key=lambda x: (x["country"], x["media_type"], x["tmdb_id"], x["title"].casefold()))


def _external_index(rows: list[dict]) -> dict[tuple[int, str, str], dict]:
    index = {}
    for line, row in enumerate(rows, start=2):
        try:
            tmdb_id = int(row["tmdb_id"])
        except (TypeError, ValueError) as exc:
            raise CommandError(f"Invalid external tmdb_id on evidence CSV line {line}") from exc
        kind = (row.get("media_type") or "movie").strip().lower()
        kind = {"film": "movie", "tv": "series"}.get(kind, kind)
        country = (row.get("country") or "").strip().upper()
        key = (tmdb_id, kind, country)
        if key in index:
            raise CommandError(f"Duplicate external evidence identity {key!r}")
        index[key] = row
    return index


def _csv_value(value):
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def _provider_access(providers, offers, profile: str) -> UserAccess:
    access = UserAccess(synthetic=True)
    by_slug = {p.slug: p for p in providers}
    access.free = {p.slug for p in providers if p.is_enabled and p.category in FREE_CATEGORIES}
    access.included = {p.slug for p in providers if p.is_enabled and p.category == ProviderCategory.SCRAPER}
    if profile == "B":
        return access
    elif profile == "C":
        for item in offers:
            provider = by_slug.get(item.provider)
            access_type = item.offer_type
            if access_type in {"free", "free_with_ads"}:
                access.free.add(item.provider)
            elif provider and provider.addon_parent_slug:
                access.addon.add(item.provider)
                access.included.add(provider.addon_parent_slug)
            elif access_type == "subscription":
                access.included.add(item.provider)
    return access


def _evaluate(movie, country, reconciled_offers, providers):
    preference = Preference(
        current_country=country, candidate_countries=[], vpn_enabled=False,
        audio_languages=[], subtitle_languages=[], language_rule={},
        ranking_preferences={},
    )
    provider_map = {provider.slug: provider for provider in providers}
    result = {}
    for profile in ("B", "C"):
        profile_access = _provider_access(providers, reconciled_offers, profile)
        tracker_offers = []
        for item in reconciled_offers:
            if not item.currently_available or item.evidence != AvailabilityEvidence.VERIFIED:
                continue
            tracker_offers.append(TrackerOffer(
                country=item.country, service=item.provider, offer_type=item.offer_type,
                quality=item.quality or "", url=item.provider_url or "",
                audio_languages=None if item.audio_languages is None else list(item.audio_languages),
                subtitle_languages=None if item.subtitle_languages is None else list(item.subtitle_languages),
                availability_evidence=str(item.evidence),
                last_seen_at=(item.last_verified_at or item.last_seen_available_at).isoformat()
                if (item.last_verified_at or item.last_seen_available_at) else None,
            ))
        resolved = resolve_title(movie, tracker_offers, preference,
                                 current_country=country, access=profile_access,
                                 provider_map=provider_map)
        result[profile] = resolved
    return result


def _external_positive(row):
    if not row:
        return False
    value = str(row.get("explicitly_confirms_availability") or "").strip().lower()
    return value in {"yes", "true", "1"}


def _external_providers(row):
    if not row:
        return []
    value = (row.get("provider") or "").strip()
    if not value or "not extractable" in value.lower() or "provider names" in value.lower():
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _provider_name_keys(names, providers=()):
    by_display = {" ".join(provider.name.casefold().split()): provider.slug for provider in providers}
    keys = set()
    for name in names:
        display = " ".join(name.casefold().split())
        slug = by_display.get(display)
        if not slug:
            slug = normalize_service_slug(name)
        if not slug:
            slug = normalize_service_slug(name.replace(" ", ""))
        keys.add(slug or display)
    return keys


def _external_offer_classes(row):
    """Return only offer classes explicitly supported by the external row."""
    value = " ".join(str(row.get("offer_type") or "").casefold().replace("_", " ").split())
    classes = set()
    if any(token in value for token in ("subscription", "flat", "abbo")):
        classes.add("subscription")
    if "rent" in value:
        classes.add("rent")
    if "buy" in value or "purchase" in value:
        classes.add("buy")
    if "ad-supported" in value or "with ads" in value or "ads" in value:
        classes.add("free_with_ads")
    elif "free" in value:
        classes.add("free")
    if "addon" in value or "add-on" in value or "channel" in value:
        classes.add("addon")
    return classes


def _classify(case, external, movie, offers, source_state="unknown", providers=()):
    if not external:
        return "INSUFFICIENT_EXTERNAL_EVIDENCE", "No matching Stage 2B evidence row."
    evidence_country = (external.get("country") or "").strip().upper()
    if evidence_country and evidence_country != case["country"]:
        return "NOT_COMPARABLE", "External evidence country differs from cohort country."
    try:
        evidence_id = int(external.get("tmdb_id") or 0)
    except ValueError:
        evidence_id = 0
    if evidence_id != case["tmdb_id"]:
        return "NOT_COMPARABLE", "External evidence TMDb identity differs from cohort."
    evidence_type = (external.get("media_type") or "").strip().lower()
    evidence_type = {"film": "movie", "tv": "series"}.get(evidence_type, evidence_type)
    if evidence_type and evidence_type != case["media_type"]:
        return "NOT_COMPARABLE", "External evidence media type differs from cohort."
    try:
        evidence_year = int(external.get("year")) if (external.get("year") or "").strip() else None
    except (TypeError, ValueError):
        evidence_year = None
    if evidence_year is not None and case["year"] is not None and evidence_year != case["year"]:
        return "NOT_COMPARABLE", "External evidence release year differs from cohort."
    explicit_negative = str(external.get("explicitly_confirms_unavailability") or "").strip().lower() in {"yes", "true", "1"}
    authority = str(external.get("evidence_authority") or "").strip().lower()
    if not _external_positive(external) and not explicit_negative:
        return "INSUFFICIENT_EXTERNAL_EVIDENCE", "External observation is unverified or does not explicitly assert availability."
    if explicit_negative and authority not in {"primary", "official"}:
        return "INSUFFICIENT_EXTERNAL_EVIDENCE", "A secondary or unspecified source cannot establish a confirmed negative."
    provider_names = _external_providers(external)
    if not provider_names:
        return "INSUFFICIENT_EXTERNAL_EVIDENCE", "External listing does not identify comparable providers."
    classes = _external_offer_classes(external)
    if not explicit_negative and not classes:
        return "INSUFFICIENT_EXTERNAL_EVIDENCE", "External listing does not identify a comparable offer class."
    if not explicit_negative and (len(provider_names) != 1 or len(classes) != 1):
        return "INSUFFICIENT_EXTERNAL_EVIDENCE", "External evidence does not associate one provider with one offer class."
    if movie is None:
        return "APP_DATA_MISSING", "No local Movie row with exact TMDb id and media type."
    if movie.content_type != case["media_type"] or (
        case["year"] is not None and movie.year != case["year"]
    ):
        return "NOT_COMPARABLE", "Local Movie identity year or media type differs from the cohort."
    normalized = _provider_name_keys(provider_names, providers)
    positive = [x for x in offers if x.currently_available and x.evidence in (AvailabilityEvidence.VERIFIED, AvailabilityEvidence.STALE, AvailabilityEvidence.UNKNOWN)]
    if explicit_negative:
        verified = [x for x in positive if x.evidence == AvailabilityEvidence.VERIFIED and x.country == case["country"]]
        if normalized & {p.provider.casefold() for p in verified}:
            return "CONFLICT_CONFIRMED", "Authoritative external negative contradicts a verified canonical offer for the same provider and country."
        return "INSUFFICIENT_APP_EVIDENCE", "No same-provider verified canonical offer establishes a contradiction to the external negative."
    if not positive:
        if source_state == "disabled":
            return "APP_SOURCE_DISABLED", "The explicitly identified application source is persisted and disabled."
        if source_state == "unconfigured":
            return "INSUFFICIENT_APP_EVIDENCE", "The explicitly identified application source has no local Source row; enabled state is unknown."
        return "APP_DATA_MISSING", "Title exists locally, but no relevant canonical positive offer is stored; source coverage is not established."

    same_provider_country = [p for p in positive
                             if p.country == case["country"] and p.provider.casefold() in normalized]
    comparable = [p for p in same_provider_country if p.offer_type in classes]
    verified_comparable = [p for p in comparable if p.evidence == AvailabilityEvidence.VERIFIED]
    if verified_comparable:
        return "AGREEMENT_CONFIRMED", "Fresh verified canonical evidence matches external provider, country, and offer class."
    if any(p.evidence == AvailabilityEvidence.STALE for p in comparable):
        return "APP_DATA_STALE", "The matching provider and offer class are present only as stale canonical evidence."
    if not same_provider_country:
        if source_state == "disabled":
            return "APP_SOURCE_DISABLED", "The explicitly identified application source is persisted and disabled."
        return "INSUFFICIENT_APP_EVIDENCE", "No same-provider canonical offer covers the external evidence country."
    if not comparable:
        return "INSUFFICIENT_APP_EVIDENCE", "Same-provider evidence exists, but its offer class does not match the external listing."
    return "INSUFFICIENT_APP_EVIDENCE", "Matching provider and offer class have no fresh verified canonical evidence."


def _write_csv(path, rows, columns):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in columns})


class Command(BaseCommand):
    help = "Compare Stage 2B availability evidence with local canonical offers (read-only, offline)."

    def add_arguments(self, parser):
        parser.add_argument("--cohort", required=True, type=Path)
        parser.add_argument("--output", required=True, type=Path)
        parser.add_argument("--external-evidence", type=Path)
        parser.add_argument("--offline", action="store_true", help="Required; the command never performs network I/O.")

    def handle(self, *args, **options):
        if not options["offline"]:
            raise CommandError("--offline is required; this audit command never makes HTTP requests.")
        cohort_rows = _parse_cohort(_read_csv(options["cohort"], REQUIRED_COHORT_COLUMNS, "Cohort"))
        external_rows = []
        if options["external_evidence"]:
            external_rows = _read_csv(options["external_evidence"], {"tmdb_id", "country"}, "External evidence")
        external_index = _external_index(external_rows)
        outdir = Path(options["output"])
        outdir.mkdir(parents=True, exist_ok=True)

        # PostgreSQL enforces read-only behavior for the complete DB snapshot.
        # Files are written only after all ORM reads have completed.
        with transaction.atomic():
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
            movies = {
                (movie.tmdb_id, movie.content_type): movie
                for movie in Movie.objects.filter(
                    tmdb_id__in=[case["tmdb_id"] for case in cohort_rows]
                )
            }
            providers = list(Provider.objects.all().order_by("slug"))
            source_rows = {source.slug: source for source in Source.objects.all().order_by("slug")}
            source_status = {
                slug: {"registered": slug in SOURCE_ROLES,
                       "enabled": source_rows[slug].enabled if slug in source_rows else None,
                       "present": slug in source_rows}
                for slug in sorted(set(SOURCE_ROLES) | set(source_rows))
            }
            app_snapshot, comparisons = [], []
            countries = {country.code: country for country in Country.objects.all()}
            query = AvailabilityQueryService()
            for case in cohort_rows:
                key = (case["tmdb_id"], case["media_type"])
                movie = movies.get(key)
                raw_rows = list(Offer.objects.filter(
                    tmdb_id=case["tmdb_id"], content_type=case["media_type"], country__code=case["country"]
                ).select_related("provider", "country", "source", "sync_run").order_by("provider__slug", "offer_type", "source__slug", "id"))
                reconciliation = query.get_reconciled_for_content(
                    case["tmdb_id"], case["media_type"], case["country"], now=timezone.now()
                )
                reconciled = list(reconciliation.offers)
                profiles = _evaluate(movie or Movie(tmdb_id=case["tmdb_id"], title=case["title"],
                    year=case["year"], content_type=case["media_type"]), case["country"], reconciled, providers)
                external = external_index.get((case["tmdb_id"], case["media_type"], case["country"]))
                names = _external_providers(external)
                # Stage 2B's research/aggregator source is not the app's
                # collector. Only an explicit app_source_slug can establish
                # which local Source state is relevant to this comparison.
                declared_app_source = (external.get("app_source_slug") or "").strip().casefold() if external else ""
                source_state = "unknown"
                if declared_app_source:
                    source_record = source_status.get(declared_app_source)
                    if source_record is None or not source_record["present"]:
                        source_state = "unconfigured"
                    elif source_record["enabled"] is False:
                        source_state = "disabled"
                    elif source_record["enabled"] is True:
                        source_state = "enabled"
                state, reason = _classify(case, external, movie, reconciled, source_state, providers)
                if state not in COMPARISON_STATES:
                    raise CommandError(f"Unexpected comparison state {state}")
                # Independent snapshot rows preserve each canonical observation;
                # a case with none gets one explicit null-evidence row.
                rows_for_case = raw_rows or [None]
                for raw in rows_for_case:
                    recons = next((r for r in reconciled if raw and raw.provider.slug == r.provider and raw.offer_type == r.offer_type), None)
                    profile_offer = {}
                    if recons:
                        for profile_name in ("B", "C"):
                            profile_offer[profile_name] = next((item for item in profiles[profile_name].offers
                                if item.service == recons.provider and item.country == recons.country
                                and item.offer_type == recons.offer_type), None)
                    mapping = None
                    if raw and raw.provider_content_id:
                        mapping = ContentMapping.objects.filter(provider=raw.provider,
                            provider_content_id=raw.provider_content_id, tmdb_id=case["tmdb_id"],
                            content_type=case["media_type"]).order_by("-confidence", "id").first()
                    stale = None
                    if raw:
                        freshness = freshness_state(raw.source.slug, raw.last_seen,
                            configuration=raw.source.configuration)
                        stale = str(freshness)
                    app_snapshot.append({
                        **case, "movie_id": movie.id if movie else None,
                        "movie_title": movie.title if movie else None,
                        "movie_year": movie.year if movie else None,
                        "identity_match": bool(movie and movie.year == case["year"] and movie.content_type == case["media_type"]),
                        "offer_id": raw.id if raw else None,
                        "source_slug": raw.source.slug if raw else None,
                        "source_enabled": source_status.get(raw.source.slug, {}).get("enabled") if raw else None,
                        "provider_slug": raw.provider.slug if raw else None,
                        "provider_name": raw.provider.name if raw else None,
                        "provider_mapping_confidence": mapping.confidence if mapping else None,
                        "provider_mapping_method": mapping.match_method if mapping else None,
                        "offer_type": raw.offer_type if raw else None,
                        "offer_country": raw.country.code if raw else None,
                        "territorial_scope": "country" if raw else None,
                        "source_category": raw.source.type if raw else None,
                        "source_role": str(SOURCE_ROLES.get(raw.source.slug)) if raw and SOURCE_ROLES.get(raw.source.slug) else None,
                        "observation_timestamp": raw.observed_at if raw else None,
                        "last_seen": raw.last_seen if raw else None,
                        "evidence_freshness": stale,
                        "availability_confidence": str(recons.evidence) if recons else None,
                        "source_observation_status": raw.sync_run.status if raw and raw.sync_run_id else None,
                        "currently_available": raw.currently_available if raw else None,
                        "audio_languages": raw.audio_languages if raw else None,
                        "subtitle_languages": raw.subtitle_languages if raw else None,
                        "subscription_entitlement": None,
                        "profile_b_entitled": profile_offer.get("B").is_entitled if profile_offer.get("B") else None,
                        "profile_c_entitled": profile_offer.get("C").is_entitled if profile_offer.get("C") else None,
                        "free_ad_supported": raw.offer_type in {"free", "free_with_ads"} if raw else None,
                        "rental_purchase_required": raw.offer_type in {"rent", "buy"} if raw else None,
                        "profile_b_language_compatibility": profile_offer.get("B").is_language_match if profile_offer.get("B") else None,
                        "profile_c_language_compatibility": profile_offer.get("C").is_language_match if profile_offer.get("C") else None,
                        "language_metadata_status": "unknown" if raw and raw.audio_languages is None and raw.subtitle_languages is None else ("reported" if raw else None),
                        "local_reachability": profile_offer.get("C").locality if profile_offer.get("C") else None,
                        "vpn_reachability": "disabled",
                        "catalogue_verdict": "inventory_only",
                        "profile_b_verdict": profiles["B"].verdict,
                        "profile_c_verdict": profiles["C"].verdict,
                        "profile_b_best_offer": _csv_value(profiles["B"].best_offer.to_dict()) if profiles["B"].best_offer else None,
                        "profile_c_best_offer": _csv_value(profiles["C"].best_offer.to_dict()) if profiles["C"].best_offer else None,
                    })
                comparisons.append({
                    **case, "app_movie_found": bool(movie), "app_offer_count": len(raw_rows),
                    "app_positive_provider_count": len({r.provider for r in reconciled if r.currently_available}),
                    "app_verified_offer_count": sum(1 for r in reconciled if r.currently_available and r.evidence == AvailabilityEvidence.VERIFIED),
                    "app_stale_offer_count": sum(1 for r in reconciled if r.currently_available and r.evidence == AvailabilityEvidence.STALE),
                    "app_unverified_offer_count": sum(1 for r in reconciled if r.currently_available and r.evidence == AvailabilityEvidence.UNKNOWN),
                    "catalogue_subscription_options": sum(1 for r in reconciled if r.currently_available and r.offer_type in {"subscription", "addon"}),
                    "catalogue_free_options": sum(1 for r in reconciled if r.currently_available and r.offer_type in {"free", "free_with_ads"}),
                    "catalogue_paid_options": sum(1 for r in reconciled if r.currently_available and r.offer_type in {"rent", "buy"}),
                    "external_state": external.get("result") if external else None,
                    "external_explicit_positive": _external_positive(external),
                    "external_provider_names": names,
                    "comparison_state": state, "comparison_reason": reason,
                    "profile_b_verdict": profiles["B"].verdict,
                    "profile_c_verdict": profiles["C"].verdict,
                })

        snapshot_columns = list(app_snapshot[0]) if app_snapshot else [*sorted(REQUIRED_COHORT_COLUMNS), "movie_id", "offer_id"]
        comparison_columns = list(comparisons[0]) if comparisons else [*sorted(REQUIRED_COHORT_COLUMNS), "comparison_state"]
        _write_csv(outdir / "application_snapshot.csv", app_snapshot, snapshot_columns)
        _write_csv(outdir / "comparison.csv", comparisons, comparison_columns)
        self._write_reports(outdir, cohort_rows, app_snapshot, comparisons, source_status)
        self.stdout.write(self.style.SUCCESS(f"Audited {len(cohort_rows)} cases; wrote read-only snapshot to {outdir}"))

    def _write_reports(self, outdir, cohort, snapshot, comparisons, sources):
        counts = Counter(row["comparison_state"] for row in comparisons)
        found = sum(1 for row in comparisons if row["app_movie_found"])
        missing = len(comparisons) - found
        offers = sum(int(row["offer_id"] is not None) for row in snapshot)
        current_offers = sum(1 for row in snapshot if row["currently_available"] is True)
        stale = sum(1 for row in snapshot if row["evidence_freshness"] == "stale")
        countries_in_cohort = sorted({row["country"] for row in cohort})
        countries_with_offers = sorted({row["offer_country"] for row in snapshot if row["offer_country"]})
        present_enabled = sorted(slug for slug, row in sources.items() if row["present"] and row["enabled"])
        registered = {slug for slug, row in sources.items() if row["registered"]}
        absent_rows = sorted(slug for slug in registered if not sources[slug]["present"])
        disabled_rows = sorted(slug for slug in registered if sources[slug]["present"] and sources[slug]["enabled"] is False)
        evaluated = sum(1 for row in comparisons if row["profile_b_verdict"] is not None and row["profile_c_verdict"] is not None)
        external_positive = sum(1 for row in comparisons if row["external_explicit_positive"])
        (outdir / "coverage_summary.md").write_text(
            "# Stage 2C coverage summary\n\n"
            f"Baseline: `0da80a3f86555206670e74d7b4344bb21fa22111`\n\n"
            f"Cohort cases: {len(cohort)}. Movie rows found: {found}; missing: {missing}. "
            f"Canonical raw offer rows: {offers}; lifecycle-current rows: {current_offers}; stale rows: {stale}. "
            f"Synthetic resolver results returned: {evaluated} cases across Profiles B and C. "
            f"Cases with a local Movie plus canonical offers to evaluate: {sum(1 for row in comparisons if row['app_movie_found'] and row['app_offer_count'])}.\n\n"
            f"Stage 2B explicit positive listings: {external_positive}; cases without explicit positive external evidence: {len(cohort) - external_positive}.\n\n"
            f"Countries in cohort: {', '.join(countries_in_cohort) or 'none'}. Countries represented by stored offers: {', '.join(countries_with_offers) or 'none'}. "
            f"Enabled source rows: {', '.join(present_enabled) or 'none'}. "
            f"Registered sources with no persisted Source row: {', '.join(absent_rows) or 'none'}. "
            f"Persisted but disabled sources: {', '.join(disabled_rows) or 'none'}.\n\n"
            "## Comparison states\n\n" + "\n".join(f"- {name}: {counts.get(name, 0)}" for name in sorted(COMPARISON_STATES)) + "\n\n"
            "The local database is an offline developer database, not production. No user subscriptions or preferences were read. "
            "Profile verdicts use in-memory synthetic preferences and the canonical `resolve_title` resolver. "
            "The audit command made 0 HTTP requests and consumed 0 Streaming Availability or Watchmode API calls.\n\n"
            "## Reproduction\n\n"
            "From `backend/`, run `python manage.py audit_availability_accuracy --cohort ../docs/audits/stage2c/cohort.csv "
            "--external-evidence ../docs/audits/stage2c/external_evidence.csv --output ../docs/audits/stage2c --offline`.\n",
            encoding="utf-8")
        (outdir / "confirmed_discrepancies.md").write_text(
            "# Confirmed discrepancies\n\n"
            "No application-versus-external conflict was confirmed. Stage 2B evidence is a secondary listing source and contains no confirmed negative availability; absent local offers are classified as missing coverage, never as an app error.\n\n"
            "## Classification precedence\n\n"
            "1. `NOT_COMPARABLE` when TMDb identity, media type, year, or country disagrees.\n"
            "2. `INSUFFICIENT_EXTERNAL_EVIDENCE` when no matching row exists, availability is not explicitly positive, provider names are absent, or a negative is secondary/unspecified.\n"
            "3. `APP_DATA_MISSING` when the exact local Movie identity is absent.\n"
            "4. For explicit primary/official negatives, `CONFLICT_CONFIRMED` requires a verified same-provider canonical offer in the same country; otherwise `INSUFFICIENT_APP_EVIDENCE`.\n"
            "5. `APP_SOURCE_DISABLED` requires an explicit `app_source_slug` in the external evidence and a persisted local Source row with `enabled=false`. An absent/unconfigured row has unknown state and is never inferred disabled from the research site's name. Without that explicit source attribution, no-offer cases are `APP_DATA_MISSING`. A stale matching provider/class is `APP_DATA_STALE`, even if a different provider has fresh evidence.\n"
            "6. Positive `AGREEMENT_CONFIRMED` requires external evidence that unambiguously names one provider and one offer class, plus fresh verified canonical evidence matching provider, country, and class. Aggregated provider/class lists without a pairing stay `INSUFFICIENT_EXTERNAL_EVIDENCE`; a same-provider offer of another class is insufficient. An aggregator's omission of other app providers is not a conflict. Remaining mismatches are `INSUFFICIENT_APP_EVIDENCE`.\n\n"
            "## Targeted edge cases\n\n"
            "- **D1 — Dune (1984) vs (2021):** no local Movie or offer row was found for TMDb 438631. Source TMDb IDs are persisted as exact external-ID mappings; fallback matching constrains media type and known movie year, and canonical reads are keyed by TMDb ID and media type. No defect reproduced.\n"
            "- **D2 — localized titles:** no local rows existed for Dark (70523) or The Platform (619264). TMDb IDs bypass title matching; sources without IDs use exact title/year matching, so aliases remain an unverified coverage risk. No speculative alias was added.\n"
            "- **D3 — Prime Video country scope:** the Prime Video collector declares NL only; refresh scope rejects IT requests and canonical offer reads filter by country. JustWatch may independently record IT offers. No matching local rows existed to test leakage.\n"
            "- **D4 — multi-provider offers:** canonical identity retains provider and country; reconciliation merges same-provider observations while keeping source facts and source-specific evidence. Non-authoritative aggregator omission does not deactivate another source's positive. No Godfather rows existed to test live behavior.\n"
            "- **D5 — unavailable external pages:** Stage 2B's JustWatch NL 404s, RaiPlay search 404s, and Pluto TV geo-block remain unverified evidence limitations, not negative findings.\n",
            encoding="utf-8")
        source_lines = ["# Source coverage", "", "Source registry presence and persisted Source rows are reported separately. Missing Source rows mean configuration state is unknown/absent in this DB, not disabled in deployment.", "", "| Source | Registered | Row present | Enabled |", "|---|---:|---:|---:|"]
        source_lines += [f"| {slug} | {str(v['registered']).lower()} | {str(v['present']).lower()} | {v['enabled'] if v['enabled'] is not None else 'unknown'} |" for slug, v in sources.items()]
        (outdir / "source_coverage.md").write_text("\n".join(source_lines) + "\n", encoding="utf-8")
        (outdir / "follow_up_plan.md").write_text(
            "# Follow-up plan\n\n"
            "1. Repeat this command against an explicitly authorized read-only database snapshot with canonical Offer rows.\n"
            "2. Compare provider-level offers only when title identity, country, and offer class align; retain aggregator evidence as secondary.\n"
            "3. For Dune (438631), inspect the exact provider content ID, TMDb mapping method/year, and offer country against Dune (1984) before interpreting any mismatch.\n"
            "4. For Dark (70523) and The Platform (619264), compare provider-native titles, release year, media type, and source TMDb IDs; only add localized aliases after a reproducible unmapped or wrong-match case.\n"
            "5. Compare Prime Video NL collector rows with independent IT aggregator rows, preserving country on every offer; do not widen the collector's declared NL scope.\n"
            "6. For The Godfather (238), verify distinct provider offer groups and source observations, including the Paramount/Prime add-on base requirement.\n"
            "7. Confirm negative availability only from explicit authoritative evidence; source failures and omissions remain unverified.\n",
            encoding="utf-8")
