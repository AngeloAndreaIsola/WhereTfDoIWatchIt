"""Query-only service for availability reads — no network calls, no refresh triggers.

AvailabilityQueryService reads only from the canonical availability database
(availability.Offer, Country, Source). It does not call StreamingAvailabilityClient
or perform any network requests.

The service provides a simple, focused interface for querying currently-available
offers by content type and TMDB ID, with optional country filtering.
"""
from __future__ import annotations

from typing import Optional

from django.db.models import QuerySet

from ..domain.enums import ContentType
from ..domain.reconciliation import ReconciliationResult
from ..persistence.repositories import OfferRepository
from .reconciliation import AvailabilityReconciliationService


class AvailabilityQueryService:
    """Local-only query service for availability data.

    This service is strictly read-only. All data comes from the availability.Offer
    table (or related tables), and no external APIs are called, no refreshes are
    triggered, and no mutations occur.

    Two read shapes are exposed:

    * ``get_for_*`` returns raw rows — the observation boundary, useful when the
      caller wants exactly what each source last wrote, deactivation included.
    * ``get_reconciled_*`` returns the reconciled view: one effective offer per
      (content, provider, country, offer type) with source disagreements
      surfaced as conflicts. The HTTP read path uses this one.
    """

    def __init__(self, reconciliation: Optional[AvailabilityReconciliationService] = None):
        self.repo = OfferRepository()
        self.reconciliation = reconciliation or AvailabilityReconciliationService()

    def get_for_content(
        self,
        tmdb_id: int,
        content_type: str,
        country: Optional[str] = None,
    ) -> QuerySet:
        """Return currently-available offers for a given TMDB content.

        Args:
            tmdb_id: TMDB ID for the movie or series.
            content_type: 'movie' or 'series' (from ContentType enum).
            country: Optional two-letter country code (e.g., 'US', 'NL').

        Returns:
            QuerySet of Offer objects matching the query criteria.
            Only currently_available=True offers are returned.
            Empty queryset when no local offers exist.

        Raises:
            ValueError: If content_type is not one of the allowed values.
        """
        if content_type not in [ContentType.MOVIE, ContentType.SERIES]:
            raise ValueError(
                f"Invalid content_type: {content_type}. "
                f"Must be one of: {ContentType.MOVIE}, {ContentType.SERIES}"
            )

        offers = self.repo.get_for_content(tmdb_id, content_type, country)
        return offers

    def get_for_movie(
        self,
        tmdb_id: int,
        country: Optional[str] = None,
    ) -> QuerySet:
        """Convenience method for movie queries.

        Args:
            tmdb_id: TMDB ID for the movie.
            country: Optional two-letter country code.

        Returns:
            QuerySet of currently-available movie offers.
        """
        return self.get_for_content(tmdb_id, ContentType.MOVIE, country)

    def get_for_series(
        self,
        tmdb_id: int,
        season_number: int,
        country: Optional[str] = None,
    ) -> QuerySet:
        """Convenience method for series queries.

        Args:
            tmdb_id: TMDB ID for the series.
            season_number: Season number (required for series).
            country: Optional two-letter country code.

        Returns:
            QuerySet of currently-available series offers for the given season.
        """
        # Phase 3 writes explicit season rows; retain the Phase 2 series-row
        # representation while the read adapter transitions.
        return self.repo.get_for_series_season(tmdb_id, season_number, country)

    def get_reconciled_for_content(
        self,
        tmdb_id: int,
        content_type: str,
        country: Optional[str] = None,
        *,
        now=None,
    ) -> ReconciliationResult:
        """Reconcile every observation of one TMDB content.

        Args:
            tmdb_id: TMDB ID for the movie or series.
            content_type: 'movie' or 'series' (from ContentType enum).
            country: Optional two-letter country code (e.g., 'US', 'NL').
            now: Optional evaluation instant for window/freshness checks.

        Returns:
            ReconciliationResult holding the effective offers (including
            currently-unavailable ones, so deactivation stays visible) and the
            conflicts found between sources. Empty result when nothing has
            been observed locally.

        Raises:
            ValueError: If content_type is not one of the allowed values.
        """
        if content_type not in [ContentType.MOVIE, ContentType.SERIES]:
            raise ValueError(
                f"Invalid content_type: {content_type}. "
                f"Must be one of: {ContentType.MOVIE}, {ContentType.SERIES}"
            )
        return self.reconciliation.reconcile(
            content_types=(content_type,),
            tmdb_id=tmdb_id,
            country=country,
            now=now,
        )

    def get_reconciled_for_movie(
        self,
        tmdb_id: int,
        country: Optional[str] = None,
        *,
        now=None,
    ) -> ReconciliationResult:
        """Reconciled convenience method for movie queries."""
        return self.get_reconciled_for_content(tmdb_id, ContentType.MOVIE, country, now=now)

    def get_reconciled_for_series(
        self,
        tmdb_id: int,
        season_number: int,
        country: Optional[str] = None,
        *,
        now=None,
    ) -> ReconciliationResult:
        """Reconciled convenience method for series queries.

        Mirrors :meth:`get_for_series`: series and season rows for the given
        season are reconciled together.
        """
        return self.reconciliation.reconcile(
            content_types=(ContentType.SERIES, ContentType.SEASON),
            tmdb_id=tmdb_id,
            country=country,
            season_number=season_number,
            now=now,
        )

    def get_reconciled_for_contents(self, contents, *, country=None, now=None):
        """Return effective offers for many title identities in fixed queries.

        List/report surfaces need only offers, not per-title diagnostics.  By
        reconciling all selected raw observations in one pass, the mapping,
        cross-content claim, sync-state, and price lookups remain bounded
        instead of repeating for every title.
        """
        pairs = {
            (int(tmdb_id), str(content_type or ContentType.MOVIE))
            for tmdb_id, content_type in contents
        }
        if not pairs:
            return {}
        invalid = {kind for _tmdb_id, kind in pairs if kind not in ContentType.values}
        if invalid:
            raise ValueError(f"Invalid content types: {sorted(invalid)!r}")
        rows = list(self.repo.observations_for_contents(pairs, country_code=country))
        result = {pair: [] for pair in pairs}
        if not rows:
            return result
        offers, _conflicts, _statuses = self.reconciliation._reconcile_rows(
            rows, now=self.reconciliation._now(now)
        )
        for offer in offers:
            key = (offer.tmdb_id, offer.content_type)
            if key in result and offer.currently_available:
                result[key].append(offer)
        return result
