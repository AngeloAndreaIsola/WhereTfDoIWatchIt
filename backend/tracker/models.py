import os
from django.db import models
from django.db.models.functions import Concat, Lower, StrIndex, Substr, Trim
from django.utils import timezone

if os.environ.get("USE_SQLITE") == "1":
    class ArrayField(models.JSONField):
        def __init__(self, base_field=None, **kwargs):
            self.base_field = base_field
            kwargs.pop("size", None)
            super().__init__(**kwargs)

        def deconstruct(self):
            name, path, args, kwargs = super().deconstruct()
            if self.base_field:
                args = [self.base_field] + list(args)
            return name, path, args, kwargs
else:
    from django.contrib.postgres.fields import ArrayField


class Movie(models.Model):
    CONTENT_TYPES = [
        ("movie", "Movie"),
        ("series", "Series"),
    ]

    tmdb_id = models.IntegerField(unique=True, db_index=True)
    title = models.CharField(max_length=255)
    year = models.IntegerField(null=True, blank=True)
    content_type = models.CharField(
        max_length=10,
        choices=CONTENT_TYPES,
        default="movie",
        db_index=True,
    )
    poster_path = models.CharField(max_length=255, blank=True, default="")
    overview = models.TextField(blank=True, default="")
    genres = models.JSONField(default=list, blank=True)
    genre_ids = models.JSONField(default=list, blank=True)
    cast = models.JSONField(default=list, blank=True)
    director = models.JSONField(default=list, blank=True, help_text="List of director names (movie) or creators (series).")
    seasons_data = models.JSONField(default=list, blank=True)
    runtime_minutes = models.IntegerField(null=True, blank=True)
    tmdb_rating = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f"{self.title} ({self.year})" if self.year else self.title


class Region(models.Model):
    provider = models.CharField(max_length=50)
    country = models.CharField(max_length=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "country"], name="uniq_region_provider_country")
        ]

    def __str__(self):
        return f"{self.provider} {self.country}"


class Availability(models.Model):
    OFFER_TYPES = [
        ("subscription", "Subscription"),
        ("free", "Free"),
        ("rent", "Rent"),
        ("buy", "Buy"),
    ]
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="availabilities")
    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="availabilities")
    last_checked_at = models.DateTimeField(null=True, blank=True)
    is_available = models.BooleanField(default=False)
    offer_type = models.CharField(max_length=20, choices=OFFER_TYPES)
    quality = models.CharField(max_length=10, blank=True)
    price_info = models.CharField(max_length=50, blank=True, default="")
    url = models.URLField(max_length=500, blank=True, default="")
    seasons_available = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["movie", "region"], name="uniq_availability_movie_region")
        ]

    def __str__(self):
        return f"{self.movie} @ {self.region} ({self.get_offer_type_display()})"


class LanguageInfo(models.Model):
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="language_infos")
    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="language_infos")
    # Series rows may be per-season (season 1..N); NULL means show-level —
    # the only shape movies use and the fallback when a season has no row.
    season = models.IntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            # One row per (movie, region, season). Postgres treats NULL seasons
            # as distinct, so show-level uniqueness is enforced separately.
            models.UniqueConstraint(
                fields=["movie", "region", "season"],
                name="uniq_languageinfo_movie_region_season",
            ),
            models.UniqueConstraint(
                fields=["movie", "region"],
                condition=models.Q(season__isnull=True),
                name="uniq_languageinfo_show_level",
            ),
        ]

    def __str__(self):
        suffix = f" S{self.season}" if self.season is not None else ""
        return f"{self.movie} @ {self.region}{suffix}"


class LanguageEntry(models.Model):
    KINDS = [("audio", "Audio"), ("subtitle", "Subtitle")]
    language_info = models.ForeignKey(LanguageInfo, on_delete=models.CASCADE, related_name="entries")
    kind = models.CharField(max_length=10, choices=KINDS)
    language_code = models.CharField(max_length=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["language_info", "kind", "language_code"],
                name="uniq_languageentry_info_kind_code",
            )
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.language_code}"


class ProviderCategory(models.TextChoices):
    OFFICIAL = "official", "Official / Major Provider"
    BVOD = "bvod", "Broadcaster Video on Demand"
    AVOD = "avod", "Ad-Supported Video on Demand"
    TVOD = "tvod", "Transactional Video on Demand"
    FAST = "fast", "Free Ad-Supported Streaming TV"
    SCRAPER = "scraper", "Scraper / Small Source"


def provider_domain_root_expr():
    """SQL expression for a provider's domain root.

    The root is the domain up to its first path separator ("bbc.co.uk" for
    "bbc.co.uk/iplayer"), lower-cased and trimmed, so two providers pointing
    at the same site collide under ``uniq_provider_domain_root`` regardless of
    the path they carry. Concatenating "/" before locating it keeps the
    "no path present" case portable (the whole domain becomes the root)
    without a CASE expression.
    """
    position = StrIndex(Concat(models.F("domain"), models.Value("/")), models.Value("/"))
    return Trim(Lower(Substr(models.F("domain"), 1, position - 1)))


def provider_domain_root(domain) -> str:
    """Python twin of :func:`provider_domain_root_expr` (keep them in sync)."""
    return (domain or "").split("/", 1)[0].strip().lower()


class Provider(models.Model):
    slug = models.SlugField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=100)
    category = models.CharField(
        max_length=20,
        choices=ProviderCategory.choices,
        default=ProviderCategory.OFFICIAL,
    )
    is_major = models.BooleanField(default=False)
    is_enabled = models.BooleanField(default=True)
    icon_name = models.CharField(max_length=50, blank=True, default="")
    domain = models.CharField(max_length=100, blank=True, default="")
    country_code = models.CharField(max_length=2, blank=True, default="", help_text="Primary country for this provider, if region-specific.")
    last_scraped_at = models.DateTimeField(null=True, blank=True)
    config = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.name} ({self.slug})"

    @property
    def is_addon(self) -> bool:
        """True when this service is an add-on channel with a base platform.

        The relationship is stored in ``config["addon_parent"]`` (a canonical
        slug) rather than a self-FK so it needs no ``Provider`` column: the
        relationship is one-directional on purpose — holding the base never
        grants the child, and holding the child never grants the base. The
        access resolver requires both entitlements.
        """
        return bool(self.addon_parent_slug)

    @property
    def addon_parent_slug(self) -> str:
        """Canonical slug of the base platform this service requires, or ``""``."""
        if isinstance(self.config, dict):
            return str(self.config.get("addon_parent") or "").strip().lower()
        return ""

    @property
    def source_indicator(self) -> str:
        """Return a short label for the source type badge in the UI."""
        if isinstance(self.config, dict) and self.config.get("business_model"):
            return str(self.config["business_model"]).upper()
        if self.category == ProviderCategory.OFFICIAL:
            return "SVOD" if self.is_major else "Official"
        if self.category == ProviderCategory.SCRAPER:
            return "Scraper"
        return self.category.upper()

    class Meta:
        constraints = [
            # One provider row per site: the domain root (host up to the first
            # path separator) must be unique across all providers with a domain.
            # This is what keeps seeded lineages and URL-driven provider creation
            # from producing duplicate cards (RaiPlay, BBC iPlayer, NHK).
            models.UniqueConstraint(
                provider_domain_root_expr(),
                condition=~models.Q(domain=""),
                name="uniq_provider_domain_root",
            ),
        ]


class ExternalProviderMapping(models.Model):
    """How one integration names one canonical provider — **per country**.

    Country-awareness is not cosmetic: aggregator integrations identify a
    provider differently in each market. JustWatch, for example, exposes
    Amazon Prime Video as ``amazonprimevideo`` (``prv``) in NL, IT, CA and AU
    but as ``amazonprime`` (``amp``) in US, GB and DE, so a single global
    external id per provider is simply *wrong* for it.

    ``country`` follows the existing tracker convention for a market — the
    same 2-letter uppercase code ``Provider.country_code`` and
    ``ScrapedContentMapping.country_code`` already use — rather than a new
    country model. It is deliberately a plain field and not a foreign key:
    ``availability.Country`` FKs *into* this app, so pointing back at it would
    make ``tracker`` depend on ``availability``.

    Empty ``country`` ("") means "unscoped/global", which is how every
    pre-existing integration row is interpreted. That keeps backward
    compatibility: an integration whose ids are country-independent (the
    Streaming Availability mapping) keeps working unchanged, and lookups fall
    back from a country-specific row to an unscoped one.
    """

    provider = models.ForeignKey(
        Provider, on_delete=models.CASCADE, related_name="external_mappings"
    )
    integration_name = models.CharField(max_length=50)  # e.g. "streaming_availability", "justwatch"
    external_id = models.CharField(max_length=100)
    country = models.CharField(
        max_length=2,
        blank=True,
        default="",
        help_text='ISO 3166-1 alpha-2 market this external id belongs to. '
                  'Empty = global/unscoped.',
    )

    class Meta:
        constraints = [
            # One external id per (provider, integration, market). The country
            # slot is what makes the row country-aware: an unscoped row
            # ("") and a per-country row are distinct entries for the same
            # provider+integration, which is exactly what an aggregator that
            # renames providers per market needs.
            models.UniqueConstraint(
                fields=["provider", "integration_name", "country"],
                name="uniq_external_provider_mapping",
            ),
        ]
        indexes = [
            # Reverse resolution: "which provider does integration X call
            # provider Y in market Z?" — the lookup the availability refresh
            # service performs for every observed offer, so it is indexed in
            # the order it is queried.
            models.Index(
                fields=["integration_name", "country", "external_id"],
                name="idx_extprov_integration_lookup",
            ),
        ]

    def save(self, *args, **kwargs):
        self.country = str(self.country or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        scope = self.country or "*"
        return f"{self.provider.slug} -> {self.integration_name}:{scope}:{self.external_id}"


class PackageResolutionStatus(models.TextChoices):
    """How a JustWatch package relates to a canonical provider."""

    MAPPED = "mapped", "Mapped to a canonical provider"
    ALIAS = "alias", "Alias of another provider"
    ADDON = "addon", "Add-on channel of a base platform"
    UNRESOLVED = "unresolved", "Unresolved"


class PackageReasonCode(models.TextChoices):
    """Why a package is unresolved (or how it resolved).

    A closed vocabulary on purpose: an unresolved package must be
    *inspectable*, not silently dropped, and a free-text reason would let a
    new class of package enter the catalogue without anyone deciding what it
    is. ``NEEDS_MANUAL_REVIEW`` is the honest default — it says a human must
    look, never that the package is unimportant.
    """

    KNOWN_ALIAS = "known_alias", "Alias of an existing provider"
    DUPLICATE_PROVIDER = "duplicate_provider", "Duplicate representation of a mapped provider"
    ADDON_PARENT_UNRESOLVED = "addon_parent_unresolved", "Add-on channel with no confirmed parent"
    CONTENT_BRAND_NOT_PROVIDER = "content_brand_not_provider", "Content brand, not an independent provider"
    UNSUPPORTED_OFFER_TYPE = "unsupported_offer_type", "Offer type outside the tracked domains (e.g. cinema)"
    MISSING_IDENTITY = "missing_identity", "No usable technical identity in the response"
    AMBIGUOUS_IDENTITY = "ambiguous_identity", "Identity collides with an existing provider"
    NEEDS_MANUAL_REVIEW = "needs_manual_review", "Requires a manual identity decision"


class ProviderCataloguePackage(models.Model):
    """One package JustWatch reports for one country — resolved or not.

    The persistent counterpart of the sync report: ``sync_providers`` upserts a
    row per ``(country, technicalName)`` so an unmapped package is kept and
    inspectable with an explicit reason instead of vanishing into a log line.
    It is a *catalogue observation*, never an availability signal: a row here
    never implies a title is watchable, and reads must not treat it as one.
    """

    country = models.CharField(max_length=2, db_index=True)
    package_id = models.IntegerField(null=True, blank=True)
    external_id = models.CharField(max_length=120, blank=True, default="")
    technical_name = models.CharField(max_length=120)
    clear_name = models.CharField(max_length=200, blank=True, default="")
    short_name = models.CharField(max_length=20, blank=True, default="")
    slug = models.CharField(max_length=120, blank=True, default="")
    monetization_types = models.JSONField(default=list, blank=True)

    #: Canonical slug of the provider this package resolved to, or ``""``.
    #: Deliberately a slug, not an FK: migration ``0033`` replays with the
    #: *live* model registry, so any new ``ForeignKey(Provider)`` would make
    #: its cascade delete reference a table that does not exist yet on a fresh
    #: database. A slug keeps this observation table self-contained.
    resolved_provider_slug = models.CharField(max_length=50, blank=True, default="", db_index=True)
    resolution_status = models.CharField(
        max_length=16,
        choices=PackageResolutionStatus.choices,
        default=PackageResolutionStatus.UNRESOLVED,
        db_index=True,
    )
    reason_code = models.CharField(
        max_length=32,
        choices=PackageReasonCode.choices,
        blank=True,
        default="",
        db_index=True,
    )
    reason = models.TextField(blank=True, default="")

    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["country", "technical_name"],
                name="uniq_catalogue_package_country_technical",
            )
        ]
        indexes = [
            models.Index(
                fields=["resolution_status", "reason_code"],
                name="idx_catpackage_status_reason",
            )
        ]

    def __str__(self):
        return f"{self.country}:{self.technical_name} ({self.resolution_status})"


class ScrapedContentMapping(models.Model):
    provider = models.ForeignKey(
        Provider, on_delete=models.CASCADE, related_name="scraped_mappings"
    )
    scraped_title = models.CharField(max_length=255)
    scraped_year = models.IntegerField(null=True, blank=True)
    scraped_url = models.URLField(max_length=500, blank=True, default="")
    country_code = models.CharField(max_length=2, blank=True, default="", help_text="Country this scrape result applies to.")
    movie = models.ForeignKey(
        Movie,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scraped_mappings",
    )
    is_confirmed = models.BooleanField(default=False)
    confidence_score = models.FloatField(default=0.0)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_scraped_at = models.DateTimeField(null=True, blank=True, help_text="When the source page was last scraped; null = never or unknown.")
    is_available = models.BooleanField(default=True, help_text="Whether the title is currently available on this source.")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "scraped_title", "scraped_year"],
                name="uniq_scraped_content_mapping",
            )
        ]

    def __str__(self):
        status_str = "Confirmed" if self.is_confirmed else "Unconfirmed"
        return f"{self.provider.slug}: '{self.scraped_title}' -> {self.movie} ({status_str})"


class Subscription(models.Model):
    user_id = models.IntegerField(default=1)
    provider = models.ForeignKey(
        Provider,
        on_delete=models.CASCADE,
        related_name="subscriptions",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=50, blank=True, default="")
    access_mode = models.CharField(
        max_length=16,
        default="included",
        choices=[
            ("included", "Included subscription"),
            ("addon", "Add-on subscription"),
            ("free", "Free access"),
            ("rent_buy", "Account for rental / purchase"),
            ("none", "Not owned / not available"),
        ],
        help_text=(
            "How the user holds this service. ``included``/``addon``/``free`` "
            "grant watchability; ``rent_buy`` only enables rent/buy offers; "
            "``none`` means explicitly not owned."
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user_id", "provider"],
                name="uniq_user_provider_subscription",
            )
        ]

    def __str__(self):
        if self.provider:
            return f"User {self.user_id} -> {self.provider.name}"
        return f"User {self.user_id} -> {self.name}"


class Preference(models.Model):
    user_id = models.IntegerField(default=1)
    audio_languages = ArrayField(models.CharField(max_length=2), default=list, blank=True)
    subtitle_languages = ArrayField(models.CharField(max_length=2), default=list, blank=True)
    candidate_countries = ArrayField(models.CharField(max_length=2), default=list, blank=True)
    current_country = models.CharField(max_length=2, blank=True, default="", null=True)
    language_rule = models.JSONField(default=dict, blank=True, null=True)
    vpn_enabled = models.BooleanField(
        default=False,
        help_text=(
            "User's explicit VPN capability. When set, offers in "
            "``candidate_countries`` (or any country, when that list is empty) "
            "count as potentially reachable. Never inferred from IP or locale."
        ),
    )

    def __str__(self):
        return f"Preference user {self.user_id}"


class WatchlistItem(models.Model):
    user_id = models.IntegerField(default=1)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="watchlist_items")
    source = models.CharField(max_length=20, default="letterboxd")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["movie", "source"], name="uniq_watchlistitem_movie_source")
        ]

    def __str__(self):
        return f"{self.source}: {self.movie}"


class ApiCallCounter(models.Model):
    service = models.CharField(max_length=50, unique=True)
    call_date = models.DateField(auto_now_add=True)
    count = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["service"], name="uniq_apicallcounter_service"),
        ]

    def __str__(self):
        return f"{self.service}: {self.count} on {self.call_date}"


class ApiCallLedger(models.Model):
    """Per-source, per-UTC-day reservation counts for rolling budgets.

    ``ApiCallCounter`` stores only the current day, so it cannot answer a
    monthly question. This ledger keeps one row per ``(service, day)`` and is
    summed over a rolling window. The unique constraint is the concurrency
    backstop; reservations are serialised by locking the corresponding
    ``ApiCallCounter`` row first.
    """

    service = models.CharField(max_length=50)
    day = models.DateField()
    count = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["service", "day"],
                name="uniq_apicallledger_service_day",
            ),
        ]

    def __str__(self):
        return f"{self.service}: {self.count} on {self.day}"


class RefreshRun(models.Model):
    service = models.CharField(max_length=50, default="streaming_availability")
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    rows_refreshed = models.IntegerField(default=0)
    rows_skipped = models.IntegerField(default=0)
    rate_limited = models.BooleanField(default=False)
    # Non-empty only when the run failed. A finished run with an empty error
    # is a genuine success (including a genuine empty result), so a failed
    # catalogue fetch can never masquerade as a successful refresh.
    error = models.TextField(blank=True, default="")
    # Set by ``cleanup_availability_history`` when it reconciles a run whose
    # worker died. Such a run says nothing about the source's health, and because
    # catalogue status reads the *newest* finished run per source, reconciling one
    # would otherwise report that source as failed - even to a refresh that never
    # asked for it. Null (the default) means an ordinary run, leaving the
    # ``finished_at`` + empty-``error`` success meaning untouched.
    abandoned_at = models.DateTimeField(null=True, blank=True, db_index=True)

    def __str__(self):
        return f"RefreshRun {self.service} @ {self.started_at}"


class RefreshPriority(models.IntegerChoices):
    """Deterministic priority for event-driven availability refresh requests.

    One ladder, one ranking system: lower numbers run first, and the order
    is exactly the user-intent hierarchy — the more directly a user asked
    for this title *now*, the earlier it drains:

        EXPLICIT < NEW < RECENTLY_VIEWED < VISIBLE < WATCHLIST
                  < WATCHED < STALE < BACKGROUND

    - ``EXPLICIT`` — a deliberate action on one title (manual match
      confirmation, link-to-TMDb, the manual "Refresh availability" button
      on a detail page): the strongest signal short of never having fetched
      the title at all. The only priority that forces the drain to perform a
      new check even while the current offers still read as fresh.
    - ``NEW`` — never fetched by the canonical path
      (``request_availability_refresh`` bumps any never-fetched title here).
    - ``RECENTLY_VIEWED`` — the title's detail page was opened (refresh on
      view); lower intent than a watchlist addition because views are cheap
      and frequent.
    - ``VISIBLE`` — the title is on screen right now. Reserved: the frontend
      has no reliable visibility signal yet, so nothing enqueues at this
      level; the slot is kept so the hierarchy stays stable when one lands.
    - ``WATCHLIST`` — added to the watchlist or imported.
    - ``WATCHED`` — marked as watched (freshness-guarded: a fresh title is
      not enqueued).
    - ``STALE`` — regular stale refresh (the default for a bare request).
    - ``BACKGROUND`` — low-priority historical/import backlog.

    Callers only describe *why* they are asking; deduplication, freshness
    gating and upgrade-only merging all live in
    ``tracker.services.refresh_requests``.
    """

    EXPLICIT = 10, "Explicit user action on this title"
    NEW = 20, "New / never fetched"
    RECENTLY_VIEWED = 30, "Recently viewed detail page"
    VISIBLE = 40, "Visible right now (reserved — no signal yet)"
    WATCHLIST = 50, "Watchlist addition"
    WATCHED = 60, "Marked as watched"
    STALE = 70, "Regular stale refresh"
    BACKGROUND = 80, "Low-priority historical content"


class RefreshRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"
    #: Deliberately **not attempted**. Terminal, and deliberately distinct from
    #: ``done`` — ``done`` asserts the source was consulted and produced a
    #: terminal answer, which is exactly what a blocked row cannot claim.
    #:
    #: The distinction is what makes a terminal run auditable. Before this
    #: existed, work the worker declined to do (out of quota, or no longer
    #: needed) was either left ``pending`` forever — so a run could go terminal
    #: with 553 rows still queued — or marked ``done``, which claimed the
    #: source had answered when it had never been asked.
    BLOCKED = "blocked", "Blocked"


class RefreshRequestOutcome(models.TextChoices):
    """What an attempted source check established.

    Request status answers whether execution succeeded; outcome answers what a
    successful execution learned.  Keeping those facts separate prevents a
    definitive upstream miss from masquerading as a transport failure.
    """

    UNKNOWN = "", "Not recorded / legacy row"
    OFFERS_FOUND = "offers_found", "Offers found"
    NO_OFFER = "no_offer", "Checked — no offer"
    TITLE_NOT_FOUND = "title_not_found", "Upstream title not found"


class BlockedReason(models.TextChoices):
    """Why a request was settled without being attempted.

    Closed vocabulary on purpose. These are *scheduling* facts about our own
    pipeline, kept separate from ``RefreshRequest.last_error`` (which records
    what the upstream said) and from
    :class:`~tracker.models.AvailabilityResolutionReason` (which describes a
    title's world). Keeping them apart is what stops a blocked row from being
    mistaken for a failed lookup.

    A closed set also means an unknown value cannot silently enter the
    database and then be rendered as nothing.
    """

    #: A source with no remaining daily budget. The request was legal when
    #: queued and will become runnable after the reset.
    QUOTA_EXHAUSTED = "quota_exhausted", "Source quota exhausted"
    #: The source ladder decided this source is unnecessary: a cheaper
    #: applicable source already resolved the title, so paying for this one
    #: would re-derive a known answer.
    #:
    #: Deliberately **not** a statement about the title. The title may well be
    #: available and resolved by a collector; only this request was declined.
    POLICY_NOT_NEEDED = "policy_not_needed", "Not needed — another source resolved it"
    #: The source is switched off or unusable by configuration.
    SOURCE_DISABLED = "source_disabled", "Source disabled"
    #: The refresh run owning this request was cancelled.
    CANCELLED = "cancelled", "Run cancelled"
    #: A newer request for the same (title, source) replaced this one.
    SUPERSEDED = "superseded", "Superseded by a newer request"


#: Request statuses that mean "this row will never change again". The
#: lifecycle invariant is stated in terms of this set, so adding a terminal
#: status means re-examining every place that enumerates terminal states.
TERMINAL_REQUEST_STATUSES = frozenset(
    {
        RefreshRequestStatus.DONE,
        RefreshRequestStatus.FAILED,
        RefreshRequestStatus.BLOCKED,
    }
)

#: Request statuses that mean "work is still outstanding".
NON_TERMINAL_REQUEST_STATUSES = frozenset({RefreshRequestStatus.PENDING})


class RefreshRequest(models.Model):
    """One idempotent request to refresh canonical availability for a title.

    Local-only by construction: creating a request is a database insert that
    never calls an external API, so it is safe inside the originating user
    transaction — it commits or rolls back with the user action and the HTTP
    response never waits for a provider. Requests are drained later by the
    existing budgeted refresh run (``process_watchlist_refresh``), which owns
    rate limiting, budget, and freshness policy.

    Deduplication invariant: at most one *pending* request exists per
    (movie, source scope), enforced by the partial unique constraint below —
    repeated events for the same title collapse into one row (a higher
    priority may upgrade it, never duplicate it).

    ``source`` is an ``availability.Source.slug`` scope (where the
    information comes from), never a provider: Provider and Source stay
    distinct, and the empty string means the default title-targeted refresh.
    """

    movie = models.ForeignKey(
        Movie, on_delete=models.CASCADE, related_name="refresh_requests"
    )
    source = models.CharField(max_length=50, blank=True, default="", db_index=True)
    country = models.CharField(max_length=2, blank=True, default="", db_index=True)
    priority = models.IntegerField(
        choices=RefreshPriority.choices, default=RefreshPriority.STALE
    )
    status = models.CharField(
        max_length=12,
        choices=RefreshRequestStatus.choices,
        default=RefreshRequestStatus.PENDING,
        db_index=True,
    )
    attempts = models.IntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    outcome = models.CharField(
        max_length=20,
        choices=RefreshRequestOutcome.choices,
        blank=True,
        default=RefreshRequestOutcome.UNKNOWN,
        db_index=True,
        help_text="Definitive result of a successful source check; blank for failures or legacy rows.",
    )
    #: Why this row was settled without being attempted. Only meaningful when
    #: ``status`` is ``blocked``, and constrained at the database level so the
    #: two cannot drift:
    #:
    #:   blocked  -> blocked_reason must be set
    #:   anything else -> blocked_reason must be empty
    #:
    #: A blocked row without a reason is unauditable (it says only that nothing
    #: happened), and a reason on a non-blocked row is a lie about a source
    #: that was actually consulted. Encoding the vocabulary in a separate
    #: column rather than in the status (``blocked_quota_exhausted``) means new
    #: reasons need no migration.
    blocked_reason = models.CharField(
        max_length=32,
        choices=BlockedReason.choices,
        blank=True,
        default="",
        db_index=True,
    )
    requested_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Bulk-run stamp: the watchlist's "Refresh all" assigns one uuid per
    # click to every request it touches (created *and* collapsed-into rows),
    # so GET /api/watchlist/refresh-status/ can count exactly that run's
    # backlog instead of every pending request in the system. NULL for
    # single-title events (detail page, imports, cron).
    batch = models.UUIDField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["priority", "requested_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["movie", "source", "country"],
                condition=models.Q(status="pending"),
                name="uniq_refreshrequest_pending_movie_source_country",
            ),
            # `blocked` and its reason are one fact stated twice; the database
            # is the right place to keep them consistent because every code
            # path that writes one of them is a place that could forget the
            # other.
            models.CheckConstraint(
                condition=(
                    models.Q(status="blocked", blocked_reason__in=BlockedReason.values)
                    | models.Q(~models.Q(status="blocked"), blocked_reason="")
                ),
                name="refreshrequest_blocked_reason_matches_status",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "priority", "requested_at"],
                name="refreshrequest_drain_order",
            ),
        ]

    def __str__(self):
        scope = self.source or "default"
        return (
            f"RefreshRequest {self.movie_id} scope={scope} "
            f"{self.get_status_display()} priority={self.priority}"
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_REQUEST_STATUSES

    def settle(
        self,
        status: str,
        *,
        blocked_reason: str = "",
        error: str = "",
    ) -> None:
        """Write a terminal outcome and stamp the moment it happened.

        One entry point for every terminal transition, so ``completed_at`` and
        ``blocked_reason`` cannot be forgotten on one code path and set on
        another. ``blocked_reason`` is cleared on non-blocked outcomes on
        purpose: reusing a row must never leave a stale reason describing work
        that was actually done.
        """
        if status not in TERMINAL_REQUEST_STATUSES:
            raise ValueError(f"{status!r} is not a terminal request status")
        if status == RefreshRequestStatus.BLOCKED:
            if not blocked_reason:
                raise ValueError("a blocked request must say why it was blocked")
            if blocked_reason not in BlockedReason.values:
                raise ValueError(f"unknown blocked reason {blocked_reason!r}")
        elif blocked_reason:
            raise ValueError(
                f"blocked_reason only applies to blocked rows, not {status!r}"
            )

        self.status = status
        self.blocked_reason = blocked_reason if status == RefreshRequestStatus.BLOCKED else ""
        self.last_error = error
        self.completed_at = timezone.now()
        self.save(
            update_fields=["status", "blocked_reason", "last_error", "completed_at"]
        )


class CatalogueRefreshStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


class CatalogueRefreshRequest(models.Model):
    """One queued "sync the catalogue-capable sources" job for the worker.

    The per-title ``RefreshRequest`` queue only covers title-targeted
    sources; sources that sync their *whole* catalogue (CBC Gem, Prime,
    BBC iPlayer, …) are refreshed by ``process_catalogue_refresh`` instead.
    The watchlist's "Refresh all availability" presses this marker when
    those sources read stale, so one button covers every source, and the
    refresh worker runs the job after draining its title queue — catalogue
    syncs spend no API budget.

    Idempotent by construction: at most one pending row exists (partial
    unique constraint), repeated clicks merge their stale-slug sets into it
    instead of stacking jobs. ``sources`` freezes the slugs that were stale
    at enqueue time; a later failure is recorded, never silently dropped —
    the next button press opens a fresh job.
    """

    sources = models.JSONField(default=list)
    status = models.CharField(
        max_length=12,
        choices=CatalogueRefreshStatus.choices,
        default=CatalogueRefreshStatus.PENDING,
        db_index=True,
    )
    requested_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["status"],
                condition=models.Q(status="pending"),
                name="uniq_cataloguerefreshrequest_pending",
            ),
        ]

    def __str__(self):
        return f"CatalogueRefreshRequest {self.get_status_display()} {self.sources}"


class AvailabilityResolutionReason(models.TextChoices):
    """Why one (title, source) pair has not produced an answer.

    The read path used to collapse every unresolved title into a single
    ``unknown``, which conflated four very different situations:

    - the source was never asked (``NOT_CHECKED``),
    - it is asked but has not run yet (``QUEUED``),
    - it cannot answer for a structural reason (``NOT_SUPPORTED`` — the
      title is not in a country the source serves, or the title-level
      endpoint does not accept this scope),
    - it answered, and the answer is legitimately "nothing here"
      (``NO_OFFER``),
    - it answered "this title does not exist" (``TITLE_NOT_FOUND``, a
      permanent upstream 404 that must never be retried),
    - it could not be asked because of budget or an outage
      (``QUOTA_EXHAUSTED`` / ``SOURCE_UNAVAILABLE``).

    Only the last two are *absence of availability*. Everything else is an
    absence of **information**, and a title whose sources are all in the
    second group is genuinely unknown — one in the first group is not
    actually unavailable.

    ``NOT_APPLICABLE`` exists for a source that is not applicable to a
    title at all (a catalogue-driven source asked about one film, or a
    country the provider does not serve). It is deliberately distinct from
    ``NO_OFFER``: "we looked and there was nothing" is an answer, "we were
    never in a position to look" is not.
    """

    NOT_CHECKED = "not_checked", "Not checked yet"
    QUEUED = "queued", "Queued, not run yet"
    RESOLVED = "resolved", "Resolved (offers found or checked)"
    NO_OFFER = "no_offer", "Checked — no offer at any provider"
    NOT_SUPPORTED = "not_supported", "Source does not serve this scope"
    NOT_APPLICABLE = "not_applicable", "Source not applicable to this title"
    TITLE_NOT_FOUND = "title_not_found", "Upstream has no such title"
    QUOTA_EXHAUSTED = "quota_exhausted", "Source quota exhausted"
    SOURCE_UNAVAILABLE = "source_unavailable", "Source temporarily unavailable"
    COLLECTOR_NOT_RUN = "collector_not_run", "Catalogue never synced"
    UNKNOWN_REASON = "unknown_reason", "Unclassified"


class AvailabilityRefreshType(models.TextChoices):
    WATCHLIST = "watchlist", "Watchlist bulk refresh"
    TITLE = "title", "Single title"
    CATALOGUE = "catalogue", "Catalogue sync"


class AvailabilityRefreshStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    PARTIAL = "partial", "Partially completed"
    FAILED = "failed", "Failed"


class AvailabilityRefresh(models.Model):
    """One persisted availability refresh *run*.

    A durable job record, not a frontend concern. The refresh button used to
    own the state of a long-running operation in component state, so
    reloading the browser erased every trace of what was running; the queue
    rows survived in the database but nothing reconstructed the run from
    them. This model is that reconstruction: the UI asks
    ``GET /api/availability/refreshes?active=true`` after a reload and
    redraws the same progress it showed before.

    Deliberately *not* an owner of work. ``RefreshRequest`` and
    ``CatalogueRefreshRequest`` remain the units of work — this row is the
    run's identity, its summary counters, and its per-source progress
    (:class:`AvailabilityRefreshSource`), so there is no second scheduling
    system to keep consistent. ``batch`` is the join key back to the
    ``RefreshRequest`` rows this run stamped.

    The title counters are the run's *outcome*, and they are the honest
    answer to "how many titles are still unknown": a title is ``unknown``
    only when no source resolved it, and that is computed by
    ``tracker.services.availability_report`` from the same per-source
    evidence the detail view shows, so the run summary and the per-title
    breakdown can never disagree.
    """

    run_type = models.CharField(
        max_length=12,
        choices=AvailabilityRefreshType.choices,
        default=AvailabilityRefreshType.WATCHLIST,
    )
    status = models.CharField(
        max_length=12,
        choices=AvailabilityRefreshStatus.choices,
        default=AvailabilityRefreshStatus.QUEUED,
        db_index=True,
    )
    # The uuid stamped onto every RefreshRequest this run touched. Nullable
    # because a catalogue-only run owns no title requests.
    batch = models.UUIDField(null=True, blank=True, db_index=True)
    title_filter = models.JSONField(default=list, blank=True)
    # Which sources this run is allowed to consult, in tier order. Frozen at
    # creation so a run's scope is auditable after the fact even if the
    # policy flags change mid-flight.
    source_policy = models.JSONField(default=list, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Outcome counters. `total_titles` is titles *considered*, and
    # available + unavailable + unknown must equal it — the invariant the
    # report service maintains and a test pins.
    total_titles = models.IntegerField(default=0)
    completed_titles = models.IntegerField(default=0)
    available_titles = models.IntegerField(default=0)
    unavailable_titles = models.IntegerField(default=0)
    unknown_titles = models.IntegerField(default=0)
    failed_titles = models.IntegerField(default=0)

    # Why titles are still unknown, as {reason: count}. This is the whole
    # point of the model: the answer to "why are these 28 films unknown?"
    # becomes a query result rather than an investigation.
    unknown_reasons = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="availrefresh_status_created"),
        ]

    def __str__(self):
        return f"AvailabilityRefresh #{self.pk} {self.get_status_display()} ({self.run_type})"

    @property
    def is_active(self) -> bool:
        return self.status in (
            AvailabilityRefreshStatus.QUEUED,
            AvailabilityRefreshStatus.RUNNING,
        )


class AvailabilityRefreshSource(models.Model):
    """Per-source progress and outcome for one :class:`AvailabilityRefresh`.

    The report a user actually needs is not "764 checks waiting" but
    "which source is producing answers, which is out of budget, and which
    has failed". This table holds one row per source per run:

        #184 RUNNING
          Collectors              ✓  312/312
          JustWatch               ↻  843/1120
          Streaming Availability  ⏸    0/764  quota exhausted

    ``status`` distinguishes a source that *finished with no results* from
    one that never ran, and ``reason`` carries the same
    :class:`AvailabilityResolutionReason` vocabulary as the per-title
    breakdown, so a blocked source reads identically in the run summary and
    on a movie page.
    """

    refresh = models.ForeignKey(
        AvailabilityRefresh, on_delete=models.CASCADE, related_name="sources"
    )
    source = models.CharField(max_length=50, db_index=True)
    # "title" (per-title checks) or "catalogue" (whole-catalogue sync). The
    # distinction is load-bearing: only a title-level source can produce a
    # per-title verdict, and the report must never imply a catalogue source
    # checked a specific film.
    kind = models.CharField(max_length=12, default="title")
    status = models.CharField(
        max_length=20,
        choices=[
            ("queued", "Queued"),
            ("running", "Running"),
            ("completed", "Completed"),
            ("skipped", "Skipped"),
            ("quota_exhausted", "Quota exhausted"),
            ("failed", "Failed"),
            ("not_applicable", "Not applicable"),
        ],
        default="queued",
        db_index=True,
    )
    total = models.IntegerField(default=0)
    completed = models.IntegerField(default=0)
    successful = models.IntegerField(default=0)
    failed = models.IntegerField(default=0)
    unknown = models.IntegerField(default=0)
    queued = models.IntegerField(default=0)
    tier = models.IntegerField(default=0)
    reason = models.CharField(
        max_length=32,
        choices=AvailabilityResolutionReason.choices,
        blank=True,
        default="",
    )
    error = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tier", "source"]
        constraints = [
            models.UniqueConstraint(
                fields=["refresh", "source"],
                name="uniq_availrefreshsource_refresh_source",
            ),
        ]

    def __str__(self):
        return f"RefreshSource {self.source} {self.status} {self.completed}/{self.total}"


class UserMovieData(models.Model):
    user_id = models.IntegerField(default=1)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="user_data")
    is_watched = models.BooleanField(default=False)
    rating = models.FloatField(null=True, blank=True)
    review = models.TextField(blank=True, default="")
    letterboxd_uri = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)
    # watched_date field for manual watchlist management
    watched_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user_id", "movie"], name="uniq_usermoviedata_user_movie")
        ]

    def __str__(self):
        return f"User {self.user_id} data for {self.movie}"


class MovieWatchEvent(models.Model):
    user_id = models.IntegerField(default=1)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name="watch_events")
    watched_date = models.DateField(null=True, blank=True)
    rating = models.FloatField(null=True, blank=True)
    review = models.TextField(blank=True, default="")
    rewatch = models.BooleanField(default=False)
    letterboxd_uri = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user_id", "movie", "watched_date", "letterboxd_uri"],
                name="uniq_moviewatchevent_user_movie_date_uri",
            )
        ]

    def __str__(self):
        return f"User {self.user_id} watched {self.movie} on {self.watched_date}"


# --------------------------------------------------------------------------
# Attribution: which refresh requested which execution
# --------------------------------------------------------------------------
#
# An association means "this execution produced work that was requested by this
# refresh". It deliberately does NOT mean "this refresh owns this execution",
# because the queues coalesce:
#
#   * one CatalogueRefreshRequest merges repeat requests, so two refreshes
#     routinely share a single marker (and therefore a single RefreshRun);
#   * one title-drain tick may process RefreshRequests from several batches and
#     still write exactly one RefreshRun.
#
# A single nullable `refresh_id` column on either table would have to pick one
# arbitrary parent, which is either a false claim or a cross-attribution - the
# 2026-10 audits could only attribute catalogue work by scraping worker logs and
# comparing clocks. Explicit many-to-many records express what is actually true:
# an execution may legitimately belong to refreshes A, B and C at once.
#
# Nothing here is read when deciding behaviour. Availability verdicts, source
# policy, quota, freshness and country resolution are unchanged; these rows are
# diagnostic only.


class RefreshRunAvailabilityRefresh(models.Model):
    """Which refreshes requested the work recorded by one ``RefreshRun``."""

    refresh_run = models.ForeignKey(
        RefreshRun, on_delete=models.CASCADE, related_name="availability_refresh_links"
    )
    availability_refresh = models.ForeignKey(
        AvailabilityRefresh, on_delete=models.CASCADE, related_name="refresh_run_links"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["refresh_run", "availability_refresh"],
                name="uniq_refreshrun_availability_refresh",
            )
        ]

    def __str__(self):
        return f"RefreshRun {self.refresh_run_id} <- refresh {self.availability_refresh_id}"


class SyncRunAvailabilityRefresh(models.Model):
    """Which refreshes requested the work recorded by one ``SyncRun``.

    A JustWatch catalogue walk writes one SyncRun per provider/country partition,
    so a single coalesced marker can produce several SyncRuns that all belong to
    the same set of refreshes.
    """

    sync_run = models.ForeignKey(
        "availability.SyncRun", on_delete=models.CASCADE, related_name="refresh_links"
    )
    availability_refresh = models.ForeignKey(
        AvailabilityRefresh, on_delete=models.CASCADE, related_name="sync_run_links"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["sync_run", "availability_refresh"],
                name="uniq_syncrun_availability_refresh",
            )
        ]

    def __str__(self):
        return f"SyncRun {self.sync_run_id} <- refresh {self.availability_refresh_id}"


class CatalogueRefreshAvailabilityRefresh(models.Model):
    """Which refreshes asked for a coalesced catalogue marker.

    Recording the requester at enqueue time is what makes attribution exact
    later: when the worker claims the marker, the set of refreshes it represents
    is already known, so it need not be inferred from timing or from "the
    currently active refresh" (several may be in flight at once).
    """

    marker = models.ForeignKey(
        CatalogueRefreshRequest, on_delete=models.CASCADE, related_name="refresh_links"
    )
    availability_refresh = models.ForeignKey(
        AvailabilityRefresh, on_delete=models.CASCADE, related_name="catalogue_marker_links"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["marker", "availability_refresh"],
                name="uniq_catalogue_marker_availability_refresh",
            )
        ]

    def __str__(self):
        return f"marker {self.marker_id} <- refresh {self.availability_refresh_id}"
