import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";
import WatchlistRefreshButton from "@/components/WatchlistRefreshButton";

const api = vi.hoisted(() => ({
  requestWatchlistRefresh: vi.fn(),
  getWatchlistRefreshStatus: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

function queued(now = 1000, count = 3) {
  return { queued: count, total_watchlist: count, now };
}

function status(pending: number, refreshed: number, failed = 0) {
  return { pending, refreshed, failed, since: 1000 };
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
    expect(api.getWatchlistRefreshStatus).toHaveBeenCalledWith(1000);
    expect(screen.getByRole("button")).toHaveTextContent("Refreshing… 0/3");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByRole("button")).toHaveTextContent("Refreshing… 2/3");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByRole("button")).toHaveTextContent("✓ Refreshed 3 titles");
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

    expect(screen.getByRole("button")).toHaveTextContent("✓ Refreshed 3 titles");
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
});
