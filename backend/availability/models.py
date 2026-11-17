"""Canonical availability persistence and the vocabulary every phase builds on.

Concept responsibilities:

- **Provider** (``tracker.Provider``) — where the *user* watches: the
  commercial brand (CBC Gem, Prime Video). Owned outside this app so the
  availability domain stays extractable; rows here reference it by FK/slug
  only, never ``tracker.Movie``/``tracker.Series``.
- **Source** — where the *information* came from (CBC collector, JustWatch,
  Streaming Availability). Provenance, never identity: several sources may
  observe the same provider and the same content.
- **ProviderContent** — the provider's own catalogue entry: *what does this
  provider say exists?* Keyed on ``(provider, provider_content_id,
  content_type[, season, episode])``, it persists with or without a TMDb id,
  so unresolved discovery is kept instead of discarded. Catalogue identity
  and metadata only — no ``offer_type``/audio/subtitle/quality here.
- **ContentMapping** — *which TMDb content does this provider content
  correspond to?* Confidence-ranked (manual wins, higher confidence wins,
  fuzzy never overwrites), optionally linked to the ``ProviderContent`` row
  it resolved. A mapping is never required for ``ProviderContent`` to exist.
- **Offer** — *what access does the provider currently expose?* The
  canonical commercial row: TMDb-identified, per country, with its own
  frozen identity and lifecycle (``first_seen``/``last_seen``/
  ``currently_available``/``available_from``/``available_until``) and the
  Phase 3 deactivation rules. It is deliberately **not** rewritten by
  Phase 7: its value-level link to provider-native identity stays
  ``Offer.provider_content_id``.

Reads never come through here — ``AvailabilityQueryService`` serves offers
and ``ProviderCatalogueQueryService`` serves the provider catalogue from the
local database only; writes belong to the refresh service and the
repositories.
"""

from datetime import timedelta

from django.db import models
from django.utils import timezone

from .domain.enums import (
    CandidateResolutionCategory,
    CandidateReviewState,
    ContentType,
    MatchMethod,
    OfferType,
    SourceType,
    SyncMode,
    SyncStatus,
)

# Slug of the Source that carries rows migrated out of the legacy scraper
# store (``tracker.ScrapedContentMapping``). It is provenance, never identity:
# the canonical catalogue reads these rows exactly like any other source's
# observation, and the legacy table itself is no longer a read path.
LEGACY_SCRAPER_SOURCE_SLUG = "legacy_scraper"


def build_offer_identity(
    provider_slug,
    country_code,
    content_type,
    tmdb_id,
    season_number,
    episode_number,
    offer_type,
    provider_content_id,
):
    """Deterministic identity for an offer.

    Slots that are not applicable (season/episode for a movie, an unknown
    provider content id) render as empty strings so the key stays stable
    across sources instead of depending on SQL NULL semantics.
    """
    return "|".join(
        [
            str(provider_slug),
            str(country_code).upper(),
            str(content_type),
            str(tmdb_id),
            "" if season_number is None else str(season_number),
            "" if episode_number is None else str(episode_number),
            str(offer_type),
            str(provider_content_id or ""),
        ]
    )


def build_provider_content_identity(
    provider_slug,
    provider_content_id,
    content_type,
    season_number=None,
    episode_number=None,
):
    """Deterministic identity for provider-native catalogue content.

    The provider's own key — provider + its content id + content type — with
    season/episode slots for providers whose single id spans every season and
    episode (CBC's slug identifies the whole series). Inapplicable slots
    render as empty strings so the key never depends on SQL NULL semantics,
    exactly like ``build_offer_identity``: repeated catalogue refreshes of
    the same entry converge on one row.
    """
    return "|".join(
        [
            str(provider_slug),
            str(provider_content_id),
            str(content_type),
            "" if season_number is None else str(season_number),
            "" if episode_number is None else str(episode_number),
        ]
    )


class Country(models.Model):
    code = models.CharField(max_length=2, unique=True)
    name = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.code = self.code.upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code})"


class Source(models.Model):
    slug = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=20, choices=SourceType.choices, default=SourceType.API)
    enabled = models.BooleanField(default=True)
    configuration = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class AvailabilityExperiment(models.Model):
    """Durable budget state for one explicitly authorized experiment token."""

    authorization_id = models.UUIDField(primary_key=True)
    manifest_digest = models.CharField(max_length=64)
    database_identity = models.CharField(max_length=255)
    maximum_attempts = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


class AvailabilityExperimentAttempt(models.Model):
    """Durable reservation; a missing outcome means the attempt is spent/ambiguous."""

    experiment = models.ForeignKey(
        AvailabilityExperiment, on_delete=models.PROTECT, related_name="attempts"
    )
    ordinal = models.PositiveSmallIntegerField()
    scope_id = models.CharField(max_length=80)
    source_slug = models.CharField(max_length=50)
    operation = models.CharField(max_length=50)
    method = models.CharField(max_length=8)
    destination_host = models.CharField(max_length=255)
    reserved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("experiment", "ordinal"), name="uniq_avail_exp_attempt_ordinal"
            )
        ]


class AvailabilityExperimentAttemptOutcome(models.Model):
    """Append-only response event; no response metadata or URL is persisted."""

    attempt = models.OneToOneField(
        AvailabilityExperimentAttempt, on_delete=models.PROTECT, related_name="outcome"
    )
    outcome = models.CharField(max_length=12, choices=(("success", "Success"), ("failure", "Failure")))
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)


class AvailabilityExperimentScope(models.Model):
    """Completion marker; incomplete reserved scopes require manual reconciliation."""

    experiment = models.ForeignKey(
        AvailabilityExperiment, on_delete=models.PROTECT, related_name="scopes"
    )
    scope_id = models.CharField(max_length=80)
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("experiment", "scope_id"), name="uniq_avail_exp_scope"
            )
        ]


class SyncRun(models.Model):
    provider = models.ForeignKey(
        "tracker.Provider", on_delete=models.PROTECT, related_name="sync_runs"
    )
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="sync_runs")
    country = models.ForeignKey(
        Country, on_delete=models.PROTECT, null=True, blank=True, related_name="sync_runs"
    )
    mode = models.CharField(max_length=20, choices=SyncMode.choices, default=SyncMode.FULL)
    status = models.CharField(
        max_length=12, choices=SyncStatus.choices, default=SyncStatus.PENDING, db_index=True
    )
    authoritative = models.BooleanField(
        default=False,
        help_text="Only a successful authoritative run may deactivate offers it did not see.",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    items_discovered = models.IntegerField(default=0)
    items_processed = models.IntegerField(default=0)
    offers_created = models.IntegerField(default=0)
    offers_updated = models.IntegerField(default=0)
    offers_deactivated = models.IntegerField(default=0)
    error_count = models.IntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.provider.slug} {self.mode} {self.status}"


class ProviderContent(models.Model):
    """A provider's own catalogue entry, independent of any TMDb identity.

    "What does this provider say exists in its catalogue?" — discovered by a
    source, keyed on the *provider's* identity, and persisted whether or not
    a TMDb mapping ever resolves. An unresolved entry therefore lives here
    with no ``ContentMapping`` and no ``Offer`` instead of being discarded;
    a later refresh that resolves it links the mapping and creates the
    offers on this same row (never a second row).

    Observation lifecycle only: ``first_seen`` never moves, ``last_seen``
    advances when a refresh *observes* the entry again, and re-observation
    flips ``currently_available`` back to True.

    - ``last_seen`` means "a successful source refresh saw this entry" — not
      that the provider guarantees it is currently available, and not that an
      offer exists. Nothing else writes it.
    - ``currently_available`` is never changed merely because a
      non-authoritative (or incremental) refresh did not return an entry the
      provider catalogue already had: absence is not evidence. Nothing here
      deactivates on absence — catalogue deactivation stays reserved for
      authoritative, complete snapshots, and no catalogue source declares
      that (CBC Gem is permanently non-authoritative), so a disappearing
      entry keeps its row.

    Reads go through ``ProviderCatalogueQueryService`` (Phase 8), never
    through the source/refresh path; offers keep their own independent
    lifecycle in ``Offer``.

    ``source`` records which source last observed the row (provenance
    follows the last writer, as with ``Offer.source``) and is deliberately
    *not* part of identity: two sources describing the same provider content
    share one row.
    """

    provider = models.ForeignKey(
        "tracker.Provider", on_delete=models.PROTECT, related_name="provider_contents"
    )
    source = models.ForeignKey(
        Source, on_delete=models.PROTECT, null=True, blank=True,
        related_name="provider_contents",
    )

    # Provider-native identity: the provider's own id for its own catalogue
    # entry, never a TMDb id and never a tracker row FK.
    provider_content_id = models.CharField(max_length=100)
    content_type = models.CharField(max_length=10, choices=ContentType.choices, db_index=True)

    title = models.CharField(max_length=255, blank=True, default="")
    # Year/release information as the provider states it (year precision is
    # stored as Jan 1 of that year, matching the source boundary value).
    release_date = models.DateField(null=True, blank=True)
    # Disambiguation for providers whose single content id spans seasons and
    # episodes; NULL for movie/series-level (and single-episode) entries.
    season_number = models.IntegerField(null=True, blank=True)
    episode_number = models.IntegerField(null=True, blank=True)

    provider_url = models.URLField(max_length=500, blank=True, default="")
    # Provider metadata that is not a first-class column: poster, description,
    # discovered external ids (e.g. {"external_ids": {"tmdb": "550"}}).
    metadata = models.JSONField(default=dict, blank=True)

    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)
    currently_available = models.BooleanField(default=True, db_index=True)

    identity = models.CharField(max_length=255, unique=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.identity:
            self.identity = build_provider_content_identity(
                self.provider.slug,
                self.provider_content_id,
                self.content_type,
                self.season_number,
                self.episode_number,
            )
        super().save(*args, **kwargs)

    class Meta:
        indexes = [
            models.Index(
                fields=["provider", "provider_content_id"],
                name="idx_providercontent_provider",
            ),
        ]
        constraints = [
            # Documented intent; the authoritative guard is the identity
            # column, because SQL NULL season/episode slots never collide.
            models.UniqueConstraint(
                fields=[
                    "provider",
                    "provider_content_id",
                    "content_type",
                    "season_number",
                    "episode_number",
                ],
                name="uniq_providercontent_slots",
            ),
            models.UniqueConstraint(fields=["identity"], name="uniq_providercontent_identity"),
        ]

    def __str__(self):
        return f"{self.provider.slug}:{self.provider_content_id} ({self.content_type})"


class Offer(models.Model):
    provider = models.ForeignKey(
        "tracker.Provider", on_delete=models.PROTECT, related_name="availability_offers"
    )
    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="offers")
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="offers")
    sync_run = models.ForeignKey(
        SyncRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="offers"
    )

    # Content identity is tmdb_id + content_type, never a Django row FK, so
    # these tables can be owned by another service later without tracker.
    content_type = models.CharField(max_length=10, choices=ContentType.choices, db_index=True)
    tmdb_id = models.IntegerField(db_index=True)
    season_number = models.IntegerField(null=True, blank=True)
    episode_number = models.IntegerField(null=True, blank=True)

    offer_type = models.CharField(max_length=20, choices=OfferType.choices)
    provider_content_id = models.CharField(max_length=100, blank=True, default="")
    provider_url = models.URLField(max_length=500, blank=True, default="")
    quality = models.CharField(max_length=10, blank=True, default="")

    # NULL means the source exposes no language metadata; [] means the source
    # explicitly reports none. The two are never interchangeable.
    audio_languages = models.JSONField(null=True, blank=True)
    subtitle_languages = models.JSONField(null=True, blank=True)

    # Provider-stated lifecycle, distinct from our observation fields below.
    available_from = models.DateTimeField(null=True, blank=True)
    available_until = models.DateTimeField(null=True, blank=True)

    # Our observation of the offer. first_seen never moves; offers are
    # deactivated, never deleted.
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)
    currently_available = models.BooleanField(default=True, db_index=True)

    source_record_id = models.CharField(max_length=150, blank=True, default="")
    source_version = models.CharField(max_length=50, blank=True, default="")
    observed_at = models.DateTimeField(null=True, blank=True)

    identity = models.CharField(max_length=255, unique=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.identity:
            self.identity = build_offer_identity(
                self.provider.slug,
                self.country.code,
                self.content_type,
                self.tmdb_id,
                self.season_number,
                self.episode_number,
                self.offer_type,
                self.provider_content_id,
            )
        super().save(*args, **kwargs)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["content_type", "tmdb_id"], name="idx_offer_content"),
            models.Index(fields=["provider", "country"], name="idx_offer_provider_country"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "provider",
                    "country",
                    "content_type",
                    "tmdb_id",
                    "season_number",
                    "episode_number",
                    "offer_type",
                    "provider_content_id",
                ],
                name="uniq_offer_identity_slots",
            ),
            models.UniqueConstraint(
                fields=["identity"],
                name="uniq_offer_identity",
            ),
        ]

    def __str__(self):
        return f"{self.content_type}:{self.tmdb_id} @ {self.provider.slug}/{self.country.code}"


class OfferPrice(models.Model):
    """One source's most recent *validated* price for one canonical offer.

    Why a separate table instead of columns on ``Offer``
    ----------------------------------------------------
    ``availability`` migrations after ``0003`` are ordered *after*
    ``tracker.0033_merge_duplicate_providers`` on a fresh database, and that
    data migration still reads ``Offer`` through the **live** model class
    (``Offer.objects.filter(provider=duplicate)``). A new column on ``Offer``
    would therefore be selected before it exists and every fresh install would
    fail. This table is additive, and it deliberately has **no**
    ``ForeignKey`` to ``Offer`` or ``Provider``: ``tracker.0033`` calls
    ``duplicate.delete()`` with the live registry, and a new relation pointing
    at ``Provider`` would make the collector query a table that does not exist
    yet. Referential integrity is maintained by the stable canonical identity
    (``offer_identity`` is exactly ``Offer.identity``) and by the refresh
    lifecycle below, not by a database cascade.

    Why not one price on ``Offer``
    ------------------------------
    ``Offer.identity`` deliberately excludes the source: two sources observing
    the same provider offer are two observations of one canonical offer, and
    the last writer owns ``Offer.source``. Storing a single amount there would
    let whichever source refreshed last overwrite another source's price.
    Keying by ``(offer_identity, source)`` preserves provenance and lets the
    read layer expose every source's price.

    Lifecycle (see ``OfferPriceRepository``)
    ----------------------------------------
    * a valid observation updates this row and marks it current;
    * an observation with no price, or with a malformed/ambiguous price,
      preserves the last known amount but marks the row not current (a missing
      price is never zero and never proof that the offer is free);
    * authoritative deactivation of the offer marks its price rows not current;
    * re-observation sets the row current again on the same row (identity is
      stable), so a removed-then-recreated offer reattaches its price.

    ``amount`` is a ``Decimal``: binary floating-point is never stored, and
    ``currency`` is an explicit ISO-4217 code supplied by the source (never
    inferred from a symbol or a localized display string).
    """

    source = models.ForeignKey(
        Source, on_delete=models.PROTECT, related_name="offer_prices"
    )

    # Exactly Offer.identity. The identity slots are denormalized so a price
    # row stays inspectable without joining back to the offer table.
    offer_identity = models.CharField(max_length=255, db_index=True)
    provider_slug = models.CharField(max_length=100, db_index=True)
    country_code = models.CharField(max_length=2)
    content_type = models.CharField(max_length=10, choices=ContentType.choices)
    tmdb_id = models.IntegerField(db_index=True)
    season_number = models.IntegerField(null=True, blank=True)
    episode_number = models.IntegerField(null=True, blank=True)
    offer_type = models.CharField(max_length=20, choices=OfferType.choices)
    provider_content_id = models.CharField(max_length=100, blank=True, default="")

    # NULL amount means "no validated amount was ever observed", never zero.
    amount = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default="")
    source_offer_id = models.CharField(max_length=150, blank=True, default="")

    # False means "last known value, no longer confirmed by a fresh
    # observation": it is kept for audit and is never displayed as current.
    is_current = models.BooleanField(default=True, db_index=True)
    # Why the latest observation did not yield a usable price (blank when it
    # did). This is the inspectable counterpart of is_current=False.
    validation_error = models.CharField(max_length=100, blank=True, default="")

    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)
    observed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["offer_identity", "source"],
                name="uniq_offer_price_identity_source",
            ),
        ]
        indexes = [
            models.Index(
                fields=["content_type", "tmdb_id", "country_code"],
                name="idx_offer_price_content",
            ),
        ]

    def __str__(self):
        amount = "—" if self.amount is None else f"{self.amount} {self.currency}"
        return f"{self.offer_identity} @ {self.source.slug}: {amount}"


class OfferEvidence(models.Model):
    """One source's current observation state for one canonical offer.

    Why this exists
    ---------------
    ``Offer.identity`` deliberately excludes the source: several sources
    describing the same provider offer share **one** canonical ``Offer`` row,
    and the last writer owns ``Offer.source``, ``Offer.currently_available`` and
    the other descriptive fields. That made canonical availability
    last-writer-wins: an authoritative removal from source A flipped the shared
    row to unavailable even though source B still reported the same offer as
    available, silently hiding an offer the user could watch.

    This table records each source's *own* statement about whether it currently
    observes the offer, keyed ``(offer_identity, source)`` - the same stable key
    ``OfferPrice`` uses. Effective canonical availability is then derived from
    *all* current evidence instead of the last writer:

    * any present evidence (``is_present=True``) keeps the offer available;
    * authoritative absence (``is_present=False``) is recorded, but only decides
      the outcome for an offer no source still has present evidence for.

    Lifecycle
    ---------
    * a source observing an offer upserts its row with ``is_present=True``;
    * an authoritative complete snapshot records ``is_present=False`` for the
      offers it stops seeing - the run source's *own* evidence only, never
      another source's;
    * a non-authoritative (or incremental) refresh never records absence:
      absence is not evidence;
    * re-observation flips ``is_present`` back to True on the same row, so a
      removed-then-reappearing offer reattaches without fabricating evidence
      for any other source.

    Migration safety
    ----------------
    Like ``OfferPrice``, this table deliberately has **no** ``ForeignKey`` to
    ``Offer`` or ``Provider``. ``tracker.0033_merge_duplicate_providers`` runs
    before this app's post-``0003`` migrations on a fresh database and deletes
    providers through the live model registry (``Offer``/``ProviderContent``/
    ``SyncRun`` are pulled in by the collector). A relation pointing at
    ``Provider`` - or a ``post_delete`` receiver that queries this table - would
    make that migration touch a table that does not exist yet. The row is joined
    to the canonical offer by the stable ``offer_identity`` string, and
    integrity is maintained by the refresh lifecycle plus
    ``OfferEvidenceRepository.purge_orphans``.

    ``authoritative`` records whether the observation that last set the row came
    from an authoritative, complete snapshot (the only kind allowed to record
    absence). It is provenance, not part of the availability key.
    """

    source = models.ForeignKey(
        Source, on_delete=models.PROTECT, related_name="offer_evidence"
    )

    # Exactly Offer.identity. The identity slots are denormalized so an evidence
    # row stays inspectable without joining back to the offer table.
    offer_identity = models.CharField(max_length=255, db_index=True)
    provider_slug = models.CharField(max_length=100, db_index=True)
    country_code = models.CharField(max_length=2)
    content_type = models.CharField(max_length=10, choices=ContentType.choices)
    tmdb_id = models.IntegerField(db_index=True)
    season_number = models.IntegerField(null=True, blank=True)
    episode_number = models.IntegerField(null=True, blank=True)
    offer_type = models.CharField(max_length=20, choices=OfferType.choices)
    provider_content_id = models.CharField(max_length=100, blank=True, default="")

    #: True means this source currently observes the offer; False means its own
    #: authoritative complete snapshot no longer listed it. Never set False by a
    #: non-authoritative refresh.
    is_present = models.BooleanField(default=True, db_index=True)
    #: Whether the observation that last set this row was authoritative.
    authoritative = models.BooleanField(default=False)

    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)
    observed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["offer_identity", "source"],
                name="uniq_offer_evidence_identity_source",
            ),
        ]
        indexes = [
            models.Index(
                fields=["content_type", "tmdb_id", "country_code"],
                name="idx_offer_evidence_content",
            ),
        ]

    def __str__(self):
        state = "present" if self.is_present else "absent"
        return f"{self.offer_identity} @ {self.source.slug}: {state}"


class ContentMapping(models.Model):
    """Which canonical/TMDb content a provider's catalogue entry corresponds to.

    The mapping is about provider-native content: its key stays
    ``(provider, provider_content_id)`` — the same provider-native identity
    ``ProviderContent`` is keyed on — and ``content`` optionally links the
    exact ``ProviderContent`` row it resolved. The link is nullable because
    a manual mapping may be recorded before the catalogue entry is ever
    discovered (and ``on_delete=SET_NULL`` keeps that manual work if a row
    ever disappears); discovery links it on a later refresh.

    Confidence rules are unchanged from Phase 5: manual mappings win,
    higher-confidence matches overwrite lower ones, equal confidence with a
    different target is ignored, and fuzzy matches never overwrite a stored
    mapping.
    """

    provider = models.ForeignKey(
        "tracker.Provider", on_delete=models.PROTECT, related_name="availability_mappings"
    )
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="mappings")
    provider_content_id = models.CharField(max_length=100)
    content_type = models.CharField(max_length=10, choices=ContentType.choices)
    tmdb_id = models.IntegerField()
    content = models.ForeignKey(
        ProviderContent, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="mappings",
    )
    confidence = models.FloatField(default=1.0)
    match_method = models.CharField(max_length=25, choices=MatchMethod.choices)
    match_evidence = models.JSONField(default=dict, blank=True)
    revision = models.PositiveIntegerField(default=1)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_verified = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_content_id"],
                name="uniq_contentmapping_provider_content",
            )
        ]

    def __str__(self):
        return f"{self.provider.slug}:{self.provider_content_id} -> tmdb {self.tmdb_id}"


class SyncCursor(models.Model):
    """Where a catalogue walk stopped, and how healthy it is.

    Two timestamps, deliberately distinct because they answer different
    questions and conflating them is what made the old single field useless:

    - ``last_successful_sync`` — the last time this walk reached its **terminal
      state**: every part of the catalogue was traversed, or (for a cycling
      source such as Tubi) a full cycle completed. This is the *completion*
      timestamp, and it is the one a freshness/staleness policy should read.
    - ``progress_at`` — the last time the walk **advanced** (a batch committed
      and its checkpoint moved forward). This is the *liveness* timestamp.

    The distinction is load-bearing for stall detection. A long walk makes
    forward progress on every run for hours while ``last_successful_sync``
    correctly stays old; and a walk that has stopped advancing keeps
    ``last_successful_sync`` at its last real completion. Reading completion as
    liveness would call a healthy in-progress walk stalled, and reading
    liveness as completion would call a stalled walk fresh.

    ``progress_at`` is only stamped when the checkpoint value actually *moves*:
    a replayed batch that re-reads the same window must not look like progress,
    or a walk stuck re-reading one page would never be detected.
    """

    provider = models.ForeignKey(
        "tracker.Provider", on_delete=models.PROTECT, related_name="sync_cursors"
    )
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="sync_cursors")
    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="sync_cursors")
    cursor = models.TextField(blank=True, default="")
    #: Last completed traversal (see the class docstring). Nullable: a walk that
    #: has never completed has no completion time — and ``null`` is not ``0``,
    #: so "never completed" stays distinguishable from "completed long ago".
    last_successful_sync = models.DateTimeField(null=True, blank=True)
    #: Last forward progress. Nullable for the same reason.
    progress_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "source", "country"],
                name="uniq_synccursor_provider_source_country",
            )
        ]

    def __str__(self):
        return f"{self.provider.slug}/{self.source.slug}/{self.country.code}"

    @property
    def is_stalled(self) -> bool:
        """Whether this walk has stopped advancing for longer than the threshold.

        Only meaningful for a walk that is *supposed* to be progressing: a
        checkpoint row exists precisely because there is more to walk, so a row
        whose ``progress_at`` is older than :data:`STALL_AFTER` is a walk that
        has stopped moving. A row with no ``progress_at`` at all has never
        advanced and is stalled by definition.
        """
        from .freshness import STALL_AFTER

        reference = self.progress_at
        if reference is None:
            return True
        return (timezone.now() - reference) > STALL_AFTER


def tmdb_default_cache_expiry():
    """Default retained for direct model creation; gateway chooses pos/neg TTL."""
    return timezone.now() + timedelta(days=30)


class TmdbSearchCache(models.Model):
    """Positive and negative cache for TMDb search queries.

    Stores the raw search result list (or empty list if no results),
    avoiding redundant external network requests.
    """

    query = models.CharField(max_length=255, db_index=True)
    year = models.IntegerField(null=True, blank=True, db_index=True)
    # A non-null identity component prevents SQL's nullable-unique semantics
    # from admitting duplicate cache records for a query without a year.
    year_fingerprint = models.CharField(max_length=16, default="year:none", editable=False)
    content_type = models.CharField(max_length=10, choices=ContentType.choices, db_index=True)
    locale = models.CharField(max_length=32, default="en-US")
    endpoint_version = models.CharField(max_length=16, default="v3")

    results = models.JSONField(default=list, blank=True)
    is_negative = models.BooleanField(default=False, db_index=True)
    expires_at = models.DateTimeField(default=tmdb_default_cache_expiry, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["query", "year_fingerprint", "content_type", "locale", "endpoint_version"],
                name="uniq_tmdbsearchcache_identity",
            ),
        ]
        indexes = [
            models.Index(fields=["query", "content_type"], name="idx_tmdbsearchcache_query_type"),
            models.Index(fields=["expires_at"], name="idx_tmdbsearchcache_expiry"),
        ]

    def save(self, *args, **kwargs):
        self.year_fingerprint = f"year:{self.year}" if self.year is not None else "year:none"
        super().save(*args, **kwargs)

    def __str__(self):
        kind = "negative" if self.is_negative else f"{len(self.results)} results"
        return f"TMDbSearchCache({self.content_type}: {self.query!r} ({self.year}) -> {kind})"


class ProviderContentCandidate(models.Model):
    """Durable candidate discovery record for an unmapped ProviderContent entry.

    Stores prospective TMDb candidates along with scoring evidence, review
    state, and resolution categorization without mutating canonical Movie,
    Offer, or ContentMapping records.
    """

    provider_content = models.ForeignKey(
        ProviderContent,
        on_delete=models.CASCADE,
        related_name="tmdb_candidates",
    )
    tmdb_id = models.IntegerField(db_index=True)
    title = models.CharField(max_length=255)
    year = models.IntegerField(null=True, blank=True)
    content_type = models.CharField(max_length=10, choices=ContentType.choices)

    confidence_score = models.FloatField(default=0.0)
    scoring_evidence = models.JSONField(default=dict, blank=True)

    review_state = models.CharField(
        max_length=20,
        choices=CandidateReviewState.choices,
        default=CandidateReviewState.PENDING,
        db_index=True,
    )
    resolution_category = models.CharField(
        max_length=30,
        choices=CandidateResolutionCategory.choices,
        default=CandidateResolutionCategory.AMBIGUOUS,
        db_index=True,
    )
    rejection_reason = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider_content", "tmdb_id"],
                name="uniq_providercontentcandidate_content_tmdb",
            ),
        ]
        indexes = [
            models.Index(
                fields=["provider_content", "confidence_score"],
                name="idx_cand_content_score",
            ),
            models.Index(
                fields=["review_state", "resolution_category"],
                name="idx_cand_review_category",
            ),
        ]

    def __str__(self):
        return (
            f"Candidate({self.provider_content_id}:{self.tmdb_id} - "
            f"{self.title} ({self.year}) [{self.review_state}])"
        )


class TmdbDetailsCache(models.Model):
    """Positive and negative cache for TMDb details queries.

    Stores the raw details result dict for movies or TV shows,
    avoiding redundant external network requests during candidate review.
    """

    tmdb_id = models.IntegerField(db_index=True)
    content_type = models.CharField(max_length=10, choices=ContentType.choices, db_index=True)
    locale = models.CharField(max_length=32, default="en-US")
    endpoint_version = models.CharField(max_length=16, default="v3")

    details = models.JSONField(default=dict, blank=True)
    is_negative = models.BooleanField(default=False, db_index=True)
    expires_at = models.DateTimeField(default=tmdb_default_cache_expiry, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tmdb_id", "content_type", "locale", "endpoint_version"],
                name="uniq_tmdbdetailscache_identity",
            ),
        ]
        indexes = [
            models.Index(fields=["expires_at"], name="idx_tmdbdetailscache_expiry"),
        ]

    def __str__(self):
        kind = "negative" if self.is_negative else f"details({self.details.get('title') or self.details.get('name')})"
        return f"TmdbDetailsCache({self.content_type}:{self.tmdb_id} -> {kind})"


class CandidateReviewAudit(models.Model):
    """Append-only audit record for human reviewer decisions on TMDb candidates."""

    ACTION_CHOICES = [
        ("accept", "Accept"),
        ("reject", "Reject"),
    ]

    provider_content = models.ForeignKey(
        ProviderContent,
        on_delete=models.CASCADE,
        related_name="review_audits",
    )
    candidate_tmdb_id = models.IntegerField(db_index=True)
    action = models.CharField(max_length=10, choices=ACTION_CHOICES, db_index=True)
    reviewer_id = models.CharField(max_length=100, default="local_operator")
    reason = models.TextField()

    previous_review_state = models.CharField(max_length=20, choices=CandidateReviewState.choices)
    resulting_review_state = models.CharField(max_length=20, choices=CandidateReviewState.choices)

    expected_revision = models.PositiveIntegerField(null=True, blank=True)
    resulting_revision = models.PositiveIntegerField(null=True, blank=True)

    verified_tmdb_id = models.IntegerField(null=True, blank=True)
    verified_content_type = models.CharField(
        max_length=10, choices=ContentType.choices, null=True, blank=True
    )
    verification_provenance = models.CharField(max_length=50, blank=True, default="")
    correlation_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["provider_content", "candidate_tmdb_id", "-created_at"],
                name="idx_cand_audit_lookup",
            ),
        ]

    def __str__(self):
        return f"CandidateReviewAudit({self.provider_content_id}:{self.candidate_tmdb_id} - {self.action} by {self.reviewer_id})"
