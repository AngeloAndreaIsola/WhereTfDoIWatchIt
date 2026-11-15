"use client";

import { useEffect, useState, useRef } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { FilterBar, FilterState } from "@/components/FilterBar";
import { useCurrentLocation } from "@/components/CurrentLocationProvider";
import { Pagination } from "@/components/Pagination";
import { unmarkMovieWatched, fetchWatchedReport, type WatchedReportItem } from "@/lib/api";
type WatchedRow = WatchedReportItem;

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
  watched: "all",
  access: "",
  duration_min: "",
  duration_max: "",
  audio_languages: "",
  subtitle_languages: "",
  provider_categories: [],
};

const parseFiltersFromURL = (sp: URLSearchParams): FilterState => {
  const providers = sp.get("providers");
  const actorIds = sp.getAll("actor_id");
  const actorNames = sp.getAll("actor_name");
  const sort = sp.get("sort_by") || sp.get("sort") || "";
  const sortBy = sort === "last_watched" ? "" :
    sort === "title" ? "title_asc" :
    sort === "rating" ? "watched_rating_desc" : sort;
  return {
    ...EMPTY_FILTERS,
    q: sp.get("q") || sp.get("search") || "",
    content_type: sp.get("type") || sp.get("content_type") || "all",
    genre: sp.get("genre") || "",
    year_min: sp.get("year_min") || "",
    year_max: sp.get("year_max") || "",
    actors: actorIds.map((raw, index) => {
      const [id, oldName] = raw.split(":");
      return { id: Number(id), name: actorNames[index] || oldName || `Actor ${id}` };
    }).filter((actor) => Number.isSafeInteger(actor.id) && actor.id > 0),
    providers: providers ? providers.split(",") : [],
    country: sp.get("country") || "",
    status: sp.get("status") || "",
    quality: sp.get("quality") || "",
    score_min: sp.get("score_min") || "",
    sort_by: sortBy,
    watched: sp.get("watched") || "all",
    access: sp.get("access") || "",
    duration_min: sp.get("duration_min") || "",
    duration_max: sp.get("duration_max") || "",
    audio_languages: "",
    subtitle_languages: "",
    provider_categories: [],
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
  if (filters.watched && filters.watched !== "all")
    params.set("watched", filters.watched);
  if (filters.access) params.set("access", filters.access);
  if (filters.duration_min) params.set("duration_min", filters.duration_min);
  if (filters.duration_max) params.set("duration_max", filters.duration_max);
  filters.actors.forEach((actor) => {
    params.append("actor_id", String(actor.id));
    params.append("actor_name", actor.name);
  });
  if (page > 1) params.set("page", page.toString());
  return params.toString();
};

const KNOWN_KEYS = [
  "q", "search", "type", "content_type", "genre", "year_min", "year_max",
  "providers", "country", "status", "quality", "score_min", "sort_by",
  "sort", "watched", "access", "duration_min", "duration_max",
  "actor_id", "actor_name", "page",
];

const parsePage = (sp: URLSearchParams) => {
  const n = Number(sp.get("page") || "1");
  return Number.isSafeInteger(n) && n > 0 ? n : 1;
};

export default function WatchedPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { currentCountry } = useCurrentLocation();

  const [filters, setFilters] = useState<FilterState>(() =>
    parseFiltersFromURL(new URLSearchParams(searchParams?.toString() || ""))
  );
  const [page, setPage] = useState(() =>
    parsePage(new URLSearchParams(searchParams?.toString() || ""))
  );
  const [items, setItems] = useState<WatchedRow[]>([]);
  const [paginationInfo, setPaginationInfo] = useState({
    totalPages: 1,
    hasNext: false,
    hasPrevious: false,
  });
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

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
    filters.score_min !== "" ||
    filters.duration_min !== "" ||
    filters.duration_max !== "";

  const resetFilters = () => handleSetFilters(EMPTY_FILTERS);

  // Browser navigation updates applied filters; local interactions explicitly
  // write the URL so back/forward is not overwritten by stale state.
  const queryFromURL = searchParams?.toString() || "";
  useEffect(() => {
    const params = new URLSearchParams(queryFromURL);
    const restored = parseFiltersFromURL(params);
    setFilters((current) =>
      JSON.stringify(current) === JSON.stringify(restored) ? current : restored
    );
    const restoredPage = parsePage(params);
    setPage((current) => current === restoredPage ? current : restoredPage);
  }, [queryFromURL]);

  const replaceWatchedURL = (nextFilters: FilterState, nextPage: number) => {
    const params = new URLSearchParams(searchParams?.toString() || "");
    KNOWN_KEYS.forEach((key) => params.delete(key));
    new URLSearchParams(buildWatchedQuery(nextFilters, nextPage)).forEach(
      (value, key) => params.append(key, value)
    );
    const query = params.toString();
    router.replace(query ? `/watched/?${query}` : "/watched/", { scroll: false });
  };

  const handleSetFilters = (nextFilters: FilterState) => {
    setPage(1);
    setFilters(nextFilters);
    replaceWatchedURL(nextFilters, 1);
  };
  const handlePageChange = (nextPage: number) => {
    setPage(nextPage);
    replaceWatchedURL(filters, nextPage);
  };

  // Fetch with abort (stale-request cancellation).
  useEffect(() => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    const params = new URLSearchParams(buildWatchedQuery(filters, page));
    params.set("page", String(page));
    params.set("page_size", "24");
    if (currentCountry) params.set("current_country", currentCountry);
    setLoading(true);
    setError(null);
    fetchWatchedReport(params, ac.signal)
      .then((data) => {
        if (ac.signal.aborted) return;
        setItems(data.report || []);
        const p = data.pagination;
        setPaginationInfo({
          totalPages: p?.total_pages || 1,
          hasNext: !!p?.has_next,
          hasPrevious: !!p?.has_previous,
        });
        setCount(p?.count || 0);
      })
      .catch((err: Error) => {
        if (ac.signal.aborted) return;
        setError(err.message || "Failed to load watched titles");
        setItems([]);
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
  }, [page, filters, currentCountry]);

  const detailHref = (r: WatchedRow) =>
    r.movie.content_type === "series" ? `/tv/${r.movie.tmdb_id}` : `/movie/${r.movie.tmdb_id}`;

  const handleUnwatch = async (tmdbId: number) => {
    try {
      await unmarkMovieWatched(tmdbId);
      setItems((prev) => prev.filter((row) => row.movie?.tmdb_id !== tmdbId));
      setCount((prev) => Math.max(0, prev - 1));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to remove from Watched"
      );
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
        showWatchedRatingSort={true}
      />

      {error && (
        <div className="p-4 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-xl text-red-800 dark:text-red-200 text-sm font-medium">
          Error: {error}
        </div>
      )}

      {loading ? (
        <div className="py-12 text-center text-gray-500 animate-pulse">
          Loading watched titles…
        </div>
      ) : error ? null : items.length === 0 ? (
        <div className="py-12 text-center border border-dashed border-gray-300 dark:border-gray-800 rounded-xl p-8 space-y-2">
          <p className="text-gray-600 dark:text-gray-400 font-medium">
            {hasActiveFilters
              ? "No watched titles match your filters."
              : "No watched titles yet."}
          </p>
          {hasActiveFilters && (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              You have watched titles, but none of them match the current
              search and filters.
            </p>
          )}
          {hasActiveFilters && (
            <button
              type="button"
              onClick={resetFilters}
              className="mt-2 inline-flex items-center justify-center rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
            >
              Clear filters
            </button>
          )}
          {!hasActiveFilters && (
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
              const watchRes = row.watchability || {};
              const symbol = watchRes.status_symbol || "⚪";
              const label = watchRes.status_label || "Unknown";

              return (
                <div
                  key={movie.tmdb_id || idx}
                  className="flex flex-col gap-2 relative"
                >
                  <Link
                    href={detailHref(row)}
                    className="border rounded p-2 flex flex-col gap-1"
                  >
                    <div className="font-semibold">{movie.title || "Untitled"}</div>
                    <div className="text-sm text-gray-500">
                      {movie.year} · {movie.content_type}
                    </div>
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-bold bg-black/60 text-white">
                      {symbol} {label}
                    </span>
                    {row.user_data?.rating != null && (
                      <div className="text-sm">★ {row.user_data.rating}</div>
                    )}
                    {row.last_watched_at && (
                      <div className="text-xs text-gray-400">{row.last_watched_at}</div>
                    )}
                    {row.watch_count != null && (
                      <div className="text-xs text-gray-400">watches: {row.watch_count}</div>
                    )}
                  </Link>
                  <button
                    onClick={() => handleUnwatch(movie.tmdb_id as number)}
                    className="w-full py-1.5 text-xs font-medium rounded-lg border border-emerald-300 dark:border-emerald-700 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-50 dark:hover:bg-emerald-800/30 transition-colors"
                  >
                    ✓ Remove from Watched
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
            onPageChange={handlePageChange}
          />
        </>
      )}
    </div>
  );
}
