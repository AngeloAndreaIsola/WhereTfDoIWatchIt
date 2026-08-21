"use client";

import { useState, useCallback, useEffect } from "react";
import Link from "next/link";

import MovieCard from "@/components/MovieCard";
import SearchBar from "@/components/SearchBar";
import type { PopularMovie } from "@/lib/api";
import { fetchPopularMovies } from "@/lib/api";

interface SearchResult {
  tmdb_id: number;
  title: string;
  year: number | null;
}

export default function HomePage() {
  const [movies, setMovies] = useState<PopularMovie[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");

  // Load popular movies
  useEffect(() => {
    let cancelled = false;

    setLoading(true);
    setError(null);

    fetchPopularMovies(24)
      .then((data) => {
        if (!cancelled) {
          setMovies(data.results);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load movies"
          );
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // Search movies
  const doSearch = useCallback(async () => {
    const q = query.trim();

    if (!q) {
      return;
    }

    setSearching(true);
    setSearchError("");
    setResults([]);

    try {
      const res = await fetch(`/api/search/?q=${encodeURIComponent(q)}`);

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));

        throw new Error(
          body.error ?? `Search failed (${res.status})`
        );
      }

      const data = await res.json();

      setResults(data.results ?? []);
    } catch (err: unknown) {
      setSearchError(
        err instanceof Error ? err.message : "Search failed"
      );
    } finally {
      setSearching(false);
    }
  }, [query]);

  // Search when pressing Enter
  const handleKeyDown = (
    e: React.KeyboardEvent<HTMLInputElement>
  ) => {
    if (e.key === "Enter") {
      void doSearch();
    }
  };

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      {/* Header */}
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
          Where TF Do I Watch It
        </h1>

        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Browse popular movies and find where to watch them.
        </p>
      </div>

      {/* Preferences */}
      <div className="mb-6 flex justify-center">
        <Link
          href="/preferences"
          className="text-sm text-gray-500 opacity-50 hover:opacity-75 dark:text-gray-400"
          title="Coming soon — preferences are managed by the server for now"
        >
          Preferences (coming soon)
        </Link>
      </div>

      {/* SearchBar component */}
      <div className="mb-8">
        <SearchBar />
      </div>

      {/* Manual Search */}
      <div className="mb-8">
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Search for a movie…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            className="flex-1 rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm placeholder-gray-400 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:placeholder-gray-500 dark:focus:border-blue-400"
          />

          <button
            type="button"
            onClick={() => void doSearch()}
            disabled={searching || !query.trim()}
            className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-blue-500 dark:hover:bg-blue-600"
          >
            {searching ? "Searching…" : "Search"}
          </button>
        </div>

        {/* Search error */}
        {searchError && (
          <div className="mt-3 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
            {searchError}
          </div>
        )}

        {/* Search results */}
        {results.length > 0 && (
          <div className="mt-4 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800">
            {results.map((movie) => (
              <Link
                key={movie.tmdb_id}
                href={`/movie/${movie.tmdb_id}`}
                className="block border-b border-gray-200 px-4 py-3 last:border-b-0 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-700"
              >
                <div className="font-medium text-gray-900 dark:text-gray-100">
                  {movie.title}
                </div>

                {movie.year && (
                  <div className="text-sm text-gray-500 dark:text-gray-400">
                    {movie.year}
                  </div>
                )}
              </Link>
            ))}
          </div>
        )}
      </div>

      {/* Loading */}
      {loading && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
          {Array.from({ length: 20 }).map((_, i) => (
            <div
              key={i}
              className="aspect-[2/3] animate-pulse rounded-lg bg-gray-200 dark:bg-gray-700"
            />
          ))}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Movie grid */}
      {!loading && !error && movies.length > 0 && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
          {movies.map((movie) => (
            <MovieCard
              key={movie.tmdb_id}
              movie={movie}
            />
          ))}
        </div>
      )}

      {/* Empty */}
      {!loading && !error && movies.length === 0 && (
        <p className="py-8 text-center text-sm text-gray-500 dark:text-gray-400">
          No movies found.
        </p>
      )}
    </main>
  );
}