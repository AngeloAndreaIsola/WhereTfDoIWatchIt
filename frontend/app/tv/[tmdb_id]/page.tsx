"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import OfferRow from "@/components/OfferRow";
import { useCurrentLocation } from "@/components/CurrentLocationProvider";
import MovieEditModal from "@/components/MovieEditModal";
import type { PrimeSeriesResponse } from "@/lib/api";
import { fetchPrimeSeriesOffers, addToWatchlist, markMovieWatched, unmarkMovieWatched, removeWatchlistItem } from "@/lib/api";

interface SeriesPageProps {
  params: { tmdb_id: string };
}

export default function SeriesPage({ params }: SeriesPageProps) {
  const tmdbId = parseInt(params.tmdb_id, 10);
  const router = useRouter();
  const { currentCountry } = useCurrentLocation();
  const [data, setData] = useState<PrimeSeriesResponse | null>(null);
  const [moviePk, setMoviePk] = useState<number | null>(null);
  const [preferences, setPreferences] = useState<{ audio_languages: string[]; subtitle_languages: string[] }>({
    audio_languages: [],
    subtitle_languages: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unofficialOffers, setUnofficialOffers] = useState<Record<string, any>[] | null>(null);
  const [showEditModal, setShowEditModal] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);

    // Fetch user preferences for exact language match highlighting
    const fetchPrefs = async () => {
      try {
        const response = await fetch("/api/preferences/");
        if (!cancelled && response.ok) {
          const p = await response.json();
          let langs: string[] = [];
          if (
            p.language_rule &&
            typeof p.language_rule === "object" &&
            p.language_rule.type === "viewers" &&
            Array.isArray(p.language_rule.viewers)
          ) {
            const seen = new Set<string>();
            for (const v of p.language_rule.viewers) {
              if (Array.isArray(v.languages)) {
                for (const l of v.languages) {
                  if (typeof l === "string") {
                    const c = l.trim().toUpperCase();
                    if (c && !seen.has(c)) {
                      seen.add(c);
                      langs.push(c);
                    }
                  }
                }
              }
            }
          }
          if (langs.length === 0) {
            langs = p.audio_languages || [];
          }
          setPreferences({
            audio_languages: langs,
            subtitle_languages: langs,
          });
        }
      } catch {}
    };

    // Fetch Prime series offers
    fetchPrimeSeriesOffers(tmdbId, currentCountry)
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setMoviePk(d.series.tmdb_id ?? null);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
          setLoading(false);
        }
      });

    // Fetch scraper offers (Phase C v2 endpoint) - both movies and series use the same endpoint
    fetch(`/api/v2/unofficial/movie/${tmdbId}/offers/`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!cancelled && d?.offers) {
          setUnofficialOffers(d.offers);
        }
      })
      .catch(() => {});

    fetchPrefs();

    return () => {
      cancelled = true;
    };
  }, [tmdbId, currentCountry]);

  const posterUrl = data?.series?.poster_path
    ? `https://image.tmdb.org/t/p/w500${data.series.poster_path}`
    : null;

  const handleAddToWatchlist = async () => {
    try {
      await addToWatchlist(tmdbId, "series");
      setData((prev) => prev ? { ...prev, is_in_watchlist: true } : prev);
    } catch {
      console.error("Failed to add to watchlist");
    }
  };

  const handleMarkWatched = async () => {
    try {
      const result = await markMovieWatched(tmdbId, {
        watched_date: new Date().toISOString().split('T')[0],
      });
      setData((prev) => prev ? { ...prev, user_data: result.user_data ?? { is_watched: true } } : prev);
    } catch {
      console.error("Failed to mark as watched");
    }
  };

  const handleUnmarkWatched = async () => {
    try {
      const result = await unmarkMovieWatched(tmdbId);
      setData((prev) => prev ? { ...prev, user_data: result.user_data ?? { ...(prev.user_data ?? {}), is_watched: false } } : prev);
    } catch {
      console.error("Failed to unmark as watched");
    }
  };

  const handleRemoveFromWatchlist = async () => {
    try {
      await removeWatchlistItem(tmdbId);
      setData((prev) => prev ? { ...prev, is_in_watchlist: false } : prev);
    } catch {
      console.error("Failed to remove from watchlist");
    }
  };

  return (
    <main className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      <div className="mb-6 flex items-center justify-between">
        <button
          type="button"
          onClick={() => {
            if (window.history.length > 1) {
              router.back();
            } else {
              router.push("/catalog");
            }
          }}
          className="text-sm font-medium text-blue-600 hover:underline dark:text-blue-400"
        >
          ← Back
        </button>
        <Link
          href="/"
          className="text-sm font-medium text-gray-500 hover:underline dark:text-gray-400"
        >
          Home
        </Link>
      </div>

      {loading && (
        <div className="py-12 text-center text-sm text-gray-500 dark:text-gray-400">
          Loading series details & availability…
        </div>
      )}

      {error && (
        <div className="rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {error}
        </div>
      )}

      {!loading && !error && data && (
        <div className="space-y-8">
          <div className="relative flex flex-col gap-6 rounded-2xl border border-gray-200 bg-white p-6 shadow-xs sm:flex-row dark:border-gray-700 dark:bg-gray-800">
            <div className="relative aspect-[2/3] w-full max-w-[180px] shrink-0 overflow-hidden rounded-xl bg-gray-202 dark:bg-gray-700">
              {posterUrl ? (
                <Image
                  src={posterUrl}
                  alt={data.series.title}
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

            <div className="flex flex-1 flex-col justify-between">
              <div>
                <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 dark:text-gray-100">
                  {data.series.title}
                  {data.series.first_air_date && (
                    <span className="ml-2 text-lg font-normal text-gray-500 dark:text-gray-400">
                      ({data.series.first_air_date})
                    </span>
                  )}
                </h1>

                {data.series.rating && (
                  <div className="mt-2 flex items-center gap-1 text-sm text-amber-600 dark:text-amber-400">
                    <span>★</span>
                    <span className="font-semibold">{data.series.rating.toFixed(1)}</span>
                    <span className="text-xs text-gray-400 dark:text-gray-500">/10 TMDb</span>
                  </div>
                )}

                {data.series.overview && (
                  <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                    {data.series.overview}
                  </p>
                )}

                {data.series.genres && data.series.genres.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {data.series.genres.map((g) => (
                      <span
                        key={g.id}
                        className="rounded-full bg-gray-100 px-2.5 py-0.5 text-xs text-gray-600 dark:bg-gray-700 dark:text-gray-300"
                      >
                        {g.name}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              <div className="mt-3 flex flex-wrap gap-2">
                {data.is_in_watchlist ? (
                  <button
                    onClick={handleRemoveFromWatchlist}
                    className="px-4 py-2 text-sm font-medium rounded-lg border border-red-300 dark:border-red-700 text-red-700 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-800/30 transition-colors"
                  >
                    ✕ Remove from Watchlist
                  </button>
                ) : (
                  <button
                    onClick={handleAddToWatchlist}
                    className="px-4 py-2 text-sm font-medium rounded-lg border border-green-300 dark:border-green-700 text-green-700 dark:text-green-300 hover:bg-green-50 dark:hover:bg-green-800/30 transition-colors"
                  >
                    + Add to Watchlist
                  </button>
                )}

                {data.user_data?.is_watched ? (
                  <button
                    onClick={handleUnmarkWatched}
                    className="px-4 py-2 text-sm font-medium rounded-lg border border-emerald-300 dark:border-emerald-700 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-50 dark:hover:bg-emerald-800/30 transition-colors"
                  >
                    ✓ Watched — Click to Unmark
                  </button>
                ) : (
                  <button
                    onClick={handleMarkWatched}
                    className="px-4 py-2 text-sm font-medium rounded-lg border border-purple-300 dark:border-purple-700 text-purple-700 dark:text-purple-300 hover:bg-purple-50 dark:hover:bg-purple-800/30 transition-colors"
                  >
                    ✓ Mark as Watched
                  </button>
                )}
              </div>
            </div>
          </div>

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
                    currentCountry={currentCountry}
                  />
                </div>

                {data.offers.length > 1 && (
                  <div className="space-y-3 pt-2">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                      All Ranked Offers
                    </h3>
                    {data.offers.slice(1).map((offer, i) => (
                      <OfferRow
                        key={`${offer.country}-${offer.service}-${offer.quality}-${i}`}
                        offer={offer}
                        tmdbId={tmdbId}
                        isBest={false}
                        preferredAudio={preferences.audio_languages}
                        preferredSubtitles={preferences.subtitle_languages}
                        currentCountry={currentCountry}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </section>

          {unofficialOffers && unofficialOffers.length > 0 && (
            <section>
              <h2 className="mb-4 text-lg font-bold text-gray-900 dark:text-gray-100">
                Scraper Sources ({unofficialOffers.length})
              </h2>
              <div className="space-y-3">
                {unofficialOffers.map((offer: Record<string, any>, i: number) => {
                  const SOURCE_BADGE_COLORS: Record<string, string> = {
                    BVOD: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/60 dark:text-emerald-300",
                    AVOD: "bg-teal-100 text-teal-800 dark:bg-teal-900/60 dark:text-teal-300",
                    FAST: "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/60 dark:text-cyan-300",
                    Official: "bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-300",
                    SVOD: "bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-300",
                    TVOD: "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/60 dark:text-indigo-300",
                    Scraper: "bg-purple-100 text-purple-800 dark:bg-purple-900/60 dark:text-purple-300",
                  };
                  const badgeColor =
                    SOURCE_BADGE_COLORS[offer.source_indicator as string] ||
                    SOURCE_BADGE_COLORS["Scraper"];
                  return (
                    <div
                      key={`${offer.service}-${offer.quality}-${i}`}
                      className="flex items-center justify-between rounded-xl border border-gray-200 bg-white px-4 py-3 dark:border-gray-700 dark:bg-gray-800"
                    >
                      <div className="flex items-center gap-3">
                        <span
                          className={`inline-flex items-center rounded px-2 py-0.5 text-[10px] font-bold ${badgeColor}`}
                        >
                          {offer.source_indicator}
                        </span>
                        <span className="font-medium text-gray-900 dark:text-gray-100">
                          {offer.service_display || offer.service}
                        </span>
                        {offer.quality && (
                          <span className="text-xs text-gray-500 dark:text-gray-400">
                            · {offer.quality.toUpperCase()}
                          </span>
                        )}
                      </div>
                      {offer.url && (
                        <a
                          href={offer.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs font-semibold text-blue-600 hover:underline dark:text-blue-400"
                        >
                          Watch →
                        </a>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
          )}
        </div>
      )}

      {showEditModal && data && moviePk && (
        <MovieEditModal
          tmdbId={tmdbId}
          moviePk={moviePk}
          currentTitle={data.series.title}
          currentPosterPath={data.series.poster_path || null}
          contentType={data.series.content_type || "series"}
          onClose={() => setShowEditModal(false)}
          onUpdated={() => {}}
          onLinked={() => {}}
        />
      )}
    </main>
  );
}
