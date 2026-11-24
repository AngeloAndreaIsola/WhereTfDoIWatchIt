"use client";

import OfferRow from "@/components/OfferRow";
import type { PrimeOffer } from "@/lib/api";
import { unverifiedOfferLabel } from "@/lib/availability";

interface LastKnownOffersProps {
  offers?: PrimeOffer[];
  tmdbId?: number;
  preferredAudio?: string[];
  preferredSubtitles?: string[];
  currentCountry?: string;
}

/**
 * Stale ("last seen") or unverified offers. Kept apart from the current offer
 * list so old evidence stays discoverable without being read as "watchable now".
 */
export default function LastKnownOffers({
  offers,
  tmdbId,
  preferredAudio,
  preferredSubtitles,
  currentCountry,
}: LastKnownOffersProps) {
  if (!offers || offers.length === 0) return null;
  return (
    <div data-testid="last-known-offers" className="space-y-3 pt-2">
      <div>
        <h3 className="text-xs font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400">
          🕓 Last known availability — needs recheck
        </h3>
        <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
          Not re-confirmed recently, so not counted as watchable now.
        </p>
      </div>
      {offers.map((offer, i) => (
        <div key={`${offer.country}-${offer.service}-${offer.offer_type}-last-known-${i}`}>
          <p
            data-testid="last-known-label"
            className="mb-1 text-xs font-semibold text-amber-700 dark:text-amber-400"
          >
            {unverifiedOfferLabel(offer)}
          </p>
          <OfferRow
            offer={offer}
            tmdbId={tmdbId}
            isBest={false}
            preferredAudio={preferredAudio}
            preferredSubtitles={preferredSubtitles}
            currentCountry={currentCountry}
          />
        </div>
      ))}
    </div>
  );
}
