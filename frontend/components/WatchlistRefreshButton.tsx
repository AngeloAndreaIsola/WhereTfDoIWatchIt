"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getWatchlistRefreshStatus,
  requestWatchlistRefresh,
} from "@/lib/api";

const WATCHLIST_POLL_INTERVAL_MS = 5000;
const WATCHLIST_POLL_TIMEOUT_MS = 5 * 60 * 1000;

type Phase = "idle" | "queueing" | "polling" | "done" | "timeout" | "error";

interface WatchlistRefreshButtonProps {
  /** Called once when the run finishes (nothing left pending) — reload rows. */
  onRefreshed?: () => void;
}

/**
 * "Refresh all availability" for the watchlist page.
 *
 * One click queues an explicit refresh for every watchlisted title (one
 * enqueue request, never one per title) and then polls the status endpoint
 * until the worker drains the queue — rate limits and the daily budget stay
 * authoritative, so a large queue simply keeps draining across ticks. On
 * completion `onRefreshed` lets the page reload the report; if polling
 * outlives its timeout the run keeps going server-side and the button
 * returns to idle with a note.
 */
export default function WatchlistRefreshButton({
  onRefreshed,
}: WatchlistRefreshButtonProps) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [queued, setQueued] = useState(0);
  const [refreshed, setRefreshed] = useState(0);
  const [failed, setFailed] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const runRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onRefreshedRef = useRef(onRefreshed);
  useEffect(() => {
    onRefreshedRef.current = onRefreshed;
  }, [onRefreshed]);
  useEffect(
    () => () => {
      runRef.current += 1; // invalidate any in-flight poll loop on unmount
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    []
  );

  const schedulePoll = useCallback(
    (since: number, startedAt: number, runId: number) => {
      timerRef.current = setTimeout(async () => {
        if (runRef.current !== runId) return;
        try {
          const status = await getWatchlistRefreshStatus(since);
          if (runRef.current !== runId) return;
          setRefreshed(status.refreshed);
          setFailed(status.failed);
          if (status.pending === 0) {
            setPhase("done");
            onRefreshedRef.current?.();
            return;
          }
        } catch {
          if (runRef.current !== runId) return;
          // Transient read failure: the run continues server-side, so keep
          // retrying until the timeout decides how long we wait.
        }
        if (Date.now() - startedAt >= WATCHLIST_POLL_TIMEOUT_MS) {
          setPhase("timeout");
          return;
        }
        schedulePoll(since, startedAt, runId);
      }, WATCHLIST_POLL_INTERVAL_MS);
    },
    []
  );

  const handleClick = useCallback(async () => {
    const runId = runRef.current + 1;
    runRef.current = runId;
    if (timerRef.current) clearTimeout(timerRef.current);
    setError(null);
    setRefreshed(0);
    setFailed(0);
    setPhase("queueing");
    try {
      const result = await requestWatchlistRefresh();
      if (runRef.current !== runId) return;
      if (result.queued === 0) {
        setQueued(0);
        setPhase("done");
        return;
      }
      setQueued(result.queued);
      setPhase("polling");
      schedulePoll(result.now, Date.now(), runId);
    } catch (err) {
      if (runRef.current !== runId) return;
      setError(err instanceof Error ? err.message : "Failed to queue refresh");
      setPhase("error");
    }
  }, [schedulePoll]);

  const busy = phase === "queueing" || phase === "polling";
  const label =
    phase === "queueing"
      ? "Queueing…"
      : phase === "polling"
        ? `Refreshing… ${refreshed}/${queued}`
        : phase === "done"
          ? queued === 0
            ? "Nothing to refresh"
            : `✓ Refreshed ${refreshed} title${refreshed === 1 ? "" : "s"}`
          : "Refresh all availability";

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        title="Re-check every watchlisted title against the streaming catalogues now"
        className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-xs transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-blue-500 dark:hover:bg-blue-600"
      >
        <svg
          className={`h-4 w-4 ${busy ? "animate-spin" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
          />
        </svg>
        {label}
      </button>
      {phase === "polling" && (
        <p className="text-xs text-gray-500 dark:text-gray-400" role="status">
          {failed > 0
            ? `${failed} failed — the rest keep draining in the background.`
            : `Checked in the background — rate limits may spread this over time.`}
        </p>
      )}
      {phase === "timeout" && (
        <p className="text-xs text-gray-500 dark:text-gray-400" role="status">
          Refresh still running in the background — check back in a few
          minutes.
        </p>
      )}
      {error && (
        <p role="alert" className="text-xs text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  );
}
