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
  quality: string;
  url?: string;
  audio_languages: string[];
  subtitle_languages: string[];
  ranking: {
    audio_rank: number;
    subtitle_rank: number;
    quality_rank: number;
  };
}

export interface PrimeMovieResponse {
  movie: {
    tmdb_id: number;
    title: string;
    year: number | null;
    poster_path?: string | null;
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
