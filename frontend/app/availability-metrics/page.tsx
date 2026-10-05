/**
 * P7 operational metrics — dashboard.
 *
 * Read-only view over `GET /api/availability/metrics/`. It is an operational
 * tool, deliberately plain: no charts library, no layout changes to the
 * watchlist panel, nothing that makes the existing availability UX move.
 *
 * The one rule that shapes the whole component: **`null` is not `0`**.
 *
 * The API distinguishes "measured zero" from "no data", and this UI has to
 * keep that distinction or it will actively mislead. A completion rate of
 * `null` renders as "no runs in this window", never as "0% complete". A source
 * that never executed is shown as "never ran", never as a row of zeroes that
 * reads like a broken source. A source that ran and found nothing is shown as
 * success with zero offers — which is a working source with an uninteresting
 * answer, and the single most important distinction this screen exists to make.
 */
"use client";

import { useEffect, useState } from "react";

interface WindowInfo {
  start: string;
  end: string;
  days: number;
  timezone: string;
  definition: string;
}

interface RefreshMetrics {
  total: number;
  completed: number;
  partial: number;
  failed: number;
  active: number;
  terminal: number;
  completion_rate: number | null;
  partial_rate: number | null;
  failure_rate: number | null;
  runs_with_duration: number;
  average_duration_seconds: number | null;
  median_duration_seconds: number | null;
  max_duration_seconds: number | null;
  titles_processed: number;
  titles_available: number;
  titles_unavailable: number;
  titles_unknown: number;
  titles_failed: number;
}

interface DailyRow {
  date: string;
  completed: number;
  partial: number;
  failed: number;
  active: number;
  total: number;
}

interface TitleMetrics {
  processed: number;
  available: number;
  unavailable: number;
  unknown: number;
  failed: number;
  unknown_reasons: Record<string, number>;
}

interface RequestMetrics {
  created: number;
  completed: number;
  failed: number;
  blocked: number;
  pending: number;
  completion_rate: number | null;
  blocked_by_reason: Record<string, number>;
}

interface SourceMetrics {
  source: string;
  runs: number;
  completed: number;
  successful: number;
  failed: number;
  quota_exhausted: number;
  skipped: number;
  not_run: number;
  not_applicable: number;
  not_checked: number;
  running: number;
  requests_processed: number;
  titles_unknown: number;
  positive_offers: number;
  no_offers: number | null;
  average_duration_seconds: number | null;
  median_duration_seconds: number | null;
  last_success_at: string | null;
  last_attempted_at: string | null;
  never_executed: boolean;
  role: string | null;
  policy_metered: boolean;
  status_breakdown: Record<string, number>;
}

interface QuotaEntry {
  source: string;
  limit: number;
  used: number;
  remaining: number;
  exhausted: boolean;
}

interface QuotaMetrics {
  sources: QuotaEntry[];
  sources_exhausted: string[];
  blocked_requests: number;
  events_available: boolean;
  events_unavailable_reason: string;
}

interface UnavailableMetric {
  metric: string;
  reason: string;
}

export interface AvailabilityMetricsPayload {
  window: WindowInfo;
  refreshes: RefreshMetrics;
  daily: DailyRow[];
  titles: TitleMetrics;
  requests: RequestMetrics;
  sources: SourceMetrics[];
  quota: QuotaMetrics;
  unavailable_metrics: UnavailableMetric[];
}

/** `null` reads as "no data". Never coerce it to 0. */
function metric(value: number | null | undefined, suffix = ""): string {
  if (value === null || value === undefined) return "—";
  return `${value}${suffix}`;
}

function percent(value: number | null): string {
  if (value === null) return "no data";
  return `${(value * 100).toFixed(1)}%`;
}

function duration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function stamp(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString();
}

function Card({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "neutral" | "good" | "warn" | "bad";
}) {
  const toneClass =
    tone === "good"
      ? "text-emerald-600 dark:text-emerald-400"
      : tone === "warn"
        ? "text-amber-600 dark:text-amber-400"
        : tone === "bad"
          ? "text-red-600 dark:text-red-400"
          : "text-gray-900 dark:text-gray-100";
  return (
    <div className="rounded-lg border border-gray-200 p-4 dark:border-gray-800">
      <p className="text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">
        {label}
      </p>
      <p className={`mt-1 text-2xl font-semibold ${toneClass}`}>{value}</p>
      {hint ? (
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{hint}</p>
      ) : null}
    </div>
  );
}

export default function AvailabilityMetricsPage() {
  const [data, setData] = useState<AvailabilityMetricsPayload | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/availability/metrics/")
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Metrics unavailable (HTTP ${response.status})`);
        }
        return response.json() as Promise<AvailabilityMetricsPayload>;
      })
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((loadError: unknown) => {
        if (!cancelled) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : "Failed to load availability metrics",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
          Availability metrics
        </h1>
        <p className="mt-4 text-sm text-gray-500 dark:text-gray-400">
          Loading operational metrics…
        </p>
      </main>
    );
  }

  if (error) {
    return (
      <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
          Availability metrics
        </h1>
        <p
          role="alert"
          className="mt-4 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300"
        >
          {error}
        </p>
      </main>
    );
  }

  if (!data) return null;

  const { refreshes, titles, requests, sources, quota, daily, window: win } = data;
  const noRuns = refreshes.total === 0;
  const executedSources = sources.filter((s) => !s.never_executed);
  const idleSources = sources.filter((s) => s.never_executed);

  return (
    <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
      <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
        Availability metrics
      </h1>
      <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
        Last {win.days} days · {win.definition} · {win.timezone}
      </p>

      {noRuns ? (
        <p className="mt-6 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          No refreshes ran in this window. Nothing below is measured — an empty
          window is not the same as a window where everything failed.
        </p>
      ) : null}

      {/* Overview */}
      <section aria-label="Overview" className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Card label="Refreshes" value={metric(refreshes.total)} hint={`${refreshes.active} active`} />
        <Card
          label="Completion rate"
          value={percent(refreshes.completion_rate)}
          tone={refreshes.completion_rate === null ? "neutral" : refreshes.completion_rate >= 0.8 ? "good" : "warn"}
          hint={`${refreshes.completed} of ${refreshes.terminal} finished`}
        />
        <Card
          label="Partial / failed"
          value={`${refreshes.partial} / ${refreshes.failed}`}
          tone={refreshes.failed > 0 ? "bad" : refreshes.partial > 0 ? "warn" : "good"}
        />
        <Card label="Avg duration" value={duration(refreshes.average_duration_seconds)} hint={`max ${duration(refreshes.max_duration_seconds)}`} />
        <Card label="Titles processed" value={metric(titles.processed)} />
        <Card
          label="Unknown titles"
          value={metric(titles.unknown)}
          tone={titles.unknown > 0 ? "warn" : "good"}
          hint={`${titles.available} available · ${titles.unavailable} unavailable`}
        />
      </section>

      {/* Refresh outcomes */}
      <section aria-label="Refresh outcomes" className="mt-8">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          Refresh outcomes
        </h2>
        {daily.length === 0 ? (
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            No refresh history in this window.
          </p>
        ) : (
          <table className="mt-2 w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
              <tr>
                <th scope="col" className="py-1">Date</th>
                <th scope="col" className="py-1">Completed</th>
                <th scope="col" className="py-1">Partial</th>
                <th scope="col" className="py-1">Failed</th>
                <th scope="col" className="py-1">Active</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-800">
              {daily.map((row) => (
                <tr key={row.date}>
                  <td className="py-1 font-mono text-xs">{row.date}</td>
                  <td className="py-1">{row.completed}</td>
                  <td className="py-1">{row.partial}</td>
                  <td className="py-1">{row.failed}</td>
                  <td className="py-1">{row.active}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Source health */}
      <section aria-label="Source health" className="mt-8">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          Source health
        </h2>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          Sources that ran are listed first. Zero offers next to a non-zero
          success count means the source answered and found nothing — which is
          not a failure.
        </p>
        <table className="mt-2 w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
            <tr>
              <th scope="col" className="py-1">Source</th>
              <th scope="col" className="py-1">Runs</th>
              <th scope="col" className="py-1">Success</th>
              <th scope="col" className="py-1">Failed</th>
              <th scope="col" className="py-1">Quota</th>
              <th scope="col" className="py-1">Offers</th>
              <th scope="col" className="py-1">Avg time</th>
              <th scope="col" className="py-1">Last success</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 dark:divide-gray-800">
            {executedSources.map((s) => (
              <tr key={s.source}>
                <td className="py-1 font-medium">{s.source}</td>
                <td className="py-1">{s.runs}</td>
                <td className="py-1">{s.successful}</td>
                <td className="py-1">{s.failed}</td>
                <td className="py-1">{s.quota_exhausted}</td>
                <td className="py-1">{s.positive_offers}</td>
                <td className="py-1">{duration(s.average_duration_seconds)}</td>
                <td className="py-1 text-xs">{stamp(s.last_success_at)}</td>
              </tr>
            ))}
            {idleSources.length > 0 ? (
              <tr className="text-gray-500 dark:text-gray-400">
                <td className="py-1" colSpan={8}>
                  Never ran in this window:{" "}
                  {idleSources.map((s) => s.source).join(", ")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>

      {/* Unknowns */}
      <section aria-label="Unknown titles" className="mt-8">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          Unknown titles
        </h2>
        {Object.keys(titles.unknown_reasons).length === 0 ? (
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            No unknown-reason breakdown recorded in this window.
          </p>
        ) : (
          <ul className="mt-2 space-y-1 text-sm">
            {Object.entries(titles.unknown_reasons)
              .sort((a, b) => b[1] - a[1])
              .map(([reason, count]) => (
                <li key={reason} className="flex justify-between border-b border-gray-100 py-1 dark:border-gray-800">
                  <span className="font-mono text-xs">{reason}</span>
                  <span>{count}</span>
                </li>
              ))}
          </ul>
        )}
      </section>

      {/* Quota */}
      <section aria-label="Quota" className="mt-8">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          Quota
        </h2>
        {quota.sources.length === 0 ? (
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            No metered sources configured.
          </p>
        ) : (
          <ul className="mt-2 space-y-1 text-sm">
            {quota.sources.map((entry) => (
              <li key={entry.source} className="flex items-center justify-between border-b border-gray-100 py-1 dark:border-gray-800">
                <span>
                  {entry.source}
                  {entry.exhausted ? (
                    <span className="ml-2 rounded bg-red-100 px-1.5 py-0.5 text-xs text-red-700 dark:bg-red-900 dark:text-red-200">
                      exhausted
                    </span>
                  ) : null}
                </span>
                <span className="text-xs">
                  {entry.used}/{entry.limit} used · {entry.remaining} left
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
          {quota.blocked_requests} request(s) blocked by quota in this window.
        </p>
        {!quota.events_available ? (
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            Exhaustion events are not available: {quota.events_unavailable_reason}
          </p>
        ) : null}
      </section>

      {requests.blocked > 0 ? (
        <section aria-label="Blocked requests" className="mt-8">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            Blocked requests by reason
          </h2>
          <ul className="mt-2 space-y-1 text-sm">
            {Object.entries(requests.blocked_by_reason).map(([reason, count]) => (
              <li key={reason} className="flex justify-between border-b border-gray-100 py-1 dark:border-gray-800">
                <span className="font-mono text-xs">{reason}</span>
                <span>{count}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section aria-label="Unavailable metrics" className="mt-8">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          Not currently measurable
        </h2>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-gray-500 dark:text-gray-400">
          {data.unavailable_metrics.map((item) => (
            <li key={item.metric}>
              <span className="font-mono">{item.metric}</span> — {item.reason}
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}