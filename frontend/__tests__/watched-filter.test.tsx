import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FilterBar, FilterState } from "@/components/FilterBar";
import WatchlistPage from "@/app/watchlist/page";

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
  useRouter: () => ({ push: vi.fn(), replace: routerReplace, back: vi.fn() }),
  usePathname: () => "/watchlist",
  useSearchParams: () => new URLSearchParams(searchParamsString),
}));

type RouteResponse = { ok?: boolean; status?: number; body: unknown };

function mockRoutes(
  routes: Array<[string, RouteResponse]>,
  fallback: RouteResponse = { ok: true, status: 200, body: { results: [] } }
) {
  const fetchMock = vi.fn().mockImplementation(async (url: string) => {
    const match = routes.find(
      ([pattern]) => typeof url === "string" && url.startsWith(pattern)
    );
    const response = match ? match[1] : fallback;
    return {
      ok: response.ok ?? true,
      status: response.status ?? 200,
      json: async () => response.body,
    };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const reportRoute = (
  report: unknown[],
  pagination: Record<string, unknown> = {}
): [string, RouteResponse] => [
  "/api/watchlist/report",
  {
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
  },
];

const watchedRow = {
  movie: { id: 1, tmdb_id: 603, title: "Seen Film", year: 1999, content_type: "movie" },
  is_watched: true,
  user_data: { is_watched: true },
};

const unwatchedRow = {
  movie: { id: 2, tmdb_id: 1396, title: "Unseen Show", year: 2008, content_type: "series" },
  is_watched: false,
  user_data: null,
};

const BASE_FILTERS: FilterState = {
  q: "",
  content_type: "all",
  genre: "",
  year_min: "",
  year_max: "",
  actors: [],
  providers: [],
  country: "",
  status: "",
  quality: "",
  score_min: "",
  sort_by: "",
  watched: "all",
};

/** Render the FilterBar and capture the next FilterState it emits. */
function renderFilterBar(initial: Partial<FilterState> = {}) {
  const onChange = vi.fn();
  render(
    React.createElement(FilterBar, {
      filters: { ...BASE_FILTERS, ...initial },
      onChange,
      showStatusFilter: true,
    })
  );
  return onChange;
}

describe("Watched filter", () => {
  beforeEach(() => {
    routerReplace.mockReset();
    searchParamsString = "";
  });

  describe("FilterBar control", () => {
    it("offers All / Watched / Unwatched and defaults to All", () => {
      renderFilterBar();

      const select = screen.getByLabelText("Watched status") as HTMLSelectElement;
      expect(select.value).toBe("all");
      expect(
        Array.from(select.options).map((o) => [o.value, o.textContent])
      ).toEqual([
        ["all", "All"],
        ["watched", "✓ Watched"],
        ["unwatched", "○ Unwatched"],
      ]);
    });

    it("emits watched / unwatched when selected", async () => {
      const user = userEvent.setup();

      // Selection alone must NOT emit; Apply is what commits it.
      const watchedChange = renderFilterBar();
      await user.selectOptions(screen.getByLabelText("Watched status"), "watched");
      expect(watchedChange).not.toHaveBeenCalled();

      await user.click(screen.getByRole("button", { name: "Apply filters" }));
      expect(watchedChange).toHaveBeenCalledWith(
        expect.objectContaining({ watched: "watched" })
      );

      const unwatchedChange = renderFilterBar();
      await user.selectOptions(
        screen.getAllByLabelText("Watched status")[1],
        "unwatched"
      );
      await user.click(
        screen.getAllByRole("button", { name: "Apply filters" })[1]
      );
      expect(unwatchedChange).toHaveBeenCalledWith(
        expect.objectContaining({ watched: "unwatched" })
      );
    });

    it("does not disturb the other filters when changed", async () => {
      const user = userEvent.setup();
      const onChange = renderFilterBar({ q: "matrix", sort_by: "year_desc" });

      await user.selectOptions(screen.getByLabelText("Watched status"), "watched");
      await user.click(screen.getByRole("button", { name: "Apply filters" }));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({
          q: "matrix",
          sort_by: "year_desc",
          watched: "watched",
        })
      );
    });

    it("reflects a watched value passed in from state", () => {
      renderFilterBar({ watched: "unwatched" });

      const selects = screen.getAllByLabelText(
        "Watched status"
      ) as HTMLSelectElement[];
      expect(selects[0].value).toBe("unwatched");
    });
  });

  describe("watchlist page wiring", () => {
    it("sends watched=watched and reflects it in the URL", async () => {
      searchParamsString = "";
      const fetchMock = mockRoutes([reportRoute([watchedRow])]);

      const user = userEvent.setup();
      render(React.createElement(WatchlistPage));

      await screen.findByText("Seen Film");

      await user.selectOptions(
        screen.getByLabelText("Watched status"),
        "watched"
      );
      await user.click(screen.getByRole("button", { name: "Apply filters" }));

      await waitFor(() => {
        expect(
          fetchMock.mock.calls.some(
            (call) =>
              typeof call[0] === "string" && call[0].includes("watched=watched")
          )
        ).toBe(true);
      });
      expect(routerReplace).toHaveBeenCalledWith(
        "/watchlist/?watched=watched",
        { scroll: false }
      );
    });

    it("omits the parameter for the default All value", async () => {
      const fetchMock = mockRoutes([reportRoute([watchedRow, unwatchedRow])]);

      render(React.createElement(WatchlistPage));

      await screen.findByText("Seen Film");
      await screen.findByText("Unseen Show");

      const reportCalls = fetchMock.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" && call[0].includes("/api/watchlist/report/")
      );
      expect(reportCalls.length).toBeGreaterThan(0);
      for (const call of reportCalls) {
        expect(String(call[0])).not.toMatch(/[?&]watched=/);
      }
    });

    it("restores the watched filter from the URL on load", async () => {
      searchParamsString = "watched=unwatched";
      const fetchMock = mockRoutes([reportRoute([unwatchedRow])]);

      render(React.createElement(WatchlistPage));

      expect(await screen.findByText("Unseen Show")).toBeInTheDocument();
      // Unwatched rows only — the watched one is filtered out server-side.
      expect(screen.queryByText("Seen Film")).toBeNull();
      expect(
        fetchMock.mock.calls.some(
          (call) =>
            typeof call[0] === "string" &&
            call[0].includes("watched=unwatched")
        )
      ).toBe(true);
      expect(
        (screen.getByLabelText("Watched status") as HTMLSelectElement).value
      ).toBe("unwatched");
    });

    it("renders mixed watched and unwatched rows when unfiltered", async () => {
      mockRoutes([reportRoute([watchedRow, unwatchedRow])]);

      render(React.createElement(WatchlistPage));

      expect(await screen.findByText("Seen Film")).toBeInTheDocument();
      expect(screen.getByText("Unseen Show")).toBeInTheDocument();
      expect(
        (screen.getByLabelText("Watched status") as HTMLSelectElement).value
      ).toBe("all");
    });

    it("keeps the watched filter alongside search and sorting", async () => {
      searchParamsString = "watched=watched&q=film&sort_by=year_desc";
      const fetchMock = mockRoutes([reportRoute([watchedRow])]);

      render(React.createElement(WatchlistPage));

      await screen.findByText("Seen Film");

      const call = fetchMock.mock.calls.find(
        (c) => typeof c[0] === "string" && c[0].includes("/api/watchlist/report/")
      );
      const url = String(call?.[0]);
      expect(url).toContain("watched=watched");
      expect(url).toContain("q=film");
      expect(url).toContain("sort_by=year_desc");
    });

    it("clearing back to All drops the parameter from the request", async () => {
      searchParamsString = "watched=watched";
      const fetchMock = mockRoutes([reportRoute([watchedRow, unwatchedRow])]);

      const user = userEvent.setup();
      render(React.createElement(WatchlistPage));

      await screen.findByText("Seen Film");
      const callsBefore = fetchMock.mock.calls.length;

      await user.selectOptions(
        screen.getByLabelText("Watched status"),
        "all"
      );
      await user.click(screen.getByRole("button", { name: "Apply filters" }));

      await waitFor(() => {
        expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBefore);
      });
      const lastCall = String(
        fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0]
      );
      expect(lastCall).not.toMatch(/[?&]watched=/);
      expect(routerReplace).toHaveBeenCalledWith("/watchlist/", { scroll: false });
    });
  });
});
