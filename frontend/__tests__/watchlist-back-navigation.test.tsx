import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode; [key: string]: unknown }) =>
    React.createElement("a", { href, ...props }, children),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/components/CurrentLocationProvider", () => ({
  useCurrentLocation: () => ({ currentCountry: "US" }),
}));

vi.mock("@/components/FilterBar", () => ({
  FilterBar: () => React.createElement("div", null, "FilterBar"),
}));

describe("Watchlist back navigation cache", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    mockFetch.mockClear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function setupFetch() {
    mockFetch.mockImplementation((url: string) => {
      const params = new URL(url, "http://localhost").searchParams;
      const watched = params.get("watched");
      const rows = [{ movie: { id: 1, tmdb_id: 1, title: "Cached Watchlist Title", year: 2021, content_type: "movie" }, is_watched: false, user_data: null }];
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ report: rows, pagination: { total_pages: 1, has_next: false, has_previous: false } }),
      });
    });
    vi.stubGlobal("fetch", mockFetch);
  }

  it("renders cached rows immediately on remount without refetching", async () => {
    setupFetch();
    const WatchlistPage = (await import("@/app/watchlist/page")).default;
    const { unmount } = render(React.createElement(WatchlistPage));
    expect(await screen.findByText("Cached Watchlist Title")).toBeInTheDocument();
    const firstCalls = mockFetch.mock.calls.filter(([u]) => String(u).includes("/api/watchlist/report/")).length;
    expect(firstCalls).toBe(1);

    unmount();
    render(React.createElement(WatchlistPage));
    expect(screen.getByText("Cached Watchlist Title")).toBeInTheDocument();
    const secondCalls = mockFetch.mock.calls.filter(([u]) => String(u).includes("/api/watchlist/report/")).length;
    expect(secondCalls).toBe(1);
  });
});
