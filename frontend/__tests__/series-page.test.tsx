import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SeriesDetailPage from "@/app/tv/[tmdb_id]/page";
import WatchPage from "@/app/watch/[tmdb_id]/page";
import MovieCard from "@/components/MovieCard";
import { fetchPrimeSeriesOffers } from "@/lib/api";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode; [key: string]: unknown }) =>
    React.createElement("a", { href, ...props }, children),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
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

function mockFetch(responses: MockedResponse[]) {
  const fetchMock = vi.fn();
  for (const response of responses) {
    fetchMock.mockResolvedValueOnce({
      ok: response.ok ?? true,
      status: response.status ?? 200,
      json: async () => response.body,
    });
  }
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const seriesBody = {
  series: {
    id: 7,
    tmdb_id: 1396,
    title: "Breaking Bad",
    year: 2008,
    poster_path: "/bb.jpg",
    tmdb_rating: 9.5,
    overview: "A chemistry teacher turns to crime.",
    genres: [{ id: 18, name: "Drama" }],
    cast: [{ id: 1, name: "Bryan Cranston", character: "Walter White" }],
    director: ["Vince Gilligan"],
    content_type: "series",
  },
  user_data: null,
  is_in_watchlist: false,
  watch_events: [],
  offers: [
    {
      country: "US",
      service: "Prime Video",
      offer_type: "subscription",
      quality: "4k",
      url: "https://www.primevideo.com/detail/1396",
      audio_languages: ["EN"],
      subtitle_languages: ["EN"],
      ranking: { audio_rank: 0, subtitle_rank: 0, quality_rank: 0, is_language_match: true },
      access_type: "subscription",
      locality: "local",
      is_entitled: true,
      is_accessible: true,
    },
  ],
};

const preferencesBody = { audio_languages: ["EN"], subtitle_languages: ["EN"] };

describe("TV series detail flow", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the series detail page with offers and series metadata", async () => {
    mockFetch([
      { body: preferencesBody },
      { body: seriesBody },
      { body: { offers: [] } },
    ]);

    render(React.createElement(SeriesDetailPage, { params: { tmdb_id: "1396" } }));

    expect(
      await screen.findByRole("heading", { name: /Breaking Bad/ })
    ).toBeInTheDocument();
    expect(screen.getByText("TV Series")).toBeInTheDocument();
    expect(screen.getByText("Created by")).toBeInTheDocument();
    expect(screen.getByText("Vince Gilligan")).toBeInTheDocument();
    expect(screen.getByText("9.5")).toBeInTheDocument();
    expect(screen.getAllByText("Best Match").length).toBeGreaterThan(0);
    expect(screen.getByText("Prime Video")).toBeInTheDocument();
  });

  it("requests the series Prime endpoint with the current country", async () => {
    const fetchMock = mockFetch([
      { body: preferencesBody },
      { body: seriesBody },
      { body: { offers: [] } },
    ]);

    render(React.createElement(SeriesDetailPage, { params: { tmdb_id: "1396" } }));
    await screen.findByRole("heading", { name: /Breaking Bad/ });

    expect(fetchMock).toHaveBeenCalledWith("/api/prime/series/1396/");
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/api/prime/movie/1396/")
    );
  });

  it("adds the series to the watchlist as a series", async () => {
    const fetchMock = mockFetch([
      { body: preferencesBody },
      { body: seriesBody },
      { body: { offers: [] } },
      { body: { message: "Added" } },
      { body: { ...seriesBody, is_in_watchlist: true } },
    ]);

    const user = userEvent.setup();
    render(React.createElement(SeriesDetailPage, { params: { tmdb_id: "1396" } }));

    await user.click(await screen.findByRole("button", { name: /Add to Watchlist/ }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/watchlist/add/",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ tmdb_id: 1396, content_type: "series" }),
        })
      );
    });
  });

  it("surfaces a series resolution error from the Prime series endpoint", async () => {
    mockFetch([
      { body: preferencesBody },
      { ok: false, status: 404, body: { offers: [], message: "Series not tracked and could not be resolved via TMDb." } },
      { body: { offers: [] } },
    ]);

    render(React.createElement(SeriesDetailPage, { params: { tmdb_id: "99999" } }));

    expect(
      await screen.findByText("Series not tracked and could not be resolved via TMDb.")
    ).toBeInTheDocument();
  });

  it("fetchPrimeSeriesOffers throws the endpoint message on failure", async () => {
    mockFetch([
      { ok: false, status: 404, body: { message: "Series not tracked and could not be resolved via TMDb." } },
    ]);

    await expect(fetchPrimeSeriesOffers(99999)).rejects.toThrow(
      "Series not tracked and could not be resolved via TMDb."
    );
  });

  it("fetchPrimeSeriesOffers forwards the current country", async () => {
    const fetchMock = mockFetch([{ body: seriesBody }]);

    await fetchPrimeSeriesOffers(1396, "JP");

    expect(fetchMock).toHaveBeenCalledWith("/api/prime/series/1396/?current_country=JP");
  });
});

describe("Series routing", () => {
  it("sends series cards to /tv/ and movie cards to /movie/", () => {
    const { container: seriesContainer } = render(
      React.createElement(MovieCard, {
        movie: { tmdb_id: 1396, title: "Breaking Bad", year: 2008, content_type: "series" },
      })
    );
    expect(seriesContainer.querySelector("a")).toHaveAttribute("href", "/tv/1396");

    const { container: movieContainer } = render(
      React.createElement(MovieCard, {
        movie: { tmdb_id: 603, title: "The Matrix", year: 1999, content_type: "movie" },
      })
    );
    expect(movieContainer.querySelector("a")).toHaveAttribute("href", "/movie/603");
  });

  it("returns to the series detail page when a series watch route has no offer", async () => {
    mockFetch([
      {
        body: {
          found: false,
          movie: { tmdb_id: 1396, title: "Breaking Bad", year: 2008, content_type: "series" },
          message: "Not currently available to watch on your subscribed services.",
        },
      },
    ]);

    render(React.createElement(WatchPage, { params: { tmdb_id: "1396" } }));

    const link = await screen.findByRole("link", { name: "← Return to Series Details" });
    expect(link).toHaveAttribute("href", "/tv/1396");
  });

  it("keeps returning movies to the movie detail page", async () => {
    mockFetch([
      {
        body: {
          found: false,
          movie: { tmdb_id: 1001, title: "Unavailable Movie", year: 2021, content_type: "movie" },
          message: "Not currently available to watch on your subscribed services.",
        },
      },
    ]);

    render(React.createElement(WatchPage, { params: { tmdb_id: "1001" } }));

    const link = await screen.findByRole("link", { name: "← Return to Movie Details" });
    expect(link).toHaveAttribute("href", "/movie/1001");
  });
});
