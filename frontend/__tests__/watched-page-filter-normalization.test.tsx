import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WatchedPage from "@/app/watched/page";
import type { FilterState } from "@/components/FilterBar";

// Reactive navigation so router.replace in the page is observable.
const navigation = vi.hoisted(() => ({
  params: new URLSearchParams(),
  listeners: new Set<() => void>(),
  router: { replace: vi.fn(), push: vi.fn(), back: vi.fn() },
}));

function navigate(query: string) {
  navigation.params = new URLSearchParams(query);
  navigation.listeners.forEach((listener) => listener());
}

vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  return {
    useRouter: () => navigation.router,
    useSearchParams: () =>
      useSyncExternalStore(
        (listener) => {
          navigation.listeners.add(listener);
          return () => navigation.listeners.delete(listener);
        },
        () => navigation.params,
      ),
  };
});

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

vi.mock("@/components/CurrentLocationProvider", () => ({
  useCurrentLocation: () => ({ currentCountry: "US" }),
}));

vi.mock("@/components/FilterBar", () => ({
  FilterBar: ({
    filters,
    onChange,
    showWatchedFilter,
  }: {
    filters: FilterState;
    onChange: (next: FilterState) => void;
    showWatchedFilter?: boolean;
  }) => (
    <div>
      <span data-testid="watched-selector">{String(showWatchedFilter)}</span>
      <span data-testid="watched-value">{filters.watched}</span>
      <input
        placeholder="Search title…"
        value={filters.q}
        onChange={(event) => onChange({ ...filters, q: event.target.value })}
      />
    </div>
  ),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  unmarkMovieWatched: vi.fn(),
}));

function response(rows: unknown[], page = 1, count = rows.length) {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      report: rows,
      pagination: {
        count,
        page,
        page_size: 24,
        total_pages: 1,
        has_next: false,
        has_previous: page > 1,
      },
      summary: {
        total_movies: count,
        available_movies: 0,
        unavailable_movies: count,
        needs_recheck_movies: 0,
        countries: [],
        services: [],
      },
    }),
  };
}

const film = {
  movie: {
    tmdb_id: 603,
    title: "The Matrix",
    year: 1999,
    content_type: "movie",
    poster_path: null,
  },
  is_watched: true,
  user_data: { is_watched: true, rating: 8, review: "", watched_date: "2024-01-01" },
  watch_count: 1,
  last_watched_at: "2024-01-01",
  watchability: {
    status: "watchable_here",
    status_symbol: "🟢",
    status_label: "Watchable here",
  },
};

let fetchMock: ReturnType<typeof vi.fn>;

function reportParams() {
  const calls = fetchMock.mock.calls.filter(([url]) =>
    String(url).includes("/api/watched/report/")
  );
  if (!calls.length) throw new Error("watched/report was not requested");
  return new URL(String(calls[calls.length - 1][0]), "http://localhost").searchParams;
}

describe("Watched page redundant filter", () => {
  beforeEach(() => {
    navigation.params = new URLSearchParams();
    navigation.router.replace.mockReset().mockImplementation((url: string) => {
      navigate(String(url).split("?")[1] || "");
    });
    fetchMock = vi.fn(async () => response([film]));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("hides the watched selector and forces the All state", async () => {
    render(<WatchedPage />);
    expect(await screen.findByRole("link", { name: /The Matrix/ })).toBeInTheDocument();
    expect(screen.getByTestId("watched-selector")).toHaveTextContent("false");
    expect(screen.getByTestId("watched-value")).toHaveTextContent("all");
  });

  it("normalizes a legacy watched=unwatched URL without an empty page", async () => {
    navigation.params = new URLSearchParams("watched=unwatched&q=Matrix&page=2");
    render(<WatchedPage />);

    expect(await screen.findByRole("link", { name: /The Matrix/ })).toBeInTheDocument();
    await waitFor(() => expect(navigation.params.has("watched")).toBe(false));
    const params = reportParams();
    expect(params.has("watched")).toBe(false);
    expect(params.get("q")).toBe("Matrix");
    expect(params.get("page")).toBe("2");
    expect(screen.getByTestId("watched-value")).toHaveTextContent("all");
  });

  it("preserves unrelated URL parameters while normalizing", async () => {
    navigation.params = new URLSearchParams("watched=watched&source=bookmark&sort_by=title_asc");
    render(<WatchedPage />);

    await screen.findByRole("link", { name: /The Matrix/ });
    await waitFor(() => expect(navigation.params.has("watched")).toBe(false));
    expect(navigation.params.get("source")).toBe("bookmark");
    expect(navigation.params.get("sort_by")).toBe("title_asc");
    expect(reportParams().has("watched")).toBe(false);
  });

  it("normalizes a legacy URL reached through browser navigation without a loop", async () => {
    render(<WatchedPage />);
    await screen.findByRole("link", { name: /The Matrix/ });
    const callsBefore = navigation.router.replace.mock.calls.length;

    await act(async () => navigate("watched=unwatched&q=Second&page=3"));

    await waitFor(() => expect(reportParams().get("q")).toBe("Second"));
    expect(reportParams().has("watched")).toBe(false);
    expect(reportParams().get("page")).toBe("3");
    expect(navigation.params.has("watched")).toBe(false);
    expect(screen.getByPlaceholderText("Search title…")).toHaveValue("Second");
    // One normalization replace, then stable: no replace/fetch loop.
    expect(navigation.router.replace.mock.calls.length).toBeGreaterThan(callsBefore);
    const afterLoop = navigation.router.replace.mock.calls.length;
    await act(async () => {});
    expect(navigation.router.replace.mock.calls.length).toBe(afterLoop);
  });

  it("keeps ordinary filter changes and URL sync working", async () => {
    render(<WatchedPage />);
    await screen.findByRole("link", { name: /The Matrix/ });
    fireEvent.change(screen.getByPlaceholderText("Search title…"), {
      target: { value: "A & B" },
    });
    await waitFor(() => expect(reportParams().get("q")).toBe("A & B"));
    expect(navigation.params.get("q")).toBe("A & B");
    expect(reportParams().has("watched")).toBe(false);
  });
});
