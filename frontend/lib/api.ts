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

export interface PrimeOffer {
  country: string;
  service: string;
  offer_type: "subscription" | "free";
  quality: string;
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
  };
  offers: PrimeOffer[];
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