import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import AvailabilityStatusPanel from "@/components/AvailabilityStatusPanel";

const mockReport = {
  sources: [
    {
      source: "mediaset_infinity",
      label: "Mediaset Infinity",
      tier: 0,
      kind: "title",
      status: "completed",
      reason: "",
      total: 191,
      completed: 191,
      successful: 191,
      failed: 0,
      unknown: 0,
      queued: 0,
    },
    {
      source: "streaming_availability",
      label: "Streaming Availability",
      tier: 2,
      kind: "title",
      status: "quota_exhausted",
      reason: "quota_exhausted" as const,
      total: 191,
      completed: 0,
      successful: 0,
      failed: 0,
      unknown: 0,
      queued: 191,
    },
  ],
  collectors: [],
  totals: { titles: 191, available: 163, unavailable: 4, unknown: 24, checks: 382 },
  unknown_reasons: { quota_exhausted: 24 },
  policy: {
    budget_order: [],
    demote_metered_aggregators: false,
    tiers: { "0": "first-party", "1": "aggregator", "2": "collectors" },
  },
  counterfactual: {
    policy: "consult_metered_only_when_unresolved",
    current: false,
    titles_still_resolved: 115,
    titles_losing_their_answer: 48,
    metered_sources: ["streaming_availability"],
    note: "n/a",
  },
  resets_at: 0,
};

function mockFetch(payload: {
  active?: unknown;
  results?: unknown[];
}) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.includes("/availability/refreshes/")
      ? { results: payload.results ?? [], active: payload.active ?? null }
      : mockReport;
    return {
      ok: true,
      json: async () => body,
    } as Response;
  });
}

describe("AvailabilityStatusPanel", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch({}));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders per-source progress, never one opaque total", async () => {
    render(<AvailabilityStatusPanel />);
    await waitFor(() =>
      expect(screen.getByText("Mediaset Infinity")).toBeInTheDocument()
    );
    expect(screen.getByText("Streaming Availability")).toBeInTheDocument();
    // 191 / 191 for the completed source, 0 / 191 for the blocked one.
    expect(screen.getByText("191 / 191")).toBeInTheDocument();
    expect(screen.getByText("0 / 191 — quota exhausted")).toBeInTheDocument();
  });

  it("separates unknown from unavailable in the headline", async () => {
    render(<AvailabilityStatusPanel />);
    await waitFor(() =>
      expect(screen.getByText(/163 available/)).toBeInTheDocument()
    );
    expect(screen.getByText(/4 unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/24 unknown/)).toBeInTheDocument();
  });

  it("explains WHY titles are unknown rather than saying 'unknown'", async () => {
    render(<AvailabilityStatusPanel />);
    await waitFor(() =>
      expect(screen.getByText(/24 still unknown/)).toBeInTheDocument()
    );
    // The reason, in words — not a bare count.
    expect(screen.getByText(/24 quota exhausted/)).toBeInTheDocument();
  });

  it("a blocked source is never worded as 'not available'", async () => {
    render(<AvailabilityStatusPanel />);
    await waitFor(() =>
      expect(screen.getByText("Streaming Availability")).toBeInTheDocument()
    );
    // A quota wall describes the pipeline, not the world. Rendering it as
    // "not available" is the exact conflation this feature removes.
    expect(screen.queryByText(/Streaming Availability not available/i)).toBeNull();
    expect(
      screen.getByText(/not moving/i)
    ).toBeInTheDocument();
  });

  it("shows an active run resumed from the backend, not from component state", async () => {
    // This is the reload case: the component mounts with NO state of its own
    // and must draw a run that a previous page load started.
    vi.stubGlobal(
      "fetch",
      mockFetch({
        active: {
          id: 184,
          type: "watchlist",
          status: "running",
          batch: "b1",
          created_at: "2026-10-05T14:00:00Z",
          started_at: "2026-10-05T14:00:00Z",
          finished_at: null,
          is_active: true,
          total_titles: 191,
          completed_titles: 100,
          available_titles: 100,
          unavailable_titles: 0,
          unknown_titles: 91,
          failed_titles: 0,
          unknown_reasons: { quota_exhausted: 91 },
          sources: [],
        },
      })
    );
    render(<AvailabilityStatusPanel />);
    await waitFor(() =>
      expect(screen.getByText(/Refresh #184 running/)).toBeInTheDocument()
    );
    expect(screen.getByText(/100\/191 titles resolved/)).toBeInTheDocument();
  });

  it("stays silent on a read failure — the page owns error reporting", async () => {
    // This panel decorates a page that renders its own "Rate limited — try
    // again later." A second alert here made that text ambiguous and broke an
    // existing test with "Found multiple elements with the text".
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) }))
    );
    const { container } = render(
      <div>
        <p>page error already shown</p>
        <AvailabilityStatusPanel />
      </div>
    );
    await waitFor(() =>
      expect(screen.getByText("page error already shown")).toBeInTheDocument()
    );
    // No alert, no duplicate message: the widget contributes nothing.
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("a partial payload must never crash the watchlist page", async () => {
    // Regression: this panel is mounted on the watchlist page, whose tests
    // mock only the endpoints they know about. `report.sources` arriving
    // undefined threw `Cannot read properties of undefined (reading
    // 'filter')` inside the component, which unmounted the ENTIRE page —
    // six unrelated watchlist tests failed with a stack trace pointing at
    // this file rather than at the real cause.
    //
    // A status widget must be unable to take down the page it decorates, so
    // an incomplete payload renders nothing instead of throwing.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const body = url.includes("/availability/refreshes/")
          ? { results: [], active: null }
          : { totals: { titles: 191, available: 163, unavailable: 4, unknown: 24 } };
        return { ok: true, json: async () => body } as Response;
      })
    );
    const { container } = render(
      <div>
        <p>watchlist still here</p>
        <AvailabilityStatusPanel />
      </div>
    );
    await waitFor(() =>
      expect(screen.getByText("watchlist still here")).toBeInTheDocument()
    );
    expect(container.textContent).toContain("watchlist still here");
  });
});