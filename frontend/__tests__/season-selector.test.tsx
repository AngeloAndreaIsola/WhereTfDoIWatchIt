import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SeriesDetailPage from "@/app/tv/[tmdb_id]/page";
import { fetchPrimeSeriesOffers } from "@/lib/api";

const nav = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  season: null as string | null,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: nav.replace, back: vi.fn() }),
  useSearchParams: () => ({
    get: (key: string) => (key === "season" ? nav.season : null),
  }),
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

function mockFetch(responses: MockedResponse[]) {
  const fetchMock = vi.fn();
  for (const item of responses) {
    fetchMock.mockResolvedValueOnce(response(item));
  }
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const seasons = [
  { season: 1, name: "Season 1", episode_count: 10, poster_path: "/s1.jpg" },
  { season: 2, name: "Season 2", episode_count: 8, poster_path: "/s2.jpg" },
  { season: 3, name: "Season 3", episode_count: 6, poster_path: "/s3.jpg" },
];

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

const seriesBody = {
  series: {
    id: 7,
    tmdb_id: 1396,
    title: "Breaking Bad",
    year: 2008,
    poster_path: "/bb.jpg",
    tmdb_rating: 9.5,
    overview: "A chemistry teacher turns to crime.",
    genres: [],
    cast: [],
    director: [],
    content_type: "series",
  },
  user_data: null,
  is_in_watchlist: false,
  watch_events: [],
  offers: [offer("Prime Video")],
};

const seriesBodyWithSeasons = { ...seriesBody, seasons, selected_season: null };
const preferencesBody = { audio_languages: ["EN"], subtitle_languages: ["EN"] };
const unofficialBody = { offers: [] };

/** preferences → series offers → scraper offers (mount order). */
const baseResponses = [
  { body: preferencesBody },
  { body: seriesBodyWithSeasons },
  { body: unofficialBody },
];

describe("Series season selector", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    nav.push.mockReset();
    nav.replace.mockReset();
    nav.season = null;
  });

  it("defaults to all seasons and keeps the unfiltered request", async () => {
    const fetchMock = mockFetch(baseResponses);

    render(React.createElement(SeriesDetailPage, { params: { tmdb_id: "1396" } }));

    const select = (await screen.findByRole("combobox", { name: "Season" })) as HTMLSelectElement;
    expect(select).toHaveValue("");
    expect(screen.getByRole("option", { name: "All seasons" })).toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(4);
    expect(fetchMock).toHaveBeenCalledWith("/api/prime/series/1396/");
    expect(screen.getByText("Prime Video")).toBeInTheDocument();
  });

  it("fetches only the chosen season and updates the URL", async () => {
    const fetchMock = mockFetch([
      ...baseResponses,
      { body: { ...seriesBodyWithSeasons, offers: [offer("Netflix")], selected_season: 2 } },
    ]);
    const user = userEvent.setup();

    render(React.createElement(SeriesDetailPage, { params: { tmdb_id: "1396" } }));
    const select = (await screen.findByRole("combobox", { name: "Season" })) as HTMLSelectElement;

    await user.selectOptions(select, "2");

    expect(await screen.findByText("Netflix")).toBeInTheDocument();
    expect(select).toHaveValue("2");
    expect(fetchMock).toHaveBeenCalledWith("/api/prime/series/1396/?season=2");
    expect(nav.replace).toHaveBeenCalledWith("/tv/1396?season=2");
    // Preferences, initial offers and scraper offers still happened once each.
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("shows an inline loading line while the season is fetched", async () => {
    const fetchMock = vi.fn();
    for (const item of baseResponses) {
      fetchMock.mockResolvedValueOnce(response(item));
    }
    let finishSeason!: (value: unknown) => void;
    fetchMock.mockReturnValueOnce(
      new Promise((resolve) => {
        finishSeason = resolve;
      })
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(React.createElement(SeriesDetailPage, { params: { tmdb_id: "1396" } }));
    const select = (await screen.findByRole("combobox", { name: "Season" })) as HTMLSelectElement;

    await user.selectOptions(select, "3");

    expect(
      await screen.findByText("Updating season availability…")
    ).toBeInTheDocument();

    finishSeason(
      response({
        body: {
          ...seriesBodyWithSeasons,
          offers: [offer("Max")],
          selected_season: 3,
        },
      })
    );

    expect(await screen.findByText("Max")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText("Updating season availability…")).not.toBeInTheDocument()
    );
  });

  it("honours a season deep link on first load", async () => {
    nav.season = "2";
    const fetchMock = mockFetch([
      { body: preferencesBody },
      {
        body: {
          ...seriesBodyWithSeasons,
          offers: [offer("Netflix")],
          selected_season: 2,
        },
      },
      { body: unofficialBody },
    ]);

    render(React.createElement(SeriesDetailPage, { params: { tmdb_id: "1396" } }));

    const select = (await screen.findByRole("combobox", { name: "Season" })) as HTMLSelectElement;
    expect(select).toHaveValue("2");
    expect(fetchMock).toHaveBeenCalledWith("/api/prime/series/1396/?season=2");
    expect(fetchMock).not.toHaveBeenCalledWith("/api/prime/series/1396/");
    expect(await screen.findByText("Netflix")).toBeInTheDocument();
  });

  it("reuses the empty state when the chosen season has no offers", async () => {
    mockFetch([
      ...baseResponses,
      { body: { ...seriesBodyWithSeasons, offers: [], selected_season: 3 } },
    ]);
    const user = userEvent.setup();

    render(React.createElement(SeriesDetailPage, { params: { tmdb_id: "1396" } }));
    const select = (await screen.findByRole("combobox", { name: "Season" })) as HTMLSelectElement;
    expect(screen.getByText("Prime Video")).toBeInTheDocument();

    await user.selectOptions(select, "3");

    // The offer list is genuinely empty for this season. P9: with no verdict
    // the page must say the season's availability is unknown, not unavailable.
    await waitFor(() =>
      expect(screen.getByTestId("availability-state")).toHaveAttribute(
        "data-state",
        "unknown",
      ),
    );
    expect(screen.queryByText("Prime Video")).not.toBeInTheDocument();
  });

  it("auto-selects and labels a lone season", async () => {
    const fetchMock = mockFetch([
      { body: preferencesBody },
      { body: { ...seriesBodyWithSeasons, seasons: [seasons[0]], selected_season: null } },
      { body: unofficialBody },
      { body: { ...seriesBodyWithSeasons, seasons: [seasons[0]], selected_season: 1 } },
    ]);

    render(React.createElement(SeriesDetailPage, { params: { tmdb_id: "1396" } }));

    expect(await screen.findByText("Season 1")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/prime/series/1396/?season=1")
    );
    // Still show-level first, then narrowed to the only season there is.
    expect(fetchMock.mock.calls[1][0]).toBe("/api/prime/series/1396/");
  });

  it("clears a season the series does not have", async () => {
    nav.season = "99";
    const fetchMock = mockFetch([
      { body: preferencesBody },
      { body: { ...seriesBodyWithSeasons, offers: [], selected_season: 99 } },
      { body: unofficialBody },
      { body: seriesBodyWithSeasons },
    ]);

    render(React.createElement(SeriesDetailPage, { params: { tmdb_id: "1396" } }));

    const select = (await screen.findByRole("combobox", { name: "Season" })) as HTMLSelectElement;
    expect(select).toHaveValue("");
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/prime/series/1396/")
    );
  });
});

describe("fetchPrimeSeriesOffers season parameter", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("composes the country and season query parameters", async () => {
    const fetchMock = mockFetch([{ body: seriesBodyWithSeasons }]);

    await fetchPrimeSeriesOffers(1396, "JP", 2);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/prime/series/1396/?current_country=JP&season=2"
    );
  });

  it("omits both parameters when neither is given", async () => {
    const fetchMock = mockFetch([{ body: seriesBodyWithSeasons }]);

    await fetchPrimeSeriesOffers(1396, undefined, null);

    expect(fetchMock).toHaveBeenCalledWith("/api/prime/series/1396/");
  });
});
