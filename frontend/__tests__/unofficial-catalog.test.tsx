import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
    [key: string]: unknown;
  }) => React.createElement("a", { href, ...props }, children),
}));

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}));

describe("Unified catalog page", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    routerPush.mockReset();
    mockFetch.mockClear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function setupFetch(
    unifiedResponse: Record<string, unknown>,
    options?: { providers?: unknown[]; prefs?: unknown }
  ) {
    mockFetch.mockImplementation((url: string) => {
      const urlStr = url.toString();
      if (urlStr.includes("/api/preferences/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => options?.prefs ?? { audio_languages: [], subtitle_languages: [] },
        });
      }
      if (urlStr.includes("/api/v2/unofficial/providers/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            count: (options?.providers ?? []).length,
            results: options?.providers ?? [],
          }),
        });
      }
      if (urlStr.includes("/api/providers/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => options?.providers ?? [],
        });
      }
      if (urlStr.includes("/api/catalog/unified/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => unifiedResponse,
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ results: [] }),
      });
    });
    vi.stubGlobal("fetch", mockFetch);
  }

  it("renders the unified catalog page title and provider filter without tabs", async () => {
    setupFetch({ count: 0, results: [], total_pages: 1 });

    const CatalogPage = (await import("@/app/catalog/page")).default;
    render(React.createElement(CatalogPage));

    expect(screen.getByText("Catalog")).toBeInTheDocument();
    expect(
      screen.getByText("Browse all titles across official, free, and unofficial streaming providers in one place.")
    ).toBeInTheDocument();
    expect(screen.queryByText("Official Sources")).not.toBeInTheDocument();
    expect(screen.queryByText("Unofficial Sources")).not.toBeInTheDocument();

    await waitFor(() => {
      expect(
        screen.getByText(/No catalog titles found matching your search and filter criteria/)
      ).toBeInTheDocument();
    });
  });

  it("displays official and unofficial catalog items in unified grid", async () => {
    setupFetch({
      count: 2,
      page: 1,
      total_pages: 1,
      results: [
        {
          id: "official_1",
          tmdb_id: 101,
          source: "official",
          source_indicator: "Official",
          title: "Official Movie",
          year: 2021,
          poster_path: "/poster1.jpg",
          content_type: "movie",
          provider: { slug: "netflix", name: "Netflix", category: "official" },
          watchability: {
            status: "watchable_here",
            status_symbol: "🟢",
            status_label: "Watchable here",
            best_offer: {
              country: "US",
              service: "netflix",
              service_display: "Netflix",
              url: "https://netflix.com",
              is_current_country: true,
            },
          },
          watch_url: "https://netflix.com",
        },
        {
          id: "unofficial_2",
          tmdb_id: null,
          source: "unofficial",
          source_indicator: "Scraper",
          title: "Test Anime",
          year: 2023,
          poster_path: "/poster2.jpg",
          content_type: "series",
          provider: { slug: "kisskh", name: "KissKH", category: "scraper" },
          unofficial_details: {
            scraped_url: "https://example.com/anime",
            confidence_score: 0.95,
            is_confirmed: true,
            audio_languages: ["JA"],
            subtitle_languages: ["EN"],
          },
          watch_url: "https://example.com/anime",
        },
      ],
    });

    const CatalogPage = (await import("@/app/catalog/page")).default;
    render(React.createElement(CatalogPage));

    await waitFor(() => {
      expect(screen.getByText("Official Movie")).toBeInTheDocument();
      expect(screen.getByText("Test Anime")).toBeInTheDocument();
    });

    expect(screen.getByText("Official")).toBeInTheDocument();
    expect(screen.getByText("Scraper")).toBeInTheDocument();
    expect(screen.getByText("Watch on KissKH")).toBeInTheDocument();
  });

  it("shows scrape panel when clicking Scrape Unofficial Sources", async () => {
    setupFetch({ count: 0, results: [] });

    const CatalogPage = (await import("@/app/catalog/page")).default;
    render(React.createElement(CatalogPage));

    await waitFor(() => {
      expect(
        screen.getByText(/No catalog titles found matching your search and filter criteria/)
      ).toBeInTheDocument();
    });

    const scrapeLink = screen.getByText("Scrape Unofficial Sources");
    await userEvent.click(scrapeLink);

    expect(screen.getByText(/Search Query \(optional\)/)).toBeInTheDocument();
    expect(screen.getByText("Run Scrape")).toBeInTheDocument();

    const runButton = screen.getByRole("button", { name: /Run Scrape/i });
    expect(runButton).toBeDisabled();
  });

  it("handles API error when loading catalog", async () => {
    mockFetch.mockImplementation((url: string) => {
      const urlStr = url.toString();
      if (urlStr.includes("/api/catalog/unified/")) {
        return Promise.resolve({
          ok: false,
          status: 500,
          json: async () => ({ error: "Server error" }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ results: [] }),
      });
    });
    vi.stubGlobal("fetch", mockFetch);

    const CatalogPage = (await import("@/app/catalog/page")).default;
    render(React.createElement(CatalogPage));

    await waitFor(() => {
      expect(
        screen.getByText(/No catalog titles found matching your search and filter criteria/)
      ).toBeInTheDocument();
    });
  });
});
