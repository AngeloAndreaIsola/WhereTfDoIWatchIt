 /**
  * Typed API client for the Prime Video MVP endpoints.
  */

export interface PopularMovie {
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  overview: string | null;
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

export interface PrimeOffer {
  country: string;
  service: string;
  offer_type: "subscription" | "free";
  source_indicator?: "Official" | "Free" | "Unofficial" | "Scraper";
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
    overview?: string | null;
    genres?: TMDbGenre[];
    cast?: TMDbCastMember[];
    director?: string[];
    content_type?: string;
  };
  user_data?: UserMovieData | null;
  watch_events?: MovieWatchEvent[];
  offers: PrimeOffer[];
}

export interface WatchResult {
  found: boolean;
  watch_url?: string;
  movie: {
    tmdb_id: number;
    title: string | null;
    year: number | null;
  };
  offer?: PrimeOffer;
  message?: string;
}

export async function fetchWatchDestination(tmdbId: number): Promise<WatchResult> {
  const res = await fetch(`/api/watch/${tmdbId}/`);
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
 * Fetch Prime Video offers for a movie.
 */
export async function fetchPrimeMovieOffers(
  tmdbId: number
): Promise<PrimeMovieResponse> {
  const res = await fetch(`/api/prime/movie/${tmdbId}/`);

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));

    throw new Error(
      body.error ?? `Failed to load offers (${res.status})`
    );
  }

  return res.json();
}

/**
 * Search for movies.
 */
export async function search(
  query: string
): Promise<SearchResult[]> {
  const trimmed = query.trim();

  if (!trimmed) {
    return [];
  }

  const res = await fetch(
    `/api/search/?q=${encodeURIComponent(trimmed)}`
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
  include_official?: boolean;
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
  if (filters.include_official)
    params.set("include_official", "true");

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
  const res = await fetch("/api/v2/unofficial/providers/?include_official=true");
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
