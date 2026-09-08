import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode; [key: string]: unknown }) =>
    React.createElement("a", { href, ...props }, children),
}));

const routerPush = vi.fn();
let navigationItemsParam = "";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
  useSearchParams: () => ({
    get: (key: string) => (key === "items" ? navigationItemsParam : null),
  }),
}));

function mockFetch(responses: Array<{ ok?: boolean; status?: number; body: unknown }>) {
  const fetchMock = vi.fn();
  for (const response of responses) {
    fetchMock.mockResolvedValueOnce({
      ok: response.ok ?? true,
      status: response.status ?? 200,
      json: async () => response.body,
    });
  }
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("frontend page smoke tests", () => {
  beforeEach(() => {
    navigationItemsParam = "";
    routerPush.mockReset();
  });

  it("searches for a movie and shows the no-offers availability state", async () => {
    const SearchPage = (await import("@/app/page")).default;
    const fetchMock = mockFetch([
      {
        body: {
          results: [
            { tmdb_id: 603, title: "The Matrix", year: 1999, categories: [] },
          ],
        },
      },
      {
        body: {
          results: [
            { tmdb_id: 603, title: "The Matrix", year: 1999, categories: [] },
          ],
        },
      },
      {
        body: {
          movie: { tmdb_id: 603, title: "The Matrix", year: 1999 },
          cache: { refreshed: false, reason: "fresh", offers: 0 },
          offers: [],
        },
      },
    ]);

    const user = userEvent.setup();
    render(React.createElement(SearchPage));

    const input = screen.getByPlaceholderText("Search for a movie by title...");
    await user.type(input, "matrix");

    const matrixCards = await screen.findAllByRole("heading", { name: "The Matrix" });
    expect(matrixCards[0]).toBeInTheDocument();
  });

  it("loads preferences and displays canonical uppercase values", async () => {
    const PreferencesPage = (await import("@/app/preferences/page")).default;
    mockFetch([
      {
        body: {
          audio_languages: ["EN", "IT"],
          subtitle_languages: ["EN"],
          candidate_countries: ["us", "gb"],
        },
      },
    ]);

    render(React.createElement(PreferencesPage));

    const countriesInput = await screen.findByLabelText("Candidate Countries");
    expect(countriesInput).toHaveValue("us, gb");
  });

  it("renders watchlist report summary and unavailable rows", async () => {
    const WatchlistReportPage = (await import("@/app/watchlist/report/page")).default;
    mockFetch([
      {
        body: {
          summary: {
            total_movies: 2,
            available_movies: 1,
            unavailable_movies: 1,
            countries: [{ country: "US", count: 1 }],
            services: [{ service: "Netflix", count: 1 }],
          },
          report: [
            {
              movie: { id: 1, tmdb_id: 1, title: "Available Movie", year: 2020 },
              available_countries: ["US"],
              best_country: "US",
              best_service: "Netflix",
            },
            {
              movie: { id: 2, tmdb_id: 2, title: "Missing Movie", year: 2021 },
              available_countries: [],
              best_country: null,
              best_service: null,
            },
          ],
        },
      },
    ]);

    render(React.createElement(WatchlistReportPage));

    expect(await screen.findByText("Watchlist Report")).toBeInTheDocument();
    expect(screen.getByText("Total Movies")).toBeInTheDocument();
    expect(screen.getByText("Available Movie")).toBeInTheDocument();
    expect(screen.getByText("Missing Movie")).toBeInTheDocument();
    expect(screen.getByText("No subscription offer")).toBeInTheDocument();
    expect(screen.getAllByText("Netflix").length).toBeGreaterThan(0);
  });

  it("submits selected TMDb matches from the ambiguous-title resolver", async () => {
    const items = [
      {
        title: "Dune",
        year: 1984,
        candidates: [
          { tmdb_id: 841, title: "Dune", year: 1984 },
          { tmdb_id: 438631, title: "Dune", year: 2021 },
        ],
      },
    ];
    navigationItemsParam = JSON.stringify(items);
    const fetchMock = mockFetch([{ body: { resolved: 1 } }]);
    const AmbiguousPage = (await import("@/app/watchlist/ambiguous/page")).default;

    const user = userEvent.setup();
    render(React.createElement(AmbiguousPage));

    await user.click(await screen.findByLabelText(/Dune.*2021/s));
    await user.click(screen.getByRole("button", { name: /Confirm & Save Selections/i }));

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/watchlist"));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/watchlist/resolve/",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          selections: [{ original_title: "Dune", year: 1984, tmdb_id: 438631 }],
        }),
      }),
    );
  });
});
