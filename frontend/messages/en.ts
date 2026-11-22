export interface TranslationCatalog {
  nav: {
    home: string;
    catalog: string;
    watchlist: string;
    watched: string;
    preferences: string;
    subscriptions: string;
    candidateReview: string;
    openMenu: string;
    closeMenu: string;
    mainNav: string;
  };
  locale: {
    label: string;
    en: string;
    it: string;
    ja: string;
    switchLanguage: string;
  };
  common: {
    appName: string;
    appTagline: string;
    loading: string;
    error: string;
    save: string;
    cancel: string;
    search: string;
    searchPlaceholder: string;
    clearFilters: string;
    noResults: string;
    all: string;
    seasons: string;
    providers: string;
    countries: string;
  };
  movie: {
    watchable: string;
    notWatchable: string;
    runtime: string;
    year: string;
  };
  leavingSoon: {
    title: string;
    description: string;
    badgeLeavingSoon: string;
    groupNext7Days: string;
    groupDays8To14: string;
    groupDays15To30: string;
    leavingProvider: string;
    lastOption: string;
    leavingDate: string;
    lastVerified: string;
    seasonLabel: string;
    seasonEpisodeLabel: string;
    accessSubscription: string;
    accessFreeWithAds: string;
    accessFree: string;
    accessAddon: string;
    accessOther: string;
    stateLoading: string;
    stateNoEvidence: string;
    stateNoEvidenceDesc: string;
    stateNoneInHorizon: string;
    stateNoneInHorizonDesc: string;
    stateEmptyWatchlist: string;
    stateEmptyWatchlistDesc: string;
    stateError: string;
    stateOffline: string;
    retry: string;
    coverageNotice: string;
    coverageNoticeSparse: string;
    srDepartureDate: string;
  };
}

export const en: TranslationCatalog = {
  nav: {
    home: "Home",
    catalog: "Catalog",
    watchlist: "Watchlist",
    watched: "Watched",
    preferences: "Preferences",
    subscriptions: "Subscriptions",
    candidateReview: "Candidate Review",
    openMenu: "Open menu",
    closeMenu: "Close menu",
    mainNav: "Main navigation",
  },
  locale: {
    label: "Language",
    en: "English",
    it: "Italiano",
    ja: "日本語",
    switchLanguage: "Switch interface language",
  },
  common: {
    appName: "Where TF Do I Watch It",
    appTagline: "Find where to stream any movie — ranked by your preferences",
    loading: "Loading...",
    error: "Error: {message}",
    save: "Save",
    cancel: "Cancel",
    search: "Search",
    searchPlaceholder: "Search for a movie or TV show...",
    clearFilters: "Clear filters",
    noResults: "No results found",
    all: "All",
    seasons: "Seasons",
    providers: "Providers",
    countries: "Countries",
  },
  movie: {
    watchable: "Watchable",
    notWatchable: "Not watchable",
    runtime: "{minutes} min",
    year: "{year}",
  },
  leavingSoon: {
    title: "Leaving Soon",
    description: "Confirmed departure dates for your watchlist in {country}.",
    badgeLeavingSoon: "Leaving Soon",
    groupNext7Days: "Next 7 days",
    groupDays8To14: "Days 8–14",
    groupDays15To30: "Days 15–30",
    leavingProvider: "Leaving {provider} in {country}",
    lastOption: "Last accessible option in {country}",
    leavingDate: "Leaves {date}",
    lastVerified: "Observed {date}",
    seasonLabel: "Season {season}",
    seasonEpisodeLabel: "S{season}:E{episode}",
    accessSubscription: "Subscription",
    accessFreeWithAds: "Free with ads",
    accessFree: "Free",
    accessAddon: "Add-on",
    accessOther: "Other",
    stateLoading: "Loading confirmed departures…",
    stateNoEvidence: "No confirmed departure data",
    stateNoEvidenceDesc: "Services in {country} have not reported confirmed departure dates for your watchlist.",
    stateNoneInHorizon: "No departures in the next 30 days",
    stateNoneInHorizonDesc: "Your watchlisted titles in {country} have no confirmed departures within this horizon.",
    stateEmptyWatchlist: "Watchlist is empty",
    stateEmptyWatchlistDesc: "Add titles to your watchlist to monitor upcoming service departures.",
    stateError: "Could not load departure dates",
    stateOffline: "Offline — departures unavailable",
    retry: "Retry",
    coverageNotice: "Coverage note",
    coverageNoticeSparse: "Only services reporting verified departure dates are shown. Missing dates do not guarantee a title will remain available.",
    srDepartureDate: "Leaves on {date}",
  },
};

export type MessageKey =
  | `nav.${keyof TranslationCatalog["nav"]}`
  | `locale.${keyof TranslationCatalog["locale"]}`
  | `common.${keyof TranslationCatalog["common"]}`
  | `movie.${keyof TranslationCatalog["movie"]}`
  | `leavingSoon.${keyof TranslationCatalog["leavingSoon"]}`;
