"use client";

import React, { useState, useEffect, useCallback } from "react";
import Link from "next/link";

/* ---------- Types ---------- */

interface Movie {
  id: number;
  tmdb_id: number;
  title: string;
  year: number | null;
}

interface WatchlistItem {
  id: number;
  movie: Movie;
  source: string;
  added_at: string;
}

interface Candidate {
  tmdb_id: number;
  title: string;
  year: number | null;
}

interface AmbiguousItem {
  title: string;
  year: number | null;
  uri?: string | null;
  candidates: Candidate[];
  reason?: string;
}

interface SkippedItem {
  title: string;
  year: number | null;
  uri?: string | null;
  reason?: string;
}

interface UploadResult {
  resolved: WatchlistItem[];
  ambiguous: AmbiguousItem[];
  skipped: SkippedItem[];
}

interface RankingInfo {
  audio_rank: number;
  subtitle_rank: number;
  quality_rank: number;
}

interface Offer {
  country: string;
  service: string;
  offer_type: string;
  quality: string;
  audio_languages: string[];
  subtitle_languages: string[];
  ranking: RankingInfo;
}

interface AvailabilityResponse {
  movie: { tmdb_id: number; title: string; year: number | null };
  cache: { refreshed: boolean; reason: string; offers: number };
  offers: Offer[];
}

interface CachedAvailability {
  loading: boolean;
  error: string;
  data: AvailabilityResponse | null;
}

/* ---------- Helpers ---------- */

function qualityLabel(q: string): string {
  const map: Record<string, string> = {
    "4k": "4K",
    uhd: "4K",
    hdr: "HDR",
    hd: "HD",
    sd: "SD",
  };
  return map[q.toLowerCase()] ?? q.toUpperCase();
}

function rankBadge(rank: number): string {
  if (rank === 0) return "★";
  if (rank < 99) return `#${rank + 1}`;
  return "—";
}

export default function WatchlistPage() {
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [loadingWatchlist, setLoadingWatchlist] = useState(true);
  const [watchlistError, setWatchlistError] = useState("");

  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);

  const [expandedTmdbId, setExpandedTmdbId] = useState<number | null>(null);
  const [availabilityCache, setAvailabilityCache] = useState<
    Record<number, CachedAvailability>
  >({});

  /* --- Fetch existing watchlist --- */
  const fetchWatchlist = useCallback(async () => {
    setLoadingWatchlist(true);
    setWatchlistError("");
    try {
      const res = await fetch("/api/watchlist/");
      if (res.status === 429) {
        const body = await res.json().catch(() => ({}));
        setWatchlistError(body.error ?? "Rate limited — try again later.");
        return;
      }
      if (!res.ok) {
        throw new Error(`Failed to load watchlist (${res.status})`);
      }
      const data: WatchlistItem[] = await res.json();
      setWatchlist(data);
    } catch (err: unknown) {
      setWatchlistError(
        err instanceof Error ? err.message : "Failed to load watchlist"
      );
    } finally {
      setLoadingWatchlist(false);
    }
  }, []);

  useEffect(() => {
    fetchWatchlist();
  }, [fetchWatchlist]);

  /* --- Upload CSV --- */
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setSelectedFile(e.target.files[0]);
    } else {
      setSelectedFile(null);
    }
  };

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile) return;

    setUploading(true);
    setUploadError("");
    setUploadResult(null);

    const formData = new FormData();
    formData.append("file", selectedFile);

    try {
      const res = await fetch("/api/watchlist/", {
        method: "POST",
        body: formData,
      });

      if (res.status === 429) {
        const body = await res.json().catch(() => ({}));
        setUploadError(body.error ?? "Rate limited — try again later.");
        return;
      }

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error ?? `Upload failed (${res.status})`);
      }

      const data: UploadResult = await res.json();
      setUploadResult(data);
      // Refresh watchlist items after successful import
      await fetchWatchlist();
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  /* --- Toggle availability on-demand --- */
  const toggleAvailability = async (tmdb_id: number) => {
    if (expandedTmdbId === tmdb_id) {
      setExpandedTmdbId(null);
      return;
    }

    setExpandedTmdbId(tmdb_id);

    // If already cached and loaded successfully, don't refetch
    if (availabilityCache[tmdb_id] && !availabilityCache[tmdb_id].error) {
      return;
    }

    setAvailabilityCache((prev) => ({
      ...prev,
      [tmdb_id]: { loading: true, error: "", data: null },
    }));

    try {
      const res = await fetch(`/api/movie/${tmdb_id}/availability/`);

      if (res.status === 404) {
        setAvailabilityCache((prev) => ({
          ...prev,
          [tmdb_id]: {
            loading: false,
            error:
              "Movie is tracked but availability has not been fetched yet.",
            data: null,
          },
        }));
        return;
      }

      if (res.status === 429) {
        const body = await res.json().catch(() => ({}));
        setAvailabilityCache((prev) => ({
          ...prev,
          [tmdb_id]: {
            loading: false,
            error: body.error ?? "Daily API limit reached; try again later.",
            data: null,
          },
        }));
        return;
      }

      if (!res.ok) {
        throw new Error(`Failed to load availability (${res.status})`);
      }

      const data: AvailabilityResponse = await res.json();
      setAvailabilityCache((prev) => ({
        ...prev,
        [tmdb_id]: { loading: false, error: "", data },
      }));
    } catch (err: unknown) {
      setAvailabilityCache((prev) => ({
        ...prev,
        [tmdb_id]: {
          loading: false,
          error:
            err instanceof Error
              ? err.message
              : "Failed to load availability",
          data: null,
        },
      }));
    }
  };

  return (
    <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
      {/* Header */}
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-bold tracking-tight">Letterboxd Watchlist</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Import your watchlist CSV and check streaming availability
        </p>
      </div>

      {/* 1. CSV Upload Form */}
      <section className="mb-8 rounded-lg border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-700 dark:bg-gray-800">
        <h2 className="mb-4 text-lg font-semibold">Upload Watchlist CSV</h2>
        <form onSubmit={handleUpload} className="space-y-4">
          <div>
            <input
              type="file"
              accept=".csv"
              onChange={handleFileChange}
              className="block w-full text-sm text-gray-500
                         file:mr-4 file:rounded-lg file:border-0
                         file:bg-blue-50 file:px-4 file:py-2
                         file:text-sm file:font-semibold file:text-blue-700
                         hover:file:bg-blue-100
                         dark:text-gray-400 dark:file:bg-blue-900/30 dark:file:text-blue-400"
            />
          </div>

          <button
            type="submit"
            disabled={!selectedFile || uploading}
            className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm
                       hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50
                       dark:bg-blue-500 dark:hover:bg-blue-600"
          >
            {uploading ? "Uploading…" : "Import Watchlist"}
          </button>
        </form>

        {uploadError && (
          <div className="mt-4 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
            {uploadError}
          </div>
        )}
      </section>

      {/* 2. Upload Result Summary & Ambiguous Banner */}
      {uploadResult && (
        <section className="mb-8 space-y-4">
          <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800/50">
            <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              Import Summary
            </h3>
            <div className="flex gap-4 text-sm font-medium">
              <span className="text-green-600 dark:text-green-400">
                Resolved: {uploadResult.resolved.length}
              </span>
              <span className="text-amber-600 dark:text-amber-400">
                Ambiguous: {uploadResult.ambiguous.length}
              </span>
              <span className="text-gray-500 dark:text-gray-400">
                Skipped: {uploadResult.skipped.length}
              </span>
            </div>
          </div>

          {/* Ambiguous Review Banner */}
          {uploadResult.ambiguous.length > 0 && (
            <div className="flex items-center justify-between rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-700 dark:bg-amber-900/30">
              <div>
                <h4 className="font-semibold text-amber-800 dark:text-amber-300">
                  {uploadResult.ambiguous.length} Ambiguous Match
                  {uploadResult.ambiguous.length > 1 ? "es" : ""}
                </h4>
                <p className="text-xs text-amber-700 dark:text-amber-400">
                  Some titles could not be automatically matched to TMDb.
                </p>
              </div>
              <Link
                href={`/watchlist/ambiguous?items=${encodeURIComponent(
                  JSON.stringify(uploadResult.ambiguous)
                )}`}
                className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-amber-700 dark:bg-amber-500 dark:hover:bg-amber-600"
              >
                Review ambiguous matches
              </Link>
            </div>
          )}

          {/* Skipped Details */}
          {uploadResult.skipped.length > 0 && (
            <div className="rounded-lg border border-gray-200 bg-white p-4 text-sm dark:border-gray-700 dark:bg-gray-800">
              <h4 className="mb-2 font-semibold text-gray-700 dark:text-gray-300">
                Skipped Items
              </h4>
              <ul className="space-y-1 text-xs text-gray-500 dark:text-gray-400">
                {uploadResult.skipped.map((item, idx) => (
                  <li key={idx}>
                    <span className="font-medium">{item.title}</span>
                    {item.year && <span> ({item.year})</span>} — Reason:{" "}
                    {item.reason ?? "no_match"}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {/* 3. Watchlist Items Section */}
      <section>
        <h2 className="mb-4 text-xl font-semibold">Watchlist Items</h2>

        {watchlistError && (
          <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
            {watchlistError}
          </div>
        )}

        {loadingWatchlist && (
          <p className="py-6 text-center text-sm text-gray-500 dark:text-gray-400">
            Loading watchlist…
          </p>
        )}

        {!loadingWatchlist && watchlist.length === 0 && (
          <p className="py-6 text-center text-sm text-gray-500 dark:text-gray-400">
            Your watchlist is empty. Upload a CSV above to get started.
          </p>
        )}

        {!loadingWatchlist && watchlist.length > 0 && (
          <div className="space-y-3">
            {watchlist.map((item) => {
              const tmdbId = item.movie.tmdb_id;
              const isExpanded = expandedTmdbId === tmdbId;
              const cache = availabilityCache[tmdbId];

              return (
                <div
                  key={item.id}
                  className="rounded-lg border border-gray-200 bg-white shadow-sm transition-colors dark:border-gray-700 dark:bg-gray-800"
                >
                  <button
                    onClick={() => toggleAvailability(tmdbId)}
                    className="flex w-full items-center justify-between p-4 text-left text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50"
                  >
                    <div>
                      <span className="text-base font-semibold">
                        {item.movie.title}
                      </span>
                      {item.movie.year && (
                        <span className="ml-2 text-sm text-gray-500 dark:text-gray-400">
                          ({item.movie.year})
                        </span>
                      )}
                    </div>
                    <span className="text-xs text-blue-600 dark:text-blue-400">
                      {isExpanded ? "Hide availability ▲" : "View availability ▼"}
                    </span>
                  </button>

                  {/* Expanded Availability Panel */}
                  {isExpanded && (
                    <div className="border-t border-gray-100 bg-gray-50/50 p-4 dark:border-gray-700 dark:bg-gray-800/80">
                      {cache?.loading && (
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                          Loading availability…
                        </p>
                      )}

                      {cache?.error && (
                        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
                          {cache.error}
                        </div>
                      )}

                      {cache?.data && (
                        <div>
                          {cache.data.offers.length === 0 ? (
                            <p className="text-xs text-gray-500 dark:text-gray-400">
                              No streaming offers found for your preferences.
                            </p>
                          ) : (
                            <div className="space-y-2">
                              {cache.data.offers.map((o, idx) => (
                                <div
                                  key={`${o.country}-${o.service}-${o.quality}-${idx}`}
                                  className={`rounded-lg border p-3 text-xs transition-colors ${
                                    idx === 0
                                      ? "border-green-400 bg-green-50 dark:border-green-600 dark:bg-green-900/20"
                                      : "border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800"
                                  }`}
                                >
                                  <div className="flex items-start justify-between gap-2">
                                    <div>
                                      <span className="font-semibold text-sm">
                                        {o.service}
                                      </span>
                                      <span className="ml-2 rounded bg-gray-100 px-1.5 py-0.5 font-medium text-gray-600 dark:bg-gray-700 dark:text-gray-300">
                                        {o.country}
                                      </span>
                                      <span className="ml-2 text-gray-400">
                                        {o.offer_type}
                                      </span>
                                    </div>
                                    <div className="flex items-center gap-2">
                                      {o.quality && (
                                        <span className="rounded bg-gray-200 px-1.5 py-0.5 font-mono font-bold dark:bg-gray-600 dark:text-gray-200">
                                          {qualityLabel(o.quality)}
                                        </span>
                                      )}
                                      {idx === 0 && (
                                        <span className="rounded-full bg-green-600 px-2 py-0.5 font-bold text-white">
                                          Best
                                        </span>
                                      )}
                                    </div>
                                  </div>

                                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-gray-500 dark:text-gray-400">
                                    <span>
                                      🔊 Audio:{" "}
                                      {o.audio_languages.length > 0
                                        ? o.audio_languages.join(", ")
                                        : "—"}
                                      <span className="ml-1 text-gray-300 dark:text-gray-600">
                                        {rankBadge(o.ranking.audio_rank)}
                                      </span>
                                    </span>
                                    <span>
                                      💬 Subs:{" "}
                                      {o.subtitle_languages.length > 0
                                        ? o.subtitle_languages.join(", ")
                                        : "—"}
                                      <span className="ml-1 text-gray-300 dark:text-gray-600">
                                        {rankBadge(o.ranking.subtitle_rank)}
                                      </span>
                                    </span>
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>
    </main>
  );
}
