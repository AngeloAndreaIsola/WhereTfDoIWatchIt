"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import OfferRow from "@/components/OfferRow";
import MovieEditModal from "@/components/MovieEditModal";
import type { PrimeOffer, PrimeMovieResponse } from "@/lib/api";
import { fetchPrimeMovieOffers } from "@/lib/api";
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

export default function MovieDetailPage({ params }: MovieDetailPageProps) {
  const tmdbId = parseInt(params.tmdb_id, 10);
  const router = useRouter();
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
  const [showEditModal, setShowEditModal] = useState(false);

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
    fetchPrimeMovieOffers(tmdbId)
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

    // Fetch unofficial offers (Phase C v2 endpoint)
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

          {/* Unofficial Offers Section (Phase C) */}
          {unofficialLoading && (
            <div className="py-4 text-center text-sm text-gray-500 dark:text-gray-400">
              Loading unofficial sources…
            </div>
          )}
          {unofficialOffers && unofficialOffers.length > 0 && (
            <section>
              <h2 className="mb-4 text-lg font-bold text-gray-900 dark:text-gray-100">
                Unofficial Sources ({unofficialOffers.length})
              </h2>
              <div className="space-y-3">
                {unofficialOffers.map((offer, i) => {
                  const SOURCE_BADGE_COLORS: Record<string, string> = {
                    Unofficial:
                      "bg-amber-100 text-amber-800 dark:bg-amber-900/60 dark:text-amber-300",
                    Free:
                      "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/60 dark:text-emerald-300",
                    Official:
                      "bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-300",
                    Scraper:
                      "bg-purple-100 text-purple-800 dark:bg-purple-900/60 dark:text-purple-300",
                  };
                  const badgeColor =
                    SOURCE_BADGE_COLORS[offer.source_indicator] ||
                    SOURCE_BADGE_COLORS["Unofficial"];
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
