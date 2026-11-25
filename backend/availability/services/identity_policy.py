"""Central provenance checks for provider-content identity mappings."""

from availability.domain.enums import MatchMethod

EXTERNAL_ID_ASSERTION_SOURCES = frozenset({
    "justwatch",
    "streaming_availability",
    "watchmode",
})

# These title-targeted adapters discover a provider-native entry from a
# homonymous title; neither has an independently reported canonical identity.
# Their automatic title/year mappings and their own request-context external
# IDs are therefore not sufficient provenance for canonical availability.
TITLE_SEARCH_IDENTITY_SOURCES = frozenset({
    "mediaset_infinity",
    "youtube",
})


def reviewed_provider_series_tmdb_id(provider_slug: str, provider_content_id: str) -> int | None:
    """Return the reviewed TMDb series ID for a known provider programme."""
    if provider_slug == "bbc_iplayer" and provider_content_id == "b00jd68z":
        # Issue #190: the BBC brand and its descendants belong to the UK Office.
        return 2996
    return None


def mapping_is_trusted(
    provider_slug: str,
    match_method: str,
    source_slug: str | None = None,
) -> bool:
    """Whether a persisted mapping can resolve this provider's content.

    Mediaset and YouTube discover provider-native content through title
    searches. Their automatic title matches and provider-sourced external-ID
    rows cannot establish canonical identity; YouTube search results do not
    report a TMDb ID, so a request-copied value is not an assertion. Explicit
    manual mappings and independently reported external IDs from trusted
    aggregator/API adapters remain valid. Other providers retain their
    existing mapping policies.
    """
    if provider_slug in TITLE_SEARCH_IDENTITY_SOURCES:
        return (
            match_method == MatchMethod.MANUAL
            or (
                match_method == MatchMethod.EXTERNAL_ID
                and source_slug in EXTERNAL_ID_ASSERTION_SOURCES
            )
        )
    return True


def trusted_mapping_query(
    *,
    provider_lookup: str,
    method_lookup: str,
    source_lookup: str,
):
    """ORM predicate equivalent to :func:`mapping_is_trusted`.

    Mapping-backed catalogue reads use this predicate so an untrusted
    title-search mapping cannot leak through a legacy/unofficial read path.
    The field paths are explicit because the same rule is used from different
    queryset roots (for example ``provider__slug`` and ``mappings__source``).
    """
    from django.db.models import Q

    trusted = (
        ~Q(**{f"{provider_lookup}__in": TITLE_SEARCH_IDENTITY_SOURCES})
        | Q(**{method_lookup: MatchMethod.MANUAL})
        | Q(
            **{
                method_lookup: MatchMethod.EXTERNAL_ID,
                f"{source_lookup}__in": EXTERNAL_ID_ASSERTION_SOURCES,
            }
        )
    )
    return trusted


def legacy_catalogue_visibility_query(
    *,
    provider_lookup: str,
    method_lookup: str,
    source_lookup: str,
):
    """Preserve the legacy catalogue's existing Mediaset visibility boundary.

    Unresolved YouTube provider-native rows remain inspectable in the
    unofficial catalogue, but Mediaset rows keep the Stage 2M rule that only
    manual or independently asserted external-ID mappings appear there.
    Canonical TMDb attribution still uses :func:`trusted_mapping_query`.
    """
    from django.db.models import Q

    return (
        ~Q(**{provider_lookup: "mediaset_infinity"})
        | Q(**{method_lookup: MatchMethod.MANUAL})
        | Q(
            **{
                method_lookup: MatchMethod.EXTERNAL_ID,
                f"{source_lookup}__in": EXTERNAL_ID_ASSERTION_SOURCES,
            }
        )
    )
