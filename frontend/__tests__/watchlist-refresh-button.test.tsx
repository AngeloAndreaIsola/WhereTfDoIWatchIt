import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";
import WatchlistRefreshButton from "@/components/WatchlistRefreshButton";

const api = vi.hoisted(() => ({
  requestWatchlistRefresh: vi.fn(),
  getWatchlistRefreshStatus: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

const BATCH = "11111111-1111-4111-8111-111111111111";
const SOURCES = ["streaming_availability", "youtube", "mediaset_infinity"];

function queued(now = 1000, count = 3, catalogue_queued: string[] = []) {
  return {
    queued: count,
    total_watchlist: count,
    now,
    batch: BATCH,
    sources: SOURCES,
    checks_per_title: SOURCES.length,
    catalogue_queued,
  };
}

/**
 * A status payload in the source-aware shape: counts are availability checks
 * (titles x sources), not titles, and rate limiting names the blocked source.
 */
function status(
  pending: number,
  refreshed: number,
  failed = 0,
  rate_limited_sources: string[] = []
) {
  return {
    total: pending + refreshed + failed,
    pending,
    refreshed,
    failed,
    titles: 3,
    checks_per_title: SOURCES.length,
    since: 1000,
    batch: BATCH,
    rate_limited: rate_limited_sources.length > 0,
    rate_limited_sources,
    source_quotas: rate_limited_sources.length
      ? { streaming_availability: { remaining: 0, limit: 100, exhausted: true } }
      : { streaming_availability: { remaining: 100, limit: 100, exhausted: false } },
    resets_at: 2000,
  };
}

async function flush() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {});
  }
}

describe("WatchlistRefreshButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("queues once, polls progress, and reports completion", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const onRefreshed = vi.fn();
    api.requestWatchlistRefresh.mockResolvedValue(queued());
    api.getWatchlistRefreshStatus
      .mockResolvedValueOnce(status(3, 0))
      .mockResolvedValueOnce(status(1, 2))
      .mockResolvedValueOnce(status(0, 3));

    render(<WatchlistRefreshButton onRefreshed={onRefreshed} />);
    await flush();

    fireEvent.click(screen.getByRole("button"));
    await flush();

    expect(api.requestWatchlistRefresh).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button")).toBeDisabled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(api.getWatchlistRefreshStatus).toHaveBeenCalledWith(1000, BATCH);
    expect(screen.getByRole("button")).toHaveTextContent("Refreshing… 0/3 checks");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByRole("button")).toHaveTextContent("Refreshing… 2/3 checks");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByRole("button")).toHaveTextContent(
      "✓ Checked 3 of 3 availability checks"
    );
    expect(screen.getByRole("button")).not.toBeDisabled();
    expect(onRefreshed).toHaveBeenCalledTimes(1);
    expect(api.requestWatchlistRefresh).toHaveBeenCalledTimes(1);
  });

  it("reports an empty queue without polling", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    api.requestWatchlistRefresh.mockResolvedValue(queued(1000, 0));

    render(<WatchlistRefreshButton />);
    await flush();
    fireEvent.click(screen.getByRole("button"));
    await flush();

    expect(screen.getByRole("button")).toHaveTextContent("Nothing to refresh");
    expect(api.getWatchlistRefreshStatus).not.toHaveBeenCalled();
    expect(screen.getByRole("button")).not.toBeDisabled();
  });

  it("surfaces an enqueue failure and returns to idle", async () => {
    api.requestWatchlistRefresh.mockRejectedValue(new Error("queue down"));

    render(<WatchlistRefreshButton />);
    await flush();
    fireEvent.click(screen.getByRole("button"));
    await flush();

    expect(screen.getByRole("alert")).toHaveTextContent("queue down");
    expect(screen.getByRole("button")).not.toBeDisabled();
    expect(screen.getByRole("button")).toHaveTextContent(
      "Refresh all availability"
    );
    expect(api.getWatchlistRefreshStatus).not.toHaveBeenCalled();
  });

  it("keeps retrying through transient poll failures", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const onRefreshed = vi.fn();
    api.requestWatchlistRefresh.mockResolvedValue(queued());
    api.getWatchlistRefreshStatus
      .mockRejectedValueOnce(new Error("read failed"))
      .mockResolvedValueOnce(status(1, 2))
      .mockResolvedValueOnce(status(0, 3));

    render(<WatchlistRefreshButton onRefreshed={onRefreshed} />);
    await flush();
    fireEvent.click(screen.getByRole("button"));
    await flush();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(screen.getByRole("button")).toHaveTextContent(
      "✓ Checked 3 of 3 availability checks"
    );
    expect(onRefreshed).toHaveBeenCalledTimes(1);
  });

  it("stops polling at the timeout and hands back the button", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    api.requestWatchlistRefresh.mockResolvedValue(queued());
    api.getWatchlistRefreshStatus.mockResolvedValue(status(2, 1));

    render(<WatchlistRefreshButton />);
    await flush();
    fireEvent.click(screen.getByRole("button"));
    await flush();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60 * 1000 + 5000);
    });

    expect(
      screen.getByText(/Refresh still running in the background/)
    ).toBeInTheDocument();
    expect(screen.getByRole("button")).not.toBeDisabled();
    expect(screen.getByRole("button")).toHaveTextContent(
      "Refresh all availability"
    );
  });

  it("scopes every poll to the run's batch id", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    api.requestWatchlistRefresh.mockResolvedValue(queued());
    api.getWatchlistRefreshStatus.mockResolvedValue(status(1, 2));

    render(<WatchlistRefreshButton />);
    await flush();
    fireEvent.click(screen.getByRole("button"));
    await flush();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(api.getWatchlistRefreshStatus).toHaveBeenCalledTimes(2);
    for (const call of api.getWatchlistRefreshStatus.mock.calls) {
      expect(call).toEqual([1000, BATCH]);
    }
  });

  it("names the blocked source instead of implying the whole run is blocked", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    api.requestWatchlistRefresh.mockResolvedValue(queued());
    api.getWatchlistRefreshStatus.mockResolvedValue(
      status(2, 1, 0, ["streaming_availability"])
    );

    render(<WatchlistRefreshButton />);
    await flush();
    fireEvent.click(screen.getByRole("button"));
    await flush();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    // The blocked collector is named, and the note explicitly says the rest
    // keep going.
    expect(
      screen.getByText(/Streaming Availability quota exhausted/)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/2 checks waiting for the reset/)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Other sources are still being checked/)
    ).toBeInTheDocument();
    // The old wording claimed every title was waiting.
    expect(screen.queryByText(/Daily API quota used/)).not.toBeInTheDocument();

    // Past the timeout the source-specific note still wins over the generic
    // "still running" copy.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60 * 1000);
    });
    expect(
      screen.queryByText(/Refresh still running in the background/)
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/Streaming Availability quota exhausted/)
    ).toBeInTheDocument();
  });

  it("a rate-limited source with pending work is NOT completion", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const onRefreshed = vi.fn();
    api.requestWatchlistRefresh.mockResolvedValue(queued());
    api.getWatchlistRefreshStatus.mockResolvedValue(
      status(2, 1, 0, ["streaming_availability"])
    );

    render(<WatchlistRefreshButton onRefreshed={onRefreshed} />);
    await flush();
    fireEvent.click(screen.getByRole("button"));
    await flush();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    // rate_limited is true, but pending > 0: the batch is not done, so the
    // button must not report success or reload the page.
    expect(screen.getByRole("button")).toBeDisabled();
    expect(screen.getByRole("button")).toHaveTextContent("Refreshing…");
    expect(onRefreshed).not.toHaveBeenCalled();
  });

  it("reports completion only when nothing is pending", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const onRefreshed = vi.fn();
    api.requestWatchlistRefresh.mockResolvedValue(queued());
    api.getWatchlistRefreshStatus.mockResolvedValue(
      status(0, 3, 0, ["streaming_availability"])
    );

    render(<WatchlistRefreshButton onRefreshed={onRefreshed} />);
    await flush();
    fireEvent.click(screen.getByRole("button"));
    await flush();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    // No pending work left: complete even though the source is still capped.
    expect(screen.getByRole("button")).not.toBeDisabled();
    expect(onRefreshed).toHaveBeenCalledTimes(1);
  });

  it("mentions queued catalogue syncs while polling", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    api.requestWatchlistRefresh.mockResolvedValue(
      queued(1000, 3, ["cbc_gem"])
    );
    api.getWatchlistRefreshStatus.mockResolvedValue(status(1, 2));

    render(<WatchlistRefreshButton />);
    await flush();
    fireEvent.click(screen.getByRole("button"));
    await flush();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(
      screen.getByText(/Also syncing 1 catalogue source/)
    ).toBeInTheDocument();
  });
});
