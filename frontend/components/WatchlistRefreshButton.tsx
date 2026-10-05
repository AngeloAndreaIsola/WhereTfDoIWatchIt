"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  type AvailabilityRefresh,
  getAvailabilityRefresh,
  getAvailabilityRefreshes,
  startAvailabilityRefresh,
} from "@/lib/api";

const POLL_INTERVAL_MS = 5000;

const TERMINAL_STATUSES = new Set(["completed", "partial", "failed"]);

function RefreshTrigger({
  busy,
  spinning,
  label,
  onClick,
}: {
  busy: boolean;
  spinning: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      title="Re-check every watchlisted title against the streaming catalogues now"
      className="inline-flex shrink-0 items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-xs transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-blue-500 dark:hover:bg-blue-600"
    >
      <svg
        className={`h-4 w-4 ${spinning ? "animate-spin" : ""}`}
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
  );
}

function CatalogueNote({ slugs }: { slugs: string[] }) {
  return (
    <p className="text-xs text-gray-500 dark:text-gray-400">
      Also syncing {slugs.length} catalogue source{slugs.length === 1 ? "" : "s"}.
    </p>
  );
}

function ErrorNote({ message }: { message: string }) {
  return (
    <p role="alert" className="text-xs text-red-600 dark:text-red-400">
      {message}
    </p>
  );
}

/**
 * "Refresh all availability" — the backend owns the run, this owns nothing.
 *
 * The previous implementation kept the whole lifecycle in React state
 * (`phase`, `queued`, `refreshed`, `pendingLeft`) and inferred completion from
 * "no pending requests remain". That is two sources of truth for one question,
 * and the wrong one: reloading the page mid-run erased every trace of a
 * refresh that was still going, and a run whose requests had all settled could
 * be declared finished while the worker had not written a status at all.
 *
 * The contract now:
 *
 *     click     -> POST  /api/availability/refresh/   -> { refresh_id }
 *     poll      -> GET   /api/availability/refreshes/<id>/
 *     reload    -> GET   /api/availability/refreshes/  -> resume `active`
 *     terminal  -> stop polling
 *
 * Every value rendered below is read back from the server on each poll. The
 * only thing kept locally is the run *id* — and even that is re-derived from
 * the list endpoint on mount, so a reload loses nothing. The button never
 * decides a run has finished; it stops polling because the backend said the
 * status is terminal.
 */
export default function WatchlistRefreshButton({
  onRefreshed,
}: {
  /** Called once when a run reaches a terminal status — reload rows. */
  onRefreshed?: () => void;
}) {
  const [run, setRun] = useState<AvailabilityRefresh | null>(null);
  const [starting, setStarting] = useState(false);
  // Catalogue sources are whole-catalogue syncs, not per-title rows, so they
  // are not part of the run's own progress. They arrive once with the POST
  // and are reported from there — the panel renders their steady-state
  // status separately.
  const [catalogueQueued, setCatalogueQueued] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Guards every async continuation against a stale run or an unmount, so a
  // slow response from an abandoned run cannot overwrite a newer one.
  const runIdRef = useRef(0);
  const onRefreshedRef = useRef(onRefreshed);
  const notifiedRef = useRef<number | null>(null);

  useEffect(() => {
    onRefreshedRef.current = onRefreshed;
  }, [onRefreshed]);

  const adopt = useCallback((next: AvailabilityRefresh | null) => {
    setRun(next);
    // Fire the completion callback exactly once per run, keyed on the run id
    // rather than on a boolean: two consecutive runs must each notify, and a
    // re-poll of an already-reported run must not.
    if (
      next &&
      TERMINAL_STATUSES.has(next.status) &&
      notifiedRef.current !== next.id
    ) {
      notifiedRef.current = next.id;
      onRefreshedRef.current?.();
    }
  }, []);

  // Poll one run to a terminal status, then stop.
  const poll = useCallback(
    async (id: number) => {
      const token = runIdRef.current;
      try {
        const next = await getAvailabilityRefresh(id);
        if (runIdRef.current !== token) return; // superseded or unmounted
        adopt(next);
        if (!TERMINAL_STATUSES.has(next.status)) {
          setTimeout(() => {
            if (runIdRef.current === token) void poll(id);
          }, POLL_INTERVAL_MS);
        }
      } catch {
        // A failed read is not a failed run. The refresh continues server-side,
        // so keep polling: the next tick either recovers or reports a real
        // terminal status. Only the click path surfaces an error to the user.
        if (runIdRef.current !== token) return;
        setTimeout(() => {
          if (runIdRef.current === token) void poll(id);
        }, POLL_INTERVAL_MS);
      }
    },
    [adopt]
  );

  // On mount: resume whatever the backend says is running. This is the whole
  // point of persisting the run — the page can be reloaded at any moment and
  // picks the refresh back up.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const { active } = await getAvailabilityRefreshes();
        if (cancelled) return;
        if (active) {
          runIdRef.current += 1;
          adopt(active);
          if (!TERMINAL_STATUSES.has(active.status)) void poll(active.id);
        }
      } catch {
        // Nothing to resume, and the page's own data load reports any real
        // failure. Silence here keeps the button from flickering an error on
        // a page that is otherwise fine.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [adopt, poll]);

  const handleClick = useCallback(async () => {
    runIdRef.current += 1;
    setStarting(true);
    setError(null);
    try {
      const started = await startAvailabilityRefresh();
      setCatalogueQueued(started.catalogue_queued ?? []);
      adopt(started.refresh);
      if (!TERMINAL_STATUSES.has(started.refresh.status)) {
        void poll(started.refresh_id);
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to start availability refresh"
      );
    } finally {
      setStarting(false);
    }
  }, [adopt, poll]);

  // One terminality check drives both the disabled state and the label, so
  // they cannot disagree — the button must never read "idle" while the run is
  // still going, or show progress for a run that already finished.
  if (!run) {
    return (
      <div className="flex flex-col items-start gap-1">
        <RefreshTrigger
          busy={starting}
          spinning={starting}
          label="Refresh all availability"
          onClick={handleClick}
        />
        {catalogueQueued.length > 0 && <CatalogueNote slugs={catalogueQueued} />}
        {error && <ErrorNote message={error} />}
      </div>
    );
  }

  const live = !TERMINAL_STATUSES.has(run.status);
  const busy = starting || live;
  const label = starting
    ? "Queueing…"
    : live
      ? `Refreshing… ${run.available_titles}/${run.total_titles} titles`
      : run.status === "completed"
        ? `✓ Refreshed ${run.total_titles} titles`
        : run.status === "partial"
          ? `✓ Refreshed — ${run.unknown_titles} still unknown`
          : "Refresh failed";

  return (
    <div className="flex flex-col items-start gap-1">
      <RefreshTrigger busy={busy} spinning={busy} label={label} onClick={handleClick} />
      {catalogueQueued.length > 0 && <CatalogueNote slugs={catalogueQueued} />}
      {error && <ErrorNote message={error} />}
    </div>
  );
}