import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import AvailabilityMetricsPage, {
  AvailabilityMetricsPayload,
} from "../app/availability-metrics/page";

/**
 * The behavioural contract this page has to keep.
 *
 * Almost every assertion below is about *not lying*: a metric the API could
 * not measure must not be rendered as a zero. `null` means "no data", and a
 * screen that turns that into "0%" or "0 offers, 0 successes" would tell an
 * operator something false — most damagingly, that a working source is broken.
 */
function payload(overrides: Partial<AvailabilityMetricsPayload> = {}): AvailabilityMetricsPayload {
  return {
    window: {
      start: "2026-09-28T00:00:00Z",
      end: "2026-10-05T00:00:00Z",
      days: 7,
      timezone: "UTC",
      definition: "now minus 7x24h through now, inclusive",
    },
    refreshes: {
      total: 10,
      completed: 7,
      partial: 2,
      failed: 1,
      active: 0,
      terminal: 10,
      completion_rate: 0.7,
      partial_rate: 0.2,
      failure_rate: 0.1,
      runs_with_duration: 10,
      average_duration_seconds: 245.5,
      median_duration_seconds: 200,
      max_duration_seconds: 900,
      titles_processed: 500,
      titles_available: 300,
      titles_unavailable: 150,
      titles_unknown: 50,
      titles_failed: 0,
    },
    daily: [
      { date: "2026-10-04", completed: 4, partial: 1, failed: 0, active: 0, total: 5 },
      { date: "2026-10-05", completed: 3, partial: 1, failed: 1, active: 0, total: 5 },
    ],
    titles: {
      processed: 500,
      available: 300,
      unavailable: 150,
      unknown: 50,
      failed: 0,
      unknown_reasons: { quota_exhausted: 30, title_not_found: 20 },
    },
    requests: {
      created: 900,
      completed: 800,
      failed: 5,
      blocked: 95,
      pending: 0,
      completion_rate: 0.888,
      blocked_by_reason: { quota_exhausted: 90, policy_not_needed: 5 },
    },
    sources: [
      {
        source: "justwatch",
        runs: 4,
        completed: 4,
        successful: 602,
        failed: 1,
        quota_exhausted: 0,
        skipped: 0,
        not_run: 0,
        not_applicable: 0,
        not_checked: 0,
        running: 0,
        requests_processed: 602,
        titles_unknown: 0,
        positive_offers: 602,
        no_offers: null,
        average_duration_seconds: 120,
        median_duration_seconds: 110,
        last_success_at: "2026-10-05T10:00:00Z",
        last_attempted_at: "2026-10-05T10:00:00Z",
        never_executed: false,
        role: "aggregator",
        policy_metered: false,
        status_breakdown: { completed: 4, failed: 0 },
      },
      {
        source: "abc_iview",
        runs: 4,
        completed: 4,
        successful: 6376,
        failed: 0,
        quota_exhausted: 0,
        skipped: 0,
        not_run: 0,
        not_applicable: 0,
        not_checked: 0,
        running: 0,
        requests_processed: 6376,
        titles_unknown: 0,
        positive_offers: 0,
        no_offers: null,
        average_duration_seconds: 300,
        median_duration_seconds: 290,
        last_success_at: "2026-10-05T09:00:00Z",
        last_attempted_at: "2026-10-05T09:00:00Z",
        never_executed: false,
        role: "collector",
        policy_metered: false,
        status_breakdown: { completed: 4, failed: 0 },
      },
      {
        source: "streaming_availability",
        runs: 0,
        completed: 0,
        successful: 0,
        failed: 0,
        quota_exhausted: 0,
        skipped: 0,
        not_run: 0,
        not_applicable: 0,
        not_checked: 0,
        running: 0,
        requests_processed: 0,
        titles_unknown: 0,
        positive_offers: 0,
        no_offers: null,
        average_duration_seconds: null,
        median_duration_seconds: null,
        last_success_at: null,
        last_attempted_at: null,
        never_executed: true,
        role: "metered_aggregator",
        policy_metered: true,
        status_breakdown: {},
      },
    ],
    quota: {
      sources: [
        {
          source: "streaming_availability",
          limit: 100,
          used: 100,
          remaining: 0,
          exhausted: true,
        },
      ],
      sources_exhausted: ["streaming_availability"],
      blocked_requests: 90,
      events_available: false,
      events_unavailable_reason:
        "ApiCallCounter stores a single daily count per service and records no event when the limit is reached, so exhaustion events and their timestamps cannot be derived from persisted state.",
    },
    unavailable_metrics: [
      { metric: "quota.exhaustion_events", reason: "No event log exists." },
    ],
    ...overrides,
  };
}

function mockFetchOnce(body: unknown, ok = true, status = 200) {
  const spy = vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => body,
  });
  global.fetch = spy as unknown as typeof fetch;
  return spy;
}

describe("AvailabilityMetricsPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a loading state before data arrives", async () => {
    // A promise that never resolves keeps the page in its loading branch.
    global.fetch = vi.fn(() => new Promise(() => {})) as unknown as typeof fetch;
    render(<AvailabilityMetricsPage />);
    expect(screen.getByText(/Loading operational metrics/i)).toBeTruthy();
  });

  it("renders the seven-day window and overview cards", async () => {
    mockFetchOnce(payload());
    render(<AvailabilityMetricsPage />);

    await waitFor(() => expect(screen.getByText("Refreshes")).toBeTruthy());
    expect(screen.getByText(/Last 7 days/i)).toBeTruthy();
    expect(screen.getByText("Completion rate")).toBeTruthy();
    expect(screen.getByText("70.0%")).toBeTruthy();
    expect(screen.getByText("Titles processed")).toBeTruthy();
  });

  it("renders the daily refresh outcome table", async () => {
    mockFetchOnce(payload());
    render(<AvailabilityMetricsPage />);

    await waitFor(() => expect(screen.getByText("Refresh outcomes")).toBeTruthy());
    expect(screen.getByText("2026-10-04")).toBeTruthy();
    expect(screen.getByText("2026-10-05")).toBeTruthy();
  });

  it("shows an active refresh count", async () => {
    const base = payload();
    mockFetchOnce({
      ...base,
      refreshes: { ...base.refreshes, active: 2 },
    });
    render(<AvailabilityMetricsPage />);

    await waitFor(() => expect(screen.getByText("Refreshes")).toBeTruthy());
    expect(screen.getByText("2 active")).toBeTruthy();
  });

  it("lists sources that ran, with offers and success side by side", async () => {
    mockFetchOnce(payload());
    render(<AvailabilityMetricsPage />);

    await waitFor(() => expect(screen.getByText("Source health")).toBeTruthy());
    expect(screen.getByText("justwatch")).toBeTruthy();
    expect(screen.getByText("abc_iview")).toBeTruthy();
  });

  it("keeps 'ran and found nothing' distinct from 'never ran'", async () => {
    mockFetchOnce(payload());
    render(<AvailabilityMetricsPage />);

    await waitFor(() => expect(screen.getByText("Source health")).toBeTruthy());

    // abc_iview ran 4 times, answered 6376 times, produced no offers. It must
    // appear as an executed source with a real success count.
    const row = screen.getByText("abc_iview").closest("tr");
    expect(row?.textContent).toContain("6376");

    // streaming_availability never ran and must be called out as such, rather
    // than appearing in the table as zeroes.
    expect(screen.getByText(/Never ran in this window/i)).toBeTruthy();
    const idleNotice = screen.getByText(/Never ran in this window/i);
    expect(idleNotice.textContent).toContain("streaming_availability");
  });

  it("renders the quota section with exhaustion state", async () => {
    mockFetchOnce(payload());
    render(<AvailabilityMetricsPage />);

    // Scoped to the heading: "Quota" is also a column header in the source
    // table, so a bare text query is ambiguous.
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Quota" })).toBeTruthy(),
    );
    expect(screen.getByText(/^exhausted$/)).toBeTruthy();
    expect(screen.getByText(/90 request\(s\) blocked by quota/i)).toBeTruthy();
  });

  it("surfaces metrics that cannot be derived instead of inventing them", async () => {
    mockFetchOnce(payload());
    render(<AvailabilityMetricsPage />);

    await waitFor(() =>
      expect(screen.getByText("Not currently measurable")).toBeTruthy(),
    );
    expect(screen.getByText(/quota\.exhaustion_events/)).toBeTruthy();
  });

  it("reports an empty window as no data, not as 0% completion", async () => {
    const base = payload();
    mockFetchOnce({
      ...base,
      refreshes: {
        ...base.refreshes,
        total: 0,
        completed: 0,
        partial: 0,
        failed: 0,
        active: 0,
        terminal: 0,
        completion_rate: null,
        partial_rate: null,
        failure_rate: null,
        average_duration_seconds: null,
        median_duration_seconds: null,
        max_duration_seconds: null,
      },
      daily: [],
    });
    render(<AvailabilityMetricsPage />);

    await waitFor(() => expect(screen.getByText("Refreshes")).toBeTruthy());
    // The em-dash is the "no data" marker; it must not read as a number.
    expect(screen.getByText("no data")).toBeTruthy();
    expect(
      screen.getByText(/No refreshes ran in this window/i),
    ).toBeTruthy();
    expect(screen.getByText(/No refresh history in this window/i)).toBeTruthy();
  });

  it("renders the empty-unknown-reason state", async () => {
    const base = payload();
    mockFetchOnce({
      ...base,
      titles: { ...base.titles, unknown_reasons: {} },
    });
    render(<AvailabilityMetricsPage />);

    await waitFor(() =>
      expect(
        screen.getByText(/No unknown-reason breakdown recorded/i),
      ).toBeTruthy(),
    );
  });

  it("surfaces an API error", async () => {
    mockFetchOnce({}, false, 503);
    render(<AvailabilityMetricsPage />);

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "Metrics unavailable (HTTP 503)",
      ),
    );
  });
});