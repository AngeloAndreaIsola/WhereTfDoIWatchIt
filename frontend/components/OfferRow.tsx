"use client";

import type { PrimeOffer } from "@/lib/api";
import LanguageBadge from "@/components/LanguageBadge";
import { normalizeLanguageCode } from "@/lib/languages";

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

/** Offer types where a per-title price is meaningful to show. */
const PRICE_OFFER_TYPES = new Set(["rent", "buy", "addon"]);

/**
 * Locale-aware currency formatting from the structured amount/currency the API
 * sends. The amount arrives as a decimal string, so it is converted at the
 * display boundary only — never stored or compared as a float.
 */
export function formatPrice(amount: string, currency: string, locale?: string): string {
  const value = Number(amount);
  if (!Number.isFinite(value)) return "";
  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      currencyDisplay: "narrowSymbol",
    }).format(value);
  } catch {
    // An unknown-but-well-formed currency code must not blank the row.
    return `${amount} ${currency}`;
  }
}

interface OfferRowProps {
  offer: PrimeOffer;
  tmdbId?: number;
  isBest?: boolean;
  preferredAudio?: string[];
  preferredSubtitles?: string[];
  currentCountry?: string;
}

export default function OfferRow({
  offer,
  tmdbId,
  isBest = false,
  preferredAudio,
  preferredSubtitles,
  currentCountry,
}: OfferRowProps) {
  const prefAudioSet = new Set(
    (preferredAudio || []).map((c) => normalizeLanguageCode(c))
  );

  const prefSubSet = new Set(
    (preferredSubtitles || []).map((c) => normalizeLanguageCode(c))
  );

  const watchUrl = offer.url || (tmdbId ? `/watch/${tmdbId}` : null);

  // A price is only shown when a source confirmed it for the latest
  // observation: a remembered (stale) value is never presented as fresh, and a
  // missing price is never rendered as "free".
  const priceApplicable = PRICE_OFFER_TYPES.has(
    (offer.offer_type || "").toLowerCase()
  );
  const confirmedPrice =
    offer.price &&
    offer.price.amount &&
    offer.price.currency &&
    offer.price.is_current !== false
      ? offer.price
      : null;
  const priceText = confirmedPrice
    ? formatPrice(confirmedPrice.amount as string, confirmedPrice.currency)
    : "";

  // An offer the user cannot play with their current services is still real
  // availability — show it, muted and labelled, rather than hiding it. This is
  // the movie-194 fix: the offer list used to drop unowned services entirely,
  // which made "available elsewhere" look like "nothing found".
  const notEntitled = offer.is_entitled === false;
  const notAccessible = offer.is_accessible === false;
  // Why the row is muted. An unowned service is the common case; an offer the
  // user *does* own can still be unreachable (wrong country) or unusable
  // (language rules), and saying "not reachable" for a language block is
  // simply wrong. Prefer the resolver's own rejection reason.
  const mutedReason = (() => {
    if (!notAccessible) return "";
    if (offer.is_addon && offer.required_base_display) {
      return `Add-on channel — requires ${offer.required_base_display}`;
    }
    if (notEntitled) return "Not in your subscriptions — shown for reference";
    const reasons = (offer.rejection_reasons || []).join(" ").toLowerCase();
    if (reasons.includes("language")) return "Not available in your languages";
    if (reasons.includes("reach") || reasons.includes("country") || reasons.includes("vpn")) {
      return "Not reachable from your settings";
    }
    return "Not available with your current settings";
  })();
  const localityBadge = offer.locality
    ? {
        local: { text: "Local", className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300" },
        anywhere: { text: "Local", className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300" },
        vpn: { text: "VPN required", className: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300" },
        out_of_reach: { text: "Not reachable", className: "bg-gray-200 text-gray-700 dark:bg-gray-700 dark:text-gray-300" },
      }[offer.locality]
    : null;

  return (
    <div
      data-testid="offer-row"
      data-entitled={notEntitled ? "false" : "true"}
      className={`
        rounded-xl border p-4 text-sm transition-all
        ${notAccessible
          ? "border-dashed border-gray-300 bg-gray-50/70 opacity-80 dark:border-gray-600 dark:bg-gray-800/40"
          : isBest
          ? "border-green-400 bg-green-50/80 shadow-xs dark:border-green-600 dark:bg-green-900/20"
          : "border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800/50"}
      `}
    >
      {notAccessible && (
        <p
          data-testid="offer-not-subscribed"
          className="mb-2 text-xs font-semibold text-gray-500 dark:text-gray-400"
        >
          {mutedReason}
        </p>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xl">{flagEmoji(offer.country)}</span>
          <span className="font-semibold text-gray-900 dark:text-gray-100">
            {offer.country}
          </span>
          <span className="rounded-md bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700 dark:bg-gray-700 dark:text-gray-300">
            {offer.service}
          </span>
          {offer.source_indicator && (
            <span
              className={`rounded-md px-2 py-0.5 text-xs font-medium ${
                offer.source_indicator === "BVOD" || offer.source_indicator === "AVOD" || offer.source_indicator === "FAST"
                  ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300"
                  : offer.source_indicator === "Official" || offer.source_indicator === "SVOD" || offer.source_indicator === "TVOD"
                  ? "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300"
                  : "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300"
              }`}
            >
              {offer.source_indicator}
            </span>
          )}
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
          {priceApplicable && confirmedPrice && priceText && (
            <span
              data-testid="offer-price"
              data-amount={confirmedPrice.amount}
              data-currency={confirmedPrice.currency}
              className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-800 dark:bg-slate-700 dark:text-slate-100"
            >
              {confirmedPrice.is_from ? `from ${priceText}` : priceText}
            </span>
          )}
          {priceApplicable && !confirmedPrice && (
            <span
              data-testid="offer-price-missing"
              className="rounded-md bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-500 dark:bg-gray-700 dark:text-gray-400"
            >
              Price not currently confirmed
            </span>
          )}
          {offer.is_addon && (
            <span className="rounded-md bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
              Add-on
              {offer.required_base_display ? ` · needs ${offer.required_base_display}` : ""}
            </span>
          )}
          {offer.access_label && !notEntitled && !offer.is_addon && (
            <span
              className={`rounded-md px-2 py-0.5 text-xs font-medium ${
                notAccessible
                  ? "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300"
                  : "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300"
              }`}
            >
              {offer.access_label}
            </span>
          )}
          {isBest && !notAccessible && (
            <span className="rounded-full bg-green-600 px-2.5 py-0.5 text-xs font-bold text-white shadow-xs">
              Best Match
            </span>
          )}
          {offer.is_language_match === false && (
            <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
              Language Mismatch
            </span>
          )}
          {localityBadge ? (
            <span
              className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${localityBadge.className}`}
            >
              {localityBadge.text}
            </span>
          ) : (
            currentCountry && (
              <span
                className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                  offer.country.toUpperCase() === currentCountry.toUpperCase()
                    ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300"
                    : "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300"
                }`}
              >
                {offer.country.toUpperCase() === currentCountry.toUpperCase() ? "Local" : "VPN required"}
              </span>
            )
          )}
        </div>

        {/* Watch Button next to offer */}
        {watchUrl && (
          <a
            href={watchUrl}
            target={offer.url ? "_blank" : "_self"}
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600 transition-all"
          >
            <span>▶</span>
            <span>Watch</span>
          </a>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-gray-600 dark:text-gray-400 border-t border-gray-100 pt-2.5 dark:border-gray-700/60">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-semibold text-gray-700 dark:text-gray-300">Audio 🎧:</span>
          {offer.audio_languages.length > 0 ? (
            offer.audio_languages.map((code) => (
              <LanguageBadge
                key={`audio-${code}`}
                code={code}
                type="audio"
                isPreferred={prefAudioSet.has(normalizeLanguageCode(code))}
              />
            ))
          ) : (
            <span className="text-gray-400">—</span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-semibold text-gray-700 dark:text-gray-300">Subs 💬:</span>
          {offer.subtitle_languages.length > 0 ? (
            offer.subtitle_languages.map((code) => (
              <LanguageBadge
                key={`sub-${code}`}
                code={code}
                type="subtitle"
                isPreferred={prefSubSet.has(normalizeLanguageCode(code))}
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
