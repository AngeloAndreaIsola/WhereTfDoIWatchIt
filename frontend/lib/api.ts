/** Typed API client for the Prime Video MVP endpoints. */

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
  movie: { tmdb_id: number; title: string; year: number | null };
  offers: PrimeOffer[];
}

export interface PopularResponse {
  results: PopularMovie[];
  count: number;
  cached: boolean;
  source: string;
}

export async function fetchPopularMovies(limit: number = 24): Promise<PopularResponse> {
  const res = await fetch(`/api/prime/popular/?limit=${limit}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `Failed to load popular movies (${res.status})`);
  }
  return res.json();
}

export async function fetchPrimeMovieOffers(tmdbId: number): Promise<PrimeMovieResponse> {
  const res = await fetch(`/api/prime/movie/${tmdbId}/`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `Failed to load offers (${res.status})`);
  }
  return res.json();
}
