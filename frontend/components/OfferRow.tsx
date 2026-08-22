"use client";

import type { PrimeOffer } from "@/lib/api";
import LanguageBadge from "@/components/LanguageBadge";

export { PrimeOffer };

const FLAG_MAP: Record<string, string> = {
  US: "🇺🇸", GB: "🇬🇧", CA: "🇨🇦", AU: "🇦🇺", DE: "🇩🇪", FR: "🇫🇷", IT: "🇮🇹",
  ES: "🇪🇸", JP: "🇯🇵", KR: "🇰🇷", BR: "🇧🇷", MX: "🇲🇽", IN: "🇮🇳", NL: "🇳🇱",
  NO: "🇳🇴", DK: "🇩🇰", FI: "🇫🇮", PL: "🇵🇱", PT: "🇵🇹", GR: "🇬🇷",
  TR: "🇹🇷", RU: "🇷🇺", CH: "🇨🇭", AT: "🇦🇹", BE: "🇧🇪", IE: "🇮🇪", SE: "🇸🇪",
  SG: "🇸🇬", HK: "🇭🇰", TW: "🇹🇼", TH: "🇹🇭", ID: "🇮🇩", MY: "🇲🇾", PH: "🇵🇭",
  AR: "🇦🇷", CL: "🇨🇱", CO: "🇨🇴", PE: "🇵🇪", ZA: "🇿🇦", NG: "🇳🇬",
  AE: "🇦🇪", SA: "🇸🇦", IL: "🇮🇱", UA: "🇺🇦", CZ: "🇨🇿", HU: "🇭🇺",
};

function flagEmoji(country: string): string {
  const code = country.toUpperCase();
  return FLAG_MAP[code] ?? "🌐";
}

function qualityLabel(q: string): string {
  const map: Record<string, string> = {
    "4k": "4K", uhd: "4K", hdr: "HDR", hd: "HD", sd: "SD",
  };
  return map[q.toLowerCase()] ?? q.toUpperCase();
}

interface OfferRowProps {
  offer: PrimeOffer;
  isBest?: boolean;
}

export default function OfferRow({ offer, isBest = false }: OfferRowProps) {
  const isAudioPreferred = offer.ranking.audio_rank === 0;
  const isSubPreferred = offer.ranking.subtitle_rank === 0;

  return (
    <div
      className={`
        rounded-xl border p-4 text-sm transition-all
        ${isBest
          ? "border-green-400 bg-green-50/80 shadow-xs dark:border-green-600 dark:bg-green-900/20"
          : "border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800/50"}
      `}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xl">{flagEmoji(offer.country)}</span>
          <span className="font-semibold text-gray-900 dark:text-gray-100">
            {offer.country}
          </span>
          <span className="rounded-md bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700 dark:bg-gray-700 dark:text-gray-300">
            {offer.service}
          </span>
          <span
            className={`rounded-md px-2 py-0.5 text-xs font-medium ${
              offer.offer_type === "free"
                ? "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300"
                : "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300"
            }`}
          >
            {offer.offer_type}
          </span>
          {offer.quality && (
            <span className="rounded-md bg-gray-200 px-2 py-0.5 font-mono text-xs font-bold text-gray-800 dark:bg-gray-600 dark:text-gray-200">
              {qualityLabel(offer.quality)}
            </span>
          )}
          {isBest && (
            <span className="rounded-full bg-green-600 px-2.5 py-0.5 text-xs font-bold text-white shadow-xs">
              Best Match
            </span>
          )}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-gray-600 dark:text-gray-400 border-t border-gray-100 pt-2.5 dark:border-gray-700/60">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-medium text-gray-500">Audio:</span>
          {offer.audio_languages.length > 0 ? (
            offer.audio_languages.map((code) => (
              <LanguageBadge
                key={`audio-${code}`}
                code={code}
                type="audio"
                isPreferred={isAudioPreferred}
              />
            ))
          ) : (
            <span className="text-gray-400">—</span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-medium text-gray-500">Subs:</span>
          {offer.subtitle_languages.length > 0 ? (
            offer.subtitle_languages.map((code) => (
              <LanguageBadge
                key={`sub-${code}`}
                code={code}
                type="subtitle"
                isPreferred={isSubPreferred}
              />
            ))
          ) : (
            <span className="text-gray-400">—</span>
          )}
        </div>
      </div>
    </div>
  );
}
