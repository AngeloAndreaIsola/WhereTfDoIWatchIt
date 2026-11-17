import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WatchedPage from "@/app/watched/page";
import { unmarkMovieWatched } from "@/lib/api";
import type { FilterState } from "@/components/FilterBar";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

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
  FilterBar: ({ filters }: { filters: FilterState }) => (
    <div data-testid="filterbar" data-watched={filters.watched} />
  ),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  unmarkMovieWatched: vi.fn(),
}));

type Watchability = {
  status?: string;
  status_symbol?: string;
  status_label?: string;
};

function makeRow(overrides: Record<string, unknown> = {}) {
  return {
    movie: {
      id: 1,
      tmdb_id: 603,
      title: "The Matrix",
      year: 1999,
      content_type: "movie",
      poster_path: "/poster.jpg",
    },
    is_watched: true,
    user_data: {
      is_watched: true,
      rating: 8,
      review: "",
      watched_date: "2024-01-01",
    },
    watchability: {
      status: "watchable_here",
      status_symbol: "🟢",
      status_label: "Watchable here",
    } as Watchability,
    watch_count: 2,
    last_watched_at: "2024-01-01",
    ...overrides,
  };
}

function response(rows: unknown[], count = rows.length) {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      report: rows,
      pagination: {
        count,
        page: 1,
        page_size: 24,
        total_pages: 1,
        has_next: false,
        has_previous: false,
      },
      summary: {
        total_movies: count,
        available_movies: 0,
        unavailable_movies: 0,
        needs_recheck_movies: 0,
        countries: [],
        services: [],
      },
    }),
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

describe("Watched page cards", () => {
  beforeEach(() => {
    vi.mocked(unmarkMovieWatched).mockReset();
    fetchMock = vi.fn(async () => response([makeRow()]));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("renders poster, badges, status and all viewing metadata with a movie link", async () => {
    render(<WatchedPage />);

    const link = await screen.findByRole("link", { name: /The Matrix/ });
    expect(link).toHaveAttribute("href", "/movie/603");
    expect(screen.getByRole("img", { name: "The Matrix" })).toHaveAttribute(
      "src",
      "https://image.tmdb.org/t/p/w500/poster.jpg"
    );
    // The type appears both as the poster badge and in the meta line.
    expect(screen.getAllByText("Movie")).toHaveLength(2);
    expect(screen.getByText("✓ Watched")).toBeInTheDocument();
    expect(screen.getByText("🟢 Watchable here")).toBeInTheDocument();
    expect(screen.getByText("★ 8")).toBeInTheDocument();
    expect(screen.getByText("2024-01-01")).toBeInTheDocument();
    expect(screen.getByText("watches: 2")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Remove from Watched/ })
    ).toBeInTheDocument();
  });

  it("identifies series with a /tv detail link", async () => {
    fetchMock.mockResolvedValue(
      response([
        makeRow({
          movie: {
            id: 2,
            tmdb_id: 1396,
            title: "Breaking Bad",
            year: 2008,
            content_type: "series",
            poster_path: null,
          },
          watchability: {
            status: "not_watchable",
            status_symbol: "⚫",
            status_label: "Not watchable",
          },
          last_watched_at: undefined,
          watch_count: undefined,
          user_data: { is_watched: true, rating: null },
        }),
      ])
    );
    render(<WatchedPage />);

    expect(
      await screen.findByRole("link", { name: /Breaking Bad/ })
    ).toHaveAttribute("href", "/tv/1396");
    expect(screen.getByText("Series")).toBeInTheDocument();
    expect(screen.getByText("TV Series")).toBeInTheDocument();
    expect(screen.getByText("⚫ Not watchable")).toBeInTheDocument();
    expect(screen.getByText("No Poster")).toBeInTheDocument();
    expect(screen.queryByText("★ 8")).not.toBeInTheDocument();
    expect(screen.queryByText("watches: 2")).not.toBeInTheDocument();
  });

  it("omits optional metadata that is absent", async () => {
    fetchMock.mockResolvedValue(
      response([
        makeRow({
          user_data: { is_watched: true, rating: null },
          watchability: null,
          watch_count: null,
          last_watched_at: null,
        }),
      ])
    );
    render(<WatchedPage />);

    await screen.findByRole("link", { name: /The Matrix/ });
    expect(screen.queryByText("★ 8")).not.toBeInTheDocument();
    expect(screen.queryByText("watches: 2")).not.toBeInTheDocument();
    expect(screen.queryByText("2024-01-01")).not.toBeInTheDocument();
    expect(screen.getByText("⚪ Unknown")).toBeInTheDocument();
  });

  it("calls the API from the remove action and reports a visible pending state", async () => {
    type UnmarkResult = Awaited<ReturnType<typeof unmarkMovieWatched>>;
    let resolveUnmark!: (value: UnmarkResult) => void;
    vi.mocked(unmarkMovieWatched).mockImplementation(
      () => new Promise<UnmarkResult>((resolve) => (resolveUnmark = resolve))
    );
    render(<WatchedPage />);
    const button = await screen.findByRole("button", {
      name: /Remove from Watched/,
    });

    await userEvent.click(button);
    expect(unmarkMovieWatched).toHaveBeenCalledWith(603);
    expect(await screen.findByRole("button", { name: "Removing…" })).toBeDisabled();

    await act(async () => resolveUnmark({} as UnmarkResult));
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: /The Matrix/ })).not.toBeInTheDocument()
    );
  });

  it("shows a mutation error without removing the card", async () => {
    vi.mocked(unmarkMovieWatched).mockRejectedValueOnce(new Error("Nope"));
    render(<WatchedPage />);
    const button = await screen.findByRole("button", {
      name: /Remove from Watched/,
    });

    await userEvent.click(button);
    expect(await screen.findByRole("alert")).toHaveTextContent("Nope");
    expect(screen.getByRole("link", { name: /The Matrix/ })).toBeInTheDocument();
  });
});
