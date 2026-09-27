"use client";

import React, { useState } from "react";

type ExportOption = {
  key: string;
  title: string;
  description: string;
  buttonLabel: string;
  path: string;
  filename: string;
  accent: string;
};

const EXPORT_OPTIONS: ExportOption[] = [
  {
    key: "watchlist",
    title: "Watchlist",
    description: "Download all movies currently in your watchlist.",
    buttonLabel: "Export Watchlist",
    path: "/api/export/watchlist/",
    filename: "wheretfdoiwatchit-watchlist.csv",
    accent: "blue",
  },
  {
    key: "watched",
    title: "Watched",
    description: "Download all movies marked as watched.",
    buttonLabel: "Export Watched",
    path: "/api/export/watched/",
    filename: "wheretfdoiwatchit-watched.csv",
    accent: "purple",
  },
  {
    key: "both",
    title: "Everything",
    description: "Download both watchlist and watched movies in one backup file.",
    buttonLabel: "Export Both",
    path: "/api/export/",
    filename: "wheretfdoiwatchit-export.csv",
    accent: "green",
  },
];

const ACCENT_CLASSES: Record<string, string> = {
  blue: "bg-blue-600 hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600",
  purple:
    "bg-purple-600 hover:bg-purple-700 dark:bg-purple-500 dark:hover:bg-purple-600",
  green:
    "bg-green-600 hover:bg-green-700 dark:bg-green-500 dark:hover:bg-green-600",
};

export default function ExportSection() {
  const [exporting, setExporting] = useState<string | null>(null);
  const [exportError, setExportError] = useState("");

  const handleExport = async (option: ExportOption) => {
    if (exporting) return;
    setExporting(option.key);
    setExportError("");
    try {
      const res = await fetch(option.path);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error ?? `Export failed (${res.status})`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = option.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setExportError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(null);
    }
  };

  return (
    <>
      {/* ─── Export ────────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
          Export
        </h2>
        <div className="h-px flex-1 bg-gray-200 dark:bg-gray-700" />
      </div>

      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-xs dark:border-gray-700 dark:bg-gray-800">
        <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
          Download your collection as a CSV backup. The file can be re-uploaded
          on this page at any time — Watchlist and Watched status are preserved.
        </p>

        <div className="space-y-4">
          {EXPORT_OPTIONS.map((option) => (
            <div
              key={option.key}
              className="flex flex-col gap-3 rounded-lg border border-gray-200 p-4 dark:border-gray-700 sm:flex-row sm:items-center sm:justify-between"
            >
              <div>
                <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                  {option.title}
                </h3>
                <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                  {option.description}
                </p>
              </div>
              <button
                type="button"
                onClick={() => handleExport(option)}
                disabled={exporting !== null}
                className={`inline-flex shrink-0 items-center justify-center rounded-lg px-4 py-2 text-sm font-medium text-white shadow-xs disabled:cursor-not-allowed disabled:opacity-50 ${
                  ACCENT_CLASSES[option.accent]
                }`}
              >
                {exporting === option.key ? "Exporting…" : option.buttonLabel}
              </button>
            </div>
          ))}
        </div>

        {exportError && (
          <div className="mt-4 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
            {exportError}
          </div>
        )}
      </section>
    </>
  );
}
