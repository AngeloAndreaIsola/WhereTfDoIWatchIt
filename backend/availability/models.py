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

from django.db import models
from django.utils import timezone

from .domain.enums import (
    ContentType,
    MatchMethod,
    OfferType,
    SourceType,
    SyncMode,
    SyncStatus,
)


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
    provider = models.ForeignKey(
        "tracker.Provider", on_delete=models.PROTECT, related_name="sync_cursors"
    )
    source = models.ForeignKey(Source, on_delete=models.PROTECT, related_name="sync_cursors")
    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="sync_cursors")
    cursor = models.TextField(blank=True, default="")
    last_successful_sync = models.DateTimeField(null=True, blank=True)
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
