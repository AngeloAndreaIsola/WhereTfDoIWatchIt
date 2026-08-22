"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import Image from "next/image";
import OfferRow from "@/components/OfferRow";
import type { PrimeOffer, PrimeMovieResponse } from "@/lib/api";
import { fetchPrimeMovieOffers } from "@/lib/api";

interface MovieDetailPageProps {
  params: { tmdb_id: string };
}

export default function MovieDetailPage({ params }: MovieDetailPageProps) {
  const tmdbId = parseInt(params.tmdb_id, 10);
  const [data, setData] = useState<PrimeMovieResponse | null>(null);
  const [preferences, setPreferences] = useState<{ audio_languages: string[]; subtitle_languages: string[] }>({
    audio_languages: [],
    subtitle_languages: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);

    // Fetch user preferences for exact language match highlighting
    fetch("/api/preferences/")
      .then((r) => (r.ok ? r.json() : null))
      .then((p) => {
        if (!cancelled && p) {
          setPreferences({
            audio_languages: p.audio_languages || [],
            subtitle_languages: p.subtitle_languages || [],
          });
        }
      })
      .catch(() => {});

    fetchPrimeMovieOffers(tmdbId)
      .then((d) => {
        if (!cancelled) {
          setData(d);
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
  }, [tmdbId]);

  const posterUrl = data?.movie?.poster_path
    ? `https://image.tmdb.org/t/p/w500${data.movie.poster_path}`
    : null;

  return (
    <main className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      {/* Back link */}
      <div className="mb-6 flex items-center justify-between">
        <Link
          href="/watchlist"
          className="text-sm font-medium text-blue-600 hover:underline dark:text-blue-400"
        >
          ← Back to Watchlist
        </Link>
        <Link
          href="/"
          className="text-sm font-medium text-gray-500 hover:underline dark:text-gray-400"
        >
          Home
        </Link>
      </div>

      {/* Loading */}
      {loading && (
        <div className="py-12 text-center text-sm text-gray-500 dark:text-gray-400">
          Loading movie details & availability…
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Content */}
      {!loading && !error && data && (
        <div className="space-y-8">
          {/* Header Card with Poster, Title, User Data, and Watch Button */}
          <div className="flex flex-col gap-6 rounded-2xl border border-gray-200 bg-white p-6 shadow-xs sm:flex-row dark:border-gray-700 dark:bg-gray-800">
            {/* Poster image */}
            <div className="relative aspect-[2/3] w-full max-w-[180px] shrink-0 overflow-hidden rounded-xl bg-gray-200 dark:bg-gray-700">
              {posterUrl ? (
                <Image
                  src={posterUrl}
                  alt={data.movie.title}
                  fill
                  sizes="180px"
                  className="object-cover"
                />
              ) : (
                <div className="flex h-full w-full flex-col items-center justify-center text-gray-400 dark:text-gray-500">
                  <span className="text-3xl">🎬</span>
                  <span className="text-xs">No Poster</span>
                </div>
              )}
            </div>

            {/* Movie Info & Actions */}
            <div className="flex flex-1 flex-col justify-between">
              <div>
                <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 dark:text-gray-100">
                  {data.movie.title}
                  {data.movie.year && (
                    <span className="ml-2 text-lg font-normal text-gray-500 dark:text-gray-400">
                      ({data.movie.year})
                    </span>
                  )}
                </h1>

                {/* Letterboxd User Data & History */}
                {data.user_data && (
                  <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
                    {data.user_data.is_watched && (
                      <span className="rounded-full bg-emerald-100 px-3 py-1 font-semibold text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">
                        ✓ Watched
                      </span>
                    )}
                    {data.user_data.rating && (
                      <span className="rounded-full bg-amber-100 px-3 py-1 font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
                        ★ Rating: {data.user_data.rating} / 5.0
                      </span>
                    )}
                  </div>
                )}

                {/* Review if present */}
                {data.user_data?.review && (
                  <div className="mt-3 rounded-lg border border-gray-100 bg-gray-50 p-3 text-xs italic text-gray-700 dark:border-gray-700 dark:bg-gray-700/50 dark:text-gray-300">
                    &ldquo;{data.user_data.review}&rdquo;
                  </div>
                )}

                {/* Multiple Diary Watch Events */}
                {data.watch_events && data.watch_events.length > 0 && (
                  <div className="mt-4 border-t border-gray-100 pt-3 dark:border-gray-700/60">
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                      Watch History ({data.watch_events.length} viewings)
                    </h3>
                    <ul className="mt-2 space-y-1.5 text-xs text-gray-600 dark:text-gray-300">
                      {data.watch_events.map((evt) => (
                        <li key={evt.id} className="flex flex-wrap items-center gap-2">
                          <span className="font-medium text-gray-800 dark:text-gray-200">
                            📅 {evt.watched_date || "Date unknown"}
                          </span>
                          {evt.rewatch && (
                            <span className="rounded bg-purple-100 px-1.5 py-0.5 text-[10px] font-semibold text-purple-800 dark:bg-purple-900/40 dark:text-purple-300">
                              Rewatch
                            </span>
                          )}
                          {evt.rating && (
                            <span className="text-amber-600 dark:text-amber-400 font-semibold">
                              ★ {evt.rating}
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              {/* Prominent Watch Button */}
              <div className="mt-6 pt-4 border-t border-gray-100 dark:border-gray-700/60">
                <Link
                  href={`/watch/${tmdbId}`}
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-blue-600 px-6 py-3 text-sm font-semibold text-white shadow-md hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600 transition-all hover:scale-[1.01]"
                >
                  <span className="text-lg">▶</span>
                  <span>Watch Movie</span>
                </Link>
              </div>
            </div>
          </div>

          {/* Offers List */}
          <section>
            <h2 className="mb-4 text-lg font-bold text-gray-900 dark:text-gray-100">
              Streaming Availability & Language Matches
            </h2>

            {data.offers.length === 0 ? (
              <div className="rounded-xl border border-gray-200 bg-white p-6 text-center text-sm text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400">
                Not currently available on your subscribed streaming services.
              </div>
            ) : (
              <div className="space-y-3">
                {/* Best match card */}
                <div className="rounded-xl border-2 border-green-400 bg-green-50 p-4 dark:border-green-600 dark:bg-green-900/20">
                  <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-green-800 dark:text-green-300">
                    Best Match
                  </h3>
                  <OfferRow
                    offer={data.offers[0]}
                    tmdbId={tmdbId}
                    isBest={true}
                    preferredAudio={preferences.audio_languages}
                    preferredSubtitles={preferences.subtitle_languages}
                  />
                </div>

                {/* All ranked offers */}
                {data.offers.length > 1 && (
                  <div className="space-y-3 pt-2">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                      All Ranked Offers
                    </h3>
                    {data.offers.slice(1).map((offer: PrimeOffer, i: number) => (
                      <OfferRow
                        key={`${offer.country}-${offer.service}-${offer.quality}-${i}`}
                        offer={offer}
                        tmdbId={tmdbId}
                        isBest={false}
                        preferredAudio={preferences.audio_languages}
                        preferredSubtitles={preferences.subtitle_languages}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
