import type { SourceReportedOffer } from "@/lib/api";
import OfferRow from "./OfferRow";

export default function SourceReportedOffers({
  offers,
  currentCountry,
}: {
  offers?: SourceReportedOffer[];
  currentCountry?: string;
}) {
  if (!offers?.length) return null;

  return (
    <section className="my-5 space-y-3">
      <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Reported by availability sources</h3>
      <p className="text-xs text-gray-600 dark:text-gray-400">Your subscription access and playback are unverified.</p>
      {offers.map((offer, index) => (
        <OfferRow
          key={`${offer.reporting_source}-${offer.country}-${offer.service}-${offer.offer_type}-${index}`}
          offer={offer}
          currentCountry={currentCountry}
        />
      ))}
    </section>
  );
}
