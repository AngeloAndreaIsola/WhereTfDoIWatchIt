"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import MovieCard from "@/components/MovieCard";
import type { PopularMovie } from "@/lib/api";
import { fetchPopularMovies } from "@/lib/api";

export default function HomePage() {
  const [movies, setMovies] = useState<PopularMovie[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      {/* Header */}
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
          Where TF Do I Watch It
        </h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Browse popular movies → click one to see Prime Video offers ranked by
          your language preferences
        </p>
      </div>

      {/* Greyed-out preferences nav */}
      <div className="mb-6 flex justify-center">
        <Link
          href="/preferences"
          className="text-sm text-gray-500 opacity-50 hover:opacity-75 dark:text-gray-400"
          title="Coming soon — preferences are managed by the server for now"
        >
          Preferences (coming soon)
        </Link>
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
            <MovieCard key={movie.tmdb_id} movie={movie} />
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
