export interface TranslationCatalog {
  nav: {
    home: string;
    catalog: string;
    watchlist: string;
    watched: string;
    preferences: string;
    subscriptions: string;
    candidateReview: string;
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
};

export type MessageKey =
  | `nav.${keyof TranslationCatalog["nav"]}`
  | `locale.${keyof TranslationCatalog["locale"]}`
  | `common.${keyof TranslationCatalog["common"]}`
  | `movie.${keyof TranslationCatalog["movie"]}`;
