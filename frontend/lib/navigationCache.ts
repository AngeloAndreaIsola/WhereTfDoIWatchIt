// Bounded in-memory cache for catalogue/watchlist pages restored on
// browser back/forward navigation. Entries are keyed by the rendered query
// (filters + page + country). The cache lives for the lifetime of the JS
// module, so a full page reload naturally discards it (fresh fetch), while
// client-side back/forward navigation reuses it.
export const CATALOG_CACHE_TTL_MS = 60_000;
export const WATCHLIST_CACHE_TTL_MS = 60_000;
export const NAV_CACHE_MAX_ENTRIES = 12;

export interface CachedPage<T> {
  key: string;
  items: T[];
  page: number;
  paginationInfo: {
    totalPages: number;
    hasNext: boolean;
    hasPrevious: boolean;
  };
  filters: unknown;
  scrollY: number;
  cachedAt: number;
  extra?: Record<string, unknown>;
}

export class BoundedPageCache<T> {
  private readonly entries = new Map<string, CachedPage<T>>();
  constructor(
    private readonly ttlMs: number,
    private readonly maxEntries: number = NAV_CACHE_MAX_ENTRIES
  ) {}

  get(key: string): CachedPage<T> | undefined {
    const entry = this.entries.get(key);
    if (!entry) return undefined;
    if (Date.now() - entry.cachedAt > this.ttlMs) {
      this.entries.delete(key);
      return undefined;
    }
    // LRU touch.
    this.entries.delete(key);
    this.entries.set(key, entry);
    return entry;
  }

  set(entry: CachedPage<T>): void {
    if (this.entries.has(entry.key)) this.entries.delete(entry.key);
    if (this.entries.size >= this.maxEntries) {
      const oldest = this.entries.keys().next().value;
      if (oldest !== undefined) this.entries.delete(oldest);
    }
    this.entries.set(entry.key, entry);
  }

  updateScroll(key: string, scrollY: number): void {
    const entry = this.entries.get(key);
    if (entry) entry.scrollY = scrollY;
  }

  delete(key: string): void {
    this.entries.delete(key);
  }

  clear(): void {
    this.entries.clear();
  }
}

export const catalogPageCache = new BoundedPageCache<unknown>(
  CATALOG_CACHE_TTL_MS
);
export const watchlistPageCache = new BoundedPageCache<unknown>(
  WATCHLIST_CACHE_TTL_MS
);

export function invalidateCatalogCache(): void {
  catalogPageCache.clear();
}

export function invalidateWatchlistCache(): void {
  watchlistPageCache.clear();
}
