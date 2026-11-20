"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
import OfferRow from "@/components/OfferRow";
import { useCurrentLocation } from "@/components/CurrentLocationProvider";
import RefreshAvailabilityButton from "@/components/RefreshAvailabilityButton";
import AvailabilityState from "@/components/AvailabilityState";
import LastKnownOffers from "@/components/LastKnownOffers";
import CastList from "@/components/CastList";
import type { PrimeOffer, PrimeSeriesResponse } from "@/lib/api";
import { fetchPrimeSeriesOffers, addToWatchlist, markMovieWatched, unmarkMovieWatched, removeWatchlistItem, requestAvailabilityRefresh } from "@/lib/api";

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

interface SeriesDetailPageProps {
  params: { tmdb_id: string };
}

/** `?season=N` → number. Anything unparsable means "no season selected". */
function parseSeason(raw: string | null | undefined): number | null {
  if (raw == null || raw.trim() === "") return null;
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : null;
}

/** How often to re-check local availability while a refresh is queued. */
const AVAILABILITY_POLL_INTERVAL_MS = 4000;
/** Give up polling after this long; the queued refresh can still land later. */
const AVAILABILITY_POLL_TIMEOUT_MS = 60000;

export default function SeriesDetailPage({ params }: SeriesDetailPageProps) {
  const tmdbId = parseInt(params.tmdb_id, 10);
  const router = useRouter();
  const searchParams = useSearchParams();
  const rawSeason = searchParams.get("season");
  const { currentCountry } = useCurrentLocation();
  const [data, setData] = useState<PrimeSeriesResponse | null>(null);
  const [preferences, setPreferences] = useState<{ audio_languages: string[]; subtitle_languages: string[] }>({
    audio_languages: [],
    subtitle_languages: [],
  });
  const [loading, setLoading] = useState(true);
  const [seasonLoading, setSeasonLoading] = useState(false);
  const [selectedSeason, setSelectedSeason] = useState<number | null>(() =>
    parseSeason(rawSeason)
  );
  const [error, setError] = useState<string | null>(null);
  const [unofficialOffers, setUnofficialOffers] = useState<UnofficialOffer[] | null>(null);
  const [unofficialLoading, setUnofficialLoading] = useState(false);
  const [pollTimedOut, setPollTimedOut] = useState(false);
  // Manual "Refresh availability" state: in flight from click until the
  // worker settles the request (or we give up waiting for it).
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  // Which series the offers currently on screen belong to, so a season change
  // refines the view instead of blanking it.
  const loadedSeriesRef = useRef<number | null>(null);

  // Deep links and back/forward keep the selection and the URL in step.
  useEffect(() => {
    setSelectedSeason(parseSeason(rawSeason));
  }, [rawSeason]);

  // Drop a season the show does not have (stale URL); auto-select a lone one.
  useEffect(() => {
    const seasons = data?.seasons;
    if (!seasons || seasons.length === 0) return;
    if (selectedSeason !== null) {
      if (!seasons.some((s) => s.season === selectedSeason)) setSelectedSeason(null);
      return;
    }
    if (seasons.length === 1) setSelectedSeason(seasons[0].season);
  }, [data, selectedSeason]);

  // Fetch user preferences for exact language match highlighting
  useEffect(() => {
    let cancelled = false;

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

    return () => {
      cancelled = true;
    };
  }, [tmdbId, currentCountry]);

  // Fetch official (Prime) offers for the series — optionally one season of it.
  useEffect(() => {
    let cancelled = false;
    const isSameSeries = loadedSeriesRef.current === tmdbId;
    if (isSameSeries) {
      // Season (or country) change: keep the current view and refresh inline.
      setSeasonLoading(true);
    } else {
      setData(null);
      setLoading(true);
    }
    setError(null);
    setPollTimedOut(false);

    fetchPrimeSeriesOffers(tmdbId, currentCountry, selectedSeason)
      .then((d) => {
        if (!cancelled) {
          setData(d);
          loadedSeriesRef.current = tmdbId;
          setLoading(false);
          setSeasonLoading(false);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message);
          setLoading(false);
          setSeasonLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [tmdbId, currentCountry, selectedSeason]);

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
      fetchPrimeSeriesOffers(tmdbId, currentCountry, selectedSeason)
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
  }, [shouldPoll, refreshing, pollTimedOut, tmdbId, currentCountry, selectedSeason]);

  // Fetch scraper offers (Phase C v2 endpoint)
  useEffect(() => {
    let cancelled = false;
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
  }, [tmdbId]);

  const posterUrl = data?.series?.poster_path
    ? `https://image.tmdb.org/t/p/w500${data.series.poster_path}`
    : null;

  const reloadSeries = useCallback(() => {
    setPollTimedOut(false);
    fetchPrimeSeriesOffers(tmdbId, currentCountry, selectedSeason)
      .then((d) => setData(d))
      .catch(() => {});
  }, [tmdbId, currentCountry, selectedSeason]);

  // Enqueue an explicit refresh and let the poll effect above watch the
  // queue settle; existing offers stay on screen until new ones land.
  const handleRefreshAvailability = async () => {
    setRefreshError(null);
    setPollTimedOut(false);
    setRefreshing(true);
    try {
      const result = await requestAvailabilityRefresh(tmdbId, "series");
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

  const seasons = data?.seasons ?? [];

  const selectSeason = (next: number | null) => {
    setSelectedSeason(next);
    const url = next === null ? `/tv/${tmdbId}` : `/tv/${tmdbId}?season=${next}`;
    if (typeof router.replace === "function") {
      router.replace(url);
    } else {
      router.push(url);
    }
  };

  const handleAddToWatchlist = async () => {
    try {
      await addToWatchlist(tmdbId, "series");
      setData((prev) => prev ? { ...prev, is_in_watchlist: true } : prev);
      reloadSeries();
    } catch (err) {
      console.error("Failed to add to watchlist:", err);
    }
  };

  const handleMarkWatched = async () => {
    try {
      const result = await markMovieWatched(tmdbId, {
        watched_date: new Date().toISOString().split('T')[0],
      }, "series");
      setData((prev) => prev ? { ...prev, user_data: result.user_data ?? { is_watched: true } } : prev);
      reloadSeries();
    } catch (err) {
      console.error("Failed to mark as watched:", err);
    }
  };

  const handleUnmarkWatched = async () => {
    try {
      const result = await unmarkMovieWatched(tmdbId, "series");
      setData((prev) => prev ? { ...prev, user_data: result.user_data ?? { ...(prev.user_data ?? {}), is_watched: false } } : prev);
      reloadSeries();
    } catch (err) {
      console.error("Failed to unmark as watched:", err);
    }
  };

  const handleRemoveFromWatchlist = async () => {
    try {
      await removeWatchlistItem(tmdbId, "series");
      setData((prev) => prev ? { ...prev, is_in_watchlist: false } : prev);
      reloadSeries();
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
          Loading series details & availability…
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
            {/* Poster image */}
            <div className="relative aspect-[2/3] w-full max-w-[180px] shrink-0 overflow-hidden rounded-xl bg-gray-200 dark:bg-gray-700">
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
                  <span className="text-3xl">📺</span>
                  <span className="text-xs">No Poster</span>
                </div>
              )}
            </div>

            {/* Series Info & Actions */}
            <div className="flex flex-1 flex-col justify-between">
              <div>
                <div className="mb-1.5 flex flex-wrap items-center gap-2">
                  <span className="rounded bg-purple-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-purple-800 dark:bg-purple-900/50 dark:text-purple-300">
                    TV Series
                  </span>
                  {data.series.content_type && data.series.content_type !== "series" && (
                    <span className="rounded bg-gray-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-gray-600 dark:bg-gray-700 dark:text-gray-300">
                      {data.series.content_type}
                    </span>
                  )}
                </div>

                <h1 className="text-2xl sm:text-3xl font-bold text-gray-900 dark:text-gray-100">
                  {data.series.title}
                  {data.series.year && (
                    <span className="ml-2 text-lg font-normal text-gray-500 dark:text-gray-400">
                      ({data.series.year})
                    </span>
                  )}
                </h1>

                {/* TMDb rating */}
                {data.series.tmdb_rating != null && (
                  <div className="mt-2 flex items-center gap-1 text-sm text-amber-600 dark:text-amber-400">
                    <span>★</span>
                    <span className="font-semibold">{data.series.tmdb_rating.toFixed(1)}</span>
                    <span className="text-xs text-gray-400 dark:text-gray-500">/10 TMDb</span>
                  </div>
                )}

                {/* Synopsis */}
                {data.series.overview && (
                  <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                    {data.series.overview}
                  </p>
                )}

                {/* Genres */}
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

                {/* Creators */}
                {data.series.director && data.series.director.length > 0 && (
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                      Created by
                    </span>
                    {data.series.director.map((name) => (
                      <span
                        key={name}
                        className="rounded-full bg-purple-50 px-2.5 py-0.5 text-xs text-purple-700 dark:bg-purple-900/30 dark:text-purple-300"
                      >
                        {name}
                      </span>
                    ))}
                  </div>
                )}

                {/* User Data & History */}
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
                <CastList cast={data.series.cast} />
              </div>
            </div>
          </div>

          {/* Offers List */}
          <section>
            <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
              <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                Streaming Availability & Language Matches
              </h2>

              <div className="flex flex-wrap items-center gap-3">
                {seasons.length > 1 ? (
                  <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
                    <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                      Season
                    </span>
                    <select
                      aria-label="Season"
                      value={
                        seasons.some((s) => s.season === selectedSeason)
                          ? String(selectedSeason)
                          : ""
                      }
                      onChange={(event) => {
                        const value = event.target.value;
                        selectSeason(value === "" ? null : Number(value));
                      }}
                      className="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm font-medium text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                    >
                      <option value="">All seasons</option>
                      {seasons.map((s) => (
                        <option key={s.season} value={String(s.season)}>
                          {s.name || `Season ${s.season}`}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : seasons.length === 1 ? (
                  <span className="text-sm text-gray-500 dark:text-gray-400">
                    {seasons[0].name || `Season ${seasons[0].season}`}
                  </span>
                ) : null}

                <RefreshAvailabilityButton
                  inProgress={refreshInProgress}
                  error={refreshError}
                  onRefresh={handleRefreshAvailability}
                />
              </div>
            </div>

            {seasonLoading && (
              <div className="mb-3 text-sm text-gray-500 dark:text-gray-400">
                Updating season availability…
              </div>
            )}

            {data.watchability && (
              <div className="mb-4 flex items-start gap-3 rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
                <span className="text-2xl leading-none" aria-hidden="true">
                  {data.watchability.status_symbol || "⚪"}
                </span>
                <div>
                  <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                    {data.watchability.status_label || "Availability unknown"}
                  </p>
                  {(data.watchability.explanation || []).length > 0 && (
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-gray-500 dark:text-gray-400">
                      {(data.watchability.explanation || []).map((line, i) => (
                        <li key={i}>{line}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            )}

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
                  {(data.last_known_offers ?? []).length === 0 && (
                    <AvailabilityState
                      availability={data.availability}
                      refreshing={false}
                    />
                  )}
                  {data.refresh_pending && (
                    <p className="mt-2 text-center text-xs text-gray-500 dark:text-gray-500">
                      An availability refresh is still queued.
                      <button
                        type="button"
                        onClick={reloadSeries}
                        className="ml-1 font-semibold text-blue-600 hover:underline dark:text-blue-400"
                      >
                        Check again
                      </button>
                    </p>
                  )}
                </>
              )
            ) : (
              (() => {
                // Lead with an offer the user can actually play; when none of
                // the found offers is included, say so rather than crowning an
                // unplayable one "Best Match".
                const bestOffer = data.offers.find((o: PrimeOffer) => o.is_accessible);
                const rest = data.offers.filter((o: PrimeOffer) => o !== bestOffer);
                return (
                  <div className="space-y-3">
                    {bestOffer ? (
                      <div className="rounded-xl border-2 border-green-400 bg-green-50 p-4 dark:border-green-600 dark:bg-green-900/20">
                        <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-green-800 dark:text-green-300">
                          Best Match
                        </h3>
                        <OfferRow
                          offer={bestOffer}
                          tmdbId={tmdbId}
                          isBest={true}
                          preferredAudio={preferences.audio_languages}
                          preferredSubtitles={preferences.subtitle_languages}
                          currentCountry={currentCountry}
                        />
                      </div>
                    ) : (
                      <p className="rounded-xl border border-dashed border-amber-300 bg-amber-50/60 p-3 text-xs text-amber-800 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-300">
                        None of the offers found is included with your
                        subscriptions — they are listed for reference.
                      </p>
                    )}

                    {rest.length > 0 && (
                      <div className="space-y-3 pt-2">
                        <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                          {bestOffer ? "All Ranked Offers" : "All Availability"}
                        </h3>
                        {rest.map((offer: PrimeOffer, i: number) => (
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

                    {/* Rent / buy: canonical availability that needs an extra
                        payment, kept apart from the included offers above. */}
                    {data.purchase_offers && data.purchase_offers.length > 0 && (
                      <div className="space-y-3 pt-2">
                        <div>
                          <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                            Rent or Buy
                          </h3>
                          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                            Available for an extra payment — not included in your subscriptions.
                          </p>
                        </div>
                        {data.purchase_offers.map((offer: PrimeOffer, i: number) => (
                          <OfferRow
                            key={`${offer.country}-${offer.service}-${offer.quality}-purchase-${i}`}
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
                );
              })()
            )}
            <LastKnownOffers
              offers={data.last_known_offers}
              tmdbId={tmdbId}
              preferredAudio={preferences.audio_languages}
              preferredSubtitles={preferences.subtitle_languages}
              currentCountry={currentCountry}
            />
          </section>

          {/* Scraper Sources Section — scraper rows carry no season signal, so the
              show-level list is hidden as soon as a season is selected (the API
              also answers `?season=N` with an empty list). */}
          {selectedSeason === null && unofficialLoading && (
            <div className="py-4 text-center text-sm text-gray-500 dark:text-gray-400">
              Loading scraper sources…
            </div>
          )}
          {selectedSeason === null && unofficialOffers && unofficialOffers.length > 0 && (
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
    </main>
  );
}
