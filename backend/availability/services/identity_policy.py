"""Central provenance checks for provider-content identity mappings."""

from availability.domain.enums import MatchMethod

EXTERNAL_ID_ASSERTION_SOURCES = frozenset({
    "justwatch",
    "streaming_availability",
    "watchmode",
})


def mapping_is_trusted(
    provider_slug: str,
    match_method: str,
    source_slug: str | None = None,
) -> bool:
    """Whether a persisted mapping can resolve this provider's content.

    Mediaset's collector reports a title and provider GUID but not a year or
    external TMDb ID. Its automatic title matches and historical
    Mediaset-sourced external-ID rows therefore cannot establish identity.
    Explicit manual mappings and independently reported external IDs from the
    trusted aggregator/API adapters remain valid. Other providers retain their
    existing mapping policies.
    """
    if provider_slug == "mediaset_infinity":
        return (
            match_method == MatchMethod.MANUAL
            or (
                match_method == MatchMethod.EXTERNAL_ID
                and source_slug in EXTERNAL_ID_ASSERTION_SOURCES
            )
        )
    return True
