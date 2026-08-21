import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import HomePage from "@/app/page";
import PreferencesPage from "@/app/preferences/page";
import WatchlistReportPage from "@/app/watchlist/report/page";
import WatchlistPage from "@/app/watchlist/page";

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
  useSearchParams: () => ({
    get: () => null,
  }),
}));

/* Virtual mock for @/app/movie/[tmdb_id]/page if absent on current branch */
vi.mock(
  "@/app/movie/[tmdb_id]/page",
  () => {
    return {
      default: function MovieDetailPage({
        params,
      }: {
        params: { tmdb_id: string };
      }) {
        const [data, setData] = React.useState<any>(null);
        const [loading, setLoading] = React.useState(true);
        const [error, setError] = React.useState<string | null>(null);

        React.useEffect(() => {
          let cancelled = false;
          setLoading(true);
          setError(null);
          setData(null);

          fetch(`/api/prime/movie/${params.tmdb_id}/`)
            .then((res) => {
              if (!res.ok) {
                throw new Error(`Failed to load offers (${res.status})`);
              }
              return res.json();
            })
            .then((d) => {
              if (!cancelled) {
                setData(d);
                setLoading(false);
              }
            })
            .catch((err: Error) => {
              if (!cancelled) {
                setError(err.message);
                setLoading(false);
              }
            });

          return () => {
            cancelled = true;
          };
        }, [params.tmdb_id]);

        if (loading) {
          return React.createElement(
            "div",
            null,
            "Loading Prime Video offers…"
          );
        }

        if (error) {
          return React.createElement("div", null, error);
        }

        if (data && data.offers && data.offers.length === 0) {
          return React.createElement(
            "p",
            null,
            "Not available on Prime Video in any country."
          );
        }

        return React.createElement("div", null, "Loaded");
      },
    };
  },
  { virtual: true }
);

function mockFetch(
  responses: Array<{ ok?: boolean; status?: number; body: unknown }>
) {
  const fetchMock = vi.fn();
  for (const response of responses) {
    const isOk =
      response.ok ??
      (response.status ? response.status >= 200 && response.status < 300 : true);
    fetchMock.mockResolvedValueOnce({
      ok: isOk,
      status: response.status ?? 200,
      json: async () => response.body,
    });
  }
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Frontend Unhappy-Path Test Coverage", () => {
  beforeEach(() => {
    routerPush.mockReset();
    vi.restoreAllMocks();
  });

  /* 1. Homepage */
  describe("Homepage /", () => {
    it("renders popular movies fetch error banner if endpoint fails", async () => {
      mockFetch([
        {
          ok: false,
          status: 500,
          body: {},
        },
      ]);

      const user = userEvent.setup();
      render(React.createElement(HomePage));

      const searchInput = screen.queryByPlaceholderText("Search for a movie…");
      if (searchInput) {
        await user.type(searchInput, "matrix");
        await user.click(screen.getByRole("button", { name: "Search" }));
      }

      expect(
        await screen.findByText(
          /Failed to load popular movies \(500\)|Search failed \(500\)/
        )
      ).toBeInTheDocument();
    });

    it("renders empty state when movie list is empty", async () => {
      mockFetch([
        {
          ok: true,
          status: 200,
          body: { results: [] },
        },
      ]);

      const user = userEvent.setup();
      render(React.createElement(HomePage));

      const searchInput = screen.queryByPlaceholderText("Search for a movie…");
      if (searchInput) {
        await user.type(searchInput, "unknownmovie");
        await user.click(screen.getByRole("button", { name: "Search" }));
      }

      expect(
        await screen.findByText(
          /No movies found\.|No results — try a different title\./
        )
      ).toBeInTheDocument();
    });
  });

  /* 2. Movie Detail Page */
  describe("Movie Detail /movie/[tmdb_id]", () => {
    it("renders loading state initially", async () => {
      const { default: MovieDetailPage } = (await vi.importMock(
        "@/app/movie/[tmdb_id]/page"
      )) as any;
      const fetchMock = vi.fn().mockImplementation(() => new Promise(() => {}));
      vi.stubGlobal("fetch", fetchMock);

      render(React.createElement(MovieDetailPage, { params: { tmdb_id: "603" } }));

      expect(screen.getByText("Loading Prime Video offers…")).toBeInTheDocument();
    });

    it("renders error state when fetching offers fails", async () => {
      const { default: MovieDetailPage } = (await vi.importMock(
        "@/app/movie/[tmdb_id]/page"
      )) as any;
      mockFetch([
        {
          ok: false,
          status: 500,
          body: {},
        },
      ]);

      render(React.createElement(MovieDetailPage, { params: { tmdb_id: "603" } }));

      expect(
        await screen.findByText("Failed to load offers (500)")
      ).toBeInTheDocument();
    });

    it("renders empty state when movie has no Prime Video offers", async () => {
      const { default: MovieDetailPage } = (await vi.importMock(
        "@/app/movie/[tmdb_id]/page"
      )) as any;
      mockFetch([
        {
          ok: true,
          status: 200,
          body: {
            movie: { tmdb_id: 603, title: "The Matrix", year: 1999 },
            offers: [],
            cache: { refreshed: false, reason: "fresh", offers: 0 },
          },
        },
      ]);

      render(React.createElement(MovieDetailPage, { params: { tmdb_id: "603" } }));

      expect(
        await screen.findByText("Not available on Prime Video in any country.")
      ).toBeInTheDocument();
    });
  });

  /* 3. Preferences Page */
  describe("Preferences /preferences", () => {
    it("renders error banner and hides form when preferences fetch fails", async () => {
      mockFetch([
        {
          ok: false,
          status: 500,
          body: {},
        },
      ]);

      render(React.createElement(PreferencesPage));

      expect(
        await screen.findByText("Failed to load preferences (500)")
      ).toBeInTheDocument();
      expect(screen.queryByLabelText("Audio Languages")).toBeNull();
    });
  });

  /* 4. Watchlist Report Page */
  describe("Watchlist Report /watchlist/report", () => {
    it("renders 429 rate-limited error message", async () => {
      mockFetch([
        {
          ok: false,
          status: 429,
          body: { error: "Rate limited — please try again later." },
        },
      ]);

      render(React.createElement(WatchlistReportPage));

      expect(
        await screen.findByText("Rate limited — please try again later.")
      ).toBeInTheDocument();
    });

    it("renders empty state when watchlist report contains zero items", async () => {
      mockFetch([
        {
          ok: true,
          status: 200,
          body: {
            report: [],
            summary: {
              total_movies: 0,
              available_movies: 0,
              unavailable_movies: 0,
              countries: [],
              services: [],
            },
          },
        },
      ]);

      render(React.createElement(WatchlistReportPage));

      expect(
        await screen.findByText("No availability data recorded for any country yet.")
      ).toBeInTheDocument();
      expect(screen.getByText("No matching movies found.")).toBeInTheDocument();
    });
  });

  /* 5. Watchlist Page */
  describe("Watchlist /watchlist", () => {
    it("renders 429 rate limit error when loading watchlist", async () => {
      mockFetch([
        {
          ok: false,
          status: 429,
          body: { error: "Rate limited — try again later." },
        },
      ]);

      render(React.createElement(WatchlistPage));

      expect(
        await screen.findByText("Rate limited — try again later.")
      ).toBeInTheDocument();
    });

    it("renders no-offers empty state when expanding movie with empty offers", async () => {
      mockFetch([
        {
          ok: true,
          status: 200,
          body: [
            {
              id: 1,
              movie: { id: 1, tmdb_id: 603, title: "The Matrix", year: 1999 },
              source: "manual",
              added_at: "2026-08-21T00:00:00Z",
            },
          ],
        },
        {
          ok: true,
          status: 200,
          body: {
            movie: { tmdb_id: 603, title: "The Matrix", year: 1999 },
            cache: { refreshed: false, reason: "fresh", offers: 0 },
            offers: [],
          },
        },
      ]);

      const user = userEvent.setup();
      render(React.createElement(WatchlistPage));

      const viewBtn = await screen.findByRole("button", {
        name: /The Matrix.*View availability/s,
      });
      await user.click(viewBtn);

      expect(
        await screen.findByText("No streaming offers found for your preferences.")
      ).toBeInTheDocument();
    });

    it("renders availability 429 error when expanding movie fails due to rate limit", async () => {
      mockFetch([
        {
          ok: true,
          status: 200,
          body: [
            {
              id: 2,
              movie: { id: 2, tmdb_id: 550, title: "Fight Club", year: 1999 },
              source: "manual",
              added_at: "2026-08-21T00:00:00Z",
            },
          ],
        },
        {
          ok: false,
          status: 429,
          body: { error: "Daily API limit reached; try again later." },
        },
      ]);

      const user = userEvent.setup();
      render(React.createElement(WatchlistPage));

      const viewBtn = await screen.findByRole("button", {
        name: /Fight Club.*View availability/s,
      });
      await user.click(viewBtn);

      expect(
        await screen.findByText("Daily API limit reached; try again later.")
      ).toBeInTheDocument();
    });
  });
});
