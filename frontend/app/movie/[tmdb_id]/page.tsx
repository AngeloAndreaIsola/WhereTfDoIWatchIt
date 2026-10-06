"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import OfferRow from "@/components/OfferRow";
import { useCurrentLocation } from "@/components/CurrentLocationProvider";
import MovieEditModal from "@/components/MovieEditModal";
import RefreshAvailabilityButton from "@/components/RefreshAvailabilityButton";
import AvailabilityState from "@/components/AvailabilityState";
import type { PrimeOffer, PrimeMovieResponse } from "@/lib/api";
import { fetchPrimeMovieOffers, addToWatchlist, markMovieWatched, unmarkMovieWatched, removeWatchlistItem, requestAvailabilityRefresh } from "@/lib/api";
import type { LinkTmdbResponse } from "@/lib/api";

interface UnofficialOffer {
  country: string;
  service: string;
  service_display: string;
  source_indicator: string;
  offer_type: string;
  quality: string;
  url?: string;
  audio: string[];
  subs: string[];
  ranking: {
    audio_rank: number;
    subtitle_rank: number;
    quality_rank: number;
    is_language_match: boolean;
  };
}

interface MovieDetailPageProps {
  params: { tmdb_id: string };
}

/** How often to re-check local availability while a refresh is queued. */
const AVAILABILITY_POLL_INTERVAL_MS = 4000;
/** Give up polling after this long; the queued refresh can still land later. */
const AVAILABILITY_POLL_TIMEOUT_MS = 60000;

export default function MovieDetailPage({ params }: MovieDetailPageProps) {
  const tmdbId = parseInt(params.tmdb_id, 10);
  const router = useRouter();
  const { currentCountry } = useCurrentLocation();
  const [data, setData] = useState<PrimeMovieResponse | null>(null);
  const [moviePk, setMoviePk] = useState<number | null>(null);
  const [preferences, setPreferences] = useState<{ audio_languages: string[]; subtitle_languages: string[] }>({
    audio_languages: [],
    subtitle_languages: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unofficialOffers, setUnofficialOffers] = useState<UnofficialOffer[] | null>(null);
  const [unofficialLoading, setUnofficialLoading] = useState(false);
  const [pollTimedOut, setPollTimedOut] = useState(false);
  // Manual "Refresh availability" state: in flight from click until the
  // worker settles the request (or we give up waiting for it).
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [showEditModal, setShowEditModal] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    setPollTimedOut(false);

    // Fetch user preferences for exact language match highlighting
    fetch("/api/preferences/")
      .then((r) => (r.ok ? r.json() : null))
      .then((p) => {
        if (!cancelled && p) {
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
      })
      .catch(() => {});

    // Fetch official (Prime) offers
    fetchPrimeMovieOffers(tmdbId, currentCountry)
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setMoviePk(d.movie.id ?? null);
          setLoading(false);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message);
          setLoading(false);
        }
      });

    // Fetch scraper offers (Phase C v2 endpoint)
    setUnofficialLoading(true);
    fetch(`/api/v2/unofficial/movie/${tmdbId}/offers/`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!cancelled && d?.offers) {
          setUnofficialOffers(d.offers);
        }
      })
      .catch(() => {})
      .finally(() => !cancelled && setUnofficialLoading(false));

    return () => {
      cancelled = true;
    };
  }, [tmdbId, currentCountry]);

  // The backend queues an async refresh when the detail page is opened (and
  // again on the manual "Refresh availability" button); the worker drains it
  // and only then are canonical offers written. While this title has no
  // offers and the queue still holds a pending request, poll the same
  // endpoint until offers arrive, the request settles, or we time out (a
  // rate-limited refresh can still land later — see the queued note).
  const waitingForRefresh = !!data && data.offers.length === 0 && !!data.refresh_pending;

  // Poll whenever the queue holds a request for this title — whether it came
  // from the page's own auto-enqueue or from an explicit click — and keep
  // polling while a manual refresh is in flight even if a stale response
  // still reads as settled.
  const shouldPoll =
    !!data && !pollTimedOut && (!!data.refresh_pending || refreshing);

  useEffect(() => {
    if (!shouldPoll) return;
    let cancelled = false;
    const startedAt = Date.now();
    const id = setInterval(() => {
      if (cancelled) return;
      if (Date.now() - startedAt >= AVAILABILITY_POLL_TIMEOUT_MS) {
        clearInterval(id);
        setPollTimedOut(true);
        if (refreshing) {
          setRefreshing(false);
          setRefreshError(
            "The refresh is still queued — try again in a minute."
          );
        }
        return;
      }
      fetchPrimeMovieOffers(tmdbId, currentCountry)
        .then((d) => {
          if (cancelled) return;
          setData(d);
          if (!d.refresh_pending && refreshing) {
            // Manual refresh settled: re-enable the button, surfacing a
            // worker-side failure so the user can retry.
            setRefreshing(false);
            if (d.refresh_status === "failed") {
              setRefreshError(
                "The refresh failed on the server — try again."
              );
            }
          }
        })
        .catch(() => {});
    }, AVAILABILITY_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [shouldPoll, refreshing, pollTimedOut, tmdbId, currentCountry]);

  const posterUrl = data?.movie?.poster_path
    ? `https://image.tmdb.org/t/p/w500${data.movie.poster_path}`
    : null;

  const handleUpdate = (title: string, posterPath: string | null) => {
    setData((prev) =>
      prev
        ? {
            ...prev,
            movie: { ...prev.movie, title, poster_path: posterPath },
          }
        : prev
    );
  };

  const handleLinked = useCallback(
    (movie: LinkTmdbResponse["movie"]) => {
      // Linking can change the movie's TMDb id — navigate to the new detail URL
      // so availability (keyed by tmdb_id) is re-fetched cleanly.
      if (movie.tmdb_id && movie.tmdb_id !== tmdbId) {
        router.push(`/movie/${movie.tmdb_id}/`);
        return;
      }
      setData((prev) =>
        prev
          ? {
              ...prev,
              movie: { ...prev.movie, title: movie.title, poster_path: movie.poster_path },
            }
          : prev
      );
    },
    [router, tmdbId]
  );

  const reloadMovie = useCallback(() => {
    setPollTimedOut(false);
    fetchPrimeMovieOffers(tmdbId, currentCountry).then((d) => setData(d)).catch(() => {});
  }, [tmdbId, currentCountry]);

  // Enqueue an explicit refresh and let the poll effect above watch the
  // queue settle; existing offers stay on screen until new ones land.
  const handleRefreshAvailability = async () => {
    setRefreshError(null);
    setPollTimedOut(false);
    setRefreshing(true);
    try {
      const result = await requestAvailabilityRefresh(tmdbId);
      setData((prev) =>
        prev
          ? {
              ...prev,
              refresh_pending: result.refresh_pending,
              refresh_status: result.refresh_status,
            }
          : prev
      );
    } catch (err) {
      setRefreshing(false);
      setRefreshError(
        err instanceof Error
          ? err.message
          : "Failed to queue the refresh — try again."
      );
    }
  };

  const refreshInProgress =
    refreshing || (!!data?.refresh_pending && !pollTimedOut);

  const handleAddToWatchlist = async () => {
    try {
      await addToWatchlist(
        tmdbId,
        data?.movie?.content_type === "series" ? "series" : "movie"
      );
      setData((prev) => prev ? { ...prev, is_in_watchlist: true } : prev);
      reloadMovie();
    } catch (err) {
      console.error("Failed to add to watchlist:", err);
    }
  };

  const handleMarkWatched = async () => {
    try {
      const result = await markMovieWatched(tmdbId, {
        watched_date: new Date().toISOString().split('T')[0],
      });
      setData((prev) => prev ? { ...prev, user_data: result.user_data ?? { is_watched: true } } : prev);
      reloadMovie();
    } catch (err) {
      console.error("Failed to mark as watched:", err);
    }
  };

  const handleUnmarkWatched = async () => {
    try {
      const result = await unmarkMovieWatched(tmdbId);
      setData((prev) => prev ? { ...prev, user_data: result.user_data ?? { ...(prev.user_data ?? {}), is_watched: false } } : prev);
      reloadMovie();
    } catch (err) {
      console.error("Failed to unmark as watched:", err);
    }
  };

  const handleRemoveFromWatchlist = async () => {
    try {
      await removeWatchlistItem(tmdbId);
      setData((prev) => prev ? { ...prev, is_in_watchlist: false } : prev);
      reloadMovie();
    } catch (err) {
      console.error("Failed to remove from watchlist:", err);
    }
  };

  return (
    <main className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      {/* Back link */}
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
          <div className="relative flex flex-col gap-6 rounded-2xl border border-gray-200 bg-white p-6 shadow-xs sm:flex-row dark:border-gray-700 dark:bg-gray-800">
            {/* Edit (pencil) button — top right corner */}
            <button
              type="button"
              onClick={() => setShowEditModal(true)}
              title="Edit title & poster / link to TMDb"
              aria-label="Edit title & poster"
              className="absolute right-3 top-3 z-10 rounded-full border border-gray-200 bg-white/90 p-2 text-gray-600 shadow-sm transition-colors hover:bg-gray-100 dark:border-gray-600 dark:bg-gray-800/90 dark:text-gray-300 dark:hover:bg-gray-700"
            >
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125M16.862 4.487L19.5 7.125" />
              </svg>
            </button>

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

              {/* TMDb rating */}
              {data.movie.rating && (
                <div className="mt-2 flex items-center gap-1 text-sm text-amber-600 dark:text-amber-400">
                  <span>★</span>
                  <span className="font-semibold">{data.movie.rating.toFixed(1)}</span>
                  <span className="text-xs text-gray-400 dark:text-gray-500">/10 TMDb</span>
                </div>
              )}

              {/* Synopsis */}
              {data.movie.overview && (
                <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                  {data.movie.overview}
                </p>
              )}

              {/* Genres */}
              {data.movie.genres && data.movie.genres.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {data.movie.genres.map((g) => (
                    <span
                      key={g.id}
                      className="rounded-full bg-gray-100 px-2.5 py-0.5 text-xs text-gray-600 dark:bg-gray-700 dark:text-gray-300"
                    >
                      {g.name}
                    </span>
                  ))}
                </div>
              )}

              {/* Director / Creator */}
              {data.movie.director && data.movie.director.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                    {data.movie.content_type === "series" ? "Created by" : "Director"}
                  </span>
                  {data.movie.director.map((name) => (
                    <span
                      key={name}
                      className="rounded-full bg-purple-50 px-2.5 py-0.5 text-xs text-purple-700 dark:bg-purple-900/30 dark:text-purple-300"
                    >
                      {name}
                    </span>
                  ))}
                </div>
              )}

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

                    {/* Manual Watchlist / Watched Management Buttons */}
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

                {/* Review if present */}
                {data.user_data?.review && (
                  <div className="mt-3 rounded-lg border border-gray-100 bg-gray-50 p-3 text-xs italic text-gray-700 dark:border-gray-700 dark:bg-gray-700/50 dark:text-gray-300">
                    &ldquo;{data.user_data.review}&rdquo;
                  </div>
                )}

                {/* Cast */}
                {data.movie.cast && data.movie.cast.length > 0 && (
                  <div className="mt-3">
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                      Cast
                    </h3>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {data.movie.cast.slice(0, 8).map((person) => (
                        <span
                          key={person.id}
                          className="rounded-full bg-blue-50 px-2.5 py-0.5 text-xs text-blue-700 dark:bg-blue-900/30 dark:text-blue-300"
                        >
                          {person.character
                            ? `${person.name} as ${person.character}`
                            : person.name}
                        </span>
                      ))}
                      {data.movie.cast.length > 8 && (
                        <span className="text-xs text-gray-400">
                          +{data.movie.cast.length - 8} more
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Offers List */}
          <section>
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Streaming Availability & Language Matches
              </h2>
              <RefreshAvailabilityButton
                inProgress={refreshInProgress}
                error={refreshError}
                onRefresh={handleRefreshAvailability}
              />
            </div>

            {data.offers.length === 0 ? (
              waitingForRefresh && !pollTimedOut ? (
                <div className="rounded-xl border border-dashed border-blue-300 bg-blue-50/50 p-6 text-center text-sm text-blue-700 dark:border-blue-700 dark:bg-blue-900/20 dark:text-blue-300">
                  <span className="inline-flex items-center gap-2">
                    <span className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
                    Checking availability…
                  </span>
                  <p className="mt-2 text-xs text-blue-600/80 dark:text-blue-400/80">
                    This title has no local availability yet — we&rsquo;re fetching it now.
                  </p>
                </div>
              ) : (
                // The empty-offers state is driven by the backend verdict, not
                // assumed: an unchecked title must not read as "unavailable".
                <>
                  <AvailabilityState
                    availability={data.availability}
                    refreshing={false}
                  />
                  {data.refresh_pending && (
                    <p className="mt-2 text-center text-xs text-gray-500 dark:text-gray-500">
                      An availability refresh is still queued.
                      <button
                        type="button"
                        onClick={reloadMovie}
                        className="ml-1 font-semibold text-blue-600 hover:underline dark:text-blue-400"
                      >
                        Check again
                      </button>
                    </p>
                  )}
                </>
              )
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
                    currentCountry={currentCountry}
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
                        currentCountry={currentCountry}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </section>

          {/* Scraper Sources Section */}
          {unofficialLoading && (
            <div className="py-4 text-center text-sm text-gray-500 dark:text-gray-400">
              Loading scraper sources…
            </div>
          )}
          {unofficialOffers && unofficialOffers.length > 0 && (
            <section>
              <h2 className="mb-4 text-lg font-bold text-gray-900 dark:text-gray-100">
                Scraper Sources ({unofficialOffers.length})
              </h2>
              <div className="space-y-3">
                {unofficialOffers.map((offer, i) => {
                  const SOURCE_BADGE_COLORS: Record<string, string> = {
                    BVOD:
                      "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/60 dark:text-emerald-300",
                    AVOD:
                      "bg-teal-100 text-teal-800 dark:bg-teal-900/60 dark:text-teal-300",
                    FAST:
                      "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/60 dark:text-cyan-300",
                    Official:
                      "bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-300",
                    SVOD:
                      "bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-300",
                    TVOD:
                      "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/60 dark:text-indigo-300",
                    Scraper:
                      "bg-purple-100 text-purple-800 dark:bg-purple-900/60 dark:text-purple-300",
                  };
                  const badgeColor =
                    SOURCE_BADGE_COLORS[offer.source_indicator] ||
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

      {/* Edit Modal */}
      {showEditModal && data && moviePk && (
        <MovieEditModal
          tmdbId={tmdbId}
          moviePk={moviePk}
          currentTitle={data.movie.title}
          currentPosterPath={data.movie.poster_path || null}
          contentType={data.movie.content_type || "movie"}
          onClose={() => setShowEditModal(false)}
          onUpdated={handleUpdate}
          onLinked={handleLinked}
        />
      )}
    </main>
  );
}
