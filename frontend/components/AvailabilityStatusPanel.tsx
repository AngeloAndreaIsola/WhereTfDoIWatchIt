"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  type AvailabilityRefresh,
  type AvailabilityReportResponse,
  type AvailabilityResolutionReason,
  getAvailabilityRefreshes,
  getAvailabilityReport,
} from "@/lib/api";

const POLL_INTERVAL_MS = 5000;

/**
 * Human wording for one resolution reason.
 *
 * The two wordings that matter most are deliberately different in kind:
 *
 * - absence reasons ("no_offer", "title_not_found") describe the *world* —
 *   we looked and it is not there;
 * - gap reasons ("quota_exhausted", "queued", "not_checked") describe *our
 *   pipeline* — we did not look yet.
 *
 * Rendering the second group as if it were the first is the bug this whole
 * feature exists to remove, so no wording here says "not available" for a
 * reason that means "we haven't asked".
 */
const REASON_LABELS: Record<AvailabilityResolutionReason, string> = {
  not_checked: "never checked",
  queued: "queued",
  resolved: "resolved",
  no_offer: "checked — nothing at any provider",
  not_supported: "does not serve this country",
  not_applicable: "not applicable",
  title_not_found: "upstream has no such title",
  quota_exhausted: "quota exhausted",
  source_unavailable: "source unavailable",
  collector_not_run: "catalogue never synced",
  unknown_reason: "inconclusive",
};

function reasonText(reason: AvailabilityResolutionReason | "" | undefined): string {
  if (!reason) return "";
  return REASON_LABELS[reason] ?? reason;
}

/** One source line: how far it got, and why it stopped. */
function SourceLine({
  source,
}: {
  source: AvailabilityReportResponse["sources"][number];
}) {
  const blocked = source.reason === "quota_exhausted";
  const marker = blocked ? "⏸" : source.status === "completed" ? "✓" : source.status === "failed" ? "✗" : "↻";

  return (
    <li className="flex items-start justify-between gap-3 py-0.5 text-xs">
      <span className="flex min-w-0 items-center gap-2">
        <span aria-hidden="true" className="w-3 shrink-0">
          {marker}
        </span>
        <span className="truncate">{source.label}</span>
      </span>
      <span className="shrink-0 text-right text-gray-500 dark:text-gray-400">
        {source.total > 0
          ? `${source.completed} / ${source.total}`
          : source.offers
            ? `${source.offers} offers`
            : "—"}
        {source.reason ? ` — ${reasonText(source.reason)}` : ""}
      </span>
    </li>
  );
}

/**
 * Source-first availability status, resumable across a browser reload.
 *
 * The previous implementation kept the entire state of a long-running
 * refresh in React component state, so reloading the page mid-run erased
 * every trace of it while the backend kept working. This component owns no
 * state that cannot be re-derived: on mount it asks the backend for the
 * active run and draws whatever comes back, so a reload at any moment
 * produces the same panel.
 *
 * Progress is per *source*, not a single opaque counter — the whole point is
 * that "0 / 764" never appears without saying which source is responsible.
 */
export default function AvailabilityStatusPanel({
  onCompleted,
}: {
  /** Called when a run transitions to a terminal state, so rows can reload. */
  onCompleted?: () => void;
}) {
  const [report, setReport] = useState<AvailabilityReportResponse | null>(null);
  const [active, setActive] = useState<AvailabilityRefresh | null>(null);
  // Tracked but never rendered: a read failure must be silent (see the guard
  // below), yet swallowing it entirely would make an always-broken endpoint
  // indistinguishable from a healthy idle state in devtools.
  const [error, setError] = useState<string | null>(null);
  const runRef = useRef(0);
  const onCompletedRef = useRef(onCompleted);

  useEffect(() => {
    onCompletedRef.current = onCompleted;
  }, [onCompleted]);

  const load = useCallback(async () => {
    const runId = runRef.current;
    try {
      const [nextReport, runs] = await Promise.all([
        getAvailabilityReport(),
        getAvailabilityRefreshes(),
      ]);
      if (runRef.current !== runId) return;
      setReport(nextReport);
      const nextActive = runs.active;
      // A run that just finished is a completion event exactly once: guard
      // on the transition so re-polling a finished run does not loop the
      // caller's reload forever.
      setActive((prev) => {
        if (prev?.is_active && nextActive === null) {
          onCompletedRef.current?.();
        }
        return nextActive;
      });
      setError(null);
    } catch (err) {
      if (runRef.current !== runId) return;
      setError(
        err instanceof Error ? err.message : "Failed to read availability status"
      );
    }
  }, []);

  useEffect(() => {
    runRef.current += 1;
    void load();
    const timer = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [load]);

  // A read failure renders NOTHING, not a second error message. This panel is
  // a decorator on a page that already surfaces its own failures: when the
  // report call 429s, the watchlist page renders "Rate limited — try again
  // later." and a second alert from here made that text ambiguous, failing an
  // existing test on `Found multiple elements with the text`. A status widget
  // must never be louder than, or duplicate, the page's own error.
  if (error || !report || !Array.isArray(report.sources) || !report.totals) {
    return null;
  }

  const { totals, unknown_reasons: unknownReasons } = report;
  const sourceRows = report.sources.filter(
    (s): s is AvailabilityReportResponse["sources"][number] => !!s && !!s.source
  );
  const blocked = sourceRows.filter(
    (s) => s.reason === "quota_exhausted" || s.status === "failed"
  );

  return (
    <div className="flex flex-col gap-2 text-xs text-gray-600 dark:text-gray-300">
      <p className="font-medium text-gray-700 dark:text-gray-200">
        {totals.available} available · {totals.unavailable} unavailable ·{" "}
        {totals.unknown} unknown
        <span className="font-normal text-gray-500 dark:text-gray-400">
          {" "}
          of {totals.titles} titles
        </span>
      </p>

      {active && (
        <p className="text-blue-600 dark:text-blue-400" role="status">
          Refresh #{active.id} {active.status} · {active.available_titles}/
          {active.total_titles} titles resolved
        </p>
      )}

      <ul className="flex flex-col">
        {sourceRows.map((source) => (
          <SourceLine key={source.source} source={source} />
        ))}
      </ul>

      {blocked.length > 0 && (
        <p className="text-gray-500 dark:text-gray-400">
          {blocked.map((s) => s.label).join(", ")}{" "}
          {blocked.length === 1 ? "is" : "are"} not moving —{" "}
          {blocked.map((s) => reasonText(s.reason)).join(", ")}. Other sources
          keep checking.
        </p>
      )}

      {totals.unknown > 0 && (
        <p className="text-gray-500 dark:text-gray-400">
          {totals.unknown} still unknown —{" "}
          {Object.entries(unknownReasons ?? {})
            .map(
              ([reason, count]) =>
                `${count} ${reasonText(reason as AvailabilityResolutionReason)}`
            )
            .join(", ")}
        </p>
      )}
    </div>
  );
}