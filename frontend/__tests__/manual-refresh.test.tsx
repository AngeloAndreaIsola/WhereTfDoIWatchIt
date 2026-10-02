import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import MovieDetailPage from "@/app/movie/[tmdb_id]/page";
import SeriesDetailPage from "@/app/tv/[tmdb_id]/page";

const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: nav.replace, back: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode; [key: string]: unknown }) =>
    React.createElement("a", { href, ...props }, children),
}));

vi.mock("next/image", () => ({
  default: (props: Record<string, unknown>) => {
    const { fill, sizes, priority, ...rest } = props;
    return React.createElement("img", rest);
  },
}));

interface MockedResponse {
  ok?: boolean;
  status?: number;
  body: unknown;
}

function response({ ok = true, status = 200, body }: MockedResponse) {
  return { ok, status, json: async () => body };
}

const preferencesBody = { audio_languages: ["EN"], subtitle_languages: ["EN"] };
const unofficialBody = { offers: [] };

const offer = (service: string) => ({
  country: "US",
  service,
  offer_type: "subscription",
  quality: "hd",
  url: "https://example.com/watch",
  audio_languages: ["EN"],
  subtitle_languages: ["EN"],
  ranking: { audio_rank: 0, subtitle_rank: 0, quality_rank: 0, is_language_match: true },
});

/** Loaded title with canonical offers and nothing queued. */
const loadedMovieBody = {
  movie: { id: 1, tmdb_id: 550, title: "Fight Club", year: 1999, content_type: "movie" },
  user_data: null,
  is_in_watchlist: false,
  watch_events: [],
  offers: [offer("Netflix")],
  refresh_pending: false,
  refresh_status: "done",
};

/** A poll answered while the manually enqueued request is still queued. */
const stillQueuedMovieBody = {
  ...loadedMovieBody,
  refresh_pending: true,
  refresh_status: "pending",
};

/** POST /refresh/ 202 body. */
const refreshQueuedBody = {
  message: "Availability refresh queued",
  tmdb_id: 550,
  content_type: "movie",
  refresh_pending: true,
  refresh_status: "pending",
  priority: 10,
  request_id: 7,
};

const loadedSeriesBody = {
  series: { id: 2, tmdb_id: 1396, title: "Breaking Bad", year: 2008, content_type: "series" },
  user_data: null,
  is_in_watchlist: false,
  watch_events: [],
  offers: [offer("Netflix")],
  seasons: [],
  selected_season: null,
  refresh_pending: false,
  refresh_status: "done",
};

/** Flush pending promise chains without relying on the fake clock. */
async function flush() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {});
  }
}

type FetchMock = ReturnType<typeof vi.fn>;

function callsMatching(fetchMock: FetchMock, substring: string) {
  return fetchMock.mock.calls.filter(
    (call) => typeof call[0] === "string" && call[0].includes(substring)
  );
}

function primeCalls(fetchMock: FetchMock, kind: "movie" | "series") {
  return callsMatching(fetchMock, `/api/prime/${kind}/`);
}

function clickRefresh() {
  fireEvent.click(screen.getByRole("button", { name: /refresh availability/i }));
}

describe("Manual refresh availability button", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    nav.push.mockReset();
    nav.replace.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("renders an idle Refresh availability button once the detail loads", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ body: preferencesBody }))
      .mockResolvedValueOnce(response({ body: loadedMovieBody }))
      .mockResolvedValueOnce(response({ body: unofficialBody }));
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(MovieDetailPage, { params: { tmdb_id: "550" } }));

    const button = await screen.findByRole("button", { name: "Refresh availability" });
    expect(button).toBeEnabled();
    expect(callsMatching(fetchMock, "/refresh/")).toHaveLength(0);
  });

  it("posts an explicit refresh and keeps the current offers on screen", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ body: preferencesBody }))
      .mockResolvedValueOnce(response({ body: loadedMovieBody }))
      .mockResolvedValueOnce(response({ body: unofficialBody }))
      .mockResolvedValueOnce(response({ body: refreshQueuedBody }))
      .mockResolvedValue(response({ body: stillQueuedMovieBody }));
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(MovieDetailPage, { params: { tmdb_id: "550" } }));
    await flush();

    clickRefresh();
    await flush();

    const posts = callsMatching(fetchMock, "/api/movie/550/refresh/");
    expect(posts).toHaveLength(1);
    expect(posts[0][1]).toMatchObject({ method: "POST" });

    expect(screen.getByRole("button", { name: "Refreshing…" })).toBeDisabled();
    // Existing offers stay visible while the worker has not written new ones.
    expect(screen.getByText("Netflix")).toBeInTheDocument();
  });

  it("polls until the worker settles the request, then re-enables the button", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ body: preferencesBody }))
      .mockResolvedValueOnce(response({ body: loadedMovieBody }))
      .mockResolvedValueOnce(response({ body: unofficialBody }))
      .mockResolvedValueOnce(response({ body: refreshQueuedBody }))
      .mockResolvedValueOnce(response({ body: stillQueuedMovieBody }))
      .mockResolvedValue(response({ body: loadedMovieBody }));
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(MovieDetailPage, { params: { tmdb_id: "550" } }));
    await flush();

    clickRefresh();
    await flush();
    expect(primeCalls(fetchMock, "movie")).toHaveLength(1);

    // First poll: still queued → the button keeps spinning.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    await flush();
    expect(primeCalls(fetchMock, "movie")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Refreshing…" })).toBeDisabled();

    // Second poll: the worker settled → button back to idle.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    await flush();
    expect(primeCalls(fetchMock, "movie")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Refresh availability" })).toBeEnabled();
    expect(screen.getByText("Netflix")).toBeInTheDocument();
  });

  it("surfaces an enqueue failure and leaves the button ready to retry", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ body: preferencesBody }))
      .mockResolvedValueOnce(response({ body: loadedMovieBody }))
      .mockResolvedValueOnce(response({ body: unofficialBody }))
      .mockResolvedValueOnce(
        response({ ok: false, status: 500, body: { error: "refresh queue unavailable" } })
      );
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(MovieDetailPage, { params: { tmdb_id: "550" } }));
    await screen.findByRole("button", { name: "Refresh availability" });

    clickRefresh();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "refresh queue unavailable"
    );
    expect(screen.getByRole("button", { name: "Refresh availability" })).toBeEnabled();
    // No polling without a queued request.
    expect(primeCalls(fetchMock, "movie")).toHaveLength(1);
  });

  it("reports a failed worker refresh instead of spinning forever", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
    const failedBody = {
      ...loadedMovieBody,
      refresh_pending: false,
      refresh_status: "failed",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ body: preferencesBody }))
      .mockResolvedValueOnce(response({ body: loadedMovieBody }))
      .mockResolvedValueOnce(response({ body: unofficialBody }))
      .mockResolvedValueOnce(response({ body: refreshQueuedBody }))
      .mockResolvedValue(response({ body: failedBody }));
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(MovieDetailPage, { params: { tmdb_id: "550" } }));
    await flush();

    clickRefresh();
    await flush();
    expect(screen.getByRole("button", { name: "Refreshing…" })).toBeDisabled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    await flush();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "The refresh failed on the server"
    );
    expect(screen.getByRole("button", { name: "Refresh availability" })).toBeEnabled();
  });

  it("series page enqueues the same refresh endpoint", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ body: preferencesBody }))
      .mockResolvedValueOnce(response({ body: loadedSeriesBody }))
      .mockResolvedValueOnce(response({ body: unofficialBody }))
      .mockResolvedValueOnce(
        response({
          body: { ...refreshQueuedBody, tmdb_id: 1396, content_type: "series" },
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(SeriesDetailPage, { params: { tmdb_id: "1396" } }));
    await screen.findByRole("button", { name: "Refresh availability" });

    clickRefresh();

    expect(await screen.findByRole("button", { name: "Refreshing…" })).toBeDisabled();
    const posts = callsMatching(fetchMock, "/api/movie/1396/refresh/");
    expect(posts).toHaveLength(1);
    expect(posts[0][1]).toMatchObject({ method: "POST" });
    expect(screen.getByText("Netflix")).toBeInTheDocument();
  });
});
