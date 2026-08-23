"use client";

import React, { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import MovieCard, { MovieCardData } from "@/components/MovieCard";

/* ---------- Types ---------- */

interface Movie {
  id: number;
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path?: string | null;
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
  watched_count?: number;
  ratings_count?: number;
  reviews_count?: number;
  diary_events_count?: number;
}

interface ReportRow {
  movie: Movie;
  available_countries: string[];
  best_country: string | null;
  best_service: string | null;
  audio_languages?: string[];
  subtitle_languages?: string[];
}

interface PreferencesData {
  audio_languages: string[];
  subtitle_languages: string[];
}

export default function WatchlistPage() {
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [loadingWatchlist, setLoadingWatchlist] = useState(true);
  const [watchlistError, setWatchlistError] = useState("");

  const [reportMap, setReportMap] = useState<Record<number, ReportRow>>({});
  const [preferences, setPreferences] = useState<PreferencesData>({
    audio_languages: [],
    subtitle_languages: [],
  });

  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  /* --- Fetch existing watchlist, report, and preferences --- */
  const fetchWatchlistData = useCallback(async () => {
    setLoadingWatchlist(true);
    setWatchlistError("");
    try {
      // 1. Fetch watchlist items
      const res = await fetch("/api/watchlist/");
      if (res.status === 429) {
        const body = await res.json().catch(() => ({}));
        setWatchlistError(body.error ?? "Rate limited — try again later.");
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setWatchlistError(body.error ?? `Failed to load watchlist (${res.status})`);
        setWatchlist([]);
        return;
      }
      const data: WatchlistItem[] = await res.json();
      setWatchlist(data);

      // 2. Fetch preferences
      try {
        const pRes = await fetch("/api/preferences/");
        if (pRes && pRes.ok) {
          const pData = await pRes.json().catch(() => null);
          if (pData) setPreferences(pData);
        }
      } catch {}

      // 3. Fetch report data for quick availability summary
      try {
        const repRes = await fetch("/api/watchlist/report/");
        if (repRes && repRes.ok) {
          const repData = await repRes.json().catch(() => null);
          if (repData && Array.isArray(repData.report)) {
            const map: Record<number, ReportRow> = {};
            repData.report.forEach((row: ReportRow) => {
              if (row.movie && row.movie.tmdb_id) {
                map[row.movie.tmdb_id] = row;
              }
            });
            setReportMap(map);
          }
        }
      } catch {}
    } catch (err: unknown) {
      setWatchlistError(
        err instanceof Error ? err.message : "Failed to load watchlist"
      );
    } finally {
      setLoadingWatchlist(false);
    }
  }, []);

  useEffect(() => {
    fetchWatchlistData();
  }, [fetchWatchlistData]);

  /* --- Drag & Drop Handlers --- */
  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setSelectedFile(e.dataTransfer.files[0]);
    }
  };

  /* --- Upload File --- */
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
      await fetchWatchlistData();
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      {/* Header */}
      <div className="mb-8 text-center sm:text-left">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
          Watchlist & Movie Imports
        </h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Your imported movies, watchlist items, and streaming availability at a glance.
        </p>
      </div>

      {/* 1. File Upload Form with Drag and Drop */}
      <section className="mb-8 rounded-xl border border-gray-200 bg-white p-6 shadow-xs dark:border-gray-700 dark:bg-gray-800">
        <h2 className="mb-2 text-lg font-semibold text-gray-900 dark:text-gray-100">
          Import Watchlist Export (Letterboxd CSV/ZIP or MyAnimeList XML/GZ)
        </h2>
        <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
          Upload Letterboxd exports (.csv, .zip) or MyAnimeList exports (.xml, .xml.gz, .gz).
        </p>

        <form onSubmit={handleUpload} className="space-y-4">
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-6 text-center transition-colors cursor-pointer ${
              isDragging
                ? "border-blue-500 bg-blue-50 dark:border-blue-400 dark:bg-blue-950/30"
                : "border-gray-300 hover:border-gray-400 dark:border-gray-700 dark:hover:border-gray-600"
            }`}
            onClick={() => document.getElementById("file-upload-input")?.click()}
          >
            <svg
              className="mx-auto h-10 w-10 text-gray-400 dark:text-gray-500"
              stroke="currentColor"
              fill="none"
              viewBox="0 0 48 48"
              aria-hidden="true"
            >
              <path
                d="M28 8H12a4 4 0 00-4 4v20m32-12v8m0 0v8a4 4 0 01-4 4H12a4 4 0 01-4-4v-4m32-4l-3.172-3.172a4 4 0 00-5.656 0L28 28M8 32l9.172-9.172a4 4 0 015.656 0L28 28m0 0l4 4m4-24h8m-4-4v8"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
              <span className="font-semibold text-blue-600 dark:text-blue-400">
                Click to choose
              </span>{" "}
              or drag & drop your export file here
            </p>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Supports .csv, .zip, .xml, .gz, .xml.gz
            </p>
            {selectedFile && (
              <p className="mt-3 rounded-md bg-blue-100 px-3 py-1 text-xs font-semibold text-blue-800 dark:bg-blue-900/50 dark:text-blue-300">
                Selected: {selectedFile.name} ({(selectedFile.size / 1024).toFixed(1)} KB)
              </p>
            )}

            <input
              id="file-upload-input"
              type="file"
              accept=".csv,.zip,.xml,.gz,.xml.gz,text/csv,application/zip,application/xml,text/xml,application/gzip,application/x-gzip"
              onChange={handleFileChange}
              className="hidden"
            />
          </div>

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={!selectedFile || uploading}
              className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-xs
                         hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50
                         dark:bg-blue-500 dark:hover:bg-blue-600"
            >
              {uploading ? "Importing…" : "Import File"}
            </button>
          </div>
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
          <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800/50">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              Import Summary
            </h3>
            <div className="flex flex-wrap gap-4 text-xs font-medium">
              <span className="text-green-600 dark:text-green-400">
                Resolved Watchlist: {uploadResult.resolved.length}
              </span>
              {(uploadResult.watched_count ?? 0) > 0 && (
                <span className="text-emerald-600 dark:text-emerald-400">
                  Watched: {uploadResult.watched_count}
                </span>
              )}
              {(uploadResult.ratings_count ?? 0) > 0 && (
                <span className="text-blue-600 dark:text-blue-400">
                  Ratings: {uploadResult.ratings_count}
                </span>
              )}
              {(uploadResult.reviews_count ?? 0) > 0 && (
                <span className="text-purple-600 dark:text-purple-400">
                  Reviews: {uploadResult.reviews_count}
                </span>
              )}
              {(uploadResult.diary_events_count ?? 0) > 0 && (
                <span className="text-indigo-600 dark:text-indigo-400">
                  Diary Entries: {uploadResult.diary_events_count}
                </span>
              )}
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
            <div className="flex items-center justify-between rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-700 dark:bg-amber-900/30">
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
                className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white shadow-xs hover:bg-amber-700 dark:bg-amber-500 dark:hover:bg-amber-600"
              >
                Review ambiguous matches
              </Link>
            </div>
          )}
        </section>
      )}

      {/* 3. Watchlist Poster Grid Section */}
      <section>
        <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100">
              Watchlist Catalog ({watchlist.length})
            </h2>
          </div>
          <Link
            href="/watchlist/report"
            className="rounded-lg bg-gray-100 px-3.5 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
          >
            View Analytics Report →
          </Link>
        </div>

        {watchlistError && (
          <div className="mb-6 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
            {watchlistError}
          </div>
        )}

        {loadingWatchlist && (
          <div className="py-12 text-center text-sm text-gray-500 dark:text-gray-400">
            Loading watchlist grid…
          </div>
        )}

        {!loadingWatchlist && watchlist.length === 0 && (
          <div className="rounded-xl border border-dashed border-gray-300 p-12 text-center text-sm text-gray-500 dark:border-gray-700 dark:text-gray-400">
            Your watchlist is empty. Upload a Letterboxd export CSV or ZIP above to get started.
          </div>
        )}

        {/* Responsive Poster Grid */}
        {!loadingWatchlist && watchlist.length > 0 && (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 sm:gap-6">
            {watchlist.map((item) => {
              const movie = item.movie;
              const rep = reportMap[movie.tmdb_id];

              const cardData: MovieCardData = {
                tmdb_id: movie.tmdb_id,
                title: movie.title,
                year: movie.year,
                poster_path: movie.poster_path,
                best_service: rep?.best_service || null,
                best_country: rep?.best_country || null,
                audio_languages: rep?.audio_languages || [],
                subtitle_languages: rep?.subtitle_languages || [],
                preferred_audio: preferences.audio_languages,
                preferred_subtitles: preferences.subtitle_languages,
              };

              return <MovieCard key={item.id} movie={cardData} />;
            })}
          </div>
        )}
      </section>
    </main>
  );
}
