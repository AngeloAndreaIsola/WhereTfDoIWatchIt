"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { FilterBar, FilterState } from "@/components/FilterBar";
import MovieCard from "@/components/MovieCard";
import { Pagination } from "@/components/Pagination";
import { useCurrentLocation } from "@/components/CurrentLocationProvider";
import { unmarkMovieWatched } from "@/lib/api";

interface WatchedRow {
  movie: {
    id?: number;
    tmdb_id?: number;
    title?: string;
    content_type?: string;
    year?: number | null;
    poster_path?: string | null;
    tmdb_rating?: number | null;
  };
  is_watched?: boolean;
  user_data?: {
    is_watched?: boolean;
    rating?: number | null;
  } | null;
}

const EMPTY_FILTERS: FilterState = {
  q: "",
  content_type: "all",
  genre: "",
  year_min: "",
  year_max: "",
  actors: [],
  providers: [],
  country: "",
  status: "",
  quality: "",
  score_min: "",
  sort_by: "",
};

const parseFiltersFromURL = (sp: URLSearchParams): FilterState => {
  const providers = sp.get("providers");
  const actors = sp.getAll("actor_id");
  return {
    ...EMPTY_FILTERS,
    q: sp.get("q") || "",
    content_type: sp.get("type") || "all",
    genre: sp.get("genre") || "",
    year_min: sp.get("year_min") || "",
    year_max: sp.get("year_max") || "",
    actors: actors
      .map((a) => {
        const [id, name] = a.split(":");
        return { id: Number(id), name: name || "" };
      })
      .filter((a) => a.id),
    providers: providers ? providers.split(",") : [],
    country: sp.get("country") || "",
    status: sp.get("status") || "",
    quality: sp.get("quality") || "",
    score_min: sp.get("score_min") || "",
    sort_by: sp.get("sort_by") || "",
  };
};

const buildWatchedQuery = (filters: FilterState, page: number): string => {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.content_type !== "all") params.set("type", filters.content_type);
  if (filters.genre) params.set("genre", filters.genre);
  if (filters.year_min) params.set("year_min", filters.year_min);
  if (filters.year_max) params.set("year_max", filters.year_max);
  if (filters.providers.length > 0) params.set("providers", filters.providers.join(","));
  if (filters.country) params.set("country", filters.country);
  if (filters.status) params.set("status", filters.status);
  if (filters.quality) params.set("quality", filters.quality);
  if (filters.score_min) params.set("score_min", filters.score_min);
  if (filters.sort_by) params.set("sort_by", filters.sort_by);
  filters.actors.forEach((a) => params.append("actor_id", `${a.id}:${a.name}`));
  if (page > 1) params.set("page", page.toString());
  return params.toString();
};

export default function WatchedPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { currentCountry } = useCurrentLocation();

  const [filters, setFilters] = useState<FilterState>(() =>
    parseFiltersFromURL(new URLSearchParams(searchParams?.toString() || ""))
  );
  const [items, setItems] = useState<WatchedRow[]>([]);
  const [page, setPage] = useState(1);
  const [paginationInfo, setPaginationInfo] = useState({
    totalPages: 1,
    hasNext: false,
    hasPrevious: false,
  });
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingTmdbId, setPendingTmdbId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // Watched titles the user actually has, ignoring the active filters. Lets
  // the empty state tell "you haven't watched anything yet" apart from
  // "your filters match nothing" instead of guessing from the empty page.
  const [hasAnyWatched, setHasAnyWatched] = useState(false);

  // A filter is "active" whenever anything other than the default All type is set.
  const hasActiveFilters =
    filters.q.trim() !== "" ||
    filters.content_type !== "all" ||
    filters.genre !== "" ||
    filters.year_min !== "" ||
    filters.year_max !== "" ||
    filters.actors.length > 0 ||
    filters.providers.length > 0 ||
    filters.country !== "" ||
    filters.status !== "" ||
    filters.quality !== "" ||
    filters.score_min !== "";

  const resetFilters = () => handleSetFilters(EMPTY_FILTERS);

  // Keep URL in sync with filters (same convention as /watchlist).
  useEffect(() => {
    const qs = buildWatchedQuery(filters, page);
    router.replace(qs ? `/watched/?${qs}` : "/watched/", { scroll: false });
  }, [filters, page, router]);

  const handleSetFilters = (newFilters: FilterState) => {
    setPage(1);
    setFilters(newFilters);
  };

  const loadItems = () => {
    setLoading(true);
    const params = new URLSearchParams();
    params.set("page", page.toString());
    params.set("page_size", "24");
    if (filters.q) params.set("q", filters.q);
    if (filters.content_type !== "all") params.set("type", filters.content_type);
    if (filters.genre) params.set("genre", filters.genre);
    if (filters.year_min) params.set("year_min", filters.year_min);
    if (filters.year_max) params.set("year_max", filters.year_max);
    if (filters.providers.length > 0) params.set("provider", filters.providers.join(","));
    if (filters.country) params.set("country", filters.country);
    if (filters.status) params.set("status", filters.status);
    if (filters.quality) params.set("quality", filters.quality);
    if (filters.score_min) params.set("score_min", filters.score_min);
    if (filters.sort_by) params.set("sort_by", filters.sort_by);
    filters.actors.forEach((a) => params.append("actor_id", a.id.toString()));
    if (currentCountry) params.set("current_country", currentCountry);

    setError(null);
    fetch(`/api/watched/report/?${params.toString()}`)
      .then(async (res) => {
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(
            errData.error || `Failed to load watched titles (${res.status})`
          );
        }
        return res.json();
      })
      .then((data) => {
        const rows = Array.isArray(data) ? data : data.report || [];
        setItems(rows);
        const p = data.pagination || {};
        setPaginationInfo({
          totalPages: p.total_pages || 1,
          hasNext: !!p.has_next,
          hasPrevious: !!p.has_previous,
        });
        setCount(p.count || 0);

        // An empty filtered page is ambiguous on its own, so ask the API how
        // many watched titles exist in total (page 1, no filters, page_size=1).
        // Only needed when the page came back empty, and a failure here must
        // not break the page — the empty state then keeps its own wording.
        if (rows.length === 0) {
          const totalParams = new URLSearchParams();
          totalParams.set("page", "1");
          totalParams.set("page_size", "1");
          fetch(`/api/watched/report/?${totalParams.toString()}`)
            .then((res) => (res.ok ? res.json() : null))
            .then((total) => {
              if (total && total.pagination) {
                setHasAnyWatched((total.pagination.count || 0) > 0);
              }
            })
            .catch(() => {});
        } else {
          setHasAnyWatched(true);
        }
      })
      .catch((err: Error) => {
        setError(err.message || "Failed to load watched titles");
        setItems([]);
      })
      .finally(() => setLoading(false));
  };

  useEffect(loadItems, [page, filters]);

  // The Watchlist's own unmark action, pointed at this list: unwatching
  // removes the row from Watched (the backend flips is_watched, the same flag
  // this page reads), so no separate workflow is introduced.
  const handleUnwatch = async (tmdbId: number) => {
    setPendingTmdbId(tmdbId);
    setActionError(null);
    try {
      await unmarkMovieWatched(tmdbId);
      setItems((prev) => prev.filter((row) => row.movie?.tmdb_id !== tmdbId));
      setCount((prev) => Math.max(0, prev - 1));
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Failed to remove from Watched"
      );
    } finally {
      setPendingTmdbId(null);
    }
  };

  return (
    <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
          Watched
        </h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Everything you have marked as watched — your viewing history. Titles
          you still want to watch live in{" "}
          <Link
            href="/watchlist"
            className="font-medium text-indigo-600 hover:underline dark:text-indigo-400"
          >
            Watchlist
          </Link>
          .
        </p>
      </div>

      <FilterBar
        filters={filters}
        onChange={handleSetFilters}
        showStatusFilter={true}
      />

      {actionError && (
        <div className="p-4 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-xl text-red-800 dark:text-red-200 text-sm font-medium">
          {actionError}
        </div>
      )}

      {error ? (
        <div className="p-4 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-xl text-red-800 dark:text-red-200 text-sm font-medium">
          {error}
        </div>
      ) : loading ? (
        <div className="py-12 text-center text-gray-500 animate-pulse">
          Loading watched titles...
        </div>
      ) : items.length === 0 ? (
        <div className="py-12 text-center border border-dashed border-gray-300 dark:border-gray-800 rounded-xl p-8 space-y-2">
          {hasAnyWatched ? (
            <>
              <p className="text-gray-600 dark:text-gray-400 font-medium">
                No watched titles match your filters.
              </p>
              <p className="text-sm text-gray-500 dark:text-gray-400">
                You have watched titles, but none of them match the current
                search and filters.
              </p>
              {hasActiveFilters && (
                <button
                  type="button"
                  onClick={resetFilters}
                  className="mt-2 inline-flex items-center justify-center rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                >
                  Clear filters
                </button>
              )}
            </>
          ) : (
            <>
              <p className="text-gray-600 dark:text-gray-400 font-medium">
                Nothing watched yet.
              </p>
              <p className="text-sm text-gray-500 dark:text-gray-400">
                Mark a title as watched from the{" "}
                <Link
                  href="/watchlist"
                  className="font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                >
                  Watchlist
                </Link>{" "}
                and it will show up here.
              </p>
            </>
          )}
        </div>
      ) : (
        <>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {count} watched {count === 1 ? "title" : "titles"}
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
            {items.map((row, idx) => {
              const movie = row.movie || {};
              if (!movie.tmdb_id) return null;
              const isWatched = !!(
                row.user_data?.is_watched ?? row.is_watched ?? true
              );
              return (
                <div key={movie.id || movie.tmdb_id || idx} className="flex flex-col gap-2">
                  <MovieCard
                    movie={{
                      tmdb_id: movie.tmdb_id,
                      title: movie.title || "Untitled",
                      year: movie.year,
                      poster_path: movie.poster_path,
                      content_type: movie.content_type,
                      is_watched: isWatched,
                      rating: row.user_data?.rating ?? null,
                    }}
                  />
                  <button
                    onClick={() => handleUnwatch(movie.tmdb_id as number)}
                    disabled={pendingTmdbId === movie.tmdb_id}
                    className="w-full py-1.5 text-xs font-medium rounded-lg border border-emerald-300 dark:border-emerald-700 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-50 dark:hover:bg-emerald-800/30 transition-colors disabled:opacity-50"
                  >
                    {pendingTmdbId === movie.tmdb_id
                      ? "Removing…"
                      : "✓ Remove from Watched"}
                  </button>
                </div>
              );
            })}
          </div>

          <Pagination
            page={page}
            totalPages={paginationInfo.totalPages}
            hasNext={paginationInfo.hasNext}
            hasPrevious={paginationInfo.hasPrevious}
            onPageChange={setPage}
          />
        </>
      )}
    </div>
  );
}