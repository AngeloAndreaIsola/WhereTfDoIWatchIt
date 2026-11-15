import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import WatchedPage from "@/app/watched/page";

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
  usePathname: () => "/watched",
  useSearchParams: () => new URLSearchParams(searchParamsString),
}));

vi.mock("@/components/CurrentLocationProvider", () => ({
  useCurrentLocation: () => ({ currentCountry: "US" }),
}));

type RouteResponse = { ok?: boolean; status?: number; body: unknown };

function mockRoutes(
  routes: Array<[string, RouteResponse]>,
  fallback: RouteResponse = { ok: true, status: 200, body: { report: [], pagination: {}, summary: {} } }
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
  "/api/watched/report",
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
  watchability: { status: "watchable_here", status_symbol: "🟢", status_label: "Watchable" },
  is_watched: true,
};

beforeEach(() => {
  routerReplace.mockReset();
  searchParamsString = "";
});

describe("Watched page actor + duration filtering", () => {
  it("renders FilterBar with actor search and duration controls", () => {
    mockRoutes([reportRoute([watchedRow])]);
    render(React.createElement(WatchedPage));

    expect(screen.getByRole("button", { name: "Apply filters" })).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("Type actor name (e.g. Ryan Gosling)...")
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("duration-filter-toggle")
    ).toBeInTheDocument();
  });

  it("initial load calls /api/watched/report/ without actor/duration params", async () => {
    const fetchMock = mockRoutes([reportRoute([watchedRow])]);

    render(React.createElement(WatchedPage));
    await screen.findByText("Seen Film");

    const reportCalls = fetchMock.mock.calls.filter(
      (call) =>
        typeof call[0] === "string" && call[0].includes("/api/watched/report/")
    );
    expect(reportCalls.length).toBeGreaterThan(0);
    for (const call of reportCalls) {
      const url = String(call[0]);
      expect(url).not.toMatch(/[?&]actor_id=/);
      expect(url).not.toMatch(/[?&]duration_min=/);
      expect(url).not.toMatch(/[?&]duration_max=/);
    }
  });

  it("restores actor_id and duration_min from the URL on load", async () => {
    searchParamsString = "actor_id=1&duration_min=90";
    const fetchMock = mockRoutes([reportRoute([watchedRow])]);

    render(React.createElement(WatchedPage));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          (call) =>
            typeof call[0] === "string" &&
            call[0].includes("actor_id=1") &&
            call[0].includes("duration_min=90")
        )
      ).toBe(true);
    });
  });

  it("applies actor_id + duration_min when filters are set via URL", async () => {
    searchParamsString = "actor_id=1&duration_min=100&page_size=24";
    const fetchMock = mockRoutes([reportRoute([watchedRow])]);

    render(React.createElement(WatchedPage));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          (call) =>
            typeof call[0] === "string" &&
            call[0].includes("actor_id=1") &&
            call[0].includes("duration_min=100")
        )
      ).toBe(true);
    });
  });
});
