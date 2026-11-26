import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import MovieDetailPage from "@/app/movie/[tmdb_id]/page";
import SeriesDetailPage from "@/app/tv/[tmdb_id]/page";
import { CurrentLocationProvider } from "@/components/CurrentLocationProvider";

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
  access_type: "subscription",
  locality: "local",
  is_entitled: true,
  is_accessible: true,
  entitlement: {
    verdict: "INCLUDED",
    reason: "PLAN_COVERS_TITLE",
    provider: "netflix",
    offer_type: "subscription",
    offer_country: "US",
    evidence_state: "verified",
    caveats: [],
  },
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

describe("Reload reconstruction (P9)", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    nav.push.mockReset();
    nav.replace.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /**
   * A browser reload destroys every piece of React state. Whatever the page
   * shows must therefore be derivable from the backend response alone —
   * `refresh_pending` and `refresh_status` — not from an in-memory flag that a
   * reload would silently clear.
   *
   * Each test mounts the page fresh, which is exactly what a reload does.
   */
  it("shows a queued refresh after a reload without any prior client state", async () => {
    // Routed by URL rather than by call order: the page fetches preferences,
    // the prime offers and the unofficial list, and the order is not the
    // subject of this test.
    const fetchMock = vi.fn((url: string) => {
      if (url.includes("/api/preferences/")) return Promise.resolve(response({ body: preferencesBody }));
      if (url.includes("/api/v2/unofficial/")) return Promise.resolve(response({ body: { movie: {}, offers: [] } }));
      if (url.includes("/api/prime/movie/")) {
        // First paint already says "pending" — as it would after a reload
        // during an active refresh.
        return Promise.resolve(response({ body: pendingMovieBody }));
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(MovieDetailPage, { params: { tmdb_id: "550" } }));

    // Reconstructed purely from the backend's refresh_pending flag: a fresh
    // mount that reads pending=true must show the checking affordance without
    // any client-side memory of a click.
    expect(
      await screen.findByText("Checking availability…"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Not currently available on your subscribed streaming services."),
    ).not.toBeInTheDocument();
  });

  it("settles to resolved after a reload without an in-flight marker", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url.includes("/api/preferences/")) return Promise.resolve(response({ body: preferencesBody }));
      if (url.includes("/api/v2/unofficial/")) return Promise.resolve(response({ body: { movie: {}, offers: [] } }));
      if (url.includes("/api/prime/movie/")) {
        return Promise.resolve(response({ body: resolvedMovieBody }));
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(MovieDetailPage, { params: { tmdb_id: "550" } }));

    // Offers exist and refresh_pending is false: nothing is pending, so no
    // "checking" affordance may be shown. getAllByText because the ranked card
    // and the unofficial section both render offer rows.
    await waitFor(() =>
      expect(screen.getAllByText("Best Match").length).toBeGreaterThan(0),
    );
    expect(screen.queryByText("Checking availability…")).not.toBeInTheDocument();
  });

  it.each(["movie", "series"] as const)("does not crown an older %s offer with boolean-only access", async (kind) => {
    const legacy = { ...offer("Netflix"), entitlement: undefined, access_label: "Included with your subscription" };
    const body = kind === "movie" ? resolvedMovieBody : pendingSeriesBody;
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url.includes("/api/preferences/")) return Promise.resolve(response({ body: preferencesBody }));
      if (url.includes("/api/v2/unofficial/")) return Promise.resolve(response({ body: unofficialBody }));
      if (url.includes(`/api/prime/${kind}/`)) {
        return Promise.resolve(response({ body: { ...body, offers: [legacy], refresh_pending: false } }));
      }
      throw new Error(`unexpected fetch: ${url}`);
    }));

    render(kind === "movie"
      ? React.createElement(MovieDetailPage, { params: { tmdb_id: "550" } })
      : React.createElement(SeriesDetailPage, { params: { tmdb_id: "1396" } }));

    expect(await screen.findByText("Plan access unconfirmed")).toBeInTheDocument();
    expect(screen.queryByText("Best Match")).not.toBeInTheDocument();
    expect(screen.queryByText("Included with your subscription")).not.toBeInTheDocument();
  });

  it.each(["movie", "series"] as const)("shows aged %s source reports without claiming access or playback", async (kind) => {
    const reported = {
      ...offer("Netflix"),
      country: "NL",
      availability_evidence: "source_reported",
      reporting_source: "justwatch",
      observed_at: "2026-11-18T10:00:00Z",
      last_checked_at: "2026-11-18T10:00:30Z",
      is_entitled: null,
      is_accessible: null,
      playback_verified: false,
    };
    const fetchMock = vi.fn((url: string) => {
      if (url.includes("/api/preferences/")) return Promise.resolve(response({ body: preferencesBody }));
      if (url.includes("/api/v2/unofficial/")) return Promise.resolve(response({ body: unofficialBody }));
      if (url.includes("/api/prime/")) {
        const detail = kind === "movie" ? pendingMovieBody : pendingSeriesBody;
        return Promise.resolve(response({
          body: { ...detail, refresh_pending: false, source_reported_offers: [reported] },
        }));
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(kind === "movie"
      ? <MovieDetailPage params={{ tmdb_id: "550" }} />
      : <SeriesDetailPage params={{ tmdb_id: "1396" }} />);
    expect(await screen.findByText("Source-reported · JustWatch")).toBeInTheDocument();
    expect(screen.getByText(/^Last checked/)).toHaveAttribute("datetime", reported.last_checked_at);
    expect(screen.getByText(/^Observed/)).toHaveAttribute("datetime", reported.observed_at);
    expect(screen.getByText("Your subscription access and playback are unverified.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Check provider" })).toHaveAttribute("href", reported.url);
    expect(screen.queryByText("Best Match")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Watch$/ })).not.toBeInTheDocument();
  });

  it("posts the active market and clears the spinner for a recent check", async () => {
    const refreshUrl = "/api/movie/550/refresh/?content_type=movie&current_country=NL";
    const fetchMock = vi.fn((url: string) => {
      if (url === refreshUrl) {
        return Promise.resolve(response({ body: { refresh_pending: false, refresh_status: null } }));
      }
      if (url.includes("/api/preferences/")) {
        return Promise.resolve(response({ body: { ...preferencesBody, current_country: "NL" } }));
      }
      if (url.includes("/api/v2/unofficial/")) return Promise.resolve(response({ body: unofficialBody }));
      if (url.includes("/api/prime/movie/")) return Promise.resolve(response({ body: resolvedMovieBody }));
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <CurrentLocationProvider>
        <MovieDetailPage params={{ tmdb_id: "550" }} />
      </CurrentLocationProvider>,
    );
    await waitFor(() => expect(primeCalls(fetchMock, "movie").some(([url]) => url.includes("NL"))).toBe(true));
    fireEvent.click(await screen.findByRole("button", { name: "Refresh availability" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(refreshUrl, expect.objectContaining({ method: "POST" })));
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh availability" })).toBeEnabled());
  });

  it("does not claim unavailable for a pending title after a reload", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url.includes("/api/preferences/")) return Promise.resolve(response({ body: preferencesBody }));
      if (url.includes("/api/v2/unofficial/")) return Promise.resolve(response({ body: { movie: {}, offers: [] } }));
      if (url.includes("/api/prime/movie/")) {
        return Promise.resolve(response({ body: pendingMovieBody }));
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(MovieDetailPage, { params: { tmdb_id: "550" } }));

    await flush();
    expect(
      screen.queryByText(
        "Not currently available on your subscribed streaming services.",
      ),
    ).not.toBeInTheDocument();
  });

  it("reads a server-side failure from refresh_status", async () => {
    const failedBody = { ...pendingMovieBody, refresh_pending: false, refresh_status: "failed" };
    const fetchMock = vi.fn((url: string) => {
      if (url.includes("/api/preferences/")) return Promise.resolve(response({ body: preferencesBody }));
      if (url.includes("/api/v2/unofficial/")) return Promise.resolve(response({ body: { movie: {}, offers: [] } }));
      if (url.includes("/api/prime/movie/")) {
        return Promise.resolve(response({ body: failedBody }));
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(React.createElement(MovieDetailPage, { params: { tmdb_id: "550" } }));

    // The failure is a backend fact surfaced after the fact; the page must not
    // have needed client state to know it, and must not show a pending spinner.
    await flush();
    expect(screen.queryByText("Checking availability…")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Not currently available on your subscribed streaming services."),
    ).not.toBeInTheDocument();
  });
});


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

    // P9: an empty offer list with no verdict is UNKNOWN, not "unavailable".
    // The fixture has no `availability` field, so the conservative default
    // applies — and it must not claim the title was checked and found absent.
    expect(
      screen.queryByText("Not currently available on your subscribed streaming services.")
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("availability-state")).toHaveAttribute(
      "data-state",
      "unknown",
    );
    expect(screen.getByText(/not known yet/i)).toBeInTheDocument();
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

    // Nothing queued and no verdict: still unknown rather than unavailable.
    await waitFor(() =>
      expect(screen.getByTestId("availability-state")).toHaveAttribute(
        "data-state",
        "unknown",
      ),
    );
    expect(
      screen.queryByText("Not currently available on your subscribed streaming services.")
    ).not.toBeInTheDocument();
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
