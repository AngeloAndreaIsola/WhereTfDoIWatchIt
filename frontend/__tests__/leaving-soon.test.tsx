import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LeavingSoonSection } from "@/components/LeavingSoonSection";
import WatchlistPage from "@/app/watchlist/page";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import {
  groupLeavingOffers,
  formatDepartureDate,
} from "@/lib/leavingSoon";
import { leavingSoonCache, invalidateLeavingSoonCache } from "@/lib/navigationCache";
import type { LeavingSoonResponse, LeavingSoonItem } from "@/lib/api";

// Mock next/link
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

// Mock next/navigation
const navigation = vi.hoisted(() => ({
  params: new URLSearchParams(),
  router: { replace: vi.fn(), push: vi.fn(), back: vi.fn() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigation.router,
  useSearchParams: () => new URLSearchParams(navigation.params.toString()),
}));

// Mock CurrentLocationProvider
let mockCurrentCountry = "NL";
vi.mock("@/components/CurrentLocationProvider", () => ({
  useCurrentLocation: () => ({ currentCountry: mockCurrentCountry }),
}));

vi.mock("@/components/FilterBar", () => ({
  FilterBar: () => React.createElement("div", { "data-testid": "filter-bar" }, "FilterBar"),
}));

const AS_OF_UTC = "2026-11-22T10:00:00+00:00"; // Reference UTC timestamp

function makeDepartureItem(
  id: number,
  title: string,
  offers: Array<{
    provider: string;
    provider_display: string;
    available_until: string;
    departure_kind?: "leaving_provider" | "last_option";
    access_type?: string;
    season_number?: number | null;
    episode_number?: number | null;
    country?: string;
  }>
): LeavingSoonItem {
  return {
    movie: {
      tmdb_id: id,
      content_type: "movie",
      title,
      year: 2022,
      poster_path: `/p_${id}.jpg`,
    },
    leaving_offers: offers.map((o) => ({
      provider: o.provider,
      provider_display: o.provider_display,
      country: o.country || "NL",
      offer_type: o.access_type || "subscription",
      access_type: o.access_type || "subscription",
      locality: "local",
      season_number: o.season_number ?? null,
      episode_number: o.episode_number ?? null,
      available_until: o.available_until,
      last_verified_at: "2026-11-21T08:00:00+00:00",
      sources: ["source_a"],
      evidence: "verified",
      departure_kind: o.departure_kind || "leaving_provider",
    })),
  };
}

describe("Leaving Soon unit logic: grouping, boundaries and UTC preservation", () => {
  it("enforces exact disjoint 7, 14, 30 day boundaries", () => {
    const asOfMs = new Date(AS_OF_UTC).getTime();
    const MS_DAY = 86_400_000;

    const items: LeavingSoonItem[] = [
      // Day 1
      makeDepartureItem(1, "Day 1 Item", [
        { provider: "p1", provider_display: "P1", available_until: new Date(asOfMs + 1 * MS_DAY).toISOString() },
      ]),
      // Exact 7 days (boundary)
      makeDepartureItem(2, "Exact 7 Days Item", [
        { provider: "p2", provider_display: "P2", available_until: new Date(asOfMs + 7 * MS_DAY).toISOString() },
      ]),
      // 7 days + 1 millisecond -> goes to Days 8–14
      makeDepartureItem(3, "Day 7 plus 1ms Item", [
        { provider: "p3", provider_display: "P3", available_until: new Date(asOfMs + 7 * MS_DAY + 1).toISOString() },
      ]),
      // Exact 14 days (boundary) -> goes to Days 8–14
      makeDepartureItem(4, "Exact 14 Days Item", [
        { provider: "p4", provider_display: "P4", available_until: new Date(asOfMs + 14 * MS_DAY).toISOString() },
      ]),
      // 14 days + 1 millisecond -> goes to Days 15–30
      makeDepartureItem(5, "Day 14 plus 1ms Item", [
        { provider: "p5", provider_display: "P5", available_until: new Date(asOfMs + 14 * MS_DAY + 1).toISOString() },
      ]),
      // Exact 30 days (boundary) -> goes to Days 15–30
      makeDepartureItem(6, "Exact 30 Days Item", [
        { provider: "p6", provider_display: "P6", available_until: new Date(asOfMs + 30 * MS_DAY).toISOString() },
      ]),
      // 30 days + 1 millisecond -> outside horizon (excluded)
      makeDepartureItem(7, "Beyond 30 Days Item", [
        { provider: "p7", provider_display: "P7", available_until: new Date(asOfMs + 30 * MS_DAY + 1).toISOString() },
      ]),
      // Past / expired (diff <= 0) -> excluded
      makeDepartureItem(8, "Expired Item", [
        { provider: "p8", provider_display: "P8", available_until: new Date(asOfMs - 1000).toISOString() },
      ]),
    ];

    const grouped = groupLeavingOffers(items, AS_OF_UTC);

    expect(grouped.next_7_days.map((e) => e.movie.title)).toEqual([
      "Day 1 Item",
      "Exact 7 Days Item",
    ]);
    expect(grouped.days_8_14.map((e) => e.movie.title)).toEqual([
      "Day 7 plus 1ms Item",
      "Exact 14 Days Item",
    ]);
    expect(grouped.days_15_30.map((e) => e.movie.title)).toEqual([
      "Day 14 plus 1ms Item",
      "Exact 30 Days Item",
    ]);
    expect(grouped.totalDepartures).toBe(6);
  });

  it("sorts items ascending by available_until timestamp", () => {
    const asOfMs = new Date(AS_OF_UTC).getTime();
    const MS_DAY = 86_400_000;

    const items: LeavingSoonItem[] = [
      makeDepartureItem(1, "Later in 7d", [
        { provider: "p1", provider_display: "P1", available_until: new Date(asOfMs + 5 * MS_DAY).toISOString() },
      ]),
      makeDepartureItem(2, "Earlier in 7d", [
        { provider: "p2", provider_display: "P2", available_until: new Date(asOfMs + 2 * MS_DAY).toISOString() },
      ]),
    ];

    const grouped = groupLeavingOffers(items, AS_OF_UTC);
    expect(grouped.next_7_days[0].movie.title).toBe("Earlier in 7d");
    expect(grouped.next_7_days[1].movie.title).toBe("Later in 7d");
  });

  it("preserves UTC grouping invariants regardless of local timezone or DST shifts", () => {
    // 7 exact 24h increments in UTC remain 7 days in grouping
    const asOfMs = new Date("2026-03-25T12:00:00+00:00").getTime(); // Spring DST transition week
    const exact7Days = new Date(asOfMs + 7 * 86_400_000).toISOString();

    const items: LeavingSoonItem[] = [
      makeDepartureItem(10, "Spring DST Boundary", [
        { provider: "p1", provider_display: "P1", available_until: exact7Days },
      ]),
    ];

    const grouped = groupLeavingOffers(items, "2026-03-25T12:00:00+00:00");
    expect(grouped.next_7_days.length).toBe(1);
    expect(grouped.days_8_14.length).toBe(0);
  });
});

describe("LeavingSoonSection: seven distinct states and rendering", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    invalidateLeavingSoonCache();
    mockCurrentCountry = "NL";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function setupMock(response: LeavingSoonResponse | Error | { status: number }) {
    fetchMock = vi.fn(async (url: string) => {
      if (response instanceof Error) {
        throw response;
      }
      if ("status" in response && response.status >= 400) {
        return {
          ok: false,
          status: response.status,
          json: async () => ({ error: `HTTP ${response.status} Error` }),
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => response,
      };
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  // 1. Loading state
  it("renders state 1: loading indicator", () => {
    fetchMock = vi.fn(() => new Promise(() => {})); // Never resolves
    vi.stubGlobal("fetch", fetchMock);

    render(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="NL" />
      </LocaleProvider>
    );

    expect(screen.getByTestId("leaving-soon-loading")).toBeInTheDocument();
    expect(screen.getByText(/Loading confirmed departures/i)).toBeInTheDocument();
  });

  // 2. Confirmed departures state with multiple providers, last_option & AVOD
  it("renders state 2: confirmed departures across groups with last_option and AVOD tags", async () => {
    const asOfMs = new Date(AS_OF_UTC).getTime();
    const MS_DAY = 86_400_000;

    const fixture: LeavingSoonResponse = {
      country: "NL",
      horizon: { days: 30, as_of: AS_OF_UTC, until: new Date(asOfMs + 30 * MS_DAY).toISOString() },
      items: [
        makeDepartureItem(101, "Exclusive Drama", [
          {
            provider: "netflix",
            provider_display: "Netflix",
            available_until: new Date(asOfMs + 3 * MS_DAY).toISOString(),
            departure_kind: "last_option",
            access_type: "subscription",
          },
        ]),
        makeDepartureItem(102, "Free Comedy Series", [
          {
            provider: "tubi",
            provider_display: "Tubi",
            available_until: new Date(asOfMs + 10 * MS_DAY).toISOString(),
            departure_kind: "leaving_provider",
            access_type: "free_with_ads",
            season_number: 2,
          },
        ]),
        makeDepartureItem(103, "Classic Movie", [
          {
            provider: "amazon_prime",
            provider_display: "Prime Video",
            available_until: new Date(asOfMs + 20 * MS_DAY).toISOString(),
            departure_kind: "leaving_provider",
            access_type: "subscription",
          },
        ]),
      ],
      coverage: {
        status: "partial_confirmed_departures",
        watchlist_titles: 10,
        titles_with_confirmed_expiry_data: 3,
        offers_with_confirmed_expiry_data: 3,
        scope: "selected_country_watchlist_only",
        completeness: "partial_not_all_providers_report_expiry",
      },
    };

    setupMock(fixture);

    render(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="NL" />
      </LocaleProvider>
    );

    expect(await screen.findByTestId("leaving-soon-departures")).toBeInTheDocument();

    // Verify all 3 groups are displayed
    expect(screen.getByTestId("group-next-7-days")).toBeInTheDocument();
    expect(screen.getByTestId("group-days-8-14")).toBeInTheDocument();
    expect(screen.getByTestId("group-days-15-30")).toBeInTheDocument();

    // Verify last_option badge with non-color-only text & icon
    const lastOptionBadge = screen.getByTestId("last-option-badge");
    expect(lastOptionBadge).toHaveTextContent("Last accessible option in NL");
    expect(lastOptionBadge).toHaveTextContent("⚠️");

    // Verify AVOD / Free with ads chip
    const avodChips = screen.getAllByTestId("access-type-chip");
    expect(avodChips.some((chip) => chip.textContent?.includes("Free with ads"))).toBe(true);

    // Verify season indicator
    expect(screen.getByText("Season 2")).toBeInTheDocument();

    // Verify coverage disclaimer note is present
    expect(screen.getByTestId("leaving-soon-coverage-note")).toBeInTheDocument();
    expect(screen.getByText(/Only services reporting verified departure dates are shown/i)).toBeInTheDocument();
  });

  // 3. No confirmed expiry evidence (sparse data)
  it("renders state 3: no confirmed expiry evidence without looking like reassurance", async () => {
    const fixture: LeavingSoonResponse = {
      country: "NL",
      horizon: { days: 30, as_of: AS_OF_UTC, until: "2026-12-22T10:00:00+00:00" },
      items: [],
      coverage: {
        status: "no_confirmed_expiry_data",
        watchlist_titles: 12,
        titles_with_confirmed_expiry_data: 0,
        offers_with_confirmed_expiry_data: 0,
        scope: "selected_country_watchlist_only",
        completeness: "partial_not_all_providers_report_expiry",
      },
    };

    setupMock(fixture);

    render(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="NL" />
      </LocaleProvider>
    );

    expect(await screen.findByTestId("leaving-soon-no-evidence")).toBeInTheDocument();
    expect(screen.getByText("No confirmed departure data")).toBeInTheDocument();
    expect(screen.getByText(/Services in NL have not reported confirmed departure dates/i)).toBeInTheDocument();
    expect(screen.getByText(/Missing dates do not guarantee a title will remain available/i)).toBeInTheDocument();
  });

  // 4. Confirmed evidence exists but nothing in 30-day horizon
  it("renders state 4: confirmed evidence but nothing in 30-day horizon", async () => {
    const fixture: LeavingSoonResponse = {
      country: "NL",
      horizon: { days: 30, as_of: AS_OF_UTC, until: "2026-12-22T10:00:00+00:00" },
      items: [],
      coverage: {
        status: "no_departures_within_horizon",
        watchlist_titles: 8,
        titles_with_confirmed_expiry_data: 2,
        offers_with_confirmed_expiry_data: 3,
        scope: "selected_country_watchlist_only",
        completeness: "partial_not_all_providers_report_expiry",
      },
    };

    setupMock(fixture);

    render(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="NL" />
      </LocaleProvider>
    );

    expect(await screen.findByTestId("leaving-soon-none-in-horizon")).toBeInTheDocument();
    expect(screen.getByText("No departures in the next 30 days")).toBeInTheDocument();
    expect(screen.getByText(/Your watchlisted titles in NL have no confirmed departures/i)).toBeInTheDocument();
  });

  // 5. Empty watchlist state
  it("renders state 5: empty watchlist", async () => {
    const fixture: LeavingSoonResponse = {
      country: "NL",
      horizon: { days: 30, as_of: AS_OF_UTC, until: "2026-12-22T10:00:00+00:00" },
      items: [],
      coverage: {
        status: "no_confirmed_expiry_data",
        watchlist_titles: 0,
        titles_with_confirmed_expiry_data: 0,
        offers_with_confirmed_expiry_data: 0,
        scope: "selected_country_watchlist_only",
        completeness: "partial_not_all_providers_report_expiry",
      },
    };

    setupMock(fixture);

    render(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="NL" />
      </LocaleProvider>
    );

    expect(await screen.findByTestId("leaving-soon-empty-watchlist")).toBeInTheDocument();
    expect(screen.getByText("Watchlist is empty")).toBeInTheDocument();
  });

  // 6. API Error with retry
  it("renders state 6: API error with working retry button", async () => {
    setupMock({ status: 500 });

    render(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="NL" />
      </LocaleProvider>
    );

    expect(await screen.findByTestId("leaving-soon-error")).toBeInTheDocument();
    expect(screen.getByText(/Could not load departure dates/i)).toBeInTheDocument();

    // Now mock a successful retry
    const fixture: LeavingSoonResponse = {
      country: "NL",
      horizon: { days: 30, as_of: AS_OF_UTC, until: "2026-12-22T10:00:00+00:00" },
      items: [],
      coverage: {
        status: "no_departures_within_horizon",
        watchlist_titles: 5,
        titles_with_confirmed_expiry_data: 1,
        offers_with_confirmed_expiry_data: 1,
        scope: "selected_country_watchlist_only",
        completeness: "partial_not_all_providers_report_expiry",
      },
    };
    setupMock(fixture);

    const retryBtn = screen.getByRole("button", { name: "Retry" });
    await userEvent.click(retryBtn);

    expect(await screen.findByTestId("leaving-soon-none-in-horizon")).toBeInTheDocument();
  });

  // 7. Offline state with retry
  it("renders state 7: offline/unavailable data with retry", async () => {
    setupMock(new TypeError("Failed to fetch"));

    render(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="NL" />
      </LocaleProvider>
    );

    expect(await screen.findByTestId("leaving-soon-offline")).toBeInTheDocument();
    expect(screen.getByText(/Offline — departures unavailable/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("LeavingSoonSection: country isolation, stale requests, and mutation invalidation", () => {
  beforeEach(() => {
    invalidateLeavingSoonCache();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("aborts stale requests and ignores mismatched country responses", async () => {
    const controllers: AbortSignal[] = [];
    const mock = vi.fn((url: string, init?: RequestInit) => {
      if (init?.signal) controllers.push(init.signal);
      const country = new URL(url, "http://localhost").searchParams.get("country");
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({
          country,
          horizon: { days: 30, as_of: AS_OF_UTC, until: "2026-12-22T10:00:00+00:00" },
          items: [],
          coverage: {
            status: "no_departures_within_horizon",
            watchlist_titles: 1,
            titles_with_confirmed_expiry_data: 0,
            offers_with_confirmed_expiry_data: 0,
            scope: "selected_country_watchlist_only",
            completeness: "partial",
          },
        }),
      });
    });
    vi.stubGlobal("fetch", mock);

    const { rerender } = render(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="NL" />
      </LocaleProvider>
    );

    expect(await screen.findByText(/Leaving Soon — NL/i)).toBeInTheDocument();
    expect(controllers[0].aborted).toBe(false);

    // Switch country to IT
    rerender(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="IT" />
      </LocaleProvider>
    );

    expect(await screen.findByText(/Leaving Soon — IT/i)).toBeInTheDocument();
    expect(controllers[0].aborted).toBe(true);
  });

  it("refetches when refreshKey increments (refreshWatchlistData path)", async () => {
    const mock = vi.fn((url: string) => {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({
          country: "NL",
          horizon: { days: 30, as_of: AS_OF_UTC, until: "2026-12-22T10:00:00+00:00" },
          items: [],
          coverage: {
            status: "no_departures_within_horizon",
            watchlist_titles: 1,
            titles_with_confirmed_expiry_data: 0,
            offers_with_confirmed_expiry_data: 0,
            scope: "selected_country_watchlist_only",
            completeness: "partial",
          },
        }),
      });
    });
    vi.stubGlobal("fetch", mock);

    const { rerender } = render(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="NL" refreshKey={0} />
      </LocaleProvider>
    );

    expect(await screen.findByText(/Leaving Soon — NL/i)).toBeInTheDocument();
    const initialCalls = mock.mock.calls.length;
    expect(initialCalls).toBe(1);

    // Rerender with same refreshKey: should NOT refetch
    rerender(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="NL" refreshKey={0} />
      </LocaleProvider>
    );
    expect(mock.mock.calls.length).toBe(1);

    // Rerender with incremented refreshKey: should refetch
    await act(async () => {
      rerender(
        <LocaleProvider initialLocale="en">
          <LeavingSoonSection country="NL" refreshKey={1} />
        </LocaleProvider>
      );
    });
    expect(mock.mock.calls.length).toBe(2);
  });
});

describe("Leaving Soon i18n: EN, IT, JA translations", () => {
  const asOfMs = new Date(AS_OF_UTC).getTime();
  const fixture: LeavingSoonResponse = {
    country: "IT",
    horizon: { days: 30, as_of: AS_OF_UTC, until: "2026-12-22T10:00:00+00:00" },
    items: [
      makeDepartureItem(201, "Cinema Paradiso", [
        {
          provider: "raiplay",
          provider_display: "RaiPlay",
          available_until: new Date(asOfMs + 4 * 86_400_000).toISOString(),
          departure_kind: "last_option",
          access_type: "free",
          country: "IT",
        },
      ]),
    ],
    coverage: {
      status: "partial_confirmed_departures",
      watchlist_titles: 5,
      titles_with_confirmed_expiry_data: 1,
      offers_with_confirmed_expiry_data: 1,
      scope: "selected_country_watchlist_only",
      completeness: "partial",
    },
  };

  beforeEach(() => {
    invalidateLeavingSoonCache();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => fixture,
      }))
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders in Italian (it) without hard-coded English strings", async () => {
    render(
      <LocaleProvider initialLocale="it">
        <LeavingSoonSection country="IT" />
      </LocaleProvider>
    );

    expect(await screen.findByText(/In scadenza — IT/i)).toBeInTheDocument();
    expect(screen.getByText(/Prossimi 7 giorni/i)).toBeInTheDocument();
    expect(screen.getByText(/Ultima opzione disponibile in IT/i)).toBeInTheDocument();
    expect(screen.getByText(/Gratis/i)).toBeInTheDocument();
  });

  it("renders in Japanese (ja) without hard-coded English strings", async () => {
    render(
      <LocaleProvider initialLocale="ja">
        <LeavingSoonSection country="IT" />
      </LocaleProvider>
    );

    expect(await screen.findByText(/配信終了予定 — IT/i)).toBeInTheDocument();
    expect(screen.getByText(/今後7日間/i)).toBeInTheDocument();
    expect(screen.getByText(/ITで視聴可能な最後の配信/i)).toBeInTheDocument();
  });
});

describe("LeavingSoonSection: review-feedback regressions", () => {
  const asOfMs = new Date(AS_OF_UTC).getTime();
  const MS_DAY = 86_400_000;

  const makeResponse = (
    country: string,
    title: string,
    until: string = new Date(asOfMs + 3 * MS_DAY).toISOString()
  ): LeavingSoonResponse => ({
    country,
    horizon: {
      days: 30,
      as_of: AS_OF_UTC,
      until: new Date(asOfMs + 30 * MS_DAY).toISOString(),
    },
    items: [makeDepartureItem(900, title, [{ provider: "p", provider_display: "Provider", available_until: until }])],
    coverage: {
      status: "partial_confirmed_departures",
      watchlist_titles: 1,
      titles_with_confirmed_expiry_data: 1,
      offers_with_confirmed_expiry_data: 1,
      scope: "selected_country_watchlist_only",
      completeness: "partial_not_all_providers_report_expiry",
    },
  });

  beforeEach(() => {
    invalidateLeavingSoonCache();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("restores a typed cached response on unmount/remount without fetching", async () => {
    const fixture = makeResponse("NL", "Cached Title");
    leavingSoonCache.set({
      key: "NL:30",
      items: [fixture],
      page: 1,
      paginationInfo: { totalPages: 1, hasNext: false, hasPrevious: false },
      filters: null,
      scrollY: 0,
      cachedAt: Date.now(),
    });
    const fetchSpy = vi.fn(() => {
      throw new Error("cached mount must not fetch");
    });
    vi.stubGlobal("fetch", fetchSpy);

    const first = render(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="NL" />
      </LocaleProvider>
    );
    expect(await screen.findByText(/Cached Title/)).toBeInTheDocument();
    expect(screen.getAllByTestId("leaving-soon-card")).toHaveLength(1);
    first.unmount();

    render(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="NL" />
      </LocaleProvider>
    );
    expect(await screen.findByText(/Cached Title/)).toBeInTheDocument();
    expect(screen.getAllByTestId("leaving-soon-card")).toHaveLength(1);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("ignores a pending request when the country switches to a cached country", async () => {
    let resolveNl: ((value: unknown) => void) | undefined;
    const nlPending = new Promise((resolve) => {
      resolveNl = resolve;
    });
    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).includes("country=NL")) return nlPending;
      throw new Error(`unexpected fetch: ${String(url)}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    leavingSoonCache.set({
      key: "IT:30",
      items: [makeResponse("IT", "Italian Snapshot")],
      page: 1,
      paginationInfo: { totalPages: 1, hasNext: false, hasPrevious: false },
      filters: null,
      scrollY: 0,
      cachedAt: Date.now(),
    });

    const view = render(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="NL" />
      </LocaleProvider>
    );
    expect(screen.getByTestId("leaving-soon-loading")).toBeInTheDocument();

    view.rerender(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="IT" />
      </LocaleProvider>
    );
    expect(await screen.findByText(/Italian Snapshot/)).toBeInTheDocument();

    await act(async () => {
      resolveNl?.({
        ok: true,
        status: 200,
        json: async () => makeResponse("NL", "Stale Dutch Response"),
      });
      await nlPending;
    });

    expect(screen.queryByText("Stale Dutch Response")).not.toBeInTheDocument();
    expect(screen.getByText(/Italian Snapshot/)).toBeInTheDocument();
  });

  it("shows error/retry for malformed successful responses", async () => {
    const malformed = [
      { country: "NL", items: [], horizon: { days: 30 } },
      { country: "NL", horizon: { days: 30, as_of: AS_OF_UTC, until: "2026-12-22T10:00:00Z" }, coverage: {} },
      { ...makeResponse("NL", "Broken"), items: [{ movie: {}, leaving_offers: [] }] },
    ];
    for (const response of malformed) {
      invalidateLeavingSoonCache();
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => ({ ok: true, status: 200, json: async () => response }))
      );
      const view = render(
        <LocaleProvider initialLocale="en">
          <LeavingSoonSection country="NL" />
        </LocaleProvider>
      );
      expect(await screen.findByTestId("leaving-soon-error")).toBeInTheDocument();
      expect(screen.queryByTestId("leaving-soon-departures")).not.toBeInTheDocument();
      view.unmount();
    }
  });

  it("renders localized date and time with timezone, including a DST-boundary expiry", async () => {
    // Europe/Rome switches to CEST on 2026-03-29; the exact UTC instant must
    // still be rendered with a locale-aware time, not just a calendar date.
    const dstExpiry = "2026-03-29T01:30:00+00:00";
    const dstFixture = {
      ...makeResponse("NL", "DST Title", dstExpiry),
      horizon: {
        days: 30,
        as_of: "2026-03-28T10:00:00+00:00",
        until: "2026-04-27T10:00:00+00:00",
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => dstFixture,
      }))
    );

    const view = render(
      <LocaleProvider initialLocale="en">
        <LeavingSoonSection country="NL" />
      </LocaleProvider>
    );
    expect(await screen.findByText(/DST Title/)).toBeInTheDocument();
    const time = screen.getByTestId("leaving-soon-card").querySelector("time");
    expect(time?.getAttribute("datetime")).toBe(dstExpiry);
    expect(time?.textContent).toMatch(/\d{1,2}:\d{2}/);
    expect(screen.getAllByText(/Times shown in/i).length).toBeGreaterThan(0);
    view.unmount();

    invalidateLeavingSoonCache();
    render(
      <LocaleProvider initialLocale="it">
        <LeavingSoonSection country="NL" />
      </LocaleProvider>
    );
    await waitFor(() =>
      expect(screen.getAllByText(/Orari indicati in/i).length).toBeGreaterThan(0)
    );
  });
});
