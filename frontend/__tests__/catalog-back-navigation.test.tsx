import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode; [key: string]: unknown }) =>
    React.createElement("a", { href, ...props }, children),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/catalog",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/components/CurrentLocationProvider", () => ({
  useCurrentLocation: () => ({ currentCountry: "US" }),
}));

describe("Catalog back navigation cache", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    mockFetch.mockClear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function setupFetch() {
    mockFetch.mockImplementation((url: string) => {
      const urlStr = url.toString();
      if (urlStr.includes("/api/preferences/")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ audio_languages: [], subtitle_languages: [] }) });
      }
      if (urlStr.includes("/api/v2/unofficial/providers/") || urlStr.includes("/api/providers/")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ results: [] }) });
      }
      if (urlStr.includes("/api/catalog/unified/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            count: 1,
            page: 1,
            total_pages: 1,
            results: [{ id: "1", tmdb_id: 1, source: "official", source_indicator: "Official", title: "Cached Catalog Title", year: 2021, poster_path: null, content_type: "movie", provider: null, watchability: null, watch_url: null }],
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
    expect(await screen.findByText("Cached Catalog Title")).toBeInTheDocument();
    const callsAfterFirst = mockFetch.mock.calls.filter(([u]) => String(u).includes("/api/catalog/unified/")).length;
    expect(callsAfterFirst).toBe(1);

    unmount();
    render(React.createElement(CatalogPage));
    expect(screen.getByText("Cached Catalog Title")).toBeInTheDocument();
    const callsAfterSecond = mockFetch.mock.calls.filter(([u]) => String(u).includes("/api/catalog/unified/")).length;
    expect(callsAfterSecond).toBe(1);
  });
});
