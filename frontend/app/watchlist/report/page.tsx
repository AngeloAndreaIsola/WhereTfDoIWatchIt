"use client";

import React, { useState, useEffect, useCallback } from "react";
import Link from "next/link";

interface Movie {
  id: number;
  tmdb_id: number;
  title: string;
  year: number | null;
}

interface ReportRow {
  movie: Movie;
  available_countries: string[];
  best_country: string | null;
  best_service: string | null;
}

interface CountrySummary {
  country: string;
  count: number;
}

interface ServiceSummary {
  service: string;
  count: number;
}

interface SummaryData {
  total_movies: number;
  available_movies: number;
  unavailable_movies: number;
  /** Titles with only stale/unverified evidence — neither available nor unavailable. */
  needs_recheck_movies?: number;
  countries: CountrySummary[];
  services: ServiceSummary[];
}

interface ReportResponse {
  report: ReportRow[];
  summary: SummaryData;
}

export default function WatchlistReportPage() {
  const [data, setData] = useState<ReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "available" | "unavailable">("all");

  const fetchReport = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/watchlist/report/");
      if (res.status === 429) {
        const body = await res.json().catch(() => ({}));
        setError(body.error ?? "Rate limited — please try again later.");
        return;
      }
      if (!res.ok) {
        throw new Error(`Failed to load report (${res.status})`);
      }
      const json: ReportResponse = await res.json();
      setData(json);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load report");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchReport();
  }, [fetchReport]);

  const filteredReport = React.useMemo(() => {
    if (!data) return [];
    return data.report.filter((row) => {
      const isAvailable = row.available_countries.length > 0;
      if (statusFilter === "available" && !isAvailable) return false;
      if (statusFilter === "unavailable" && isAvailable) return false;

      if (search.trim()) {
        const query = search.toLowerCase();
        const matchesTitle = row.movie.title.toLowerCase().includes(query);
        const matchesYear = row.movie.year?.toString().includes(query) ?? false;
        if (!matchesTitle && !matchesYear) return false;
      }
      return true;
    });
  }, [data, statusFilter, search]);

  return (
    <main className="mx-auto max-w-4xl px-4 py-10 sm:px-6">
      {/* Top Header */}
      <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Watchlist Report</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Streaming availability analytics & provider breakdown for your watchlist
          </p>
        </div>
        <div>
          <Link
            href="/watchlist"
            className="inline-flex items-center gap-1 text-sm font-medium text-blue-600 hover:underline dark:text-blue-400"
          >
            ← Back to Watchlist
          </Link>
        </div>
      </div>

      {loading && (
        <div className="py-12 text-center text-sm text-gray-500 dark:text-gray-400">
          Loading report & analytics…
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {error}
        </div>
      )}

      {!loading && !error && data && (
        <div className="space-y-8">
          {/* Summary Metric Cards */}
          <section className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-700 dark:bg-gray-800">
              <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                Total Movies
              </span>
              <div className="mt-2 text-3xl font-bold text-gray-900 dark:text-gray-100">
                {data.summary.total_movies}
              </div>
            </div>

            <div className="rounded-lg border border-green-200 bg-green-50/50 p-5 shadow-sm dark:border-green-900/50 dark:bg-green-950/20">
              <span className="text-xs font-semibold uppercase tracking-wider text-green-700 dark:text-green-400">
                Available
              </span>
              <div className="mt-2 text-3xl font-bold text-green-700 dark:text-green-400">
                {data.summary.available_movies}
              </div>
            </div>

            <div className="rounded-lg border border-amber-200 bg-amber-50/50 p-5 shadow-sm dark:border-amber-900/50 dark:bg-amber-950/20">
              <span className="text-xs font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400">
                Unavailable
              </span>
              <div className="mt-2 text-3xl font-bold text-amber-700 dark:text-amber-400">
                {data.summary.unavailable_movies}
              </div>
            </div>

            {(data.summary.needs_recheck_movies ?? 0) > 0 && (
              <div
                data-testid="needs-recheck-count"
                className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-700 dark:bg-gray-800 sm:col-span-3"
              >
                <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                  🕓 Needs recheck (last seen, not recently verified)
                </span>
                <div className="mt-2 text-3xl font-bold text-gray-700 dark:text-gray-300">
                  {data.summary.needs_recheck_movies}
                </div>
              </div>
            )}
          </section>

          {/* Analytics Breakdown Grid */}
          <section className="grid grid-cols-1 gap-6 md:grid-cols-2">
            {/* Top Countries Breakdown */}
            <div className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-700 dark:bg-gray-800">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                Top Countries
              </h2>
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                Ranked by number of available movies
              </p>

              {data.summary.countries.length === 0 ? (
                <p className="mt-4 text-xs text-gray-400 dark:text-gray-500">
                  No availability data recorded for any country yet.
                </p>
              ) : (
                <div className="mt-4 divide-y divide-gray-100 dark:divide-gray-700/50">
                  {data.summary.countries.map((c, idx) => {
                    const pct = data.summary.total_movies > 0
                      ? Math.round((c.count / data.summary.total_movies) * 100)
                      : 0;
                    return (
                      <div key={c.country} className="flex items-center justify-between py-2 text-sm">
                        <div className="flex items-center gap-3">
                          <span className="w-5 text-xs font-bold text-gray-400">
                            #{idx + 1}
                          </span>
                          <span className="rounded bg-gray-100 px-2 py-0.5 font-mono text-xs font-bold text-gray-800 dark:bg-gray-700 dark:text-gray-200">
                            {c.country}
                          </span>
                        </div>
                        <div className="flex items-center gap-2 text-xs">
                          <span className="font-semibold text-gray-700 dark:text-gray-300">
                            {c.count} movie{c.count !== 1 ? "s" : ""}
                          </span>
                          <span className="text-gray-400">({pct}%)</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Top Services Breakdown */}
            <div className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-700 dark:bg-gray-800">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                Top Services
              </h2>
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                Ranked by number of available movies
              </p>

              {data.summary.services.length === 0 ? (
                <p className="mt-4 text-xs text-gray-400 dark:text-gray-500">
                  No availability data recorded for any service yet.
                </p>
              ) : (
                <div className="mt-4 divide-y divide-gray-100 dark:divide-gray-700/50">
                  {data.summary.services.map((s, idx) => {
                    const pct = data.summary.total_movies > 0
                      ? Math.round((s.count / data.summary.total_movies) * 100)
                      : 0;
                    return (
                      <div key={s.service} className="flex items-center justify-between py-2 text-sm">
                        <div className="flex items-center gap-3">
                          <span className="w-5 text-xs font-bold text-gray-400">
                            #{idx + 1}
                          </span>
                          <span className="font-medium text-gray-900 dark:text-gray-100">
                            {s.service}
                          </span>
                        </div>
                        <div className="flex items-center gap-2 text-xs">
                          <span className="font-semibold text-gray-700 dark:text-gray-300">
                            {s.count} movie{s.count !== 1 ? "s" : ""}
                          </span>
                          <span className="text-gray-400">({pct}%)</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </section>

          {/* Per-Movie Watchlist Report Table */}
          <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-700 dark:bg-gray-800">
            <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">
                Movie Availability Detail
              </h2>

              {/* Filters */}
              <div className="flex flex-wrap items-center gap-3">
                <input
                  type="text"
                  placeholder="Filter movies…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs focus:border-blue-500 focus:outline-none dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
                />

                <div className="inline-flex rounded-lg border border-gray-200 p-0.5 dark:border-gray-700">
                  <button
                    onClick={() => setStatusFilter("all")}
                    className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                      statusFilter === "all"
                        ? "bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900"
                        : "text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
                    }`}
                  >
                    All ({data.summary.total_movies})
                  </button>
                  <button
                    onClick={() => setStatusFilter("available")}
                    className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                      statusFilter === "available"
                        ? "bg-green-600 text-white dark:bg-green-500"
                        : "text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
                    }`}
                  >
                    Available ({data.summary.available_movies})
                  </button>
                  <button
                    onClick={() => setStatusFilter("unavailable")}
                    className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                      statusFilter === "unavailable"
                        ? "bg-amber-600 text-white dark:bg-amber-500"
                        : "text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
                    }`}
                  >
                    Unavailable ({data.summary.unavailable_movies})
                  </button>
                </div>
              </div>
            </div>

            {filteredReport.length === 0 ? (
              <p className="py-8 text-center text-sm text-gray-500 dark:text-gray-400">
                No matching movies found.
              </p>
            ) : (
              <div className="space-y-3">
                {filteredReport.map((row) => {
                  const isAvailable = row.available_countries.length > 0;
                  return (
                    <div
                      key={row.movie.id}
                      className={`flex flex-col justify-between rounded-lg border p-4 transition-colors sm:flex-row sm:items-center ${
                        isAvailable
                          ? "border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800"
                          : "border-amber-200 bg-amber-50/30 dark:border-amber-900/40 dark:bg-amber-950/10"
                      }`}
                    >
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-gray-900 dark:text-gray-100">
                            {row.movie.title}
                          </span>
                          {row.movie.year && (
                            <span className="text-xs text-gray-500 dark:text-gray-400">
                              ({row.movie.year})
                            </span>
                          )}
                          {!isAvailable && (
                            <span className="ml-2 inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800 dark:bg-amber-900/50 dark:text-amber-300">
                              Unavailable
                            </span>
                          )}
                        </div>

                        {/* Available Countries List */}
                        {isAvailable && (
                          <div className="mt-2 flex flex-wrap items-center gap-1 text-xs">
                            <span className="text-gray-500 dark:text-gray-400">Countries:</span>
                            {row.available_countries.map((c) => (
                              <span
                                key={c}
                                className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[11px] font-bold text-gray-700 dark:bg-gray-700 dark:text-gray-300"
                              >
                                {c}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>

                      {/* Best Offer Info */}
                      <div className="mt-3 sm:mt-0 sm:text-right">
                        {isAvailable ? (
                          <div className="text-xs">
                            <span className="font-semibold text-gray-900 dark:text-gray-100">
                              {row.best_service}
                            </span>
                            {row.best_country && (
                              <span className="ml-1.5 rounded bg-blue-50 px-1.5 py-0.5 font-mono font-semibold text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">
                                {row.best_country}
                              </span>
                            )}
                          </div>
                        ) : (
                          <span className="text-xs text-amber-600 dark:text-amber-400 font-medium">
                            No subscription offer
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
