import type {
  LeavingSoonItem,
  LeavingSoonMovie,
  LeavingSoonOffer,
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
