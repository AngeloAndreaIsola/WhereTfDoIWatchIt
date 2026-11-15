"use client";

import { useState, useEffect } from "react";
import Link from "next/link";

import MovieCard from "@/components/MovieCard";
import SearchBar from "@/components/SearchBar";
import type { PopularMovie } from "@/lib/api";
import { fetchPopularMovies, fetchPopularSeries } from "@/lib/api";

export default function HomePage() {
  const [movies, setMovies] = useState<PopularMovie[]>([]);
  const [series, setSeries] = useState<PopularMovie[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Load popular movies and popular series
  useEffect(() => {
    let cancelled = false;

    setLoading(true);
    setError(null);

    Promise.all([fetchPopularMovies(24), fetchPopularSeries(48)])
      .then(([moviesData, seriesData]) => {
        if (!cancelled) {
          setMovies(moviesData.results || []);
          setSeries(seriesData.results || []);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load popular items"
          );
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 space-y-10">
      {/* Header */}
      <div className="text-center">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
          Where TF Do I Watch It
        </h1>

        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Browse popular movies and TV series and find where to watch them.
        </p>
      </div>

      {/* Preferences */}
      <div className="-mt-4 flex justify-center">
        <Link
          href="/preferences"
          className="text-sm text-gray-500 opacity-50 hover:opacity-75 dark:text-gray-400"
          title="Preferences management"
        >
          Preferences
        </Link>
      </div>

      {/* SearchBar component */}
      <div>
        <SearchBar />
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

      {!loading && !error && (
        <>
          {/* Popular Movies section */}
          <section className="space-y-4">
            <div>
              <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100">
                Popular Movies
              </h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Trending movies this week
              </p>
            </div>
            {movies.length > 0 ? (
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
                {movies.map((movie) => (
                  <MovieCard key={`movie-${movie.tmdb_id}`} movie={movie} />
                ))}
              </div>
            ) : (
              <p className="py-4 text-sm text-gray-500 dark:text-gray-400">
                No popular movies found.
              </p>
            )}
          </section>

          {/* Series section */}
          <section className="space-y-4 pt-4 border-t border-gray-200 dark:border-gray-800">
            <div>
              <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100">
                Series Across Active Providers
              </h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Browse series available across active providers
              </p>
            </div>
            {series.length > 0 ? (
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
                {series.map((show) => (
                  <MovieCard key={`series-${show.tmdb_id}`} movie={show} />
                ))}
              </div>
            ) : (
              <p className="py-4 text-sm text-gray-500 dark:text-gray-400">
                No popular series found.
              </p>
            )}
          </section>
        </>
      )}
    </main>
  );
}