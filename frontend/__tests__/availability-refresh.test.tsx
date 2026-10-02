import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
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

/** Never fetched: no offers, worker still holds the queued request. */
const pendingMovieBody = {
  movie: { id: 1, tmdb_id: 550, title: "Fight Club", year: 1999, content_type: "movie" },
  user_data: null,
  is_in_watchlist: false,
  watch_events: [],
  offers: [],
  refresh_pending: true,
};

/** Worker drained the request and wrote canonical offers. */
const resolvedMovieBody = {
  ...pendingMovieBody,
  offers: [offer("Netflix")],
  refresh_pending: false,
};

const pendingSeriesBody = {
  series: { id: 2, tmdb_id: 1396, title: "Breaking Bad", year: 2008, content_type: "series" },
  user_data: null,
  is_in_watchlist: false,
  watch_events: [],
  offers: [],
  seasons: [],
  selected_season: null,
  refresh_pending: true,
};

/** Flush pending promise chains without relying on the fake clock. */
async function flush() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {});
  }
}

function primeCalls(fetchMock: ReturnType<typeof vi.fn>, kind: "movie" | "series") {
  return fetchMock.mock.calls.filter(
    (call) => typeof call[0] === "string" && call[0].includes(`/api/prime/${kind}/`)
  );
}

describe("Availability refresh pending flow", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    nav.push.mockReset();
    nav.replace.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("shows a checking state and polls until the worker delivers offers", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ body: preferencesBody }))
      .mockResolvedValueOnce(response({ body: pendingMovieBody }))
      .mockResolvedValueOnce(response({ body: unofficialBody }))
      .mockResolvedValue(response({ body: pendingMovieBody }));
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(MovieDetailPage, { params: { tmdb_id: "550" } }));
    await flush();

    expect(screen.getByText("Checking availability…")).toBeInTheDocument();
    expect(screen.queryByText(/Not currently available/)).not.toBeInTheDocument();
    expect(primeCalls(fetchMock, "movie")).toHaveLength(1);

    // The worker drained the queue: the next poll gets canonical offers.
    fetchMock.mockResolvedValue(response({ body: resolvedMovieBody }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    await flush();

    expect(screen.getByText("Netflix")).toBeInTheDocument();
    expect(screen.queryByText("Checking availability…")).not.toBeInTheDocument();
    expect(primeCalls(fetchMock, "movie")).toHaveLength(2);
  });

  it("falls back to the empty state with a queued note after the poll timeout", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ body: preferencesBody }))
      .mockResolvedValueOnce(response({ body: pendingMovieBody }))
      .mockResolvedValueOnce(response({ body: unofficialBody }))
      .mockResolvedValue(response({ body: pendingMovieBody }));
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(MovieDetailPage, { params: { tmdb_id: "550" } }));
    await flush();
    expect(screen.getByText("Checking availability…")).toBeInTheDocument();

    // Still nothing after the polling budget — but the request is queued.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(61000);
    });
    await flush();

    expect(
      screen.getByText("Not currently available on your subscribed streaming services.")
    ).toBeInTheDocument();
    expect(screen.getByText(/still queued/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check again" })).toBeInTheDocument();
    expect(screen.queryByText("Checking availability…")).not.toBeInTheDocument();
  });

  it("shows the plain empty state when nothing is queued", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ body: preferencesBody }))
      .mockResolvedValueOnce(response({ body: { ...pendingMovieBody, refresh_pending: false } }))
      .mockResolvedValueOnce(response({ body: unofficialBody }));
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(MovieDetailPage, { params: { tmdb_id: "550" } }));

    expect(
      await screen.findByText("Not currently available on your subscribed streaming services.")
    ).toBeInTheDocument();
    expect(screen.queryByText(/still queued/)).not.toBeInTheDocument();
    expect(screen.queryByText("Checking availability…")).not.toBeInTheDocument();
  });

  it("series page shows the checking state while a refresh is queued", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ body: preferencesBody }))
      .mockResolvedValueOnce(response({ body: pendingSeriesBody }))
      .mockResolvedValueOnce(response({ body: unofficialBody }));
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(SeriesDetailPage, { params: { tmdb_id: "1396" } }));

    expect(await screen.findByText("Checking availability…")).toBeInTheDocument();
    expect(screen.queryByText(/Not currently available/)).not.toBeInTheDocument();
  });
});
