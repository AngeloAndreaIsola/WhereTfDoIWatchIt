 /**
  * Typed API client for the Prime Video MVP endpoints.
  */

export interface PopularMovie {
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  overview: string | null;
  content_type?: "movie" | "series";
  categories: string[];
}

export interface UserMovieData {
  is_watched: boolean;
  rating?: number | null;
  review?: string;
  updated_at?: string;
}

export interface MovieWatchEvent {
  id: number;
  watched_date?: string | null;
  rating?: number | null;
  review?: string;
  rewatch?: boolean;
  created_at?: string;
}

export type OfferType =
  | "subscription"
  | "addon"
  | "free"
  | "free_with_ads"
  | "rent"
  | "buy"
  | "unknown";

export type AccessType = OfferType;
export type OfferLocality = "local" | "vpn" | "out_of_reach" | "anywhere";

/**
 * A structured price attached to an offer. `amount` is a decimal string (never
 * a float, so precision survives) and `currency` an explicit ISO-4217 code.
 * `is_current` is false for a remembered value that the latest observation did
 * not confirm; such a price must not be presented as freshly confirmed.
 */
export interface OfferPrice {
  amount: string | null;
  currency: string;
  source?: string;
  source_offer_id?: string;
  observed_at?: string | null;
  is_current?: boolean;
  /** True when several current prices share a currency but differ ("from €X"). */
  is_from?: boolean;
  validation_error?: string;
}

export interface PrimeOffer {
  country: string;
  service: string;
  offer_type: OfferType;
  source_indicator?: "Official" | "SVOD" | "BVOD" | "AVOD" | "FAST" | "TVOD" | "Scraper" | string;
  quality: string;
  url?: string;
  audio_languages: string[];
  subtitle_languages: string[];
  is_language_match?: boolean;
  ranking: {
    audio_rank: number;
    subtitle_rank: number;
    quality_rank: number;
    is_language_match?: boolean;
  };
  /** Access model of this offer — subscription, add-on, free, free_with_ads, rent or buy. */
  access_type?: AccessType;
  access_label?: string;
  locality?: OfferLocality;
  /** True only when the user is entitled, the language rule passes and the country is reachable. */
  is_accessible?: boolean;
  is_entitled?: boolean;
  requires_payment?: boolean;
  /** True when this offer is delivered as an add-on channel. */
  is_addon?: boolean;
  /** Base platform slug the user must also hold (add-on offers only). */
  required_base?: string;
  /** Display name of the base platform the add-on requires. */
  required_base_display?: string;
  rejection_reasons?: string[];
  /** Selected display price (rent/buy/add-on); absent or null means "no confirmed price". */
  price?: OfferPrice | null;
  /** Every source-specific price row, for inspection. */
  prices?: OfferPrice[];
}

/** The backend's single access verdict for a title (detail pages). */
export interface TitleWatchability {
  verdict?: string;
  status?: string;
  status_label?: string;
  status_symbol?: string;
  explanation?: string[];
  refresh_state?: string | null;
  best_offer?: PrimeOffer | null;
  access_flags?: {
    present_access_types?: AccessType[];
    available_access_types?: AccessType[];
    has_subscription?: boolean;
    has_addon?: boolean;
    has_free?: boolean;
    has_free_with_ads?: boolean;
    has_rent?: boolean;
    has_buy?: boolean;
    requires_payment_only?: boolean;
  };
}

export interface TMDbGenre {
  id: number;
  name: string;
}

export interface TMDbCastMember {
  id: number;
  name: string;
  character: string;
}

export interface PrimeMovieResponse {
  movie: {
    id: number;
    tmdb_id: number;
    title: string;
    year: number | null;
    poster_path?: string | null;
    rating?: number | null;
    tmdb_rating?: number | null;
    overview?: string | null;
    genres?: TMDbGenre[];
    cast?: TMDbCastMember[];
    director?: string[];
    content_type?: string;
  };
  user_data?: UserMovieData | null;
  is_in_watchlist?: boolean;
  watch_events?: MovieWatchEvent[];
  offers: PrimeOffer[];
  /**
   * Rent/buy offers that resolve canonical availability but require an extra
   * payment. Kept apart from `offers` so "buy it" is never read as "you have it".
   */
  purchase_offers?: PrimeOffer[];
  /**
   * The shared access verdict (same resolver as the watchlist badge) — the one
   * field that tells the detail page how the title is reachable for this user.
   */
  watchability?: TitleWatchability | null;
  /** True while this title still waits in the backend refresh queue — the page polls until it clears. */
  refresh_pending?: boolean;
  /** Status of the newest refresh request: `"pending"` / `"done"` / `"failed"`, null when never requested. */
  refresh_status?: "pending" | "done" | "failed" | null;
  /**
   * The backend's own verdict on this title. Present so an empty `offers`
   * list is not ambiguous: `unavailable` means checked-and-nothing,
   * `unknown` means the evidence never resolved. Optional because an older
   * backend omits it — callers must treat absence as unknown, never as
   * unavailable.
   */
  availability?: TitleAvailabilityVerdict;
}


/**
 * Shared by movie and series detail pages. Both render availability from the
 * same backend verdict, so they must not drift apart.
 */
export interface SeriesDetail {
  id: number;
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path?: string | null;
  tmdb_rating?: number | null;
  overview?: string | null;
  genres?: TMDbGenre[];
  cast?: TMDbCastMember[];
  director?: string[];
  content_type?: string;
  seasons_data?: unknown[];
}

export interface SeasonSummary {
  season: number;
  name: string;
  episode_count: number;
  poster_path: string;
}

export interface PrimeSeriesResponse {
  series: SeriesDetail;
  user_data?: UserMovieData | null;
  is_in_watchlist?: boolean;
  watch_events?: MovieWatchEvent[];
  offers: PrimeOffer[];
  /** Rent/buy offers, kept apart from `offers` (see `PrimeMovieResponse`). */
  purchase_offers?: PrimeOffer[];
  /** The shared access verdict for this title (see `PrimeMovieResponse`). */
  watchability?: TitleWatchability | null;
  /** TMDb season list for the selector (series responses only). */
  seasons?: SeasonSummary[];
  /** Season echoed back for `?season=N`; null/absent when unfiltered. */
  selected_season?: number | null;
  /** True while this title still waits in the backend refresh queue — the page polls until it clears. */
  refresh_pending?: boolean;
  /** Status of the newest refresh request: `"pending"` / `"done"` / `"failed"`, null when never requested. */
  refresh_status?: "pending" | "done" | "failed" | null;
  /**
   * The backend's own verdict on this title. Present so an empty `offers`
   * list is not ambiguous: `unavailable` means checked-and-nothing,
   * `unknown` means the evidence never resolved. Optional because an older
   * backend omits it — callers must treat absence as unknown, never as
   * unavailable.
   */
  availability?: TitleAvailabilityVerdict;
}


export interface WatchResult {
  found: boolean;
  watch_url?: string;
  movie: {
    tmdb_id: number;
    title: string | null;
    year: number | null;
    content_type?: string;
  };
  offer?: PrimeOffer;
  message?: string;
}

export async function fetchWatchDestination(tmdbId: number, currentCountry?: string): Promise<WatchResult> {
  const params = currentCountry ? `?current_country=${encodeURIComponent(currentCountry)}` : "";
  const res = await fetch(`/api/watch/${tmdbId}/${params}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    return {
      found: false,
      movie: { tmdb_id: tmdbId, title: null, year: null },
      message: body.message || body.error || `Failed to resolve watch destination (${res.status})`,
    };
  }
  return res.json();
}

export interface PopularResponse {
  results: PopularMovie[];
  count: number;
  cached: boolean;
  source: string;
}

export interface SearchResult {
  tmdb_id: number;
  title: string;
  year: number | null;
  content_type: "movie" | "series";
}

// ─── Unified Catalog ─────────────────────────────────────────────────────────

export interface UnifiedCatalogItem {
  id: string;
  tmdb_id: number | null;
  source: "official" | "scraper";
  source_indicator: string;
  title: string;
  year: number | null;
  poster_path: string | null;
  content_type: string;
  provider: {
    slug: string;
    name: string;
    category: string;
  } | null;
  watchability?: {
    status?: string;
    status_symbol?: string;
    status_label?: string;
    best_offer?: {
      country: string;
      service: string;
      service_display?: string;
      url: string;
      is_current_country: boolean;
    };
    explanation?: string[];
    excluded_offers?: unknown[];
  } | null;
  unofficial_details?: {
    scraped_url: string;
    confidence_score: number;
    is_confirmed: boolean;
    audio_languages: string[];
    subtitle_languages: string[];
  } | null;
  watch_url: string | null;
}

export interface UnifiedCatalogResponse {
  count: number;
  page: number;
  page_size: number;
  total_pages: number;
  has_next: boolean;
  has_previous: boolean;
  results: UnifiedCatalogItem[];
}

export async function fetchUnifiedCatalog(
  params: Record<string, string> = {}
): Promise<UnifiedCatalogResponse> {
  const searchParams = new URLSearchParams(params);
  const res = await fetch(`/api/catalog/unified/?${searchParams.toString()}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.error ?? `Failed to fetch unified catalog (${res.status})`
    );
  }
  return res.json();
}

/**
 * Fetch popular movies.
 */
export async function fetchPopularMovies(
  limit: number = 24
): Promise<PopularResponse> {
  const res = await fetch(
    `/api/prime/popular/?limit=${encodeURIComponent(limit)}`
  );

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));

    throw new Error(
      body.error ?? `Failed to load popular movies (${res.status})`
    );
  }

  return res.json();
}

/**
 * Fetch popular TV series.
 */
export async function fetchPopularSeries(
  limit: number = 24
): Promise<PopularResponse> {
  const res = await fetch(
    `/api/prime/popular-series/?limit=${encodeURIComponent(limit)}`
  );

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));

    throw new Error(
      body.error ?? `Failed to load popular series (${res.status})`
    );
  }

  return res.json();
}

/**
 * Fetch Prime Video offers for a movie.
 */
export async function fetchPrimeMovieOffers(
  tmdbId: number,
  currentCountry?: string
): Promise<PrimeMovieResponse> {
  const params = currentCountry ? `?current_country=${encodeURIComponent(currentCountry)}` : "";
  const res = await fetch(`/api/prime/movie/${tmdbId}/${params}`);

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));

    throw new Error(
      body.error ?? `Failed to load offers (${res.status})`
    );
  }

  return res.json();
}

/**
 * Fetch Prime Video offers for a series. Same pipeline as the movie endpoint,
 * but keyed on `series` and resolved through the TMDb TV endpoint.
 */
export async function fetchPrimeSeriesOffers(
  tmdbId: number,
  currentCountry?: string,
  season?: number | null
): Promise<PrimeSeriesResponse> {
  const params = new URLSearchParams();
  if (currentCountry) params.set("current_country", currentCountry);
  if (season !== undefined && season !== null) params.set("season", String(season));
  const query = params.toString();
  const res = await fetch(`/api/prime/series/${tmdbId}/${query ? `?${query}` : ""}`);

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));

    throw new Error(
      body.error ?? body.message ?? `Failed to load offers (${res.status})`
    );
  }

  return res.json();
}

/**
 * Queue an explicit availability refresh for one title (movie or series).
 *
 * Enqueue-only: the backend creates a high-priority refresh request and
 * returns immediately — the refresh worker performs the actual provider
 * fetch, and the detail page picks the new canonical offers up by polling
 * `refresh_pending` on the detail endpoint.
 */
export interface RefreshAvailabilityResponse {
  message: string;
  tmdb_id: number;
  content_type: string;
  refresh_pending: boolean;
  refresh_status: "pending";
  priority: number;
  request_id: number | null;
}

export async function requestAvailabilityRefresh(
  tmdbId: number
): Promise<RefreshAvailabilityResponse> {
  const res = await fetch(`/api/movie/${tmdbId}/refresh/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.error ??
        body.detail ??
        `Failed to queue availability refresh (${res.status})`
    );
  }
  return res.json();
}

/**
 * Queue an explicit availability refresh for every watchlisted title.
 *
 * Enqueue-only: one local pass creates one pending refresh request per
 * (title, source) across every registered title-level source — deduplicated
 * against anything already queued for that same scope, freshness bypassed —
 * and answers immediately. The refresh worker drains the queue under each
 * source's own rate limit. Progress is read back with
 * `getWatchlistRefreshStatus`, using the returned `now` as the `since`
 * anchor and `batch` to scope every count to *this* run's backlog, so
 * requests queued elsewhere never inflate or deflate it.
 */
export interface WatchlistRefreshQueuedResponse {
  /** Source-scoped checks this batch now owns (titles x sources). */
  queued: number;
  total_watchlist: number;
  /** Server epoch seconds captured when the run started. */
  now: number;
  /** Id of the batch this run stamped onto every request it touched. */
  batch: string;
  /** Title-level sources the run fanned out over. */
  sources: string[];
  /** How many source checks each watchlisted title received. */
  checks_per_title: number;
  /** Catalogue-capable sources that were stale and got queued too. */
  catalogue_queued?: string[];
}

export async function requestWatchlistRefresh(): Promise<WatchlistRefreshQueuedResponse> {
  const res = await fetch(`/api/watchlist/refresh-explicit/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.error ??
        body.detail ??
        `Failed to queue watchlist refresh (${res.status})`
    );
  }
  return res.json();
}

/**
 * Progress of one bulk watchlist refresh run.
 *
 * The unit of work is a **source check**, not a title: Refresh All queues one
 * request per (title, source) across every registered title-level source, so
 * `total`/`pending`/`refreshed`/`failed` all count checks — `29` titles over
 * `3` sources is `87` checks. `titles` and `checks_per_title` let the UI state
 * both honestly instead of implying one check per title.
 *
 * Rate limiting is per source, so `rate_limited_sources` names the blocked
 * collector(s) and `rate_limited` only means "at least one source is waiting
 * for its quota" — never "the whole refresh is blocked". `source_quotas`
 * carries the remaining budget per *metered* source; sources with no declared
 * daily quota are absent rather than reported with a fabricated number.
 */
export interface WatchlistRefreshStatusResponse {
  /** Total source-scoped checks this batch owns. */
  total: number;
  pending: number;
  refreshed: number;
  failed: number;
  /** Distinct watchlist titles covered by the batch (0 without a batch). */
  titles: number;
  /** Source checks per title for this batch (0 without a batch). */
  checks_per_title: number;
  since: number;
  batch: string | null;
  /** True when at least one source is waiting for its daily quota. */
  rate_limited: boolean;
  /** Sources whose own daily budget is currently spent. */
  rate_limited_sources: string[];
  /** Remaining budget per metered source. Unmetered sources are omitted. */
  source_quotas: Record<
    string,
    { remaining: number; limit: number; exhausted: boolean }
  >;
  /** Epoch seconds of the next UTC midnight — when daily quotas reset. */
  resets_at: number;
}

export async function getWatchlistRefreshStatus(
  since: number,
  batch?: string
): Promise<WatchlistRefreshStatusResponse> {
  const params = new URLSearchParams({ since: String(since) });
  if (batch) params.set("batch", batch);
  const res = await fetch(`/api/watchlist/refresh-status/?${params}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.error ??
        body.detail ??
        `Failed to read refresh progress (${res.status})`
    );
  }
  return res.json();
}

// --- Source-first availability observability -------------------------------
//
// These replace the button's component state as the source of truth for "what
// is running". The backend persists every run, so the UI can be reloaded at
// any moment and ask the same question the button was answering from memory.

/**
 * Why one (title, source) pair has not produced an answer.
 *
 * The load-bearing distinction is between the two groups:
 *
 * - absence reasons (`no_offer`, `title_not_found`) mean a source *was asked
 *   and answered* — the title is genuinely unavailable;
 * - information-gap reasons (`queued`, `quota_exhausted`, `not_checked`, …)
 *   mean nobody got to ask — the title is unknown.
 *
 * A title is `available` if ANY source resolved it, no matter how many others
 * are still silent, which is why one blocked source can never turn a real
 * answer into "unknown".
 */
export type AvailabilityResolutionReason =
  | "not_checked"
  | "queued"
  | "resolved"
  | "no_offer"
  | "not_supported"
  | "not_applicable"
  | "title_not_found"
  | "quota_exhausted"
  | "source_unavailable"
  | "collector_not_run"
  | "unknown_reason";

/**
 * One title's availability verdict, as returned by the detail endpoints.
 *
 * This is a *presentation* of the backend classifier, not a second one: the
 * same three states and the same reasons the availability report already uses.
 * Declared here once and referenced by both the movie and series detail
 * responses so the two pages cannot drift apart.
 *
 * `reason` reuses the report's `AvailabilityResolutionReason` union, so a new
 * backend reason is a type error in one place rather than a silent `string`.
 */
export interface TitleAvailabilityVerdict {
  state: AvailabilityTitleState;
  reason: AvailabilityResolutionReason | null;
  resolved_sources: string[];
  unresolved_sources: string[];
  sources: TitleAvailabilitySource[];
  /** Provider slugs a source reported this title as available on. */
  available_on?: string[];
  /**
   * How the state was reached, so the page can tell a concrete offer apart
   * from a source's bare "it is available" claim:
   * - `offers` — at least one currently-available canonical offer exists.
   * - `title_signal` — a source reports the title as available, but no
   *   concrete offer could be matched/displayed. Never a playable option.
   * - `absence` — sources were checked and none offered it.
   * - `none` — no evidence resolved either way.
   */
  evidence?: "offers" | "title_signal" | "absence" | "none";
  /** Number of currently-available canonical offers behind `state`. */
  offer_count?: number;
  /** Title-level availability claims with no displayable offer. */
  title_signals?: Array<{
    provider: string;
    country?: string;
    offer_type?: string;
    source: string;
    checked_at?: string | null;
  }>;
}

/**
 * One source's contribution to a *single title's* verdict.
 *
 * Deliberately not `AvailabilitySourceRow`: that is the report's per-source
 * aggregate (`tier`, `total`, `completed`, `kind`, quota, counters) and forces
 * every title-level row to carry roll-up fields it never has. This type holds
 * exactly what the detail endpoint emits, so a backend field that is missing
 * here is a compile error rather than an `undefined` read at runtime.
 */
export interface TitleAvailabilitySource {
  source: string;
  label: string;
  status: string;
  reason: AvailabilityResolutionReason | null;
  /** Whether *this source* produced an answer. Null when it produced none. */
  available: boolean | null;
  /**
   * When this source actually answered. Null when it was never consulted —
   * which is precisely why absence of an offer is not absence of a check.
   */
  checked_at: string | null;
}

export type AvailabilityTitleState = "available" | "unavailable" | "unknown";

export interface AvailabilitySourceRow {
  source: string;
  label: string;
  tier: number;
  role?: string;
  kind: "title" | "catalogue";
  status: string;
  reason: AvailabilityResolutionReason | "";
  total: number;
  completed: number;
  successful: number;
  failed: number;
  unknown: number;
  queued: number;
  offers?: number;
  last_sync?: string | null;
  quota?: { remaining: number; limit: number; exhausted: boolean } | null;
  /** When the source produced an *answer* (an offer was found). Null when the
   *  source answered "nothing here" — absence of an offer is not absence of a
   *  check, which is why this is separate from `last_attempted_at`. */
  checked_at?: string | null;
  /** When the source was last consulted at all, answered or not. Null only
   *  when no request row exists, i.e. genuinely never attempted. */
  last_attempted_at?: string | null;
  available?: boolean;
}

export interface AvailabilityReportResponse {
  sources: AvailabilitySourceRow[];
  collectors: AvailabilitySourceRow[];
  totals: {
    titles: number;
    available: number;
    unavailable: number;
    unknown: number;
    checks: number;
  };
  /** `{reason: count}` over the titles that are genuinely unknown. */
  unknown_reasons: Partial<Record<AvailabilityResolutionReason, number>>;
  policy: {
    budget_order: string[];
    demote_metered_aggregators: boolean;
    tiers: Record<string, string>;
  };
  counterfactual: {
    policy: string;
    current: boolean;
    titles_still_resolved: number;
    titles_losing_their_answer: number;
    metered_sources: string[];
    note: string;
  };
  resets_at: number;
}

export async function getAvailabilityReport(): Promise<AvailabilityReportResponse> {
  const res = await fetch("/api/availability/report/");
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.error ?? `Failed to read availability report (${res.status})`
    );
  }
  return res.json();
}

export interface AvailabilityTitleReport {
  tmdb_id: number;
  title: string;
  state: AvailabilityTitleState;
  reason: AvailabilityResolutionReason | "";
  available_on: string[];
  sources: AvailabilitySourceRow[];
  collectors: AvailabilitySourceRow[];
  resolved_sources: string[];
  unresolved_sources: string[];
}

export async function getAvailabilityTitleReport(
  tmdbId: number
): Promise<AvailabilityTitleReport> {
  const res = await fetch(`/api/availability/report/${tmdbId}/`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.error ?? `Failed to read title availability (${res.status})`
    );
  }
  return res.json();
}

export interface AvailabilityRefresh {
  id: number;
  type: string;
  status: "queued" | "running" | "completed" | "partial" | "failed";
  batch: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  is_active: boolean;
  total_titles: number;
  completed_titles: number;
  available_titles: number;
  unavailable_titles: number;
  unknown_titles: number;
  failed_titles: number;
  unknown_reasons: Partial<Record<AvailabilityResolutionReason, number>>;
  sources: AvailabilitySourceRow[];
  /** The run's frozen policy, so the UI never has to guess which ladder
   *  produced these numbers. */
  policy?: {
    roles?: Record<string, string>;
    groups?: Record<string, string[]>;
    consult_metered_only_when_unresolved?: boolean;
    ladder?: string;
  };
}

export interface AvailabilityRefreshListResponse {
  /** Recent runs, newest first — including finished ones, so the outcome
   *  of a completed refresh survives a reload. */
  results: AvailabilityRefresh[];
  /** The newest non-terminal run, or null. This is what a page load polls. */
  active: AvailabilityRefresh | null;
}

export async function getAvailabilityRefreshes(): Promise<AvailabilityRefreshListResponse> {
  const res = await fetch("/api/availability/refreshes/");
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.error ?? `Failed to read refresh runs (${res.status})`
    );
  }
  return res.json();
}

export async function getAvailabilityRefresh(
  id: number
): Promise<AvailabilityRefresh> {
  const res = await fetch(`/api/availability/refreshes/${id}/`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.error ?? `Failed to read refresh #${id} (${res.status})`
    );
  }
  return res.json();
}

/** Response from POSTing a new refresh. */
export interface AvailabilityRefreshStartResponse {
  /** The id to poll and to resume from after a reload. */
  refresh_id: number;
  batch: string;
  now: number;
  /** Requests this run owns. Already policy-filtered — with the metered gate
   *  on, a resolved title contributes no metered row at all. */
  queued: number;
  total_watchlist: number;
  /** Sources this run may consult, union across its titles. */
  sources: string[];
  /** Catalogue sources queued in the same action. */
  catalogue_queued: string[] | null;
  refresh: AvailabilityRefresh;
}

/**
 * POST a watchlist availability refresh.
 *
 * Enqueue-only, like the endpoint it replaces: this never contacts a provider
 * and returns as soon as the queue is written. The returned `refresh_id` is
 * the handle for everything after — polling, reload recovery and the terminal
 * state all key off it, so the client's own "am I refreshing?" state is only
 * ever a cache of the backend's.
 *
 * Note the URL is `availability/refresh/` (singular) while the reads are
 * `availability/refreshes/` (plural): the singular path is the POST that
 * *creates* a run, the plural collection is the GET that lists them. Posting
 * to the plural path answers 405, not 404.
 */
export async function startAvailabilityRefresh(): Promise<AvailabilityRefreshStartResponse> {
  const res = await fetch("/api/availability/refresh/", { method: "POST" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.error ?? `Failed to start availability refresh (${res.status})`
    );
  }
  return res.json();
}

/**
 * Search TMDb. `type` defaults to "movie" (existing behavior); pass "series"
 * or "all" to widen the search.
 */
export async function search(
  query: string,
  type: "movie" | "series" | "all" = "movie"
): Promise<SearchResult[]> {
  const trimmed = query.trim();

  if (!trimmed) {
    return [];
  }

  const res = await fetch(
    `/api/search/?q=${encodeURIComponent(trimmed)}&type=${encodeURIComponent(type)}`
  );

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));

    throw new Error(
      body.error ?? `Search failed (${res.status})`
    );
  }

  const data: { results?: SearchResult[] } = await res.json();

  return data.results ?? [];
}

// ─── Unofficial sources (Phase C) ────────────────────────────────────────────

export interface UnofficialCatalogItem {
  id: number;
  scraped_title: string;
  scraped_year: number | null;
  scraped_url: string;
  provider: {
    id: number;
    slug: string;
    name: string;
    category: string;
    source_indicator: string;
    is_enabled: boolean;
    is_major: boolean;
    icon_name: string | null;
    domain: string;
    last_scraped_at: string | null;
    is_stale: boolean;
  };
  movie: {
    id: number;
    tmdb_id: number;
    title: string;
    year: number | null;
    poster_path: string;
  } | null;
  is_confirmed: boolean;
  confidence_score: number;
  raw_payload: Record<string, unknown>;
  is_available: boolean;
  source_indicator: string;
  provider_category: string;
}

export interface UnofficialCatalogResponse {
  count: number;
  filters: {
    provider: string | null;
    country: string | null;
    audio_lang: string | null;
    subtitle_lang: string | null;
    available_only: boolean;
    include_official: boolean;
  };
  results: UnofficialCatalogItem[];
}

export interface UnofficialProvider {
  id: number;
  slug: string;
  name: string;
  category: string;
  source_indicator: string;
  is_enabled: boolean;
  is_major: boolean;
  icon_name: string | null;
  domain: string;
  last_scraped_at: string | null;
  is_stale: boolean;
}

export interface UnofficialMovieOffer {
  country: string;
  service: string;
  service_display: string;
  source_indicator: string;
  offer_type: string;
  quality: string;
  url?: string;
  audio: string[];
  subs: string[];
}

export interface UnofficialMovieOffersResponse {
  movie: {
    tmdb_id: number;
    title: string;
    year: number | null;
    poster_path: string;
  };
  offers: UnofficialMovieOffer[];
}

export interface UnofficialProvidersResponse {
  count: number;
  results: UnofficialProvider[];
}

export interface UnofficialCatalogFilters {
  provider?: string;
  country?: string;
  audio_lang?: string;
  subtitle_lang?: string;
  q?: string;
  available?: boolean;
  include_bvod?: boolean;
}

export async function fetchUnofficialCatalog(
  filters: UnofficialCatalogFilters = {}
): Promise<UnofficialCatalogResponse> {
  const params = new URLSearchParams();
  if (filters.provider) params.set("provider", filters.provider);
  if (filters.country) params.set("country", filters.country);
  if (filters.audio_lang) params.set("audio_lang", filters.audio_lang);
  if (filters.subtitle_lang) params.set("subtitle_lang", filters.subtitle_lang);
  if (filters.q) params.set("q", filters.q);
  if (filters.available !== undefined)
    params.set("available", filters.available ? "true" : "false");
  if (filters.include_bvod)
    params.set("include_bvod", "true");

  const res = await fetch(`/api/v2/unofficial/?${params.toString()}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.error ?? `Failed to load unofficial catalog (${res.status})`
    );
  }
  return res.json();
}

export async function fetchUnofficialProviders(): Promise<UnofficialProvidersResponse> {
  const res = await fetch("/api/v2/unofficial/providers/?include_bvod=true");
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.error ?? `Failed to load unofficial providers (${res.status})`
    );
  }
  return res.json();
}

export async function fetchUnofficialMovieOffers(
  tmdbId: number
): Promise<UnofficialMovieOffersResponse> {
  const res = await fetch(`/api/v2/unofficial/movie/${tmdbId}/offers/`);
  if (!res.ok) {
    return { movie: { tmdb_id: tmdbId, title: "", year: null, poster_path: "" }, offers: [] };
  }
  return res.json();
}

export async function triggerUnofficialScrape(
  providerSlug: string,
  query?: string
): Promise<{ count: number; results: unknown[] }> {
  const res = await fetch("/api/v2/unofficial/scrape/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider_slug: providerSlug, query: query ?? "" }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.error ?? `Scrape failed (${res.status})`
    );
  }
  return res.json();
}

export interface ScrapeResult {
  provider: string;
  provider_name: string;
  provider_category: string;
  source_indicator: string;
  count: number;
  results: Array<{
    scraped_title: string;
    scraped_year: number | null;
    confidence_score: number;
    is_confirmed: boolean;
    matched_movie: { tmdb_id: number; title: string; year: number | null; poster_path: string } | null;
  }>;
}

export async function triggerScraperRun(
  providerSlug: string
): Promise<ScrapeResult> {
  const res = await fetch("/api/scrapers/run/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider_slug: providerSlug }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.error ?? `Scrape failed (${res.status})`
    );
  }
  return res.json();
}

// ─── Per-source refresh (square source cards) ───────────────────────────────

/**
 * One card, one source: run *this* provider's collector and nothing else.
 *
 * The endpoint resolves the collector itself (registry, not request input),
 * so the response tells the caller what kind of work was started:
 *
 * - `kind: "catalogue"` — enqueue-only (202): the refresh worker drains the
 *   queued source; the card stays in its loading state until `GET
 *   /api/providers/` reports `refreshing: false` again. Poll the list, do
 *   not poll this endpoint.
 * - `kind: "scraper"` — synchronous: the scraper ran inside this request and
 *   the response carries the scraped `count` and the new `last_run`.
 *
 * A provider with no registered collector answers 409 with `error` — the
 * cards hide their refresh control for those, so that status is a real
 * failure, not an expected state.
 */
export interface ProviderRefreshResponse {
  provider: string;
  kind: "catalogue" | "scraper";
  source?: string;
  queued: boolean;
  refreshing?: boolean;
  count?: number;
  last_run?: string | null;
  marker_id?: number;
  error?: string;
}

export async function refreshProvider(
  providerSlug: string
): Promise<ProviderRefreshResponse> {
  const res = await fetch(
    `/api/providers/${encodeURIComponent(providerSlug)}/refresh/`,
    { method: "POST" }
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.error ?? `Refresh failed (${res.status})`);
  }
  return body as ProviderRefreshResponse;
}


// ─── Service access modes ───────────────────────────────────────────────────

/** How the user holds a service (mirrors the backend Subscription.access_mode). */
export type ProviderAccessMode = "included" | "addon" | "free" | "rent_buy" | "none";

export interface ProviderAccessResult {
  provider: string;
  is_subscribed: boolean;
  access_mode: ProviderAccessMode;
  created?: boolean;
  message?: string;
}

/**
 * Declare the user's access to a provider. ``none`` removes the selection;
 * every other mode records *how* they hold it (included subscription, add-on,
 * free, or a rent/buy account).
 */
export async function setProviderAccessMode(
  providerSlug: string,
  accessMode: ProviderAccessMode
): Promise<ProviderAccessResult> {
  const res = await fetch(
    `/api/providers/${encodeURIComponent(providerSlug)}/toggle/`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ access_mode: accessMode }),
    }
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.error ?? `Failed to update access (${res.status})`);
  }
  return body as ProviderAccessResult;
}

export interface ProviderCatalogueSyncReport {
  integration?: string;
  countries?: string[];
  packages_seen?: number;
  mapped?: Array<{ country: string; slug: string; technical_name: string }>;
  created_providers?: string[];
  mappings_created?: string[];
  errors?: Array<{ country: string; error: string }>;
  unmapped?: Array<{ technical_name: string; clear_name: string; countries: string[] }>;
  unmapped_count?: number;
  providers_touched?: string[];
}

/**
 * Reconcile the local provider catalogue from JustWatch's provider list.
 * Idempotent; newly discovered services are created disabled.
 */
export async function syncProviderCatalogue(
  countries?: string[]
): Promise<ProviderCatalogueSyncReport> {
  const res = await fetch("/api/providers/sync/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(countries && countries.length ? { country: countries } : {}),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.error ?? `Catalogue sync failed (${res.status})`);
  }
  return body as ProviderCatalogueSyncReport;
}


// ─── Movie editing & poster matching ────────────────────────────────────────

export interface TMDbSearchResult {
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  overview: string | null;
  rating: number | null;
}

export interface MovieUpdateResponse {
  tmdb_id: number;
  title: string;
  poster_path: string;
  tmdb_rating: number | null;
}

export interface MovieSearchMatchesResponse {
  query: string;
  results: TMDbSearchResult[];
}

export async function updateMovie(
  tmdbId: number,
  updates: { title?: string; poster_path?: string; tmdb_rating?: number | null }
): Promise<MovieUpdateResponse> {
  const res = await fetch(`/api/movie/${tmdbId}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `Failed to update movie (${res.status})`);
  }
  return res.json();
}

export async function searchMovieMatches(
  tmdbId: number,
  query: string
): Promise<MovieSearchMatchesResponse> {
  const res = await fetch(`/api/movie/${tmdbId}/search-matches/?q=${encodeURIComponent(query)}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `Failed to search matches (${res.status})`);
  }
  return res.json();
}

export interface LinkTmdbResponse {
  message: string;
  movie: {
    id: number;
    tmdb_id: number;
    title: string;
    year: number | null;
    content_type: string;
    poster_path: string;
    overview: string;
    genres: unknown[];
    genre_ids: unknown[];
    cast: unknown[];
    director: string[];
    seasons_data: unknown[];
    runtime_minutes: number | null;
    tmdb_rating: number | null;
  };
}

/**
 * Manually link a DB movie to a TMDb entry. Syncs title, year, poster,
 * overview, genres, cast, director, and rating from TMDb into the DB movie.
 */
export async function linkTmdb(
  moviePk: number,
  tmdbId: number,
  opts: { title?: string; content_type?: string } = {}
): Promise<LinkTmdbResponse> {
  const res = await fetch(`/api/movie/${moviePk}/link-tmdb/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tmdb_id: tmdbId,
      title: opts.title ?? "",
      content_type: opts.content_type ?? "movie",
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `Failed to link movie (${res.status})`);
  }
  return res.json();
}

// ─── Google Interests importer ──────────────────────────────────────────────

// ─── Importer result shapes (shared by the Letterboxd and Google importers) ─

export interface ResolvedWatchlistRow {
  movie?: {
    id?: number;
    tmdb_id?: number;
    title?: string;
    content_type?: string;
    year?: number;
    poster_path?: string;
  };
  watchability?: {
    status?: string;
    status_symbol?: string;
    status_label?: string;
    best_offer?: {
      country: string;
      service: string;
      service_display?: string;
      url: string;
      is_current_country: boolean;
    };
    explanation?: string[];
    excluded_offers?: unknown[];
  };
}

export interface AmbiguousMatchItem {
  title: string;
  year: number | null;
  uri?: string | null;
  candidates: Array<{ tmdb_id: number; title: string; year: number | null }>;
  reason?: string;
}

export interface SkippedImportItem {
  title?: string;
  year?: number | null;
  uri?: string | null;
  /** Present instead of `title` when a whole URL failed to fetch. */
  url?: string;
  reason?: string;
}

export interface GoogleInterestsImportResult {
  resolved: ResolvedWatchlistRow[];
  ambiguous: AmbiguousMatchItem[];
  skipped: SkippedImportItem[];
  watchlist_count: number;
}

export interface GoogleInterestsImportPayload {
  cookies?: Record<string, string>;
  urls: string[];
  user_id?: number;
}

export async function importGoogleInterests(
  payload: GoogleInterestsImportPayload
): Promise<GoogleInterestsImportResult> {
  const res = await fetch("/api/importers/google-interests/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `Google Interests import failed (${res.status})`);
  }
  return res.json();
}

// ─── Letterboxd public-profile URL importer ─────────────────────────────────

export type LetterboxdImportType = "watchlist" | "watched";

export interface LetterboxdUrlImportResult {
  source: "letterboxd_url";
  username: string;
  import_type: LetterboxdImportType;
  pages_fetched: number;
  rows_found: number;
  resolved: ResolvedWatchlistRow[];
  ambiguous: AmbiguousMatchItem[];
  skipped: SkippedImportItem[];
  watched_count: number;
  ratings_count: number;
  reviews_count: number;
  diary_events_count: number;
}

export interface LetterboxdUrlImportPayload {
  url: string;
  /** Required when `url` is a bare profile URL. */
  type?: LetterboxdImportType;
  user_id?: number;
}

export async function importLetterboxdUrl(
  payload: LetterboxdUrlImportPayload
): Promise<LetterboxdUrlImportResult> {
  const res = await fetch("/api/importers/letterboxd-url/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `Letterboxd import failed (${res.status})`);
  }
  return res.json();
}

// ─── Manual watchlist management ────────────────────────────────────────────

export interface AddToWatchlistRequest {
  tmdb_id: number;
  content_type?: "movie" | "series";
}

export interface AddToWatchlistResponse {
  message: string;
  watchlist_item?: {
    id: number;
    movie: {
      id: number;
      tmdb_id: number;
      title: string;
      year: number | null;
      content_type: string;
      poster_path: string | null;
    };
    source: string;
    added_at: string;
  };
  user_data?: {
    is_watched: boolean;
    rating?: number | null;
    review?: string;
    updated_at?: string;
  };
}

export interface MarkWatchedRequest {
  watched_date?: string;
  rating?: number;
  review?: string;
  rewatch?: boolean;
}

export interface MarkWatchedResponse {
  message: string;
  user_data?: {
    is_watched: boolean;
    rating?: number | null;
    review?: string;
    updated_at?: string;
  };
}

export interface UnmarkWatchedResponse {
  message: string;
  user_data?: {
    is_watched: boolean;
    rating?: number | null;
    review?: string;
    updated_at?: string;
  };
}

export interface RemoveFromWatchlistResponse {
  message: string;
}

export async function addToWatchlist(
  tmdbId: number,
  contentType: "movie" | "series" = "movie"
): Promise<AddToWatchlistResponse> {
  const res = await fetch("/api/watchlist/add/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tmdb_id: tmdbId, content_type: contentType }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `Failed to add to watchlist (${res.status})`);
  }
  return res.json();
}

export async function markMovieWatched(
  tmdbId: number,
  data: MarkWatchedRequest = {}
): Promise<MarkWatchedResponse> {
  const res = await fetch(`/api/watched/${tmdbId}/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `Failed to mark as watched (${res.status})`);
  }
  return res.json();
}

export async function unmarkMovieWatched(
  tmdbId: number
): Promise<MarkWatchedResponse> {
  const res = await fetch(`/api/unwatched/${tmdbId}/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `Failed to unmark as watched (${res.status})`);
  }
  return res.json();
}

export async function removeWatchlistItem(
  tmdbId: number
): Promise<{ message: string }> {
  const res = await fetch(`/api/watchlist/remove/${tmdbId}/`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `Failed to remove from watchlist (${res.status})`);
  }
  return res.json();
}

export interface WatchedMovie {
  tmdb_id: number;
  title: string;
  year: number | null;
  content_type: string;
  poster_path: string | null;
}

export interface WatchedUserData {
  is_watched: boolean;
  rating: number | null;
  review: string;
  watched_date: string | null;
}

export interface WatchedItem {
  movie: WatchedMovie;
  user_data: WatchedUserData;
  watch_count: number;
  last_watched_at: string | null;
  is_in_watchlist: boolean;
}

export interface WatchedPagination {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface WatchedListResponse {
  results: WatchedItem[];
  pagination: WatchedPagination;
}

export async function fetchWatched(params?: Record<string, string>): Promise<WatchedListResponse> {
  const qs = params ? new URLSearchParams(params).toString() : "";
  const res = await fetch(`/api/watched/${qs ? "?" + qs : ""}`);
  if (!res.ok) throw new Error(`fetchWatched failed: ${res.status}`);
  return res.json();
}
