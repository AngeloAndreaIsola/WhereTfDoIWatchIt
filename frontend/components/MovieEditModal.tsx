"use use client";

import { useState, useCallback } from "react";
import Image from "next/image";
import { linkTmdb, searchMovieMatches, type LinkTmdbResponse } from "@/lib/api";

interface MovieEditModalProps {
  tmdbId: number;
  moviePk: number;
  currentTitle: string;
  currentPosterPath: string | null;
  contentType?: string;
  onClose: () => void;
  onUpdated: (title: string, posterPath: string | null) => void;
  onLinked: (movie: LinkTmdbResponse["movie"]) => void;
}

interface SearchCandidate {
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  overview: string | null;
  rating: number | null;
}

export default function MovieEditModal({
  tmdbId,
  moviePk,
  currentTitle,
  currentPosterPath,
  contentType = "movie",
  onClose,
  onUpdated,
  onLinked,
}: MovieEditModalProps) {
  const [title, setTitle] = useState(currentTitle);
  const [posterPath, setPosterPath] = useState<string | null>(currentPosterPath);
  const [searchQuery, setSearchQuery] = useState("");
  const [candidates, setCandidates] = useState<SearchCandidate[]>([]);
  const [selected, setSelected] = useState<SearchCandidate | null>(null);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [linking, setLinking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSearch = useCallback(async (q: string) => {
    setSearchQuery(q);
    setSelected(null);
    if (!q.trim()) {
      setCandidates([]);
      return;
    }
    setSearching(true);
    try {
      const data = await searchMovieMatches(tmdbId, q);
      setCandidates(data.results || []);
    } catch {
      setCandidates([]);
    } finally {
      setSearching(false);
    }
  }, [tmdbId]);

  const handleSelect = (candidate: SearchCandidate) => {
    setSelected(candidate);
    setTitle(candidate.title);
    setPosterPath(candidate.poster_path);
  };

  const handleSaveManual = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/movie/${tmdbId}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, poster_path: posterPath || "" }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error ?? "Failed to save");
      }
      onUpdated(title, posterPath);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const handleLink = async () => {
    if (!selected) return;
    setLinking(true);
    setError(null);
    try {
      const result = await linkTmdb(moviePk, selected.tmdb_id, {
        title: selected.title,
        content_type: contentType,
      });
      onLinked(result.movie);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to link movie");
    } finally {
      setLinking(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg rounded-2xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800 shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            Edit Movie
          </h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
          >
            ✕
          </button>
        </div>

        <div className="px-6 py-4 space-y-4">
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Search TMDb and{" "}
            <span className="font-semibold text-gray-700 dark:text-gray-200">
              link
            </span>{" "}
            to sync the title, poster, synopsis, cast, director and rating. Or
            just edit the title and poster manually below.
          </p>

          {/* Title edit */}
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-500 dark:text-gray-400">
              Title
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-900 focus:border-blue-500 focus:outline-none dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
            />
          </div>

          {/* Poster search */}
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-500 dark:text-gray-400">
              Find on TMDb
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => handleSearch(e.target.value)}
                placeholder="Search for a movie…"
                className="flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-900 focus:border-blue-500 focus:outline-none dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
              />
            </div>

            {/* Search results */}
            {searching && (
              <p className="mt-2 text-xs text-gray-400">Searching…</p>
            )}
            {!searching && candidates.length > 0 && (
              <div className="mt-2 space-y-2">
                {candidates.map((c) => {
                  const isSelected = selected?.tmdb_id === c.tmdb_id;
                  return (
                    <button
                      key={c.tmdb_id}
                      onClick={() => handleSelect(c)}
                      className={`flex w-full items-center gap-3 rounded-lg border p-2 text-sm text-left transition ${
                        isSelected
                          ? "border-blue-500 bg-blue-50 dark:bg-blue-900/30"
                          : "border-gray-100 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-700/50"
                      }`}
                    >
                      {c.poster_path ? (
                        <div className="relative aspect-[2/3] h-10 w-7 shrink-0 overflow-hidden rounded bg-gray-200 dark:bg-gray-700">
                          <Image
                            src={`https://image.tmdb.org/t/p/w92${c.poster_path}`}
                            alt={c.title}
                            fill
                            className="object-cover"
                          />
                        </div>
                      ) : (
                        <div className="flex h-10 w-7 shrink-0 items-center justify-center rounded bg-gray-100 text-xs text-gray-400 dark:bg-gray-700">
                          no poster
                        </div>
                      )}
                      <div className="flex-1">
                        <div className="font-medium text-gray-900 dark:text-gray-100">
                          {c.title}
                        </div>
                        <div className="text-xs text-gray-500 dark:text-gray-400">
                          {c.year && `${c.year}`}
                          {c.rating && ` · ★ ${c.rating.toFixed(1)}`}
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
            {!searching && candidates.length === 0 && searchQuery && (
              <p className="mt-2 text-xs text-gray-400">No results found.</p>
            )}
          </div>

          {/* Current poster preview */}
          {posterPath && (
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500 dark:text-gray-400">
                Current Poster
              </label>
              <div className="relative aspect-[2/3] w-20 overflow-hidden rounded-lg bg-gray-200 dark:bg-gray-700">
                <Image
                  src={`https://image.tmdb.org/t/p/w200${posterPath}`}
                  alt="Poster"
                  fill
                  className="object-cover"
                />
              </div>
            </div>
          )}

          {error && (
            <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 border-t border-gray-100 px-6 py-4 dark:border-gray-700">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
          >
            Cancel
          </button>
          <button
            onClick={handleSaveManual}
            disabled={saving || linking || !title.trim()}
            className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
          >
            {saving ? "Saving…" : "Save manually"}
          </button>
          <button
            onClick={handleLink}
            disabled={saving || linking || !selected}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {linking ? "Linking…" : "Link & sync"}
          </button>
        </div>
      </div>
    </div>
  );
}
