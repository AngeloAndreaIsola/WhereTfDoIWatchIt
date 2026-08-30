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

describe("Unofficial catalog (Phase C)", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    routerPush.mockReset();
    mockFetch.mockClear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function setupFetch(
    unofficialResponse: Record<string, unknown>,
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
      if (urlStr.includes("/api/catalog/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            results: [],
            total_pages: 1,
            has_next: false,
            has_previous: false,
          }),
        });
      }
      if (urlStr.includes("/api/v2/unofficial/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => unofficialResponse,
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

  it("shows the Unofficial Sources tab and switches to it", async () => {
    setupFetch({ count: 0, results: [] });

    const CatalogPage = (await import("@/app/catalog/page")).default;
    render(React.createElement(CatalogPage));

    expect(screen.getByText("Official Sources")).toBeInTheDocument();
    expect(screen.getByText("Unofficial Sources")).toBeInTheDocument();

    const unofficialTab = screen.getByText("Unofficial Sources");
    await userEvent.click(unofficialTab);

    await waitFor(
      () => {
        expect(
          screen.getByText(/No unofficial source results found/)
        ).toBeInTheDocument();
      },
      { timeout: 5000 }
    );
  });

  it("displays unofficial catalog items with language badges", async () => {
    setupFetch({
      count: 1,
      results: [
        {
          id: 1,
          scraped_title: "Test Anime",
          scraped_year: 2023,
          scraped_url: "https://example.com/anime",
          provider: {
            id: 1,
            slug: "kisskh",
            name: "KissKH",
            category: "scraper",
            source_indicator: "Scraper",
            is_enabled: true,
            is_major: false,
            icon_name: null,
            domain: "kisskh.do",
            last_scraped_at: null,
            is_stale: false,
          },
          movie: {
            id: 10,
            tmdb_id: 123,
            title: "Test Anime",
            year: 2023,
            poster_path: "/poster.jpg",
          },
          is_confirmed: true,
          confidence_score: 0.95,
          raw_payload: {
            audio_languages: ["JA"],
            subtitle_languages: ["EN"],
            quality: "1080p",
          },
          is_available: true,
          source_indicator: "Scraper",
          provider_category: "scraper",
        },
      ],
    });

    const CatalogPage = (await import("@/app/catalog/page")).default;
    render(React.createElement(CatalogPage));

    const unofficialTab = screen.getByText("Unofficial Sources");
    await userEvent.click(unofficialTab);

    await waitFor(
      () => {
        expect(screen.getByText("Test Anime")).toBeInTheDocument();
      },
      { timeout: 5000 }
    );

    expect(screen.getByText(/→ Test Anime \(2023\)/)).toBeInTheDocument();
    expect(screen.getByText("KissKH · kisskh.do")).toBeInTheDocument();
    expect(screen.getByText("✓ Matched")).toBeInTheDocument();
    expect(screen.getByText("Audio:")).toBeInTheDocument();
    expect(screen.getByText("Subs:")).toBeInTheDocument();
    expect(screen.getByText("Quality:")).toBeInTheDocument();

    const watchLink = screen.getByText("Watch →");
    expect(watchLink).toHaveAttribute("href", "https://example.com/anime");
  });

  it("shows scrape panel when clicking Scrape Unofficial Sources", async () => {
    setupFetch({ count: 0, results: [] });

    const CatalogPage = (await import("@/app/catalog/page")).default;
    render(React.createElement(CatalogPage));

    const unofficialTab = screen.getByText("Unofficial Sources");
    await userEvent.click(unofficialTab);

    await waitFor(
      () => {
        expect(
          screen.getByText(/No unofficial source results found/)
        ).toBeInTheDocument();
      },
      { timeout: 5000 }
    );

    const scrapeLink = screen.getByText("Scrape Unofficial Sources");
    await userEvent.click(scrapeLink);

    expect(screen.getByText(/Search Query \(optional\)/)).toBeInTheDocument();
    expect(screen.getByText("Run Scrape")).toBeInTheDocument();

    const runButton = screen.getByRole("button", { name: /Run Scrape/i });
    expect(runButton).toBeDisabled();
  });

  it("handles API error when loading unofficial catalog", async () => {
    mockFetch.mockImplementation((url: string) => {
      const urlStr = url.toString();
      if (urlStr.includes("/api/v2/unofficial/")) {
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

    const unofficialTab = screen.getByText("Unofficial Sources");
    await userEvent.click(unofficialTab);

    await waitFor(
      () => {
        expect(
          screen.getByText(/No unofficial source results found/)
        ).toBeInTheDocument();
      },
      { timeout: 5000 }
    );
  });
});
