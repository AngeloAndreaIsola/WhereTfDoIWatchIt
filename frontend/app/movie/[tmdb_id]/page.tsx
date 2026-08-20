"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import OfferRow from "@/components/OfferRow";
import type { PrimeOffer, PrimeMovieResponse } from "@/lib/api";
import { fetchPrimeMovieOffers } from "@/lib/api";

interface MovieDetailPageProps {
  params: { tmdb_id: string };
}

export default function MovieDetailPage({ params }: MovieDetailPageProps) {
  const tmdbId = parseInt(params.tmdb_id, 10);
  const [data, setData] = useState<PrimeMovieResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);

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

  return (
    <main className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      {/* Back link */}
      <Link
        href="/"
        className="mb-4 inline-block text-sm text-blue-600 hover:underline dark:text-blue-400"
      >
        ← Back to browse
      </Link>

      {/* Loading */}
      {loading && (
        <div className="py-8 text-center text-sm text-gray-500 dark:text-gray-400">
          Loading Prime Video offers…
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Content */}
      {!loading && !error && data && (
        <>
          <h1 className="mb-1 text-2xl font-bold text-gray-900 dark:text-gray-100">
            {data.movie.title}
            {data.movie.year && (
              <span className="ml-2 text-base font-normal text-gray-500 dark:text-gray-400">
                ({data.movie.year})
              </span>
            )}
          </h1>

          {data.offers.length === 0 ? (
            <p className="py-6 text-center text-sm text-gray-500 dark:text-gray-400">
              Not available on Prime Video in any country.
            </p>
          ) : (
            <div className="mt-6 space-y-3">
              {/* Best match card */}
              <div className="rounded-lg border-2 border-green-400 bg-green-50 p-4 dark:border-green-600 dark:bg-green-900/20">
                <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-green-800 dark:text-green-300">
                  Best Match
                </h2>
                <OfferRow offer={data.offers[0]} isBest={true} />
              </div>

              {/* All ranked offers */}
              <h2 className="mt-6 text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                All Prime Video Offers
              </h2>
              {data.offers.map((offer: PrimeOffer, i: number) => (
                <OfferRow
                  key={`${offer.country}-${offer.service}-${offer.quality}-${i}`}
                  offer={offer}
                  isBest={i === 0}
                />
              ))}
            </div>
          )}
        </>
      )}
    </main>
  );
}
