"""Read-only eligibility checks for applying an exactly verified association."""
from availability.models import ContentMapping, ProviderContent
from tracker.models import CanonicalBootstrapEvidence, Movie
from tracker.services.catalogue_reconciliation import _provider_content_id, _trusted_external_tmdb_id


def canonical_association_conflict(record, media, year, tmdb_id, *, lock=False):
    """Return rejection evidence, without creating or updating any rows.

    Preview reads current state. Commit calls this again inside its transaction
    with row locks; never reuse the selector's cached proof or mapping objects.
    Absent-row races still require the final writer's guards and constraints.
    """
    def current(model, **filters):
        qs = model.objects.filter(**filters)
        return (qs.select_for_update() if lock else qs).first()

    def conflict(action, evidence):
        return {"action": action, "evidence": [evidence]}

    target = (tmdb_id, media)
    native_id = _provider_content_id(record)
    mapping = current(ContentMapping, provider_id=record.provider_id, provider_content_id=native_id)
    if mapping:
        if (mapping.tmdb_id, mapping.content_type) != target:
            return conflict("existing_canonical_mapping_conflict",
                            "existing canonical mapping preserved; candidate requires operator review")
        if mapping.content_id:
            content = current(ProviderContent, pk=mapping.content_id)
            if content is None or (content.provider_id, content.provider_content_id, content.content_type) != (
                    record.provider_id, native_id, media):
                return conflict("existing_provider_content_conflict",
                                "existing mapping links a contradictory provider-content identity; preserved for review")

    trusted = _trusted_external_tmdb_id(record)
    if trusted and int(trusted) != tmdb_id:
        return conflict("trusted_identifier_conflict", "candidate contradicts the discovery's trusted TMDb identifier")

    proof = current(CanonicalBootstrapEvidence, discovered_record_id=record.pk)
    if proof:
        proven = current(Movie, pk=proof.movie_id)
        if proven is None or (proven.tmdb_id, proven.content_type) != target:
            return conflict("existing_verification_identity_conflict",
                            "existing record-specific proof has another canonical identity; preserved for review")
        stored = proof.evidence
        if (not isinstance(stored, dict) or stored.get("tmdb_id") != proven.tmdb_id
                or stored.get("content_type") != proven.content_type
                or stored.get("tmdb_year", proven.year) != proven.year):
            return conflict("contradictory_verification_evidence",
                            "stored verification evidence contradicts its canonical identity; preserved for review")

    movie = current(Movie, tmdb_id=tmdb_id, content_type=media)
    if movie and movie.year != year:
        return conflict("existing_canonical_year_conflict", "existing canonical release year conflicts")
    return None
