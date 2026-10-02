"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { FilterBar, FilterState } from "@/components/FilterBar";
import { useCurrentLocation } from "@/components/CurrentLocationProvider";
import { VpnWatchModal } from "@/components/VpnWatchModal";
import { WhyCantIWatchModal, ExcludedOffer } from "@/components/WhyCantIWatchModal";
import { Pagination } from "@/components/Pagination";
import WatchlistRefreshButton from "@/components/WatchlistRefreshButton";
import {
  addToWatchlist,
  markMovieWatched,
  unmarkMovieWatched,
  removeWatchlistItem,
} from "@/lib/api";

interface WatchlistItemRow {
  movie?: {
    id?: number;
    tmdb_id?: number;
    title?: string;
    content_type?: string;
    year?: number;
    poster_path?: string;
    user_data?: {
      is_watched?: boolean;
    };
  };
  user_data?: {
    is_watched?: boolean;
    rating?: number | null;
    review?: string;
    updated_at?: string;
  } | null;
  is_watched?: boolean;
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
    excluded_offers?: ExcludedOffer[];
  };
}

const buildWatchlistQuery = (filters: FilterState, page: number): string => {
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

export default function WatchlistPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { currentCountry } = useCurrentLocation();

  const parseFiltersFromURL = (sp: URLSearchParams): FilterState => {
    const providers = sp.get("providers");
    const actors = sp.getAll("actor_id");
    return {
      q: sp.get("q") || "",
      content_type: sp.get("type") || "all",
      genre: sp.get("genre") || "",
      year_min: sp.get("year_min") || "",
      year_max: sp.get("year_max") || "",
      actors: actors.map((a) => {
        const [id, name] = a.split(":");
        return { id: Number(id), name: name || "" };
      }).filter((a) => a.id),
      providers: providers ? providers.split(",") : [],
      country: sp.get("country") || "",
      status: sp.get("status") || "",
      quality: sp.get("quality") || "",
      score_min: sp.get("score_min") || "",
      sort_by: sp.get("sort_by") || "",
    };
  };

  const [filters, setFilters] = useState<FilterState>(() =>
    parseFiltersFromURL(new URLSearchParams(searchParams?.toString() || ""))
  );

  const [items, setItems] = useState<WatchlistItemRow[]>([]);
  const [page, setPage] = useState(1);
  const [paginationInfo, setPaginationInfo] = useState({
    totalPages: 1,
    hasNext: false,
    hasPrevious: false,
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [vpnModalState, setVpnModalState] = useState<{
    isOpen: boolean;
    requiredCountry: string;
    serviceName: string;
    watchUrl: string;
  }>({
    isOpen: false,
    requiredCountry: "US",
    serviceName: "",
    watchUrl: "",
  });

  const [whyModalState, setWhyModalState] = useState<{
    isOpen: boolean;
    title: string;
    explanations: string[];
    excludedOffers: ExcludedOffer[];
  }>({
    isOpen: false,
    title: "",
    explanations: [],
    excludedOffers: [],
  });

  // Keep URL in sync with filters
  useEffect(() => {
    const qs = buildWatchlistQuery(filters, page);
    const newUrl = qs ? `/watchlist/?${qs}` : "/watchlist/";
    router.replace(newUrl, { scroll: false });
  }, [filters, page, router]);

  const refreshWatchlistData = () => {
    setPage(1);
    setFilters({ ...filters });
  };

  const handleSetFilters = (newFilters: FilterState) => {
    setPage(1);
    setFilters(newFilters);
  };

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams();
    params.set("page", page.toString());
    params.set("page_size", "24");
    if (filters.q) params.set("q", filters.q);
    if (filters.content_type !== "all") params.set("type", filters.content_type);
    if (filters.genre) params.set("genre", filters.genre);
    if (filters.year_min) params.set("year_min", filters.year_min);
    if (filters.year_max) params.set("year_max", filters.year_max);
    if (filters.providers && filters.providers.length > 0) {
      params.set("provider", filters.providers.join(","));
    }
    if (filters.status) params.set("status", filters.status);
    if (filters.score_min) params.set("score_min", filters.score_min);
    if (filters.sort_by) params.set("sort_by", filters.sort_by);
    filters.actors.forEach((a) => params.append("actor_id", a.id.toString()));
    if (currentCountry) params.set("current_country", currentCountry);

    setError(null);
    const resPromise = fetch(`/api/watchlist/report/?${params.toString()}`);
    if (!resPromise || typeof resPromise.then !== "function") {
      setLoading(false);
      return;
    }

    resPromise
      .then(async (res) => {
        if (!res || !res.ok) {
          const errData =
            res && typeof res.json === "function"
              ? await res.json().catch(() => ({}))
              : {};
          throw new Error(
            errData.error ||
              `Failed to load watchlist (${res ? res.status : 500})`
          );
        }
        return res.json();
      })
      .then((data) => {
        // Handle array response for backwards compatibility or report wrapper
        const reportList = Array.isArray(data) ? data : data.report || [];
        setItems(reportList);
        const p = data.pagination || {};
        setPaginationInfo({
          totalPages: p.total_pages || 1,
          hasNext: !!p.has_next,
          hasPrevious: !!p.has_previous,
        });
      })
      .catch((err: Error) => {
        setError(err.message || "Failed to load watchlist");
        setItems([]);
      })
      .finally(() => setLoading(false));
  }, [page, filters]);

  const handleWatchClick = (
    bestOffer: { country: string; service: string; url: string; is_current_country: boolean }
  ) => {
    if (!bestOffer) return;
    if (bestOffer.is_current_country) {
      window.open(bestOffer.url, "_blank", "noopener,noreferrer");
    } else {
      setVpnModalState({
        isOpen: true,
        requiredCountry: bestOffer.country,
        serviceName: bestOffer.service,
        watchUrl: bestOffer.url,
      });
    }
  };

  const openWhyModal = (item: WatchlistItemRow) => {
    const movie = item.movie || {};
    const watchRes = item.watchability || {};
    setWhyModalState({
      isOpen: true,
      title: movie.title || "Untitled",
      explanations: watchRes.explanation || [
        "No qualifying subscription/provider found.",
      ],
      excludedOffers: watchRes.excluded_offers || [],
    });
  };

  // Manual watchlist management functions
  const handleAddToWatchlist = async (tmdbId: number, contentType: "movie" | "series" = "movie") => {
    try {
      const result = await addToWatchlist(tmdbId, contentType);
      console.log("Added to watchlist:", result);
      // Refresh watchlist data
      refreshWatchlistData();
    } catch (err) {
      console.error("Failed to add to watchlist:", err);
    }
  };

  const updateItemWatchedState = (
    tmdbId: number,
    userData: { is_watched?: boolean; rating?: number | null; review?: string; updated_at?: string } | null
  ) => {
    setItems((prev) =>
      prev.map((item) =>
        item.movie?.tmdb_id === tmdbId
          ? {
              ...item,
              is_watched: !!userData?.is_watched,
              user_data: userData,
              movie: {
                ...item.movie,
                user_data: userData ? { is_watched: !!userData.is_watched } : undefined,
              },
            }
          : item
      )
    );
  };

  const handleMarkWatched = async (tmdbId: number) => {
    try {
      const result = await markMovieWatched(tmdbId, {
        watched_date: new Date().toISOString().split('T')[0],
      });
      updateItemWatchedState(tmdbId, result.user_data ?? { is_watched: true });
    } catch (err) {
      console.error("Failed to mark as watched:", err);
    }
  };

  const handleUnmarkWatched = async (tmdbId: number) => {
    try {
      const result = await unmarkMovieWatched(tmdbId);
      updateItemWatchedState(tmdbId, result.user_data ?? { is_watched: false });
    } catch (err) {
      console.error("Failed to unmark as watched:", err);
    }
  };

  // Current filters/page as a query string, carried to the import page so
  // coming back restores the same view.
  const watchlistQuery = buildWatchlistQuery(filters, page);
  const importHref = watchlistQuery
    ? `/watchlist/import/?${watchlistQuery}`
    : "/watchlist/import";

  return (
    <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
          Wishlist / Watchlist
        </h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          All titles you want to watch. Unavailable items are greyed out with
          explanations.
        </p>
      </div>

      {/* ─── Import / Export entry point ───────────────────────────────────── */}
      <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-xs dark:border-gray-700 dark:bg-gray-800">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              Import / Export
            </h2>
            <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
              Import your watchlist from Letterboxd, Google, or CSV — or export
              a backup.
            </p>
          </div>
          <Link
            href={importHref}
            className="inline-flex shrink-0 items-center justify-center rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-xs hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600"
          >
            Import / Export
          </Link>
        </div>
      </section>

      {/* ─── Bulk availability refresh ─────────────────────────────────────── */}
      <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-xs dark:border-gray-700 dark:bg-gray-800">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              Availability
            </h2>
            <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
              Re-check every watchlisted title against the streaming
              catalogues now — queued in the background, so rate limits may
              spread it over time.
            </p>
          </div>
          <WatchlistRefreshButton onRefreshed={refreshWatchlistData} />
        </div>
      </section>

      <FilterBar
        filters={filters}
        onChange={handleSetFilters}
        showStatusFilter={true}
      />

      {error ? (
        <div className="p-4 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-xl text-red-800 dark:text-red-200 text-sm font-medium">
          {error}
        </div>
      ) : loading ? (
        <div className="py-12 text-center text-gray-500 animate-pulse">
          Loading wishlist items...
        </div>
      ) : items.length === 0 ? (
        <div className="py-12 text-center border border-dashed border-gray-300 dark:border-gray-800 rounded-xl p-8">
          <p className="text-gray-600 dark:text-gray-400 font-medium">
            Your wishlist is empty.
          </p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
            {items.map((row, idx) => {
              const movie = row.movie || {};
              const watchRes = row.watchability || {};
              const bestOffer = watchRes.best_offer;
              const status = watchRes.status || "unknown";
              const symbol = watchRes.status_symbol || "⚪";
              const label = watchRes.status_label || "Unknown";

              const isWatchable =
                status === "watchable_here" || status === "watchable_vpn";
              const isWatched = !!(row.user_data?.is_watched ?? row.is_watched ?? movie.user_data?.is_watched);

              return (
                <div
                  key={movie.id || idx}
                  className={`bg-white dark:bg-gray-900 border rounded-xl overflow-hidden shadow-sm flex flex-col transition-all ${
                    isWatchable
                      ? "border-gray-200 dark:border-gray-800 hover:shadow-md"
                      : "border-gray-200 dark:border-gray-800/60 opacity-60 grayscale hover:opacity-100 hover:grayscale-0"
                  }`}
                >
                  <Link
                    href={
                      movie.content_type === "series"
                        ? `/tv/${movie.tmdb_id}`
                        : `/movie/${movie.tmdb_id}`
                    }
                  >
                    <div className="aspect-[2/3] bg-gray-100 dark:bg-gray-800 relative">
                      {movie.poster_path ? (
                        <img
                          src={`https://image.tmdb.org/t/p/w500${movie.poster_path}`}
                          alt={movie.title || "Poster"}
                          className="w-full h-full object-cover"
                        />
                      ) : (
                        <div className="w-full h-full flex items-center justify-center text-gray-400 text-xs">
                          No Poster
                        </div>
                      )}
                      <span className="absolute top-2 right-2 px-2 py-1 rounded-md text-xs font-bold bg-black/70 text-white backdrop-blur-sm">
                        {symbol} {label}
                      </span>
                    </div>
                  </Link>

                  <div className="p-4 flex-1 flex flex-col justify-between space-y-3">
                    <div>
                      <h3 className="font-bold text-gray-900 dark:text-gray-100 line-clamp-1">
                        {movie.title}
                      </h3>
                      <p className="text-xs text-gray-500">
                        <span>
                          {movie.content_type === "series"
                            ? "TV Series"
                            : "Movie"}
                        </span>
                        {movie.year && <span> • {movie.year}</span>}
                        {movie.year && (
                          <span className="sr-only">{movie.year}</span>
                        )}
                      </p>
                    </div>

                    {isWatchable && bestOffer ? (
                      <div className="space-y-2">
                        <div className="bg-gray-50 dark:bg-gray-800/60 p-2.5 rounded-lg text-xs space-y-1">
                          <p className="font-bold text-gray-700 dark:text-gray-300">
                            Best way to watch:
                          </p>
                          <p className="text-indigo-600 dark:text-indigo-400 font-semibold">
                            {bestOffer.country
                              ? `${bestOffer.service_display || bestOffer.service} (${bestOffer.country})`
                              : bestOffer.service_display ||
                                bestOffer.service}
                          </p>
                        </div>
                        <button
                          onClick={() => handleWatchClick(bestOffer)}
                          className="w-full py-2 text-xs font-semibold rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white transition-colors"
                        >
                          Watch Now
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => openWhyModal(row)}
                        className="w-full py-2 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
                      >
                        Why can&apos;t I watch this?
                      </button>
                    )}

                    {/* Manual watchlist management buttons */}
                    <div className="space-y-2 mt-3">
                      {movie.tmdb_id ? (
                        <div className="space-y-2">
                          <div className="p-2 bg-green-50 dark:bg-green-900/30 rounded-lg text-xs text-green-700 dark:text-green-300">
                            ✓ In Watchlist
                          </div>
                          {isWatched ? (
                            <button
                              onClick={() => {
                                if (movie.tmdb_id) {
                                  handleUnmarkWatched(movie.tmdb_id);
                                }
                              }}
                              className="w-full py-2 text-xs font-medium rounded-lg border border-emerald-300 dark:border-emerald-700 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-50 dark:hover:bg-emerald-800/30 transition-colors"
                            >
                              ✓ Watched — Click to Unmark
                            </button>
                          ) : (
                            <button
                              onClick={() => {
                                if (movie.tmdb_id) {
                                  handleMarkWatched(movie.tmdb_id);
                                }
                              }}
                              className="w-full py-2 text-xs font-medium rounded-lg border border-purple-300 dark:border-purple-700 text-purple-700 dark:text-purple-300 hover:bg-purple-50 dark:hover:bg-purple-800/30 transition-colors"
                            >
                              ✓ Mark as Watched
                            </button>
                          )}
                          <button
                            onClick={() => {
                              if (movie.tmdb_id) {
                                removeWatchlistItem(movie.tmdb_id)
                                  .then(() => refreshWatchlistData())
                                  .catch(err => console.error('Failed to remove from watchlist:', err));
                              }
                            }}
                            className="w-full py-2 text-xs font-medium rounded-lg border border-red-300 dark:border-red-700 text-red-700 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-800/30 transition-colors"
                          >
                            ✕ Remove from Watchlist
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => {
                            if (movie?.tmdb_id) {
                              handleAddToWatchlist(
                                movie.tmdb_id,
                                movie.content_type === "series" ? "series" : "movie"
                              );
                            }
                          }}
                          className={`w-full py-2 text-xs font-medium rounded-lg border transition-colors ${
                            isWatchable
                              ? "border-green-300 dark:border-green-700 text-green-700 dark:text-green-300 bg-green-50 dark:bg-green-800/30 cursor-not-allowed"
                              : "border-green-300 dark:border-green-700 text-green-700 dark:text-green-300 hover:bg-green-50 dark:hover:bg-green-800/30"
                          }`}
                        >
                          + Add to Watchlist
                        </button>
                      )}
                    </div>
                  </div>
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

      <VpnWatchModal
        isOpen={vpnModalState.isOpen}
        onClose={() =>
          setVpnModalState({ ...vpnModalState, isOpen: false })
        }
        requiredCountry={vpnModalState.requiredCountry}
        serviceName={vpnModalState.serviceName}
        watchUrl={vpnModalState.watchUrl}
      />

      <WhyCantIWatchModal
        isOpen={whyModalState.isOpen}
        onClose={() =>
          setWhyModalState({ ...whyModalState, isOpen: false })
        }
        title={whyModalState.title}
        explanations={whyModalState.explanations}
        excludedOffers={whyModalState.excludedOffers}
      />
    </div>
  );
}
