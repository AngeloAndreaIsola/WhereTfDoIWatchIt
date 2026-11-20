import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WatchlistPage from "@/app/watchlist/page";
import { markMovieWatched, unmarkMovieWatched } from "@/lib/api";
import type { FilterState } from "@/components/FilterBar";

// Reactive navigation so URL rewrites performed by the page are observable,
// exactly as they are in the browser.
const navigation = vi.hoisted(() => ({
  params: new URLSearchParams(),
  router: { replace: vi.fn(), push: vi.fn(), back: vi.fn() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigation.router,
  useSearchParams: () => new URLSearchParams(navigation.params.toString()),
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

// The watched selector is wired to onChange directly. FilterBar's staged
// Apply/Reset semantics are covered separately (issue #93) and are not
// changed by these tests.
vi.mock("@/components/FilterBar", () => ({
  FilterBar: ({
    filters,
    onChange,
  }: {
    filters: FilterState;
    onChange: (next: FilterState) => void;
  }) => (
    <select
      aria-label="Watched status"
      value={filters.watched || "all"}
      onChange={(event) => onChange({ ...filters, watched: event.target.value })}
    >
      <option value="all">All</option>
      <option value="watched">Watched</option>
      <option value="unwatched">Unwatched</option>
    </select>
  ),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  markMovieWatched: vi.fn(),
  unmarkMovieWatched: vi.fn(),
}));

type Row = {
  movie: { id: number; tmdb_id: number; title: string; year: number; content_type: string };
  is_watched: boolean;
  user_data: { is_watched: boolean } | null;
};

function row(id: number, isWatched: boolean, title = `Film ${id}`): Row {
  return {
    movie: { id, tmdb_id: id, title, year: 2000 + id, content_type: "movie" },
    is_watched: isWatched,
    user_data: isWatched ? { is_watched: true } : null,
  };
}

function json(data: unknown) {
  return { ok: true, status: 200, json: async () => data };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((r, j) => {
    resolve = r;
    reject = j;
  });
  return { promise, resolve, reject };
}

let serverRows: Row[];
let fetchMock: ReturnType<typeof vi.fn>;

function reportForCurrentParams(params: URLSearchParams) {
  const watched = params.get("watched");
  const rows =
    watched === "watched"
      ? serverRows.filter((r) => r.is_watched)
      : watched === "unwatched"
        ? serverRows.filter((r) => !r.is_watched)
        : serverRows;
  const page = Number(params.get("page") || "1");
  const size = Number(params.get("page_size") || "24");
  const totalPages = Math.max(1, Math.ceil(rows.length / size));
  return {
    report: rows.slice((page - 1) * size, page * size),
    pagination: {
      count: rows.length,
      page,
      page_size: size,
      total_pages: totalPages,
      has_next: page < totalPages,
      has_previous: page > 1,
    },
    summary: { total_movies: rows.length },
  };
}

/** Server state is read on every request, so a revalidation sees mutations. */
function installServer(rows: Row[]) {
  serverRows = rows;
  fetchMock = vi.fn(async (url: string) => {
    const params = new URL(url, "http://localhost").searchParams;
    return json(reportForCurrentParams(params));
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.mocked(markMovieWatched).mockImplementation(async (id) => {
    const found = serverRows.find((r) => r.movie.tmdb_id === id);
    if (found) {
      found.is_watched = true;
      found.user_data = { is_watched: true };
    }
    return { user_data: { is_watched: true } } as never;
  });
  vi.mocked(unmarkMovieWatched).mockImplementation(async (id) => {
    const found = serverRows.find((r) => r.movie.tmdb_id === id);
    if (found) {
      found.is_watched = false;
      found.user_data = null;
    }
    return { user_data: { is_watched: false } } as never;
  });
  return fetchMock;
}

function lastReportParams() {
  const calls = fetchMock.mock.calls.filter(([url]) =>
    String(url).includes("/api/watchlist/report/")
  );
  if (!calls.length) throw new Error("watchlist/report was not requested");
  return new URL(String(calls[calls.length - 1][0]), "http://localhost").searchParams;
}

describe("Watchlist watched mutation consistency", () => {
  beforeEach(() => {
    navigation.params = new URLSearchParams();
    navigation.router.replace.mockReset().mockImplementation((url: string) => {
      navigation.params = new URLSearchParams(String(url).split("?")[1] || "");
    });
  });

  afterEach(() => vi.unstubAllGlobals());

  it("marking watched while filtering Unwatched removes the row and shows the empty state", async () => {
    navigation.params = new URLSearchParams("watched=unwatched");
    const mock = installServer([row(1, false, "Unseen Film")]);
    render(<WatchlistPage />);
    expect(await screen.findByText("Unseen Film")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Mark as Watched/ }));

    await waitFor(() =>
      expect(screen.queryByText("Unseen Film")).not.toBeInTheDocument()
    );
    expect(screen.getByText(/Your wishlist is empty/)).toBeInTheDocument();
    const calls = mock.mock.calls.filter(([url]) =>
      String(url).includes("/api/watchlist/report/")
    );
    expect(calls.length).toBeGreaterThan(1);
    expect(lastReportParams().get("watched")).toBe("unwatched");
    expect(markMovieWatched).toHaveBeenCalledWith(1, expect.objectContaining({ watched_date: expect.any(String) }), "movie");
  });

  it("unmarking watched while filtering Watched removes the row", async () => {
    navigation.params = new URLSearchParams("watched=watched");
    const series = row(2, true, "Seen Series");
    series.movie.content_type = "series";
    installServer([series]);
    render(<WatchlistPage />);
    expect(await screen.findByText("Seen Series")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Click to Unmark/ }));

    await waitFor(() =>
      expect(screen.queryByText("Seen Series")).not.toBeInTheDocument()
    );
    expect(screen.getByText(/Your wishlist is empty/)).toBeInTheDocument();
    expect(lastReportParams().get("watched")).toBe("watched");
    expect(unmarkMovieWatched).toHaveBeenCalledWith(2, "series");
  });

  it("refreshes the label from the server when no watched filter is active", async () => {
    installServer([row(3, false, "Mixed Film")]);
    render(<WatchlistPage />);
    expect(await screen.findByText("Mixed Film")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Mark as Watched/ }));
    expect(
      await screen.findByRole("button", { name: /Click to Unmark/ })
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Click to Unmark/ }));
    expect(
      await screen.findByRole("button", { name: /Mark as Watched/ })
    ).toBeInTheDocument();
  });

  it("moves back to the last valid page when the final item disappears", async () => {
    navigation.params = new URLSearchParams("watched=unwatched");
    const rows = Array.from({ length: 25 }, (_, i) => row(i + 1, false));
    installServer(rows);
    render(<WatchlistPage />);

    // The watchlist starts on page 1; reach the single-item second page via
    // the same Pagination control a user would click.
    expect(await screen.findByText("Film 1")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Next/ }));
    const lastName = "Film 25";
    expect(await screen.findByText(lastName)).toBeInTheDocument();
    expect(screen.queryByText("Film 1")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Mark as Watched/ }));

    await waitFor(() => expect(screen.getByText("Film 1")).toBeInTheDocument());
    expect(screen.queryByText(lastName)).not.toBeInTheDocument();
    expect(navigation.params.has("page")).toBe(false);
    expect(lastReportParams().get("page")).toBe("1");
  });

  it("keeps the previous rows and reports the error when a mutation fails", async () => {
    installServer([row(4, false, "Still Here")]);
    vi.mocked(markMovieWatched).mockRejectedValueOnce(new Error("Network down"));
    render(<WatchlistPage />);
    expect(await screen.findByText("Still Here")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Mark as Watched/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Network down");
    expect(screen.getByText("Still Here")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Mark as Watched/ })).toBeInTheDocument();
  });

  it("keeps the previous rows when a refresh fails", async () => {
    const mock = installServer([row(5, false, "Kept Row")]);
    render(<WatchlistPage />);
    expect(await screen.findByText("Kept Row")).toBeInTheDocument();

    // Fail the *next* request (the one triggered by the filter change), not
    // the initial load that populated the rows we want to preserve.
    mock.mockImplementationOnce(async () => ({
      ok: false,
      status: 500,
      json: async () => ({ error: "Refresh failed" }),
    }));
    fireEvent.change(screen.getByLabelText("Watched status"), {
      target: { value: "all" },
    });

    await waitFor(() =>
      expect(screen.getByText(/Refresh failed|Failed to load watchlist/)).toBeInTheDocument()
    );
    expect(screen.getByText("Kept Row")).toBeInTheDocument();
  });

  it("discards a stale revalidation response after a newer filter change", async () => {
    installServer([row(6, false, "Fresh Only")]);
    // Queue the next two responses explicitly so the test does not depend on
    // how many incidental renders React performs before the mutation.
    const pending: Array<ReturnType<typeof deferred<ReturnType<typeof json>>>> = [];
    fetchMock.mockImplementation((url: string) => {
      const next = pending.shift();
      if (next) return next.promise;
      return Promise.resolve(
        json(reportForCurrentParams(new URL(String(url), "http://localhost").searchParams))
      );
    });

    render(<WatchlistPage />);
    expect(await screen.findByText("Fresh Only")).toBeInTheDocument();

    const older = deferred<ReturnType<typeof json>>();
    const newer = deferred<ReturnType<typeof json>>();
    pending.push(older, newer);

    // Marking watched triggers revalidation #1; changing the filter triggers
    // revalidation #2, which supersedes it.
    await userEvent.click(screen.getByRole("button", { name: /Mark as Watched/ }));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(1));
    fireEvent.change(screen.getByLabelText("Watched status"), {
      target: { value: "watched" },
    });
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(2));

    await act(async () => {
      newer.resolve(json({ report: [row(7, true, "Newest Result")], pagination: { total_pages: 1 } }));
    });
    expect(await screen.findByText("Newest Result")).toBeInTheDocument();

    await act(async () => {
      older.resolve(json({ report: [row(8, false, "Stale Result")], pagination: { total_pages: 1 } }));
    });
    expect(screen.queryByText("Stale Result")).not.toBeInTheDocument();
    expect(screen.getByText("Newest Result")).toBeInTheDocument();
  });
});
