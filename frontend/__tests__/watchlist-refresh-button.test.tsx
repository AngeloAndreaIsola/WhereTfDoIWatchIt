import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";
import WatchlistRefreshButton from "@/components/WatchlistRefreshButton";

/**
 * The button is now a *display layer* over persisted backend state, so these
 * tests mock the three endpoints the real component calls and assert that it
 * never decides anything the backend has not already decided.
 *
 * Two contracts this file deliberately no longer tests, because the
 * implementation they covered is gone:
 *
 * - "completion == nothing pending". Completion is now the backend's
 *   `status`, read straight off the run. Inferring it client-side from request
 *   counts is what let a GET mark a run finished.
 * - batch-scoped poll URLs. Polling is by run id; the batch is the backend's
 *   business.
 *
 * The concerns underneath them survive and *are* still tested: a transient
 * read failure must not end the run, a blocked source must never be rendered
 * as "unavailable", and the completion callback must fire exactly once.
 */
const api = vi.hoisted(() => ({
  startAvailabilityRefresh: vi.fn(),
  getAvailabilityRefresh: vi.fn(),
  getAvailabilityRefreshes: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

const RUN_ID = 184;

function run(
  status: "queued" | "running" | "completed" | "partial" | "failed",
  overrides: Record<string, unknown> = {}
) {
  return {
    id: RUN_ID,
    type: "watchlist",
    status,
    batch: "11111111-1111-4111-8111-111111111111",
    created_at: "2026-10-05T14:00:00Z",
    started_at: "2026-10-05T14:00:01Z",
    finished_at: null,
    is_active: !["completed", "partial", "failed"].includes(status),
    total_titles: 191,
    completed_titles: 100,
    available_titles: 100,
    unavailable_titles: 0,
    unknown_titles: 91,
    failed_titles: 0,
    unknown_reasons: { quota_exhausted: 91 },
    sources: [
      {
        source: "watchmode",
        label: "Watchmode",
        tier: 1,
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
    ...overrides,
  };
}

function started(status: "queued" | "running" = "queued", catalogue: string[] = []) {
  return {
    refresh_id: RUN_ID,
    batch: "11111111-1111-4111-8111-111111111111",
    now: 1000,
    queued: 764,
    total_watchlist: 191,
    sources: ["watchmode", "streaming_availability"],
    catalogue_queued: catalogue,
    refresh: run(status),
  };
}

async function flush() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {});
  }
}

describe("WatchlistRefreshButton — persisted refresh lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getAvailabilityRefreshes.mockResolvedValue({ results: [], active: null });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("POSTs and starts polling the returned run id", async () => {
    api.startAvailabilityRefresh.mockResolvedValue(started());
    api.getAvailabilityRefresh.mockResolvedValue(run("running"));

    render(<WatchlistRefreshButton />);
    await flush();

    fireEvent.click(screen.getByRole("button"));
    await flush();

    expect(api.startAvailabilityRefresh).toHaveBeenCalledTimes(1);
    // Polling keys off the id the server handed back, never a locally
    // invented batch or timestamp.
    expect(api.getAvailabilityRefresh).toHaveBeenCalledWith(RUN_ID);
  });

  it("resumes an active run on mount, with no click", async () => {
    // The reload case: the page mounts, the user has not pressed anything, and
    // the refresh the previous page load started is still being displayed.
    api.getAvailabilityRefreshes.mockResolvedValue({
      results: [],
      active: run("running"),
    });
    api.getAvailabilityRefresh.mockResolvedValue(run("running"));

    render(<WatchlistRefreshButton />);
    await flush();

    expect(api.startAvailabilityRefresh).not.toHaveBeenCalled();
    expect(api.getAvailabilityRefresh).toHaveBeenCalledWith(RUN_ID);
    expect(screen.getByRole("button")).toBeDisabled();
    expect(screen.getByText(/Refreshing/)).toBeInTheDocument();
  });

  it("stays idle when the backend has no active run", async () => {
    render(<WatchlistRefreshButton />);
    await flush();

    expect(screen.getByRole("button")).not.toBeDisabled();
    expect(screen.getByText("Refresh all availability")).toBeInTheDocument();
    expect(api.getAvailabilityRefresh).not.toHaveBeenCalled();
  });

  it("never declares completion the backend has not declared", async () => {
    api.getAvailabilityRefreshes.mockResolvedValue({
      results: [],
      active: run("running"),
    });
    // Every source is finished and nothing is pending — but the backend still
    // says RUNNING, so the button must too. This is the exact case the old
    // implementation got wrong by inferring completion from counts.
    api.getAvailabilityRefresh.mockResolvedValue(
      run("running", { completed_titles: 191, available_titles: 163, unknown_titles: 0 })
    );

    render(<WatchlistRefreshButton />);
    await flush();

    expect(screen.getByText(/Refreshing/)).toBeInTheDocument();
    expect(screen.queryByText(/Refreshed/)).toBeNull();
  });

  it("stops polling once the backend reports a terminal status", async () => {
    api.getAvailabilityRefreshes.mockResolvedValue({
      results: [],
      active: run("running"),
    });
    api.getAvailabilityRefresh.mockResolvedValue(run("completed"));

    const onRefreshed = vi.fn();
    render(<WatchlistRefreshButton onRefreshed={onRefreshed} />);
    await flush();

    expect(screen.getByText(/Refreshed 191 titles/)).toBeInTheDocument();
    expect(screen.getByRole("button")).not.toBeDisabled();
    expect(onRefreshed).toHaveBeenCalledTimes(1);

    const callsAtTerminal = api.getAvailabilityRefresh.mock.calls.length;
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    // Terminal means terminal: no further polls scheduled.
    expect(api.getAvailabilityRefresh.mock.calls.length).toBe(callsAtTerminal);
  });

  it("fires the completion callback once per run, not once per poll", async () => {
    api.getAvailabilityRefreshes.mockResolvedValue({
      results: [],
      active: run("running"),
    });
    api.getAvailabilityRefresh.mockResolvedValue(run("completed"));

    const onRefreshed = vi.fn();
    render(<WatchlistRefreshButton onRefreshed={onRefreshed} />);
    await flush();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(onRefreshed).toHaveBeenCalledTimes(1);
  });

  it("keeps polling through a transient read failure", async () => {
    api.getAvailabilityRefreshes.mockResolvedValue({
      results: [],
      active: run("running"),
    });
    api.getAvailabilityRefresh
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValue(run("running"));

    render(<WatchlistRefreshButton />);
    await flush();

    // The button must not conclude anything from a failed read — it neither
    // reports an error to the user nor stops following the run.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(/Refreshing/)).toBeInTheDocument();
  });

  it("surfaces an enqueue failure, which is the one real user error", async () => {
    api.startAvailabilityRefresh.mockRejectedValue(new Error("queue down"));

    render(<WatchlistRefreshButton />);
    await flush();
    fireEvent.click(screen.getByRole("button"));
    await flush();

    expect(screen.getByRole("alert")).toHaveTextContent("queue down");
  });

  it("reports a partial run as partial, with the unknown count", async () => {
    api.getAvailabilityRefreshes.mockResolvedValue({
      results: [],
      active: run("running"),
    });
    api.getAvailabilityRefresh.mockResolvedValue(
      run("partial", { unknown_titles: 24, unknown_reasons: { quota_exhausted: 24 } })
    );

    render(<WatchlistRefreshButton />);
    await flush();

    expect(screen.getByText(/24 still unknown/)).toBeInTheDocument();
  });

  it("mentions queued catalogue syncs", async () => {
    api.startAvailabilityRefresh.mockResolvedValue(
      started("queued", ["justwatch", "tubi"])
    );

    render(<WatchlistRefreshButton />);
    await flush();
    fireEvent.click(screen.getByRole("button"));
    await flush();

    expect(screen.getByText(/Also syncing 2 catalogue sources/)).toBeInTheDocument();
  });

  it("a quota-blocked source is never worded as 'not available'", async () => {
    api.getAvailabilityRefreshes.mockResolvedValue({
      results: [],
      active: run("running"),
    });
    api.getAvailabilityRefresh.mockResolvedValue(run("running"));

    const { container } = render(<WatchlistRefreshButton />);
    await flush();

    expect(container.textContent).not.toMatch(/not available/i);
    // The run reports 91 unknown, and says why, without ever claiming
    // absence.
    expect(container.textContent).not.toMatch(/unavailable \d/i);
  });
});