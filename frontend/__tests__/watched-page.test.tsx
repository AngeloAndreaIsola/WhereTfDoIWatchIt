import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WatchedPage from "@/app/watched/page";
import { fetchWatched, type WatchedItem, type WatchedListResponse } from "@/lib/api";

// Model the router's reactive query parameters, including back/forward navigation.
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

vi.mock("@/lib/api", () => ({ fetchWatched: vi.fn() }));
const fetchMock = vi.mocked(fetchWatched);

function item(id: number, title: string, contentType = "movie"): WatchedItem {
  return {
    movie: { tmdb_id: id, title, year: 2024, content_type: contentType, poster_path: "" },
    user_data: { is_watched: true, rating: 8, review: "", watched_date: "2024-01-01" },
    watch_count: 2,
    last_watched_at: "2024-01-01",
    is_in_watchlist: false,
  };
}

function response(results = [item(1, "Movie title")], page = 1): WatchedListResponse {
  return {
    results,
    pagination: {
      count: 60, page, page_size: 24, total_pages: 3,
      has_next: page < 3, has_previous: page > 1,
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe("Watched page", () => {
  beforeEach(() => {
    navigation.params = new URLSearchParams();
    navigation.router.replace.mockReset().mockImplementation((url: string) => {
      navigate(url.split("?")[1] || "");
    });
    fetchMock.mockReset().mockImplementation(async (params) =>
      response(undefined, Number(params?.page || 1)));
  });

  it("links movies and series to their respective detail pages", async () => {
    fetchMock.mockResolvedValue(response([item(603, "The Matrix"), item(1396, "Breaking Bad", "series")]));
    render(<WatchedPage />);
    expect(await screen.findByRole("link", { name: /The Matrix/ })).toHaveAttribute("href", "/movie/603");
    expect(screen.getByRole("link", { name: /Breaking Bad/ })).toHaveAttribute("href", "/tv/1396");
    expect(screen.getAllByText("watches: 2")).toHaveLength(2);
  });

  it("preserves filters, sorting, and pagination from a deep link", async () => {
    navigation.params = new URLSearchParams("search=Space&content_type=series&sort=rating&page=3");
    render(<WatchedPage />);
    await screen.findByRole("link", { name: /Movie title/ });
    expect(screen.getByPlaceholderText("Search title…")).toHaveValue("Space");
    expect(screen.getAllByRole("combobox")[0]).toHaveValue("series");
    expect(screen.getAllByRole("combobox")[1]).toHaveValue("rating");
    expect(fetchMock).toHaveBeenLastCalledWith({ search: "Space", content_type: "series", sort: "rating", page: "3" });
    expect(navigation.params.get("page")).toBe("3");
    expect(screen.getByRole("button", { name: /Next/ })).toBeDisabled();
  });

  it("updates controls and requests when browser back and forward change the URL", async () => {
    navigation.params = new URLSearchParams("search=First&sort=title&page=2");
    render(<WatchedPage />);
    await screen.findByRole("link", { name: /Movie title/ });
    await act(async () => navigate("search=Second&content_type=series&sort=rating&page=3"));
    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith({ search: "Second", content_type: "series", sort: "rating", page: "3" }));
    expect(screen.getByPlaceholderText("Search title…")).toHaveValue("Second");
    expect(screen.getAllByRole("combobox")[0]).toHaveValue("series");
    await act(async () => navigate("search=First&sort=title&page=2"));
    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith({ search: "First", sort: "title", page: "2" }));
    expect(screen.getByPlaceholderText("Search title…")).toHaveValue("First");
    expect(screen.getAllByRole("combobox")[0]).toHaveValue("all");
    expect(screen.getAllByRole("combobox")[1]).toHaveValue("title");
    await act(async () => navigate("search=Second&content_type=series&sort=rating&page=3"));
    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith({ search: "Second", content_type: "series", sort: "rating", page: "3" }));
  });

  it.each(["search", "content_type", "sort"])("synchronizes %s changes and resets pagination while preserving other URL parameters", async (filter) => {
    navigation.params = new URLSearchParams("page=3&source=bookmark");
    render(<WatchedPage />);
    await screen.findByRole("link", { name: /Movie title/ });
    const user = userEvent.setup();
    if (filter === "search") {
      await user.type(screen.getByPlaceholderText("Search title…"), "A & B");
    } else {
      await user.selectOptions(screen.getAllByRole("combobox")[filter === "sort" ? 1 : 0], filter === "sort" ? "title" : "series");
    }
    const value = filter === "search" ? "A & B" : filter === "sort" ? "title" : "series";
    await waitFor(() => expect(navigation.params.get(filter)).toBe(value));
    expect(navigation.params.has("page")).toBe(false);
    expect(navigation.params.get("source")).toBe("bookmark");
    expect(fetchMock).toHaveBeenLastCalledWith({ sort: "last_watched", [filter]: value });
  });

  it("removes default filters from the URL", async () => {
    navigation.params = new URLSearchParams("search=Space&content_type=movie&sort=title");
    render(<WatchedPage />);
    await screen.findByRole("link", { name: /Movie title/ });
    const user = userEvent.setup();
    await user.clear(screen.getByPlaceholderText("Search title…"));
    await user.selectOptions(screen.getAllByRole("combobox")[0], "all");
    await user.selectOptions(screen.getAllByRole("combobox")[1], "last_watched");
    await waitFor(() => expect(navigation.params.toString()).toBe(""));
    expect(fetchMock).toHaveBeenLastCalledWith({ sort: "last_watched" });
  });

  it("paginates forward and back with synchronized URLs and disabled boundaries", async () => {
    render(<WatchedPage />);
    const user = userEvent.setup();
    await screen.findByRole("link", { name: /Movie title/ });
    expect(screen.getByRole("button", { name: /Previous/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Next/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith({ sort: "last_watched", page: "2" }));
    expect(navigation.params.get("page")).toBe("2");
    await user.click(screen.getByRole("button", { name: /Next/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Next/ })).toBeDisabled());
    expect(navigation.params.get("page")).toBe("3");
    await user.click(screen.getByRole("button", { name: /Previous/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith({ sort: "last_watched", page: "2" }));
  });

  it("invalidates stale results after rapid filter changes", async () => {
    const old = deferred<WatchedListResponse>();
    const middle = deferred<WatchedListResponse>();
    const latest = deferred<WatchedListResponse>();
    fetchMock.mockReturnValueOnce(old.promise).mockReturnValueOnce(middle.promise).mockReturnValueOnce(latest.promise);
    render(<WatchedPage />);
    fireEvent.change(screen.getByPlaceholderText("Search title…"), { target: { value: "Old" } });
    fireEvent.change(screen.getByPlaceholderText("Search title…"), { target: { value: "New" } });
    await act(async () => latest.resolve(response([item(3, "Newest result")])));
    expect(screen.getByRole("link", { name: /Newest result/ })).toBeInTheDocument();
    await act(async () => {
      middle.resolve(response([item(2, "Middle result")]));
      old.resolve(response([item(1, "Stale result")]));
    });
    expect(screen.queryByRole("link", { name: /Stale result|Middle result/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Newest result/ })).toBeInTheDocument();
  });

  it("ignores stale errors and keeps loading until the latest request settles", async () => {
    const old = deferred<WatchedListResponse>();
    const latest = deferred<WatchedListResponse>();
    fetchMock.mockReturnValueOnce(old.promise).mockReturnValueOnce(latest.promise);
    render(<WatchedPage />);
    fireEvent.change(screen.getByPlaceholderText("Search title…"), { target: { value: "New" } });
    await act(async () => old.reject(new Error("Stale failure")));
    expect(screen.getByText("Loading watched titles…")).toBeInTheDocument();
    expect(screen.queryByText(/Stale failure/)).not.toBeInTheDocument();
    await act(async () => latest.resolve(response()));
    expect(screen.queryByText("Loading watched titles…")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Movie title/ })).toBeInTheDocument();
  });

  it("shows a loading state without prematurely showing the empty state", () => {
    fetchMock.mockReturnValue(new Promise(() => {}));
    render(<WatchedPage />);
    expect(screen.getByText("Loading watched titles…")).toBeInTheDocument();
    expect(screen.queryByText(/No watched titles yet/)).not.toBeInTheDocument();
  });

  it("shows the empty state and hides pagination for an empty response", async () => {
    fetchMock.mockResolvedValue({ ...response([]), pagination: { count: 0, page: 1, page_size: 24, total_pages: 1, has_next: false, has_previous: false } });
    render(<WatchedPage />);
    expect(await screen.findByText(/No watched titles yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Next|Previous/ })).not.toBeInTheDocument();
  });

  it("shows an error and recovers on the next filter request", async () => {
    fetchMock.mockRejectedValueOnce(new Error("Server unavailable"));
    render(<WatchedPage />);
    expect(await screen.findByText(/Error:.*Server unavailable/)).toBeInTheDocument();
    expect(screen.queryByText(/No watched titles yet/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Search title…"), { target: { value: "Retry" } });
    expect(await screen.findByRole("link", { name: /Movie title/ })).toBeInTheDocument();
    expect(screen.queryByText(/Server unavailable/)).not.toBeInTheDocument();
  });
});
