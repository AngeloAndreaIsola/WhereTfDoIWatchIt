import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

let searchQuery = "";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode; [key: string]: unknown }) =>
    React.createElement("a", { href, ...props }, children),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/catalog",
  useSearchParams: () => new URLSearchParams(searchQuery),
}));

vi.mock("@/components/CurrentLocationProvider", () => ({
  useCurrentLocation: () => ({ currentCountry: "US" }),
}));

describe("Catalog back navigation cache", () => {
  const mockFetch = vi.fn();
  const scrollToMock = vi.fn();

  beforeEach(() => {
    searchQuery = "";
    mockFetch.mockClear();
    scrollToMock.mockClear();
    Object.defineProperty(window, "scrollTo", { value: scrollToMock, writable: true, configurable: true });
    Object.defineProperty(window, "scrollY", { value: 0, writable: true, configurable: true });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function setupFetch(options?: { deferred?: { promise: Promise<unknown>; resolve: (value: unknown) => void } }) {
    mockFetch.mockImplementation((url: string) => {
      const urlStr = url.toString();
      if (urlStr.includes("/api/preferences/")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ audio_languages: [], subtitle_languages: [] }) });
      }
      if (urlStr.includes("/api/v2/unofficial/providers/") || urlStr.includes("/api/providers/")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ results: [] }) });
      }
      if (urlStr.includes("/api/catalog/unified/")) {
        if (options?.deferred) return options.deferred.promise as Promise<unknown>;
        const params = new URL(url, "http://localhost").searchParams;
        const q = params.get("q") || "none";
        const page = params.get("page") || "1";
        const title = `${q}-page-${page}`;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            count: 1,
            page: Number(page),
            total_pages: 1,
            results: [{ id: `${q}-${page}`, tmdb_id: 1, source: "official", source_indicator: "Official", title, year: 2021, poster_path: null, content_type: "movie", provider: null, watchability: null, watch_url: null }],
          }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ results: [] }) });
    });
    vi.stubGlobal("fetch", mockFetch);
  }

  it("renders cached cards immediately on remount without refetching", async () => {
    setupFetch();
    const CatalogPage = (await import("@/app/catalog/page")).default;
    const { unmount } = render(React.createElement(CatalogPage));
    expect(await screen.findByText("none-page-1")).toBeInTheDocument();
    const firstCalls = mockFetch.mock.calls.filter(([u]) => String(u).includes("/api/catalog/unified/")).length;
    expect(firstCalls).toBe(1);

    unmount();
    render(React.createElement(CatalogPage));
    expect(screen.getByText("none-page-1")).toBeInTheDocument();
    const secondCalls = mockFetch.mock.calls.filter(([u]) => String(u).includes("/api/catalog/unified/")).length;
    expect(secondCalls).toBe(1);
  });

  it("restores non-default URL filters and page from cache", async () => {
    searchQuery = "q=foo&page=2";
    setupFetch();
    const CatalogPage = (await import("@/app/catalog/page")).default;
    const { unmount } = render(React.createElement(CatalogPage));
    expect(await screen.findByText("foo-page-2")).toBeInTheDocument();

    unmount();
    render(React.createElement(CatalogPage));
    expect(screen.getByText("foo-page-2")).toBeInTheDocument();
    const calls = mockFetch.mock.calls.filter(([u]) => String(u).includes("/api/catalog/unified/")).length;
    expect(calls).toBe(1);
  });

  it("does not cross-contaminate cached results across filter/page changes", async () => {
    searchQuery = "q=foo&page=2";
    setupFetch();
    const CatalogPage = (await import("@/app/catalog/page")).default;
    const first = render(React.createElement(CatalogPage));
    expect(await screen.findByText("foo-page-2")).toBeInTheDocument();
    first.unmount();

    searchQuery = "q=bar&page=1";
    const second = render(React.createElement(CatalogPage));
    expect(await screen.findByText("bar-page-1")).toBeInTheDocument();
    second.unmount();

    searchQuery = "q=foo&page=2";
    render(React.createElement(CatalogPage));
    expect(screen.getByText("foo-page-2")).toBeInTheDocument();
    expect(screen.queryByText("bar-page-1")).not.toBeInTheDocument();
    const calls = mockFetch.mock.calls.filter(([u]) => String(u).includes("/api/catalog/unified/")).length;
    expect(calls).toBe(2);
  });

  it("aborts an in-flight fetch on unmount so invalidation prevents stale repopulation", async () => {
    const deferred = { promise: null as Promise<unknown>, resolve: (_v: unknown) => {} };
    deferred.promise = new Promise((resolve) => {
      deferred.resolve = resolve;
    });
    setupFetch({ deferred });
    const CatalogPage = (await import("@/app/catalog/page")).default;
    const { unmount } = render(React.createElement(CatalogPage));
    unmount();
    const { invalidateCatalogCache } = await import("@/lib/navigationCache");
    invalidateCatalogCache();
    deferred.resolve({
      ok: true,
      status: 200,
      json: async () => ({
        count: 1,
        page: 1,
        total_pages: 1,
        results: [{ id: "stale", tmdb_id: 1, source: "official", source_indicator: "Official", title: "stale-row", year: 2021, poster_path: null, content_type: "movie", provider: null, watchability: null, watch_url: null }],
      }),
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    searchQuery = "";
    setupFetch();
    render(React.createElement(CatalogPage));
    expect(await screen.findByText("none-page-1")).toBeInTheDocument();
    expect(screen.queryByText("stale-row")).not.toBeInTheDocument();
  });

  it("restores scroll position from the cached entry", async () => {
    setupFetch();
    const CatalogPage = (await import("@/app/catalog/page")).default;
    const { unmount } = render(React.createElement(CatalogPage));
    expect(await screen.findByText("none-page-1")).toBeInTheDocument();
    Object.defineProperty(window, "scrollY", { value: 321, writable: true, configurable: true });
    unmount();

    render(React.createElement(CatalogPage));
    await waitFor(() => expect(scrollToMock).toHaveBeenCalledWith(0, 321));
  });

  it("expires stale cache entries by TTL", async () => {
    const { catalogPageCache } = await import("@/lib/navigationCache");
    catalogPageCache.set({
      key: "ttl-key",
      items: [],
      page: 1,
      paginationInfo: { totalPages: 1, hasNext: false, hasPrevious: false },
      filters: {},
      scrollY: 0,
      cachedAt: 0,
    });
    expect(catalogPageCache.get("ttl-key")).toBeUndefined();
  });

  it("evicts the oldest entry when the cache exceeds its bound", async () => {
    const { catalogPageCache } = await import("@/lib/navigationCache");
    for (let i = 0; i < 13; i += 1) {
      catalogPageCache.set({
        key: `lru-${i}`,
        items: [],
        page: 1,
        paginationInfo: { totalPages: 1, hasNext: false, hasPrevious: false },
        filters: {},
        scrollY: 0,
        cachedAt: Date.now(),
      });
    }
    expect(catalogPageCache.get("lru-0")).toBeUndefined();
    expect(catalogPageCache.get("lru-12")).toBeDefined();
  });
});
