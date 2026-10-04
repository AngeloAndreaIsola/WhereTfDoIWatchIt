import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import WatchedPage from "@/app/watched/page";
import { AppNav } from "@/components/AppNav";

const routerReplace = vi.fn();
let searchParamsString = "";

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

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: routerReplace,
    back: vi.fn(),
  }),
  usePathname: () => "/watched",
  useSearchParams: () => new URLSearchParams(searchParamsString),
}));

type RouteResponse = { ok?: boolean; status?: number; body: unknown };

/** Route-matched fetch stub; first matching prefix wins, otherwise fallback. */
function mockRoutes(
  routes: Array<[string, RouteResponse]>,
  fallback: RouteResponse = { ok: true, status: 200, body: { results: [] } }
) {
  const fetchMock = vi.fn().mockImplementation(async (url: string) => {
    const match = routes.find(
      ([pattern]) => typeof url === "string" && url.startsWith(pattern)
    );
    const response = match ? match[1] : fallback;
    const ok =
      response.ok ??
      (response.status
        ? response.status >= 200 && response.status < 300
        : true);
    return {
      ok,
      status: response.status ?? 200,
      json: async () => response.body,
    };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** One route pair for `mockRoutes`. */
const route = (
  pattern: string,
  response: RouteResponse
): [string, RouteResponse] => [pattern, response];

/** The watched report endpoint, stubbed with `report` rows + pagination. */
const reportRoute = (
  report: unknown[],
  pagination: Record<string, unknown> = {}
): Array<[string, RouteResponse]> => [
  route("/api/watched/report", {
    body: {
      report,
      summary: {
        total_movies: report.length,
        available_movies: 0,
        unavailable_movies: report.length,
        countries: [],
        services: [],
      },
      pagination: {
        count: report.length,
        page: 1,
        page_size: 24,
        total_pages: 1,
        has_next: false,
        has_previous: false,
        ...pagination,
      },
    },
  }),
];

const watchedMovieRow = {
  movie: {
    id: 1,
    tmdb_id: 603,
    title: "The Matrix",
    year: 1999,
    content_type: "movie",
    poster_path: "/poster.jpg",
  },
  is_watched: true,
  user_data: { is_watched: true, rating: 9 },
};

const watchedSeriesRow = {
  movie: {
    id: 2,
    tmdb_id: 1396,
    title: "Breaking Bad",
    year: 2008,
    content_type: "series",
    poster_path: "/bb.jpg",
  },
  is_watched: true,
  user_data: { is_watched: true },
};

describe("Watched section", () => {
  beforeEach(() => {
    routerReplace.mockReset();
    searchParamsString = "";
  });

  it("is reachable from the app navigation alongside Watchlist", () => {
    render(React.createElement(AppNav));

    const nav = screen.getByRole("navigation");
    expect(nav).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Watchlist" })).toHaveAttribute(
      "href",
      "/watchlist"
    );
    const watchedLink = screen.getByRole("link", { name: "Watched" });
    expect(watchedLink).toHaveAttribute("href", "/watched");
    // Clearly distinguished from the Watchlist entry.
    expect(watchedLink).not.toHaveAttribute("href", "/watchlist");
  });

  it("loads watched titles from the watched report endpoint", async () => {
    const fetchMock = mockRoutes(reportRoute([watchedMovieRow]));

    render(React.createElement(WatchedPage));

    expect(await screen.findByText("The Matrix")).toBeInTheDocument();
    expect(screen.getByText("1999")).toBeInTheDocument();
    // 1 watched title
    expect(screen.getByText("1 watched title")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/api/watched/report/")
      )
    ).toBe(true);
  });

  it("renders an empty state when nothing is watched", async () => {
    // The unfiltered probe also reports 0 → "nothing watched yet".
    mockRoutes([
      ...reportRoute([]),
      route("/api/watched/report", {
        body: {
          report: [],
          summary: {
            total_movies: 0,
            available_movies: 0,
            unavailable_movies: 0,
            countries: [],
            services: [],
          },
          pagination: {
            count: 0,
            page: 1,
            page_size: 1,
            total_pages: 1,
            has_next: false,
            has_previous: false,
          },
        },
      }),
    ]);

    render(React.createElement(WatchedPage));

    expect(await screen.findByText("Nothing watched yet.")).toBeInTheDocument();
    expect(
      screen.queryByText("No watched titles match your filters.")
    ).toBeNull();
    // Both the intro line and the empty state point back at the Watchlist.
    const watchlistLinks = screen.getAllByRole("link", { name: "Watchlist" });
    expect(watchlistLinks.length).toBeGreaterThan(0);
    for (const link of watchlistLinks) {
      expect(link).toHaveAttribute("href", "/watchlist");
    }
    expect(screen.queryByText("The Matrix")).toBeNull();
  });

  it("distinguishes a filtered-empty result from an empty watch history", async () => {
    // Start with a filter that matches nothing. The stub keys off the query
    // string: `q=` present → filtered (no rows), absent → unfiltered probe
    // (titles exist), which is exactly the ambiguity the page must resolve.
    searchParamsString = "q=NoSuchTitle";
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      const isFiltered = typeof url === "string" && url.includes("q=");
      const rows = isFiltered ? [] : [watchedMovieRow, watchedSeriesRow];
      return {
        ok: true,
        status: 200,
        json: async () => ({
          report: rows,
          summary: {
            total_movies: rows.length,
            available_movies: 0,
            unavailable_movies: rows.length,
            countries: [],
            services: [],
          },
          pagination: {
            count: isFiltered ? 0 : 4,
            page: 1,
            page_size: 24,
            total_pages: 1,
            has_next: false,
            has_previous: false,
          },
        }),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(WatchedPage));

    expect(
      await screen.findByText("No watched titles match your filters.")
    ).toBeInTheDocument();
    // Not the first-run wording: the user does have watched titles.
    expect(screen.queryByText("Nothing watched yet.")).toBeNull();
  });

  it("offers a Clear filters action that restores the unfiltered list", async () => {
    searchParamsString = "q=NoSuchTitle";
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      const isFiltered = typeof url === "string" && url.includes("q=");
      const rows = isFiltered ? [] : [watchedMovieRow];
      return {
        ok: true,
        status: 200,
        json: async () => ({
          report: rows,
          summary: {
            total_movies: rows.length,
            available_movies: 0,
            unavailable_movies: 0,
            countries: [],
            services: [],
          },
          pagination: {
            count: isFiltered ? 0 : 1,
            page: 1,
            page_size: 24,
            total_pages: 1,
            has_next: false,
            has_previous: false,
          },
        }),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(React.createElement(WatchedPage));

    await user.click(
      await screen.findByRole("button", { name: /Clear filters/ })
    );

    // Filters reset → the watched title is visible again.
    expect(await screen.findByText("The Matrix")).toBeInTheDocument();
    expect(
      screen.queryByText("No watched titles match your filters.")
    ).toBeNull();
    expect(routerReplace).toHaveBeenCalledWith("/watched/", { scroll: false });
  });

  it("renders mixed movie and TV titles, linking each to its detail page", async () => {
    mockRoutes(reportRoute([watchedMovieRow, watchedSeriesRow]));

    const { container } = render(React.createElement(WatchedPage));

    expect(await screen.findByText("The Matrix")).toBeInTheDocument();
    expect(screen.getByText("Breaking Bad")).toBeInTheDocument();
    expect(screen.getByText("2 watched titles")).toBeInTheDocument();

    const hrefs = Array.from(container.querySelectorAll("a")).map((a) =>
      a.getAttribute("href")
    );
    expect(hrefs).toContain("/movie/603");
    expect(hrefs).toContain("/tv/1396");
  });

  it("removes an item from Watched using the existing unmark action", async () => {
    const fetchMock = mockRoutes([
      ...reportRoute([watchedMovieRow, watchedSeriesRow]),
      route("/api/unwatched/603/", {
        body: { message: "Movie unmarked as watched" },
      }),
    ]);

    const user = userEvent.setup();
    render(React.createElement(WatchedPage));

    const removeButtons = await screen.findAllByRole("button", {
      name: /Remove from Watched/,
    });
    expect(removeButtons).toHaveLength(2);

    await user.click(removeButtons[0]);

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((call) => call[0] === "/api/unwatched/603/")
      ).toBe(true);
    });

    // The unwatched title leaves the section; the other one stays.
    await waitFor(() => {
      expect(screen.queryByText("The Matrix")).toBeNull();
    });
    expect(screen.getByText("Breaking Bad")).toBeInTheDocument();
    expect(screen.getByText("1 watched title")).toBeInTheDocument();
  });

  it("surfaces an error when the unmark request fails and keeps the row", async () => {
    mockRoutes([
      ...reportRoute([watchedMovieRow]),
      route("/api/unwatched/603/", {
        ok: false,
        status: 502,
        body: { error: "Failed to unmark as watched: boom" },
      }),
    ]);

    const user = userEvent.setup();
    render(React.createElement(WatchedPage));

    await user.click(
      (await screen.findAllByRole("button", { name: /Remove from Watched/ }))[0]
    );

    expect(
      await screen.findByText("Failed to unmark as watched: boom")
    ).toBeInTheDocument();
    expect(screen.getByText("The Matrix")).toBeInTheDocument();
  });

  it("keeps filters in the URL and forwards them to the report request", async () => {
    searchParamsString = "type=series";
    const fetchMock = mockRoutes(reportRoute([watchedSeriesRow]));

    const user = userEvent.setup();
    render(React.createElement(WatchedPage));

    await screen.findByText("Breaking Bad");

    // Initial load reflects the type filter from the URL.
    expect(
      fetchMock.mock.calls.some(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/api/watched/report/") &&
          call[0].includes("type=series")
      )
    ).toBe(true);

    await user.click(screen.getByRole("button", { name: "Movies" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          (call) =>
            typeof call[0] === "string" && call[0].includes("type=movie")
        )
      ).toBe(true);
    });
    // Filters are mirrored into the page URL, like /watchlist does.
    expect(routerReplace).toHaveBeenCalledWith("/watched/?type=movie", {
      scroll: false,
    });
  });

  it("pages through results with the shared Pagination component", async () => {
    const fetchMock = mockRoutes(
      reportRoute([watchedMovieRow], {
        count: 30,
        total_pages: 2,
        has_next: true,
        has_previous: false,
      })
    );

    const user = userEvent.setup();
    render(React.createElement(WatchedPage));

    await screen.findByText("The Matrix");
    expect(screen.getByText("30 watched titles")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Next/ }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          (call) =>
            typeof call[0] === "string" && call[0].includes("page=2")
        )
      ).toBe(true);
    });
  });

  it("forwards the country filter to the report request and keeps it in the URL", async () => {
    searchParamsString = "country=GB";
    const fetchMock = mockRoutes(reportRoute([watchedMovieRow]));

    render(React.createElement(WatchedPage));

    await screen.findByText("The Matrix");

    expect(
      fetchMock.mock.calls.some(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/api/watched/report/") &&
          call[0].includes("country=GB")
      )
    ).toBe(true);
    // URL state stays synchronized with the country filter.
    expect(routerReplace).toHaveBeenCalledWith("/watched/?country=GB", {
      scroll: false,
    });
  });

  it("omits the country filter from the request when it is cleared", async () => {
    searchParamsString = "";
    const fetchMock = mockRoutes(reportRoute([watchedMovieRow]));

    render(React.createElement(WatchedPage));

    await screen.findByText("The Matrix");

    // No country selected → no `country` param, and no empty `country=`.
    const reportCalls = fetchMock.mock.calls.filter(
      (call) => typeof call[0] === "string" && call[0].includes("/api/watched/report/")
    );
    expect(reportCalls.length).toBeGreaterThan(0);
    for (const call of reportCalls) {
      expect(String(call[0])).not.toMatch(/[?&]country=/);
    }
  });

  it("shows the API error message when the report request fails", async () => {
    mockRoutes([
      route("/api/watched/report", {
        ok: false,
        status: 429,
        body: { error: "Rate limited — try again later." },
      }),
    ]);

    render(React.createElement(WatchedPage));

    expect(
      await screen.findByText("Rate limited — try again later.")
    ).toBeInTheDocument();
  });
});