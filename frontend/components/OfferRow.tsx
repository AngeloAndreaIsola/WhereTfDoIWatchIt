"use client";

import type { PrimeOffer } from "@/lib/api";
import LanguageBadge from "@/components/LanguageBadge";
import { normalizeLanguageCode } from "@/lib/languages";
import { isUnverifiedOffer, UNVERIFIED_BADGE } from "@/lib/availability";
import {
  explainReason,
  isAccessibleEntitlementOffer,
  isLegacyPlanOffer,
  isPositiveVerdict,
  presentVerdict,
} from "@/lib/entitlement";

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
  offer: Omit<PrimeOffer, "ranking"> & Partial<Pick<PrimeOffer, "ranking">>;
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

  const reported = offer.availability_evidence === "source_reported";
  const unverified = isUnverifiedOffer(offer);
  const entitlement = offer.entitlement ?? null;
  const verdict = entitlement?.verdict ?? null;
  const verdictPresentation = presentVerdict(entitlement);
  const legacyPlan = isLegacyPlanOffer(offer);
  const positive = !unverified &&
    (entitlement ? isPositiveVerdict(entitlement) : !legacyPlan && offer.is_entitled === true);
  const unconfirmed = legacyPlan || verdict === "UNKNOWN";
  const bestMatch = isBest && isAccessibleEntitlementOffer(offer);
  const transaction = verdict === "RENT_OR_BUY";
  const watchUrl = offer.url || (!reported && !transaction && tmdbId ? `/watch/${tmdbId}` : null);
  const linkLabel = unverified
    ? offer.url ? "Check provider" : "Verify availability"
    : transaction
    ? verdictPresentation.label
    : unconfirmed
    ? offer.url ? "Check provider" : "Why can't I watch?"
    : entitlement && !isAccessibleEntitlementOffer(offer)
    ? offer.url ? "Check provider" : "Why can't I watch?"
    : "Watch";

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
  const notEntitled =
    !unverified && !unconfirmed &&
    (entitlement
      ? !positive
      : offer.is_entitled === false);
  const notAccessible = !reported && offer.is_accessible === false;
  // Why the row is muted. An unowned service is the common case; an offer the
  // user *does* own can still be unreachable (wrong country) or unusable
  // (language rules), and saying "not reachable" for a language block is
  // simply wrong. Prefer the resolver's own rejection reason.
  const mutedReason = (() => {
    if (!notAccessible) return "";
    if (unverified && !entitlement) return "Availability needs a recheck";
    if (entitlement) {
      if (verdict === "UNKNOWN") return explainReason(entitlement.reason);
      if (verdict === "BLOCKED") return explainReason(entitlement.reason);
      if (verdict === "REQUIRES_ADDON") {
        const channel = entitlement.required_channel ?? "this channel";
        const host = entitlement.required_base ?? offer.required_base_display ?? "";
        return `Add-on channel ${channel} is not held${host ? ` on ${host}` : ""}`;
      }
      if (verdict === "RENT_OR_BUY") return "A separate paid action is required — not included in your plan";
    }
    if (offer.is_addon && offer.required_base_display) {
      return `Add-on channel — requires ${offer.required_base_display}`;
    }
    if (notEntitled) return "Not in your subscriptions — shown for reference";
    const reasons = (offer.rejection_reasons || []).join(" ").toLowerCase();
    if (reasons.includes("territory information unknown")) return "Territory unknown";
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
        unknown: { text: "Territory unknown", className: "bg-gray-200 text-gray-700 dark:bg-gray-700 dark:text-gray-300" },
      }[offer.locality]
    : null;

  return (
    <div
      data-testid="offer-row"
      data-entitled={
        unverified || unconfirmed
          ? "unknown"
          : positive ? "true" : notEntitled ? "false" : "unknown"
      }
      data-entitlement-verdict={verdict ?? "missing"}
      className={`
        rounded-xl border p-4 text-sm transition-all
        ${notAccessible
          ? "border-dashed border-gray-300 bg-gray-50/70 opacity-80 dark:border-gray-600 dark:bg-gray-800/40"
          : bestMatch
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
          {(entitlement || legacyPlan) && (
            <span
              data-testid="entitlement-verdict"
              data-verdict={entitlement?.verdict ?? "missing"}
              data-reason={entitlement?.reason}
              className={`rounded-md px-2 py-0.5 text-xs font-semibold ${
                verdictPresentation.tone === "positive"
                  ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300"
                  : verdictPresentation.tone === "ads"
                  ? "bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-300"
                  : verdictPresentation.tone === "transaction"
                  ? "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-300"
                  : verdictPresentation.tone === "addon"
                  ? "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300"
                  : verdictPresentation.tone === "blocked"
                  ? "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300"
                  : "bg-gray-200 text-gray-700 dark:bg-gray-700 dark:text-gray-200"
              }`}
            >
              {verdictPresentation.label}
            </span>
          )}
          {unverified && (
            <span
              data-testid="unverified-badge"
              data-verification-state={offer.availability_evidence}
              className="rounded-md bg-amber-100 px-2 py-0.5 text-xs font-bold text-amber-900 dark:bg-amber-900/40 dark:text-amber-200"
              title={reported
                ? "An availability report from the named source"
                : "Last seen or uncorroborated availability — recheck required. Not a confirmed current offer."}
            >
              {reported ? "Source-reported" : UNVERIFIED_BADGE}
              {reported && offer.reporting_source === "justwatch" && " · JustWatch"}
              {reported && offer.reporting_source === "watchmode" && " · Watchmode"}
            </span>
          )}
          {offer.quality && (
            <span className="rounded-md bg-gray-200 px-2 py-0.5 font-mono text-xs font-bold text-gray-800 dark:bg-gray-600 dark:text-gray-200">
              {qualityLabel(offer.quality)}
            </span>
          )}
          {reported && offer.observed_at && offer.observed_at !== offer.last_checked_at && (
            <time className="text-xs text-gray-500 dark:text-gray-400" dateTime={offer.observed_at}>
              Observed {new Date(offer.observed_at).toLocaleString(undefined, {
                dateStyle: "medium", timeStyle: "short", timeZone: "UTC",
              })} UTC
            </time>
          )}
          {reported && offer.last_checked_at && (
            <time className="text-xs text-gray-500 dark:text-gray-400" dateTime={offer.last_checked_at}>
              Last checked {new Date(offer.last_checked_at).toLocaleString(undefined, {
                dateStyle: "medium", timeStyle: "short", timeZone: "UTC",
              })} UTC
            </time>
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
          {offer.access_label && !entitlement && !unverified && !legacyPlan && !notEntitled && !offer.is_addon && (
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
          {bestMatch && (
            <span className="rounded-full bg-green-600 px-2.5 py-0.5 text-xs font-bold text-white shadow-xs">
              Best Match
            </span>
          )}
          {offer.is_language_match === false && (
            <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
              Language Mismatch
            </span>
          )}
          {offer.ranking?.explanation && offer.ranking.explanation.length > 0 && (
            <span
              data-testid="ranking-explanation"
              className="rounded-md bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300"
              title={offer.ranking.explanation.join(" · ")}
            >
              {offer.ranking.explanation[0]}
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

        {watchUrl && (
          <a
            href={watchUrl}
            target={offer.url ? "_blank" : "_self"}
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-xs hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600 transition-all"
          >
            {linkLabel === "Watch" && <span aria-hidden="true">▶</span>}
            <span>{linkLabel}</span>
          </a>
        )}
      </div>

      {entitlement && (
        <div className="mt-3 space-y-1 border-t border-gray-100 pt-2.5 text-[11px] dark:border-gray-700/60">
          {!positive && (
            <p data-testid="entitlement-reason" className="text-gray-600 dark:text-gray-300">
              {explainReason(entitlement.reason)}
            </p>
          )}
          {entitlement.billing_country && entitlement.offer_country &&
            entitlement.billing_country.toUpperCase() !== entitlement.offer_country.toUpperCase() && (
              <p data-testid="region-caveat" className="text-amber-700 dark:text-amber-400">
                Billing {entitlement.billing_country} · offer {entitlement.offer_country} —
                plan coverage in another market needs reviewed portability.
              </p>
            )}
          {(entitlement.reason === "PORTABILITY_UNVERIFIED" ||
            entitlement.reason === "REGION_EXCLUDED" ||
            entitlement.reason === "TERRITORY_UNKNOWN") && (
            <p data-testid="region-caveat" className="text-amber-700 dark:text-amber-400">
              Region caveat: this market is not confirmed by your selected plan.
            </p>
          )}
          {entitlement.plan_id && (
            <p className="text-gray-500 dark:text-gray-400">
              Selected plan: {entitlement.plan_id}
              {entitlement.required_channel && ` · channel: ${entitlement.required_channel}`}
            </p>
          )}
          {entitlement.caveats.map((caveat) => (
            <p
              key={caveat}
              data-testid="entitlement-caveat"
              className="text-gray-500 dark:text-gray-400"
            >
              {caveat}
            </p>
          ))}
          {entitlement.source_label && (
            <p className="text-gray-400 dark:text-gray-500">
              Evidence: {entitlement.source_label}
            </p>
          )}
        </div>
      )}

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
