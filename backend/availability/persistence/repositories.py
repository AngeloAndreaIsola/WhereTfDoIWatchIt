from django.db.models import Count, Q
from django.utils import timezone

from tracker.models import ProviderCategory

from ..domain.enums import ContentType, MatchMethod, SyncStatus
from ..models import (
    LEGACY_SCRAPER_SOURCE_SLUG,
    Country,
    ContentMapping,
    Offer,
    ProviderContent,
    SyncCursor,
    SyncRun,
    build_offer_identity,
    build_provider_content_identity,
)
from ..services.normalize import truncate_field

#: Column width for a provider-native content id, read from the model itself so
#: a future migration widening the column is picked up automatically instead of
#: silently leaving this clamp behind at the old width.
PROVIDER_CONTENT_ID_MAX_LENGTH = ProviderContent._meta.get_field(
    "provider_content_id"
).max_length


class InactiveSyncError(Exception):
    """Raised when a sync run is not allowed to deactivate unseen offers."""


class OfferRepository:
    """Read/write persistence for canonical offers. No fetching, no querying policy."""

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
        if created:
            return offer, True

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
        return offer, False

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

    def deactivate_unseen(self, sync_run, seen_identities):
        """Mark previously-active offers absent from an authoritative full snapshot.

        Rows are never deleted and last_seen never moves: a deactivated offer
        is still "last seen" when we last observed it.
        """
        if sync_run.status != SyncStatus.SUCCEEDED or not sync_run.authoritative:
            raise InactiveSyncError(
                "Only a successful authoritative sync may deactivate unseen offers "
                f"(run {sync_run.pk}: status={sync_run.status}, "
                f"authoritative={sync_run.authoritative})."
            )
        qs = Offer.objects.filter(
            provider=sync_run.provider,
            currently_available=True,
            content_type__in=[ContentType.MOVIE, ContentType.SERIES],
        )
        if sync_run.country_id is not None:
            qs = qs.filter(country=sync_run.country)
        if seen_identities:
            qs = qs.exclude(identity__in=seen_identities)
        return qs.update(
            currently_available=False,
            sync_run=sync_run,
            updated_at=timezone.now(),
        )


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
        mappings = ContentMapping.objects.filter(
            provider_id__in={key[0] for key in keys},
            provider_content_id__in={key[1] for key in keys},
        ).values_list("provider_id", "provider_content_id", "tmdb_id")
        # The two __in filters can cross-match rows of *different* providers
        # that share a native id, so only exact keys are kept.
        return {
            (provider_id, provider_content_id): tmdb_id
            for provider_id, provider_content_id, tmdb_id in mappings
            if (provider_id, provider_content_id) in keys
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
        rows = ContentMapping.objects.filter(
            provider_id__in=provider_ids,
            provider_content_id__in=pcids,
        ).values_list("provider_id", "provider_content_id", "tmdb_id")
        for provider_id, pcid, tmdb_id in rows:
            key = (provider_id, pcid)
            if key in keys:
                resolved[key] = tmdb_id
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
        rows = ContentMapping.objects.filter(
            provider_id__in={provider_id for provider_id, _ in keys},
            provider_content_id__in={pcid for _, pcid in keys},
        ).values_list(
            "provider_id", "provider_content_id",
            "tmdb_id", "confidence", "match_method",
        )
        return {
            (provider_id, provider_content_id): (
                tmdb_id, confidence, match_method
            )
            for provider_id, provider_content_id, tmdb_id, confidence, match_method
            in rows
            if (provider_id, provider_content_id) in keys
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
        now = now or timezone.now()
        obj, _created = SyncCursor.objects.update_or_create(
            provider=provider,
            source=source,
            country=country,
            defaults={"cursor": cursor, "last_successful_sync": now},
        )
        return obj
