import type {
  LeavingSoonItem,
  LeavingSoonMovie,
  LeavingSoonOffer,
  LeavingSoonResponse,
} from "./api";

export interface LeavingSoonDepartureEntry {
  movie: LeavingSoonMovie;
  offer: LeavingSoonOffer;
}

export interface GroupedLeavingOffers {
  next_7_days: LeavingSoonDepartureEntry[];
  days_8_14: LeavingSoonDepartureEntry[];
  days_15_30: LeavingSoonDepartureEntry[];
  totalDepartures: number;
}

const MS_PER_DAY = 86_400_000;

/**
 * `BoundedPageCache` stores a CachedPage whose `items` array holds the page
 * payload. Leaving Soon stores one response per country key; reading the
 * cache entry itself as a response would make `items` an array of responses
 * and silently drop every departure.
 */
export function getCachedLeavingSoon(
  cached: unknown
): LeavingSoonResponse | undefined {
  if (!cached || typeof cached !== "object") return undefined;
  const items = (cached as { items?: unknown }).items;
  if (!Array.isArray(items) || items.length !== 1) return undefined;
  const response = items[0];
  if (!response || typeof response !== "object") return undefined;
  const candidate = response as LeavingSoonResponse;
  if (
    typeof candidate.country !== "string" ||
    !candidate.horizon ||
    !Array.isArray(candidate.items) ||
    !candidate.coverage
  ) {
    return undefined;
  }
  return candidate;
}

const VALID_COVERAGE_STATUSES = new Set([
  "no_confirmed_expiry_data",
  "no_departures_within_horizon",
  "partial_confirmed_departures",
]);

const validIso = (value: unknown): value is string =>
  typeof value === "string" && !Number.isNaN(new Date(value).getTime());

const validCount = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0;

/**
 * Fail closed: a successful HTTP 200 with missing or malformed fields must
 * become an error state, never an apparently valid "no evidence" result.
 */
export function parseLeavingSoonResponse(
  raw: unknown,
  expectedCountry: string
): LeavingSoonResponse {
  if (!raw || typeof raw !== "object") {
    throw new Error("Malformed Leaving Soon response");
  }
  const value = raw as Partial<LeavingSoonResponse>;
  const horizon = value.horizon;
  const coverageValue = value.coverage;
  if (
    typeof value.country !== "string" ||
    value.country.trim().toUpperCase() !== expectedCountry ||
    !horizon ||
    !Number.isInteger(horizon.days) ||
    horizon.days < 1 ||
    horizon.days > 90 ||
    !validIso(horizon.as_of) ||
    !validIso(horizon.until) ||
    new Date(horizon.until).getTime() <= new Date(horizon.as_of).getTime() ||
    !Array.isArray(value.items) ||
    !coverageValue ||
    !VALID_COVERAGE_STATUSES.has(coverageValue.status) ||
    !validCount(coverageValue.watchlist_titles) ||
    !validCount(coverageValue.titles_with_confirmed_expiry_data) ||
    !validCount(coverageValue.offers_with_confirmed_expiry_data) ||
    typeof coverageValue.scope !== "string" ||
    coverageValue.scope.length === 0 ||
    typeof coverageValue.completeness !== "string" ||
    coverageValue.completeness.length === 0
  ) {
    throw new Error("Malformed Leaving Soon response");
  }
  for (const item of value.items) {
    if (
      !item ||
      !item.movie ||
      typeof item.movie.tmdb_id !== "number" ||
      typeof item.movie.title !== "string" ||
      typeof item.movie.content_type !== "string" ||
      !Array.isArray(item.leaving_offers)
    ) {
      throw new Error("Malformed Leaving Soon response");
    }
    for (const offer of item.leaving_offers) {
      if (
        !offer ||
        typeof offer.provider !== "string" ||
        typeof offer.country !== "string" ||
        offer.country.trim().toUpperCase() !== expectedCountry ||
        typeof offer.provider_display !== "string" ||
        typeof offer.offer_type !== "string" ||
        typeof offer.access_type !== "string" ||
        typeof offer.locality !== "string" ||
        (offer.season_number != null &&
          (!Number.isInteger(offer.season_number) || offer.season_number < 0)) ||
        (offer.episode_number != null &&
          (!Number.isInteger(offer.episode_number) || offer.episode_number < 0)) ||
        (offer.last_verified_at != null && !validIso(offer.last_verified_at)) ||
        !Array.isArray(offer.sources) ||
        !offer.sources.every((source) => typeof source === "string") ||
        offer.evidence !== "verified" ||
        !validIso(offer.available_until) ||
        (offer.departure_kind !== "leaving_provider" &&
          offer.departure_kind !== "last_option")
      ) {
        throw new Error("Malformed Leaving Soon response");
      }
    }
  }
  return value as LeavingSoonResponse;
}

/**
 * Groups leaving offers into three disjoint local-time / UTC-preserving horizon buckets:
 * - Next 7 days: (0, 7 days]
 * - Days 8–14: (7 days, 14 days]
 * - Days 15–30: (14 days, 30 days]
 *
 * Grouping is based on absolute UTC durations from `as_of` (preserving UTC boundary
 * consistency across local timezones and DST transitions), while display formatting
 * presents localized time.
 */
export function groupLeavingOffers(
  items: LeavingSoonItem[] = [],
  asOfIso: string
): GroupedLeavingOffers {
  const asOfTime = new Date(asOfIso).getTime();

  const next7: LeavingSoonDepartureEntry[] = [];
  const days8To14: LeavingSoonDepartureEntry[] = [];
  const days15To30: LeavingSoonDepartureEntry[] = [];

  for (const item of items) {
    if (!item.movie || !Array.isArray(item.leaving_offers)) continue;
    for (const offer of item.leaving_offers) {
      if (!offer.available_until) continue;
      const expiryTime = new Date(offer.available_until).getTime();
      if (Number.isNaN(expiryTime) || Number.isNaN(asOfTime)) continue;

      const diffMs = expiryTime - asOfTime;

      // Exclude expired (diffMs <= 0) or outside 30-day horizon (diffMs > 30 days)
      if (diffMs <= 0 || diffMs > 30 * MS_PER_DAY) {
        continue;
      }

      const entry: LeavingSoonDepartureEntry = {
        movie: item.movie,
        offer,
      };

      if (diffMs <= 7 * MS_PER_DAY) {
        next7.push(entry);
      } else if (diffMs <= 14 * MS_PER_DAY) {
        days8To14.push(entry);
      } else {
        days15To30.push(entry);
      }
    }
  }

  const sortEntries = (a: LeavingSoonDepartureEntry, b: LeavingSoonDepartureEntry) => {
    const timeA = new Date(a.offer.available_until).getTime();
    const timeB = new Date(b.offer.available_until).getTime();
    if (timeA !== timeB) return timeA - timeB;

    const titleA = (a.movie.title || "").toLowerCase();
    const titleB = (b.movie.title || "").toLowerCase();
    if (titleA !== titleB) return titleA.localeCompare(titleB);

    const provA = (a.offer.provider_display || a.offer.provider || "").toLowerCase();
    const provB = (b.offer.provider_display || b.offer.provider || "").toLowerCase();
    if (provA !== provB) return provA.localeCompare(provB);

    const sA = a.offer.season_number ?? -1;
    const sB = b.offer.season_number ?? -1;
    if (sA !== sB) return sA - sB;

    const eA = a.offer.episode_number ?? -1;
    const eB = b.offer.episode_number ?? -1;
    return eA - eB;
  };

  next7.sort(sortEntries);
  days8To14.sort(sortEntries);
  days15To30.sort(sortEntries);

  return {
    next_7_days: next7,
    days_8_14: days8To14,
    days_15_30: days15To30,
    totalDepartures: next7.length + days8To14.length + days15To30.length,
  };
}

export function formatDepartureDate(iso: string, locale?: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(locale || "en", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function formatDepartureDateTime(iso: string, locale?: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(locale || "en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatObservedDate(iso: string, locale?: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(locale || "en", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function getAccessTypeLabel(
  accessType: string | undefined,
  t: (key: string, params?: Record<string, string | number>) => string
): string {
  switch (accessType) {
    case "subscription":
      return t("leavingSoon.accessSubscription");
    case "free_with_ads":
      return t("leavingSoon.accessFreeWithAds");
    case "free":
      return t("leavingSoon.accessFree");
    case "addon":
      return t("leavingSoon.accessAddon");
    default:
      return t("leavingSoon.accessOther");
  }
}
