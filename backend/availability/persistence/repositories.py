from django.db.models import Count, Q
from django.utils import timezone

from tracker.models import ProviderCategory

from ..domain.enums import ContentType, MatchMethod, SyncStatus
from ..models import (
    LEGACY_SCRAPER_SOURCE_SLUG,
    Country,
    ContentMapping,
    Offer,
    OfferEvidence,
    OfferPrice,
    ProviderContent,
    SyncCursor,
    SyncRun,
    build_offer_identity,
    build_provider_content_identity,
)
from ..services.normalize import truncate_field
from ..services.identity_policy import (
    EXTERNAL_ID_ASSERTION_SOURCES,
    mapping_is_trusted,
)

#: Column width for a provider-native content id, read from the model itself so
#: a future migration widening the column is picked up automatically instead of
#: silently leaving this clamp behind at the old width.
PROVIDER_CONTENT_ID_MAX_LENGTH = ProviderContent._meta.get_field(
    "provider_content_id"
).max_length


class InactiveSyncError(Exception):
    """Raised when a sync run is not allowed to deactivate unseen offers."""


class OfferEvidenceRepository:
    """Persistence for per-source offer observation evidence.

    One row per ``(offer_identity, source)``: the identity is the exact
    canonical ``Offer.identity``, so evidence follows the offer across
    refreshes, deactivation and recreation without a database FK (see
    ``OfferEvidence`` for why a FK is unsafe here). The read/write policy lives
    in ``OfferRepository``; this class owns only the row lifecycle and the
    integrity helpers.
    """

    def record(self, *, offer_identity, source, provider_slug, country_code,
               content_type, tmdb_id, season_number=None, episode_number=None,
               offer_type, provider_content_id="", is_present=True,
               authoritative=False, observed_at=None, now=None):
        """Upsert one source's observation state for one canonical offer.

        ``first_seen`` never moves; ``last_seen``/``observed_at`` follow the
        latest observation and ``is_present``/``authoritative`` state the
        current one.
        """
        now = now or timezone.now()
        defaults = {
            "provider_slug": provider_slug,
            "country_code": country_code,
            "content_type": content_type,
            "tmdb_id": tmdb_id,
            "season_number": season_number,
            "episode_number": episode_number,
            "offer_type": offer_type,
            "provider_content_id": provider_content_id,
            "is_present": is_present,
            "authoritative": authoritative,
            "last_seen": now,
            "observed_at": observed_at if observed_at is not None else now,
        }
        row, _created = OfferEvidence.objects.update_or_create(
            offer_identity=offer_identity,
            source=source,
            defaults=defaults,
            create_defaults={**defaults, "first_seen": now},
        )
        return row

    def record_absent_bulk(self, offers, *, source, now=None):
        """Record one source's authoritative absence for many canonical offers.

        ``offers`` is an iterable of ``Offer`` rows (provider/country may be
        preloaded). Only *this* source's evidence is touched: a second source
        that still observes an offer keeps its present row. Returns the number
        of evidence rows written (created or updated).
        """
        now = now or timezone.now()
        offers = list(offers)
        if not offers:
            return 0
        identities = [offer.identity for offer in offers]
        existing = {
            row.offer_identity: row
            for row in OfferEvidence.objects.filter(
                offer_identity__in=identities, source=source
            )
        }
        to_create = []
        to_update = []
        for offer in offers:
            row = existing.get(offer.identity)
            if row is None:
                to_create.append(OfferEvidence(
                    source=source,
                    offer_identity=offer.identity,
                    provider_slug=offer.provider.slug,
                    country_code=offer.country.code,
                    content_type=offer.content_type,
                    tmdb_id=offer.tmdb_id,
                    season_number=offer.season_number,
                    episode_number=offer.episode_number,
                    offer_type=offer.offer_type,
                    provider_content_id=offer.provider_content_id,
                    is_present=False,
                    authoritative=True,
                    first_seen=now,
                    last_seen=now,
                    observed_at=now,
                ))
            else:
                row.is_present = False
                row.authoritative = True
                row.last_seen = now
                row.observed_at = now
                row.updated_at = now
                to_update.append(row)
        if to_create:
            OfferEvidence.objects.bulk_create(to_create)
        if to_update:
            OfferEvidence.objects.bulk_update(
                to_update,
                ["is_present", "authoritative", "last_seen", "observed_at",
                 "updated_at"],
            )
        return len(to_create) + len(to_update)

    def present_identities(self, identities):
        """The subset of ``identities`` some source currently observes."""
        identities = [identity for identity in identities if identity]
        if not identities:
            return set()
        return set(
            OfferEvidence.objects.filter(
                offer_identity__in=identities, is_present=True
            ).values_list("offer_identity", flat=True)
        )

    def for_identities(self, identities):
        """Every evidence row (present or absent) for the given identities."""
        identities = [identity for identity in identities if identity]
        if not identities:
            return []
        return list(
            OfferEvidence.objects.filter(offer_identity__in=identities)
            .select_related("source")
            .order_by("source__slug", "id")
        )

    def recompute_availability(self, identities, *, now=None):
        """Re-derive ``Offer.currently_available`` from current evidence.

        Used after evidence changes made outside the refresh pipeline (a source
        purge), so stored availability never drifts from the evidence it is
        derived from. An identity with no remaining evidence at all is set
        unavailable: the caller only recomputes identities whose evidence it
        just removed, so "no evidence" here means "the observations it had are
        gone", never "a legacy row that predates evidence". Returns the number
        of canonical rows changed.
        """
        identities = list({identity for identity in identities if identity})
        if not identities:
            return 0
        present = self.present_identities(identities)
        available = [identity for identity in identities if identity in present]
        unavailable = [identity for identity in identities if identity not in present]
        return self._set_availability(available, unavailable, now=now)

    def purge_orphans(self):
        """Delete evidence rows whose canonical offer no longer exists.

        The explicit integrity counterpart to ``OfferEvidence`` having no FK to
        ``Offer``: run it after any manual offer deletion. An evidence row is
        only removed when its identity is absent from ``Offer``. Returns the
        number of deleted rows.
        """
        deleted, _ = OfferEvidence.objects.exclude(
            offer_identity__in=Offer.objects.values("identity")
        ).delete()
        return deleted

    def purge_for_source(self, source):
        """Remove all evidence for one source and recompute affected offers.

        The explicit policy for a source that must stop contributing evidence
        (a deleted or retired source). Deleting the ``Source`` row itself is
        refused while evidence exists (FK ``PROTECT``), so this runs first.
        After removal an offer another source still observes stays available;
        one this source alone observed becomes unavailable - its only
        observations are gone. Returns ``(deleted, recomputed)``.
        """
        identities = list(
            OfferEvidence.objects.filter(source=source)
            .values_list("offer_identity", flat=True)
            .distinct()
        )
        deleted, _ = OfferEvidence.objects.filter(source=source).delete()
        recomputed = self.recompute_availability(identities)
        return deleted, recomputed

    @staticmethod
    def _set_availability(available, unavailable, *, now=None):
        now = now or timezone.now()
        changed = 0
        if available:
            changed += Offer.objects.filter(
                identity__in=available, currently_available=False
            ).update(currently_available=True, updated_at=now)
        if unavailable:
            changed += Offer.objects.filter(
                identity__in=unavailable, currently_available=True
            ).update(currently_available=False, updated_at=now)
        return changed


class OfferRepository:
    """Read/write persistence for canonical offers. No fetching, no querying policy."""

    def __init__(self, evidence_repository=None):
        self.evidence = evidence_repository or OfferEvidenceRepository()

    def upsert(self, *, provider, country, source, content_type, tmdb_id, offer_type,
               season_number=None, episode_number=None, provider_content_id="",
               provider_url="", quality="", audio_languages=None, subtitle_languages=None,
               available_from=None, available_until=None, source_record_id="",
               source_version="", observed_at=None, sync_run=None, now=None):
        if content_type not in ContentType.values:
            raise ValueError(
                f"Invalid content type: {content_type}. Must be a canonical ContentType."
            )
        if content_type in [ContentType.SEASON, ContentType.EPISODE] and season_number is None:
            raise ValueError("Season and episode offers require a season number")
        if content_type == ContentType.EPISODE and episode_number is None:
            raise ValueError("Episode offers require an episode number")
        if season_number is not None and season_number < 0:
            raise ValueError(
                "Season number cannot be negative"
            )

        # Offers carry the same provider-native id on the same-width column, so
        # they take the same clamp (see truncate_field).
        provider_content_id = truncate_field(
            provider_content_id, PROVIDER_CONTENT_ID_MAX_LENGTH
        )

        identity = build_offer_identity(
            provider.slug,
            country.code,
            content_type,
            tmdb_id,
            season_number,
            episode_number,
            offer_type,
            provider_content_id,
        )

        now = now or timezone.now()

        defaults = {
            "provider": provider,
            "country": country,
            "source": source,
            "sync_run": sync_run,
            "content_type": content_type,
            "tmdb_id": tmdb_id,
            "season_number": season_number,
            "episode_number": episode_number,
            "offer_type": offer_type,
            "provider_content_id": provider_content_id,
            "provider_url": provider_url,
            "quality": quality,
            "audio_languages": audio_languages,
            "subtitle_languages": subtitle_languages,
            "available_from": available_from,
            "available_until": available_until,
            "first_seen": now,
            "last_seen": now,
            "currently_available": True,
            "source_record_id": source_record_id,
            "source_version": source_version,
            "observed_at": observed_at,
            "identity": identity,
        }
        offer, created = Offer.objects.get_or_create(
            identity=identity,
            defaults=defaults,
        )
        if not created:
            # first_seen is deliberately absent: an offer's first sighting never moves.
            offer.source = source
            offer.sync_run = sync_run
            offer.provider_url = provider_url
            offer.quality = quality
            offer.audio_languages = audio_languages
            offer.subtitle_languages = subtitle_languages
            offer.available_from = available_from
            offer.available_until = available_until
            offer.source_record_id = source_record_id
            offer.source_version = source_version
            offer.observed_at = observed_at
            offer.last_seen = now
            offer.currently_available = True
            offer.save(update_fields=[
                "source",
                "sync_run",
                "provider_url",
                "quality",
                "audio_languages",
                "subtitle_languages",
                "available_from",
                "available_until",
                "source_record_id",
                "source_version",
                "observed_at",
                "last_seen",
                "currently_available",
                "updated_at",
            ])
        # The observation is also recorded per source, so a later authoritative
        # removal by *another* source cannot erase this source's evidence for
        # the shared canonical row.
        self.evidence.record(
            offer_identity=identity,
            source=source,
            provider_slug=provider.slug,
            country_code=country.code,
            content_type=content_type,
            tmdb_id=tmdb_id,
            season_number=season_number,
            episode_number=episode_number,
            offer_type=offer_type,
            provider_content_id=provider_content_id,
            is_present=True,
            authoritative=bool(sync_run is not None and sync_run.authoritative),
            observed_at=observed_at,
            now=now,
        )
        return offer, created

    def get_for_content(self, tmdb_id, content_type, country_code=None):
        qs = Offer.objects.filter(
            tmdb_id=tmdb_id,
            content_type=content_type,
            currently_available=True,
        )
        if country_code:
            country = Country.objects.filter(code=country_code.upper()).first()
            if country is None:
                return qs.none()
            qs = qs.filter(country=country)
        return qs.select_related("provider", "country", "source", "sync_run").order_by("id")

    def get_for_series_season(self, tmdb_id, season_number, country_code=None):
        qs = Offer.objects.filter(
            tmdb_id=tmdb_id,
            content_type__in=[ContentType.SERIES, ContentType.SEASON],
            season_number=season_number,
            currently_available=True,
        )
        if country_code:
            country = Country.objects.filter(code=country_code.upper()).first()
            if country is None:
                return qs.none()
            qs = qs.filter(country=country)
        return qs.select_related("provider", "country", "source", "sync_run").order_by("id")

    def observations_for(self, *, tmdb_id, content_types, season_number=None,
                         episode_number=None, country_code=None):
        """Raw observation rows for reconciliation: every row, any lifecycle.

        Unlike the read boundary above, deactivation is not applied here:
        reconciliation has to see rows an authoritative snapshot removed in
        order to reconcile what each source claims about them.
        """
        qs = Offer.objects.filter(tmdb_id=tmdb_id, content_type__in=content_types)
        if season_number is not None:
            qs = qs.filter(season_number=season_number)
        if episode_number is not None:
            qs = qs.filter(episode_number=episode_number)
        if country_code:
            qs = qs.filter(country__code=country_code.upper())
        return qs.select_related("provider", "country", "source", "sync_run").order_by("id")

    def observations_for_contents(self, contents):
        """Raw observations for several ``(tmdb_id, content_type)`` pairs.

        This is the list-page counterpart to :meth:`observations_for`.  The
        pairs must stay coupled: filtering ``tmdb_id__in`` and
        ``content_type__in`` independently could mix a movie with a series
        that happens to share its external id.
        """
        predicate = Q()
        for tmdb_id, content_type in set(contents):
            predicate |= Q(tmdb_id=tmdb_id, content_type=content_type)
        if not predicate:
            return Offer.objects.none()
        return Offer.objects.filter(predicate).select_related(
            "provider", "country", "source", "sync_run"
        ).order_by("id")

    def all_observations(self):
        """Every raw observation row, for database-wide reconciliation audits."""
        return Offer.objects.select_related(
            "provider", "country", "source", "sync_run"
        ).order_by("id")

    def alternate_content_claims(self, observations):
        """Active tmdb ids claiming each (provider, provider_content_id) key.

        Returns ``{(provider_id, pcid): {tmdb_id, ...}}`` across *all* contents,
        so a provider content id that is active under two TMDB ids is visible to
        the reconciliation layer. Inactive rows do not claim an id.
        """
        keys = {
            (row.provider_id, row.provider_content_id)
            for row in observations
            if row.provider_content_id
        }
        if not keys:
            return {}
        provider_ids = {provider_id for provider_id, _ in keys}
        pcids = {pcid for _, pcid in keys}
        claims = {}
        rows = Offer.objects.filter(
            provider_id__in=provider_ids,
            provider_content_id__in=pcids,
            currently_available=True,
        ).values_list("provider_id", "provider_content_id", "tmdb_id")
        for provider_id, pcid, tmdb_id in rows:
            key = (provider_id, pcid)
            if key in keys:
                claims.setdefault(key, set()).add(tmdb_id)
        return claims

    def withdraw_observed(self, *, source, provider, country, content):
        """Withdraw one source's assertion after an explicit item observation.

        This does not infer removal from a partial snapshot. It preserves any
        independent positive evidence and never touches another source's price.
        """
        identities = OfferEvidence.objects.filter(
            source=source, provider_slug=provider.slug,
            country_code=country.code, provider_content_id=content.provider_content_id,
        ).values_list("offer_identity", flat=True)
        candidates = list(Offer.objects.filter(
            Q(identity__in=identities) | Q(source=source),
            provider=provider, country=country,
            provider_content_id=content.provider_content_id,
            content_type=content.content_type, season_number=content.season_number,
            episode_number=content.episode_number, currently_available=True,
        ).select_related("provider", "country"))
        if not candidates:
            return 0
        now = timezone.now()
        self.evidence.record_absent_bulk(candidates, source=source, now=now)
        keys = [offer.identity for offer in candidates]
        self.evidence.recompute_availability(keys, now=now)
        OfferPrice.objects.filter(offer_identity__in=keys, source=source, is_current=True).update(
            is_current=False, updated_at=now,
        )
        return Offer.objects.filter(identity__in=keys, currently_available=False).count()

    def deactivate_unseen(self, sync_run, seen_identities):
        """Mark this source's evidence absent for offers it did not see.

        Rows are never deleted and last_seen never moves: a deactivated offer
        is still "last seen" when we last observed it.

        Source-scoped by design. The canonical identity deliberately excludes
        the source, so a second source may still be reporting the same offer.
        This run only proves its *own* source stopped observing it, so it:

        * records an authoritative absence for ``sync_run.source`` only;
        * recomputes effective availability from *all* sources' present
          evidence - an offer any other source still observes stays available;
        * marks only this source's price rows stale (never another source's).

        Effective availability is therefore no longer last-writer-wins: it is
        derived from the union of current per-source evidence.
        """
        if sync_run.status != SyncStatus.SUCCEEDED or not sync_run.authoritative:
            raise InactiveSyncError(
                "Only a successful authoritative sync may deactivate unseen offers "
                f"(run {sync_run.pk}: status={sync_run.status}, "
                f"authoritative={sync_run.authoritative})."
            )
        now = timezone.now()
        qs = (
            Offer.objects.filter(
                provider=sync_run.provider,
                currently_available=True,
                content_type__in=[ContentType.MOVIE, ContentType.SERIES],
            )
            .select_related("provider", "country")
        )
        if sync_run.country_id is not None:
            qs = qs.filter(country=sync_run.country)
        if seen_identities:
            qs = qs.exclude(identity__in=seen_identities)
        candidates = list(qs)
        if not candidates:
            return 0
        stale_identities = [offer.identity for offer in candidates]

        # 1. This run's own authoritative absence, and only this source's.
        self.evidence.record_absent_bulk(candidates, source=sync_run.source, now=now)

        # 2. Effective availability is the OR over every source's current
        #    evidence: an offer a co-reporting source still observes stays up.
        present = self.evidence.present_identities(stale_identities)
        to_deactivate = [
            identity for identity in stale_identities if identity not in present
        ]
        deactivated = 0
        if to_deactivate:
            deactivated = Offer.objects.filter(identity__in=to_deactivate).update(
                currently_available=False,
                sync_run=sync_run,
                updated_at=now,
            )

        # 3. Price lifecycle stays source-scoped, and covers every offer this
        #    run stopped seeing - even one another source keeps available. The
        #    rows themselves stay (last known value, is_current=False) so the
        #    removal is inspectable and a reappearing offer reuses them.
        OfferPrice.objects.filter(
            offer_identity__in=stale_identities,
            source=sync_run.source,
            is_current=True,
        ).update(is_current=False, updated_at=now)
        return deactivated


class OfferPriceRepository:
    """Persistence for per-source price observations.

    One row per ``(offer_identity, source)``: the identity is the exact
    canonical ``Offer.identity``, so a price follows the offer across refreshes,
    deactivation and recreation without a database FK (see ``OfferPrice`` for
    why a FK is unsafe here). Reads never cross the availability boundary
    themselves; ``AvailabilityReconciliationService`` attaches these rows to the
    observations it already selected.
    """

    def record(self, *, offer, source, amount, currency, source_offer_id="",
               error="", observed_at=None, now=None):
        """Persist one source observation's price.

        ``amount``/``currency`` are already validated by
        ``availability.services.normalize.normalize_price``. A valid pair
        updates the row and makes it current. Anything else preserves the last
        known value and marks it not current — a missing price is never stored
        as zero and never erases a value observed by *another* source (those
        are different rows).
        """
        now = now or timezone.now()
        currency = (currency or "").strip().upper()
        valid = amount is not None and bool(currency) and not error

        row = OfferPrice.objects.filter(
            offer_identity=offer.identity, source=source
        ).first()
        if row is None:
            # Nothing to record when the source simply supplied no price and
            # there is no previous value to preserve. A malformed/ambiguous
            # observation is kept so the reason is inspectable.
            if amount is None and not error:
                return None
            row = OfferPrice(
                offer_identity=offer.identity,
                source=source,
                first_seen=now,
            )

        row.provider_slug = offer.provider.slug
        row.country_code = offer.country.code
        row.content_type = offer.content_type
        row.tmdb_id = offer.tmdb_id
        row.season_number = offer.season_number
        row.episode_number = offer.episode_number
        row.offer_type = offer.offer_type
        row.provider_content_id = offer.provider_content_id
        row.source_offer_id = source_offer_id or ""
        row.last_seen = now
        row.observed_at = observed_at or now
        if valid:
            row.amount = amount
            row.currency = currency
            row.is_current = True
            row.validation_error = ""
        else:
            # Keep the last known amount/currency: absence or malformed data is
            # not proof that the price disappeared, and zero would be a lie.
            row.is_current = False
            row.validation_error = (error or "price not reported")[:100]
        row.save()
        return row

    def for_identities(self, identities):
        """Every price row for the given canonical offer identities."""
        identities = [identity for identity in identities if identity]
        if not identities:
            return []
        return list(
            OfferPrice.objects.filter(offer_identity__in=identities)
            .select_related("source")
            .order_by("source__slug", "id")
        )

    def purge_orphans(self):
        """Delete price rows whose canonical offer no longer exists.

        ``OfferPrice`` deliberately has no database ``ForeignKey`` to ``Offer``
        (see :class:`~availability.models.OfferPrice`), so deleting an offer -
        by hand, through the admin, or by an operator script - cannot cascade.
        The row is harmless (reads are always keyed by live offer identities),
        but it would otherwise accumulate forever. This is the explicit
        integrity counterpart: run it after any manual offer deletion. A price
        row is only ever removed when its identity is absent from ``Offer``.

        Returns the number of deleted rows.
        """
        deleted, _ = OfferPrice.objects.exclude(
            offer_identity__in=Offer.objects.values("identity")
        ).delete()
        return deleted


class ProviderContentRepository:
    """Persistence for provider-native catalogue entries (Phase 7), plus the
    local catalogue read surface (Phase 8).

    Discovery-only: the row answers "what does this provider say exists?" and
    is upserted from the source boundary value, so repeated refreshes converge
    on one row keyed by the provider's own identity. Nothing here requires a
    TMDb id, a country, or an offer — mapping (``MappingRepository``) and
    commercial offers (``OfferRepository``) stay separate concerns.

    Reads answer only from the local database — no source, no HTTP, no
    refresh — with deterministic ordering (unique ``identity``; title then
    identity for search) so callers and tests always see the same catalogue.
    Mapping and offer presence are resolved in bulk (``tmdb_ids_for``,
    ``active_offer_counts``) so assembling many views never degenerates into
    one query per row.
    """

    def get(self, *, provider, provider_content_id, content_type,
            season_number=None, episode_number=None):
        """The row for this provider-native identity, or None."""
        identity = build_provider_content_identity(
            provider.slug, provider_content_id, content_type,
            season_number, episode_number,
        )
        return (
            ProviderContent.objects.filter(identity=identity)
            .select_related("provider")
            .first()
        )

    def list(self, *, provider=None, content_type=None, provider_content_id=None,
             season_number=None, episode_number=None, currently_available=None):
        """Catalogue rows matching the given dimensions, deterministically ordered.

        ``None`` for any filter means "do not filter on it" — in particular a
        ``None`` ``season_number`` is *not* an ``IS NULL`` filter (use
        ``content_type`` for that distinction). Identity/dimension filters
        only; never a lookup of another table's primary key.
        """
        qs = ProviderContent.objects.all()
        if provider is not None:
            qs = qs.filter(provider=provider)
        if content_type is not None:
            qs = qs.filter(content_type=content_type)
        if provider_content_id is not None:
            qs = qs.filter(provider_content_id=provider_content_id)
        if season_number is not None:
            qs = qs.filter(season_number=season_number)
        if episode_number is not None:
            qs = qs.filter(episode_number=episode_number)
        if currently_available is not None:
            qs = qs.filter(currently_available=currently_available)
        return qs.select_related("provider").order_by("identity")

    def list_unofficial(self, *, provider_slugs=None, search=None, country=None,
                        year_min=None, year_max=None, provider_categories=None,
                        currently_available=True):
        """Catalogue rows for the unofficial section of the unified catalogue.

        An *unofficial* row is one observed by the legacy scraper ingestion
        bridge (``legacy_scraper`` source) or owned by a scraper-category
        provider — the same entries the unified catalogue has always shown in
        its unofficial section, now served from the canonical tables instead
        of ``tracker.ScrapedContentMapping``.

        Filters mirror that legacy read: enabled providers, an optional
        provider slug set, case-insensitive title search, provider-or-row
        country, and a year range over the stored year-precision release date
        (rows without one drop out of a bounded range, as legacy rows without
        a year did). ``provider_categories`` narrows to raw ``Provider``
        categories (the ``/api/v2/unofficial/`` category scope);
        ``currently_available`` is tri-state — ``True`` (default) only
        available rows, ``None`` does not filter on availability at all (the
        legacy ``available=false`` answer), ``False`` only unavailable ones.
        Ordered by recency of observation with identity as a stable tiebreak.
        Purely local: no source, client, or refresh module is reachable from
        here.
        """
        qs = (
            ProviderContent.objects.select_related("provider", "source")
            .filter(provider__is_enabled=True)
            .filter(
                Q(provider__category=ProviderCategory.SCRAPER)
                | Q(source__slug=LEGACY_SCRAPER_SOURCE_SLUG)
            )
            .filter(
                ~Q(provider__slug="mediaset_infinity")
                | Q(mappings__match_method=MatchMethod.MANUAL)
                | Q(
                    mappings__match_method=MatchMethod.EXTERNAL_ID,
                    mappings__source__slug__in=EXTERNAL_ID_ASSERTION_SOURCES,
                )
            )
        )
        if currently_available is not None:
            qs = qs.filter(currently_available=currently_available)
        if provider_categories is not None:
            qs = qs.filter(provider__category__in=list(provider_categories))
        if provider_slugs is not None:
            qs = qs.filter(provider__slug__in=list(provider_slugs))
        if search:
            qs = qs.filter(title__icontains=search)
        if country:
            qs = qs.filter(
                Q(provider__country_code=country) | Q(metadata__country_code=country)
            )
        if year_min is not None:
            qs = qs.filter(release_date__year__gte=year_min)
        if year_max is not None:
            qs = qs.filter(release_date__year__lte=year_max)
        return qs.order_by("-updated_at", "identity")

    def list_mapped_to_tmdb(self, *, tmdb_id, provider_categories=None):
        """Catalogue rows whose ``ContentMapping`` resolves to *tmdb_id*.

        The canonical replacement for reading ``ScrapedContentMapping`` by
        ``movie=`` (the prime BVOD block and ``/api/v2/unofficial/movie/<id>/offers/``).
        Same *unofficial row* predicate and observation filters as
        ``list_unofficial`` — enabled providers, currently available, owned by
        a scraper-category provider or observed by the legacy scraper bridge —
        narrowed through the mapping join, with an optional raw provider
        category set (bvod/avod/tvod/fast vs scraper). Rows a canonical
        source (BBC iPlayer, Tubi, …) observed for the same provider are
        deliberately out of scope: they surface through the official read
        path, never here. Purely local: no source, client, or refresh module
        is reachable from here.
        """
        qs = (
            ProviderContent.objects.select_related("provider", "source")
            .filter(
                mappings__tmdb_id=tmdb_id,
                provider__is_enabled=True,
                currently_available=True,
            )
            .filter(
                Q(provider__category=ProviderCategory.SCRAPER)
                | Q(source__slug=LEGACY_SCRAPER_SOURCE_SLUG)
            )
            .filter(
                ~Q(provider__slug="mediaset_infinity")
                | Q(mappings__match_method=MatchMethod.MANUAL)
                | Q(
                    mappings__match_method=MatchMethod.EXTERNAL_ID,
                    mappings__source__slug__in=EXTERNAL_ID_ASSERTION_SOURCES,
                )
            )
        )
        if provider_categories is not None:
            qs = qs.filter(provider__category__in=list(provider_categories))
        return qs.distinct().order_by("-updated_at", "identity")

    def search_by_title(self, title, *, provider=None, content_type=None,
                        currently_available=None):
        """Case-insensitive partial title match over persisted catalogue rows.

        Database-backed (``icontains``), deterministic (title then identity),
        safe for partial terms. No trigram index, no fuzzy matching, no
        external search — the caller narrows with the same filters ``list``
        accepts.
        """
        return (
            self.list(
                provider=provider,
                content_type=content_type,
                currently_available=currently_available,
            )
            .filter(title__icontains=title)
            .order_by("title", "identity")
        )

    def tmdb_ids_for(self, rows) -> dict:
        """ContentMapping resolution for many rows in one query.

        Keyed by ``(provider_id, provider_content_id)`` — the mapping's own
        identity (``ContentMapping`` is unique per provider-native id, not per
        season slot), so a series row and its seasons report the same TMDb id.
        A row absent from the result is simply unresolved: no mapping exists
        yet, which is a first-class state, never an error.

        ``rows`` is any iterable of ``ProviderContent``; pass an evaluated
        list to reuse it for the view build.
        """
        keys = {(row.provider_id, row.provider_content_id) for row in rows}
        if not keys:
            return {}
        mappings = ContentMapping.objects.select_related("provider", "source").filter(
            provider_id__in={key[0] for key in keys},
            provider_content_id__in={key[1] for key in keys},
        )
        # The two __in filters can cross-match rows of *different* providers
        # that share a native id, so only exact keys are kept.
        return {
            (mapping.provider_id, mapping.provider_content_id): mapping.tmdb_id
            for mapping in mappings
            if (mapping.provider_id, mapping.provider_content_id) in keys
            and mapping_is_trusted(
                mapping.provider.slug, mapping.match_method, mapping.source.slug
            )
        }

    def active_offer_counts(self, rows) -> dict:
        """Currently-available offer counts for many rows in one query.

        Keyed by ``(provider_id, provider_content_id)`` — the value-level link
        ``Offer.provider_content_id`` already stores — so a series row reports
        the active offers discovered under its id at any season/episode
        granularity. Deactivated offers are deliberately not counted: this is
        *presence of active offers*, independent of the catalogue row's own
        ``currently_available``, and it is never written back to either table.
        """
        keys = {(row.provider_id, row.provider_content_id) for row in rows}
        if not keys:
            return {}
        grouped = (
            Offer.objects.filter(
                provider_id__in={key[0] for key in keys},
                provider_content_id__in={key[1] for key in keys},
                currently_available=True,
            )
            .values("provider_id", "provider_content_id")
            .annotate(offer_count=Count("id"))
        )
        return {
            (row["provider_id"], row["provider_content_id"]): row["offer_count"]
            for row in grouped
            if (row["provider_id"], row["provider_content_id"]) in keys
        }

    def upsert(self, *, provider, source, provider_content_id, content_type,
               title="", release_date=None, season_number=None,
               episode_number=None, provider_url="", metadata=None, now=None):
        """Create or refresh one provider-native catalogue row.

        The identity slots (provider, id, content type, season/episode) are
        the key and never change. Observed metadata is last-writer-wins
        *except* for sparse values: an empty incoming title/date/url/metadata
        never erases something a previous refresh recorded, because a source
        omitting a field today is not evidence that the field is gone.
        ``first_seen`` never moves; ``last_seen``, ``source`` and
        ``currently_available`` always follow the latest observation.
        """
        if content_type not in ContentType.values:
            raise ValueError(
                f"Invalid content type: {content_type}. Must be a canonical ContentType."
            )
        if content_type in [ContentType.SEASON, ContentType.EPISODE] and season_number is None:
            raise ValueError("Season and episode content requires a season number")
        if content_type == ContentType.EPISODE and episode_number is None:
            raise ValueError("Episode content requires an episode number")
        if season_number is not None and season_number < 0:
            raise ValueError("Season number cannot be negative")

        now = now or timezone.now()
        # Clamp to our column width: the provider's own id is unbounded (a
        # Pluto TV replay slug measured 125 chars vs a 100-char column) and an
        # over-length value aborts the whole catalogue refresh, not just its own
        # row. Applied before the identity is built so the key stays consistent
        # with what is actually stored.
        provider_content_id = truncate_field(
            provider_content_id, PROVIDER_CONTENT_ID_MAX_LENGTH
        )
        identity = build_provider_content_identity(
            provider.slug, provider_content_id, content_type,
            season_number, episode_number,
        )
        defaults = {
            "provider": provider,
            "source": source,
            "provider_content_id": provider_content_id,
            "content_type": content_type,
            "title": title,
            "release_date": release_date,
            "season_number": season_number,
            "episode_number": episode_number,
            "provider_url": provider_url,
            "metadata": dict(metadata or {}),
            "first_seen": now,
            "last_seen": now,
            "currently_available": True,
            "identity": identity,
        }
        content, created = ProviderContent.objects.get_or_create(
            identity=identity,
            defaults=defaults,
        )
        if created:
            return content, True

        content.source = source
        if title:
            content.title = title
        if release_date is not None:
            content.release_date = release_date
        if provider_url:
            content.provider_url = provider_url
        if metadata:
            content.metadata = {**content.metadata, **metadata}
        content.last_seen = now
        content.currently_available = True
        content.save(update_fields=[
            "source",
            "title",
            "release_date",
            "provider_url",
            "metadata",
            "last_seen",
            "currently_available",
            "updated_at",
        ])
        return content, False


class MappingRepository:
    """Confidence-aware persistence for provider content -> tmdb mappings."""

    def record(self, *, provider, source, provider_content_id, content_type, tmdb_id,
               confidence, match_method, content=None, now=None):
        """Record a mapping, respecting the stored-mapping precedence rules.

        ``content`` is the ``ProviderContent`` row this mapping resolves. It
        is attached (or backfilled onto an existing mapping) but never
        participates in precedence: confidence and match method alone decide
        whether a stored mapping may change, so linking the provider-native
        row can never weaken the manual/high-confidence guarantees.
        """
        now = now or timezone.now()
        # Same column clamp as ProviderContentRepository.upsert — this id is the
        # same provider string on a same-width column, so it needs the same
        # guard, and clamping it before the lookup keeps the query and the
        # stored value in agreement.
        provider_content_id = truncate_field(
            provider_content_id, PROVIDER_CONTENT_ID_MAX_LENGTH
        )
        existing = ContentMapping.objects.filter(
            provider=provider, provider_content_id=provider_content_id
        ).first()
        if existing is None:
            mapping = ContentMapping.objects.create(
                provider=provider,
                source=source,
                provider_content_id=provider_content_id,
                content_type=content_type,
                tmdb_id=tmdb_id,
                confidence=confidence,
                match_method=match_method,
                content=content,
                last_verified=now,
            )
            return mapping, True

        if existing.match_method == MatchMethod.MANUAL and match_method != MatchMethod.MANUAL:
            return self._attach(existing, content), False
        if confidence > existing.confidence:
            existing.source = source
            existing.content_type = content_type
            existing.tmdb_id = tmdb_id
            existing.confidence = confidence
            existing.match_method = match_method
            existing.revision = (existing.revision or 1) + 1
            existing.last_verified = now
            if content is not None:
                existing.content = content
            existing.save()
            return existing, True
        if confidence == existing.confidence and tmdb_id == existing.tmdb_id:
            existing.last_verified = now
            fields = ["last_verified", "updated_at"]
            if content is not None and existing.content_id != content.pk:
                existing.content = content
                fields.insert(0, "content")
            existing.save(update_fields=fields)
            return existing, True
        return self._attach(existing, content), False

    @staticmethod
    def _attach(mapping, content):
        """Link the provider-native row without touching mapping semantics."""
        if content is not None and mapping.content_id != content.pk:
            mapping.content = content
            mapping.save(update_fields=["content", "updated_at"])
        return mapping

    def tmdb_ids_for_provider_content(self, observations):
        """Bulk mapping lookup: ``{(provider_id, pcid): tmdb_id}`` for observed keys.

        An absent key means "no stored mapping yet", which is a first-class
        state (a title can be watched before anything matched it), never an
        error.
        """
        keys = {
            (row.provider_id, row.provider_content_id)
            for row in observations
            if row.provider_content_id
        }
        if not keys:
            return {}
        provider_ids = {provider_id for provider_id, _ in keys}
        pcids = {pcid for _, pcid in keys}
        resolved = {}
        rows = ContentMapping.objects.select_related("provider", "source").filter(
            provider_id__in=provider_ids,
            provider_content_id__in=pcids,
        )
        for mapping in rows:
            key = (mapping.provider_id, mapping.provider_content_id)
            if key in keys:
                resolved[key] = (
                    mapping.tmdb_id
                    if mapping_is_trusted(
                        mapping.provider.slug, mapping.match_method, mapping.source.slug
                    )
                    else None
                )
        return resolved

    def summaries_for_provider_content(self, observations) -> dict:
        """Bulk mapping read: ``{(provider_id, pcid): (tmdb_id, confidence, match_method)}``.

        Same keys and cross-match guards as ``tmdb_ids_for_provider_content``,
        carrying the provenance the unified catalogue's unofficial section
        surfaces (resolution outcome plus how it was matched). An absent key
        means unresolved — first-class state, never an error.
        """
        keys = {
            (row.provider_id, row.provider_content_id)
            for row in observations
            if row.provider_content_id
        }
        if not keys:
            return {}
        rows = ContentMapping.objects.select_related("provider", "source").filter(
            provider_id__in={provider_id for provider_id, _ in keys},
            provider_content_id__in={pcid for _, pcid in keys},
        )
        return {
            (row.provider_id, row.provider_content_id): (
                row.tmdb_id, row.confidence, row.match_method
            )
            for row in rows
            if (row.provider_id, row.provider_content_id) in keys
            and mapping_is_trusted(row.provider.slug, row.match_method, row.source.slug)
        }


class SyncRunRepository:
    def start(self, *, provider, source, mode, country=None, authoritative=False):
        return SyncRun.objects.create(
            provider=provider,
            source=source,
            country=country,
            mode=mode,
            status=SyncStatus.RUNNING,
            authoritative=authoritative,
            started_at=timezone.now(),
        )

    def mark_partial(self, sync_run, *, items_discovered=None, items_processed=None,
                     offers_created=None, offers_updated=None):
        """Record a run that completed some partitions and failed others.

        Deliberately a distinct status from ``SUCCEEDED``: the run wrote real
        rows, but its observation is incomplete, so it must never be read as a
        complete snapshot. Deactivation is refused automatically because
        :meth:`OfferRepository.deactivate_unseen` accepts ``SUCCEEDED`` only.
        """
        sync_run.status = SyncStatus.PARTIAL
        if items_discovered is not None:
            sync_run.items_discovered = items_discovered
        if items_processed is not None:
            sync_run.items_processed = items_processed
        if offers_created is not None:
            sync_run.offers_created = offers_created
        if offers_updated is not None:
            sync_run.offers_updated = offers_updated
        sync_run.completed_at = timezone.now()
        sync_run.save()
        return sync_run

    def mark_succeeded(self, sync_run, *, items_discovered=None, items_processed=None,
                       offers_created=None, offers_updated=None, offers_deactivated=None):
        if items_discovered is not None:
            sync_run.items_discovered = items_discovered
        if items_processed is not None:
            sync_run.items_processed = items_processed
        if offers_created is not None:
            sync_run.offers_created = offers_created
        if offers_updated is not None:
            sync_run.offers_updated = offers_updated
        if offers_deactivated is not None:
            sync_run.offers_deactivated = offers_deactivated
        sync_run.status = SyncStatus.SUCCEEDED
        sync_run.completed_at = timezone.now()
        sync_run.save()
        return sync_run

    def mark_failed(self, sync_run, error):
        sync_run.status = SyncStatus.FAILED
        sync_run.completed_at = timezone.now()
        sync_run.last_error = str(error)
        sync_run.error_count += 1
        sync_run.save()
        return sync_run

    def latest_by_scope(self, scopes):
        """Latest run status per ``(provider_id, source_id, country_id)`` scope.

        Newest run wins (``-created_at, -id`` breaks ties deterministically).
        A scope with no history is absent from the result, so the caller can
        distinguish "never synced" from "synced and failed".
        """
        wanted = {tuple(scope) for scope in scopes}
        if not wanted:
            return {}
        runs = SyncRun.objects.filter(
            provider_id__in={scope[0] for scope in wanted},
            source_id__in={scope[1] for scope in wanted},
        ).order_by("-created_at", "-id").values_list(
            "provider_id", "source_id", "country_id", "status",
        )
        statuses = {}
        for provider_id, source_id, country_id, status in runs:
            key = (provider_id, source_id, country_id)
            if key in wanted and key not in statuses:
                statuses[key] = status
        return statuses


class CursorRepository:
    def get(self, provider, source, country):
        return SyncCursor.objects.filter(
            provider=provider, source=source, country=country
        ).first()

    def set(self, provider, source, country, cursor, now=None):
        """Record forward progress for one walk.

        ``progress_at`` moves **only when the stored checkpoint value actually
        changes**. That is what makes stall detection honest: a replayed batch
        that re-reads the same window (an interrupted run retrying, or a
        deliberately repeated batch) persists the same value and must not look
        like progress — otherwise a walk wedged re-reading one page would keep
        resetting its own liveness clock and never be reported as stalled.

        ``last_successful_sync`` is deliberately **not** touched here. This call
        records that a walk *moved*, not that it *finished*; only
        :meth:`complete` may claim completion, so a partial run can never look
        complete.
        """
        now = now or timezone.now()
        existing = self.get(provider=provider, source=source, country=country)
        advanced = existing is None or existing.cursor != cursor
        obj, _created = SyncCursor.objects.update_or_create(
            provider=provider,
            source=source,
            country=country,
            defaults={
                "cursor": cursor,
                **({"progress_at": now} if advanced else {}),
            },
        )
        return obj

    def complete(self, provider, source, country, now=None):
        """Record that one walk reached its terminal state.

        Called only when the traversal is genuinely exhausted or a full cycle
        completed, and only for a walk whose checkpoint row **survives**
        completion — the cycling and multi-partition cases. It stamps both
        timestamps, because finishing is also the strongest evidence of
        progress.

        A walk whose row is deleted on completion cannot be stamped here (the
        write would be removed with it); per-source completion for those is
        recorded on the run audit row instead. See the ``SyncCursor`` docstring
        and ``AvailabilityRefreshService.refresh_catalogue``.
        """
        now = now or timezone.now()
        obj, _created = SyncCursor.objects.update_or_create(
            provider=provider,
            source=source,
            country=country,
            defaults={"cursor": "", "progress_at": now,
                      "last_successful_sync": now},
        )
        return obj

    def delete(self, provider, source, country):
        """Drop a cursor row entirely.

        Used only to undo a write made by a fetch that then failed: a partition
        that had no cursor before must not leave one behind, or the next run
        would resume from an offset that was never real.
        """
        deleted, _ = SyncCursor.objects.filter(
            provider=provider, source=source, country=country
        ).delete()
        return deleted
