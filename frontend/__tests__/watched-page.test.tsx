import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WatchedPage from "@/app/watched/page";
import { unmarkMovieWatched } from "@/lib/api";
import type { FilterState } from "@/components/FilterBar";

// Simulate reactive browser navigation rather than a fixed URL snapshot.
const navigation = vi.hoisted(() => ({
  params: new URLSearchParams(),
  listeners: new Set<() => void>(),
  router: { replace: vi.fn() },
}));

function navigate(query: string) {
  navigation.params = new URLSearchParams(query);
  navigation.listeners.forEach((listener) => listener());
}

vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  return {
    useRouter: () => navigation.router,
    useSearchParams: () => useSyncExternalStore(
      (listener) => {
        navigation.listeners.add(listener);
        return () => navigation.listeners.delete(listener);
      },
      () => navigation.params,
    ),
  };
});

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) =>
    <a href={href} {...props}>{children}</a>,
}));

vi.mock("@/components/CurrentLocationProvider", () => ({
  useCurrentLocation: () => ({ currentCountry: "US" }),
}));

// Test the watched page's wiring and history independently of the large shared
// FilterBar's draft/application UI. The actual FilterBar has separate tests.
vi.mock("@/components/FilterBar", () => ({
  FilterBar: ({ filters, onChange }: {
    filters: FilterState;
    onChange: (next: FilterState) => void;
  }) => (
    <div>
      <input
        placeholder="Search title…"
        value={filters.q}
        onChange={(event) => onChange({ ...filters, q: event.target.value })}
      />
      <select
        aria-label="Content type"
        value={filters.content_type}
        onChange={(event) => onChange({ ...filters, content_type: event.target.value })}
      >
        <option value="all">All</option>
        <option value="movie">Movies</option>
        <option value="series">Series</option>
      </select>
      <select
        aria-label="Sort order"
        value={filters.sort_by}
        onChange={(event) => onChange({ ...filters, sort_by: event.target.value })}
      >
        <option value="">Recently watched</option>
        <option value="title_asc">Title</option>
        <option value="watched_rating_desc">Your rating</option>
      </select>
    </div>
  ),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  unmarkMovieWatched: vi.fn(),
}));

function row(id: number, title: string, contentType = "movie") {
  return {
    movie: { id, tmdb_id: id, title, year: 2024, content_type: contentType, poster_path: null },
    user_data: { is_watched: true, rating: 8, review: "", watched_date: "2024-01-01" },
    watch_count: 2,
    last_watched_at: "2024-01-01",
    watchability: { status: "watchable_here", status_symbol: "🟢", status_label: "Watchable" },
    is_watched: true,
  };
}

function report(
  rows = [row(1, "Movie title")],
  page = 1,
  count = 60,
) {
  return {
    report: rows,
    pagination: {
      count, page, page_size: 24, total_pages: Math.max(1, Math.ceil(count / 24)),
      has_next: page < Math.ceil(count / 24), has_previous: page > 1,
    },
    summary: {
      total_movies: count, available_movies: 0, unavailable_movies: count,
      needs_recheck_movies: 0, countries: [], services: [],
    },
  };
}

function jsonResponse(data: unknown, ok = true, status = 200) {
  return { ok, status, json: async () => data };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((r, j) => { resolve = r; reject = j; });
  return { promise, resolve, reject };
}

let fetchMock: ReturnType<typeof vi.fn>;

function reportParams() {
  const calls = fetchMock.mock.calls.filter(
    (args) => String(args[0]).startsWith("/api/watched/report/")
  );
  if (!calls.length) throw new Error("watched/report/ was not requested");
  const url = String(calls[calls.length - 1][0]);
  return new URL(url, "http://localhost").searchParams;
}

describe("Watched page", () => {
  beforeEach(() => {
    navigation.params = new URLSearchParams();
    navigation.router.replace.mockReset().mockImplementation((url: string) => {
      navigate(url.split("?")[1] || "");
    });
    vi.mocked(unmarkMovieWatched).mockReset();
    fetchMock = vi.fn(async (url: string) => {
      const requestedPage = Number(new URL(url, "http://localhost").searchParams.get("page") || "1");
      return jsonResponse(report(undefined, requestedPage));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("links movies and series and preserves watched-history details", async () => {
    fetchMock.mockResolvedValue(jsonResponse(report([
      row(603, "The Matrix"), row(1396, "Breaking Bad", "series"),
    ])));
    render(<WatchedPage />);
    expect(await screen.findByRole("link", { name: /The Matrix/ })).toHaveAttribute("href", "/movie/603");
    expect(screen.getByRole("link", { name: /Breaking Bad/ })).toHaveAttribute("href", "/tv/1396");
    expect(screen.getAllByText("watches: 2")).toHaveLength(2);
    expect(screen.getAllByText("★ 8")).toHaveLength(2);
  });

  it("restores old-format deep-link filters, sorting, and page", async () => {
    navigation.params = new URLSearchParams("search=Space&content_type=series&sort=rating&page=3");
    render(<WatchedPage />);
    await waitFor(() => expect(reportParams().get("page")).toBe("3"));
    expect(screen.getByPlaceholderText("Search title…")).toHaveValue("Space");
    expect(screen.getByRole("combobox", { name: "Content type" })).toHaveValue("series");
    expect(screen.getByRole("combobox", { name: "Sort order" })).toHaveValue("watched_rating_desc");
    expect(reportParams().get("q")).toBe("Space");
    expect(reportParams().get("type")).toBe("series");
    expect(reportParams().get("sort_by")).toBe("watched_rating_desc");
    expect(screen.getByRole("button", { name: /Next/ })).toBeDisabled();
  });

  it("responds to browser back/forward without overwriting history", async () => {
    navigation.params = new URLSearchParams("q=First&sort_by=title_asc&page=2");
    render(<WatchedPage />);
    await waitFor(() => expect(reportParams().get("q")).toBe("First"));
    await act(async () => navigate("q=Second&type=series&sort_by=watched_rating_desc&page=3"));
    await waitFor(() => expect(reportParams().get("q")).toBe("Second"));
    expect(reportParams().get("page")).toBe("3");
    expect(screen.getByPlaceholderText("Search title…")).toHaveValue("Second");
    expect(screen.getByRole("combobox", { name: "Content type" })).toHaveValue("series");
    await act(async () => navigate("q=First&sort_by=title_asc&page=2"));
    await waitFor(() => expect(reportParams().get("q")).toBe("First"));
    expect(reportParams().get("page")).toBe("2");
    expect(screen.getByRole("combobox", { name: "Content type" })).toHaveValue("all");
    expect(screen.getByRole("combobox", { name: "Sort order" })).toHaveValue("title_asc");
  });

  it.each(["q", "type", "sort_by"])(
    "applies %s and resets pagination without losing unrelated URL parameters",
    async (key) => {
      navigation.params = new URLSearchParams("page=3&source=bookmark");
      render(<WatchedPage />);
      await screen.findByRole("link", { name: /Movie title/ });
      if (key === "q") {
        fireEvent.change(screen.getByPlaceholderText("Search title…"), { target: { value: "A & B" } });
      } else if (key === "type") {
        fireEvent.change(screen.getByRole("combobox", { name: "Content type" }), { target: { value: "series" } });
      } else {
        fireEvent.change(screen.getByRole("combobox", { name: "Sort order" }), { target: { value: "title_asc" } });
      }
      const expected = key === "q" ? "A & B" : key === "type" ? "series" : "title_asc";
      await waitFor(() => expect(navigation.params.get(key)).toBe(expected));
      expect(navigation.params.has("page")).toBe(false);
      expect(navigation.params.get("source")).toBe("bookmark");
      await waitFor(() => expect(reportParams().get(key)).toBe(expected));
    },
  );

  it("removes default filters from the URL", async () => {
    navigation.params = new URLSearchParams("q=Space&type=movie&sort_by=title_asc");
    render(<WatchedPage />);
    await screen.findByRole("link", { name: /Movie title/ });
    fireEvent.change(screen.getByPlaceholderText("Search title…"), { target: { value: "" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Content type" }), { target: { value: "all" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Sort order" }), { target: { value: "" } });
    await waitFor(() => expect(navigation.params.toString()).toBe(""));
    expect(reportParams().has("sort_by")).toBe(false);
  });

  it("paginates forward and backward with correct URL and boundaries", async () => {
    render(<WatchedPage />);
    const user = userEvent.setup();
    await screen.findByRole("link", { name: /Movie title/ });
    expect(screen.getByRole("button", { name: /Previous/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Next/ }));
    await waitFor(() => expect(reportParams().get("page")).toBe("2"));
    expect(navigation.params.get("page")).toBe("2");
    await user.click(screen.getByRole("button", { name: /Next/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Next/ })).toBeDisabled());
    expect(navigation.params.get("page")).toBe("3");
    await user.click(screen.getByRole("button", { name: /Previous/ }));
    await waitFor(() => expect(reportParams().get("page")).toBe("2"));
  });

  it("rejects stale request results when filters change rapidly", async () => {
    const old = deferred<ReturnType<typeof jsonResponse>>();
    const middle = deferred<ReturnType<typeof jsonResponse>>();
    const latest = deferred<ReturnType<typeof jsonResponse>>();
    fetchMock.mockReset().mockReturnValueOnce(old.promise).mockReturnValueOnce(middle.promise).mockReturnValueOnce(latest.promise);
    render(<WatchedPage />);
    fireEvent.change(screen.getByPlaceholderText("Search title…"), { target: { value: "Old" } });
    fireEvent.change(screen.getByPlaceholderText("Search title…"), { target: { value: "New" } });
    await act(async () => latest.resolve(jsonResponse(report([row(3, "Newest result")]))));
    expect(screen.getByRole("link", { name: /Newest result/ })).toBeInTheDocument();
    await act(async () => {
      middle.resolve(jsonResponse(report([row(2, "Middle result")])));
      old.resolve(jsonResponse(report([row(1, "Stale result")])));
    });
    expect(screen.queryByRole("link", { name: /Stale result|Middle result/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Newest result/ })).toBeInTheDocument();
  });

  it("ignores stale failures and waits for the newest request", async () => {
    const old = deferred<ReturnType<typeof jsonResponse>>();
    const latest = deferred<ReturnType<typeof jsonResponse>>();
    fetchMock.mockReset().mockReturnValueOnce(old.promise).mockReturnValueOnce(latest.promise);
    render(<WatchedPage />);
    fireEvent.change(screen.getByPlaceholderText("Search title…"), { target: { value: "New" } });
    await act(async () => old.reject(new Error("Stale failure")));
    expect(screen.getByText("Loading watched titles…")).toBeInTheDocument();
    expect(screen.queryByText(/Stale failure/)).not.toBeInTheDocument();
    await act(async () => latest.resolve(jsonResponse(report())));
    expect(screen.queryByText("Loading watched titles…")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Movie title/ })).toBeInTheDocument();
  });

  it("shows loading state without prematurely showing empty state", () => {
    fetchMock.mockReturnValue(new Promise(() => {}));
    render(<WatchedPage />);
    expect(screen.getByText("Loading watched titles…")).toBeInTheDocument();
    expect(screen.queryByText(/No watched titles yet/)).not.toBeInTheDocument();
  });

  it("shows an empty state without pagination", async () => {
    fetchMock.mockResolvedValue(jsonResponse(report([], 1, 0)));
    render(<WatchedPage />);
    expect(await screen.findByText(/No watched titles yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Next|Previous/ })).not.toBeInTheDocument();
  });

  it("distinguishes an empty filtered result from an empty viewing history", async () => {
    navigation.params = new URLSearchParams("q=NoMatches");
    fetchMock.mockResolvedValue(jsonResponse(report([], 1, 0)));
    render(<WatchedPage />);
    expect(await screen.findByText(/No watched titles match your filters/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Clear filters/ })).toBeInTheDocument();
  });

  it("shows a server error and recovers after a new filter request", async () => {
    fetchMock.mockRejectedValueOnce(new Error("Server unavailable"));
    render(<WatchedPage />);
    expect(await screen.findByText(/Error: Server unavailable/)).toBeInTheDocument();
    expect(screen.queryByText(/No watched titles yet/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Search title…"), { target: { value: "Retry" } });
    expect(await screen.findByRole("link", { name: /Movie title/ })).toBeInTheDocument();
    expect(screen.queryByText(/Server unavailable/)).not.toBeInTheDocument();
  });

  it("uses numeric actor ids in the API request, restoring actor display names from the URL", async () => {
    navigation.params = new URLSearchParams("actor_id=1&actor_name=Actor+One&duration_min=90");
    render(<WatchedPage />);
    await waitFor(() => expect(reportParams().get("actor_id")).toBe("1"));
    expect(reportParams().get("actor_name")).toBe("Actor One");
    expect(reportParams().get("duration_min")).toBe("90");
  });

  it("unmarks a title locally after successful API confirmation", async () => {
    vi.mocked(unmarkMovieWatched).mockResolvedValue(undefined as never);
    fetchMock.mockResolvedValue(jsonResponse(report([row(603, "Seen Film")])));
    render(<WatchedPage />);
    const user = userEvent.setup();
    await screen.findByRole("link", { name: /Seen Film/ });
    await user.click(screen.getByRole("button", { name: /Remove from Watched/ }));
    await waitFor(() => expect(unmarkMovieWatched).toHaveBeenCalledWith(603, "movie"));
    expect(screen.queryByRole("link", { name: /Seen Film/ })).not.toBeInTheDocument();
  });
});
