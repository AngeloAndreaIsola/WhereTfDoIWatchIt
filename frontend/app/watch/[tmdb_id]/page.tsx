"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchWatchDestination, WatchResult } from "@/lib/api";
import { useCurrentLocation } from "@/components/CurrentLocationProvider";

interface WatchPageProps {
  params: { tmdb_id: string };
}

export default function WatchPage({ params }: WatchPageProps) {
  const tmdbId = parseInt(params.tmdb_id, 10);
  const { currentCountry } = useCurrentLocation();
  const [result, setResult] = useState<WatchResult | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    fetchWatchDestination(tmdbId, currentCountry)
      .then((res) => {
        if (!cancelled) {
          setResult(res);
          setLoading(false);
          if (res.found && res.watch_url) {
            window.location.href = res.watch_url;
          }
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setResult({
            found: false,
            movie: { tmdb_id: tmdbId, title: null, year: null },
            message: err instanceof Error ? err.message : "Failed to resolve watch route",
          });
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [tmdbId, currentCountry]);

  return (
    <main className="mx-auto flex min-h-[60vh] max-w-xl flex-col items-center justify-center px-4 py-12 text-center sm:px-6">
      {loading && (
        <div className="space-y-3">
          <div className="mx-auto h-8 w-8 animate-spin rounded-full border-4 border-blue-600 border-t-transparent"></div>
          <p className="text-base font-medium text-gray-700 dark:text-gray-300">
            Finding best streaming destination…
          </p>
        </div>
      )}

      {!loading && result?.found && result.watch_url && (
        <div className="space-y-4 rounded-2xl border border-green-200 bg-green-50 p-8 shadow-sm dark:border-green-800 dark:bg-green-900/20">
          <span className="text-4xl">🍿</span>
          <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">
            Redirecting to watch {result.movie.title || "movie"}…
          </h1>
          <p className="text-sm text-gray-600 dark:text-gray-300">
            You are being forwarded to <span className="font-semibold text-green-700 dark:text-green-300">{result.offer?.service}</span>.
          </p>
          <a
            href={result.watch_url}
            className="inline-block rounded-lg bg-green-600 px-6 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-green-700 dark:bg-green-500 dark:hover:bg-green-600"
          >
            Click here if you are not redirected automatically →
          </a>
        </div>
      )}

      {!loading && (!result || !result.found || !result.watch_url) && (
        <div className="space-y-6 rounded-2xl border border-gray-200 bg-white p-8 shadow-sm dark:border-gray-700 dark:bg-gray-800">
          <span className="text-4xl">📺</span>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
            Not currently available to watch
          </h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {result?.message || "No verified streaming link is available for your subscribed services and preferences."}
          </p>
          <div className="pt-2">
            <Link
              href={`/movie/${tmdbId}`}
              className="inline-block rounded-lg bg-blue-600 px-6 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600"
            >
              ← Return to Movie Details
            </Link>
          </div>
        </div>
      )}
    </main>
  );
}
