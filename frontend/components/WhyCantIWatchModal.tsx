"use client";

export interface ExcludedOffer {
  service: string;
  country: string;
  offer_type: string;
  price_info?: string;
  rejection_reasons?: string[];
}

interface WhyCantIWatchModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  explanations: string[];
  excludedOffers?: ExcludedOffer[];
}

export function WhyCantIWatchModal({
  isOpen,
  onClose,
  title,
  explanations,
  excludedOffers = [],
}: WhyCantIWatchModalProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-2xl max-w-lg w-full p-6 shadow-xl space-y-4 max-h-[85vh] overflow-y-auto">
        <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-800 pb-3">
          <h3 className="text-lg font-bold text-gray-900 dark:text-gray-100">
            Why can&apos;t I watch &quot;{title}&quot;?
          </h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl font-bold"
          >
            ×
          </button>
        </div>

        {explanations.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
              Summary Reasons
            </h4>
            <ul className="list-disc list-inside text-sm text-gray-700 dark:text-gray-300 space-y-1 bg-gray-50 dark:bg-gray-800/50 p-3 rounded-lg">
              {explanations.map((exp, idx) => (
                <li key={idx}>{exp}</li>
              ))}
            </ul>
          </div>
        )}

        {excludedOffers.length > 0 && (
          <div className="space-y-2 pt-2">
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
              Excluded / Non-Qualifying Offers
            </h4>
            <div className="space-y-2">
              {excludedOffers.map((offer, idx) => (
                <div
                  key={idx}
                  className="p-3 border border-gray-200 dark:border-gray-800 rounded-lg text-xs space-y-1 bg-white dark:bg-gray-800"
                >
                  <div className="flex items-center justify-between font-semibold">
                    <span>
                      {offer.service} ({offer.country})
                    </span>
                    <span className="capitalize text-amber-600 dark:text-amber-400">
                      {offer.offer_type} {offer.price_info && `(${offer.price_info})`}
                    </span>
                  </div>
                  {offer.rejection_reasons && offer.rejection_reasons.length > 0 && (
                    <p className="text-gray-500 dark:text-gray-400 italic">
                      Reason: {offer.rejection_reasons.join("; ")}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="pt-2 text-right">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium rounded-lg bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-800 dark:text-gray-200"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
