"use client";

import { useState, useCallback } from "react";
import OfferList, { Offer } from "@/components/OfferList";

/* ---------- Types ---------- */

interface SearchResult {
  tmdb_id: number;
  title: string;
  year: number | null;
}

interface AvailabilityResponse {
  movie: { tmdb_id: number; title: string; year: number | null };
  cache: { refreshed: boolean; reason: string; offers: number };
  offers: Offer[];
}

/* ---------- Helpers ---------- */

/* ---------- SearchPage ---------- */

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");

  const [selected, setSelected] = useState<SearchResult | null>(null);
  const [offers, setOffers] = useState<Offer[]>([]);
  const [availLoading, setAvailLoading] = useState(false);
  const [availError, setAvailError] = useState("");
  const [cacheInfo, setCacheInfo] = useState<AvailabilityResponse["cache"] | null>(null);

  /* --- Search --- */
  const doSearch = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setSearchError("");
    setResults([]);
    setSelected(null);
    setOffers([]);
    setCacheInfo(null);
    setAvailError("");

    try {
      const res = await fetch(`/api/search/?q=${encodeURIComponent(q)}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error ?? `Search failed (${res.status})`);
      }
      const data = await res.json();
      setResults(data.results ?? []);
    } catch (err: unknown) {
      setSearchError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setSearching(false);
    }
  }, [query]);

  /* --- Availability --- */
  const fetchAvailability = useCallback(async (movie: SearchResult) => {
    setSelected(movie);
    setOffers([]);
    setCacheInfo(null);
    setAvailLoading(true);
    setAvailError("");

    try {
      const res = await fetch(`/api/movie/${movie.tmdb_id}/availability/`);
      if (res.status === 404) {
        setAvailError("Movie not tracked yet — availability data hasn't been fetched.");
        return;
      }
      if (res.status === 429) {
        const body = await res.json().catch(() => ({}));
        setAvailError(body.error ?? "Rate limited — try again later.");
        return;
      }
      if (!res.ok) {
        throw new Error(`Failed to load availability (${res.status})`);
      }
      const data: AvailabilityResponse = await res.json();
      setOffers(data.offers);
      setCacheInfo(data.cache);
    } catch (err: unknown) {
      setAvailError(err instanceof Error ? err.message : "Failed to load availability");
    } finally {
      setAvailLoading(false);
    }
  }, []);

  /* --- Keyboard shortcut --- */
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") doSearch();
  };

  return (
    <main className="mx-auto max-w-2xl px-4 py-10 sm:px-6">
      {/* Header */}
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-bold tracking-tight">Where TF Do I Watch It</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Find where to stream any movie — ranked by your preferences
        </p>
      </div>

      {/* Search bar */}
      <div className="flex gap-2">
        <input
          type="text"
          placeholder="Search for a movie…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          className="flex-1 rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm
                     placeholder-gray-400 shadow-sm
                     focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20
                     dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100
                     dark:placeholder-gray-500 dark:focus:border-blue-400"
        />
        <button
          onClick={doSearch}
          disabled={searching || !query.trim()}
          className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm
                     hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50
                     dark:bg-blue-500 dark:hover:bg-blue-600"
        >
          {searching ? "Searching…" : "Search"}
        </button>
      </div>

      {/* Search error */}
      {searchError && (
        <div className="mt-4 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700
                        dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {searchError}
        </div>
      )}

      {/* Search results */}
      {results.length > 0 && (
        <div className="mt-6">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Results
          </h2>
          <ul className="divide-y divide-gray-200 rounded-lg border border-gray-200
                         dark:divide-gray-700 dark:border-gray-700">
            {results.map((r) => (
              <li key={r.tmdb_id}>
                <button
                  onClick={() => fetchAvailability(r)}
                  className={`flex w-full items-center justify-between px-4 py-3 text-left text-sm
                              transition-colors hover:bg-gray-50 dark:hover:bg-gray-800
                              ${selected?.tmdb_id === r.tmdb_id
                                ? "bg-blue-50 dark:bg-blue-900/20"
                                : ""}`}
                >
                  <span className="font-medium">{r.title}</span>
                  <span className="ml-2 text-xs text-gray-400">{r.year ?? "—"}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* No results */}
      {!searching && results.length === 0 && query.trim() && !searchError && (
        <p className="mt-6 text-center text-sm text-gray-500 dark:text-gray-400">
          No results — try a different title.
        </p>
      )}

      {/* Availability section */}
      {selected && (
        <div className="mt-8">
          <h2 className="mb-1 text-lg font-semibold">
            {selected.title}
            {selected.year && (
              <span className="ml-1 text-base font-normal text-gray-400">
                ({selected.year})
              </span>
            )}
          </h2>

          {cacheInfo && (
            <p className="mb-4 text-xs text-gray-400 dark:text-gray-500">
              {cacheInfo.refreshed ? "Refreshed just now" : "Cached"} ·{" "}
              {cacheInfo.offers} offer{cacheInfo.offers !== 1 && "s"} stored
            </p>
          )}

          {/* Loading */}
          {availLoading && (
            <div className="flex items-center gap-2 py-6 text-sm text-gray-500">
              <svg
                className="h-4 w-4 animate-spin"
                viewBox="0 0 24 24"
                fill="none"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
                />
              </svg>
              Loading availability…
            </div>
          )}

          {/* Availability error */}
          {availError && (
            <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800
                            dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
              {availError}
            </div>
          )}

          {/* Offers */}
          {!availLoading && !availError && offers.length > 0 && (
            <OfferList
              tmdbId={selected.tmdb_id}
              offers={offers}
              onRefreshAvailability={() => fetchAvailability(selected)}
            />
          )}

          {/* No offers */}
          {!availLoading && !availError && offers.length === 0 && cacheInfo && (
            <p className="py-4 text-center text-sm text-gray-500 dark:text-gray-400">
              No streaming offers found for your preferences.
            </p>
          )}
        </div>
      )}
    </main>
  );
}
