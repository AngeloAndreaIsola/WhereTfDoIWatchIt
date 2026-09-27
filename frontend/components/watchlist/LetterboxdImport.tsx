"use client";

import { useState } from "react";
import AmbiguousReviewLink from "@/components/AmbiguousReviewLink";
import {
  importLetterboxdUrl,
  LetterboxdImportType,
  LetterboxdUrlImportResult,
} from "@/lib/api";

export default function LetterboxdImport() {
  // Letterboxd profile/list URL import state
  const [letterboxdUrl, setLetterboxdUrl] = useState("");
  const [letterboxdImporting, setLetterboxdImporting] = useState(false);
  const [letterboxdError, setLetterboxdError] = useState("");
  const [letterboxdResults, setLetterboxdResults] = useState<
    LetterboxdUrlImportResult[]
  >([]);

  const handleLetterboxdImport = async (types: LetterboxdImportType[]) => {
    const url = letterboxdUrl.trim();
    if (!url || types.length === 0 || letterboxdImporting) return;

    setLetterboxdImporting(true);
    setLetterboxdError("");
    setLetterboxdResults([]);

    try {
      const results: LetterboxdUrlImportResult[] = [];
      // Sequential on purpose: one import at a time keeps TMDb/Letterboxd
      // usage predictable and surfaces the first failure clearly.
      for (const importType of types) {
        results.push(await importLetterboxdUrl({ url, type: importType }));
      }
      setLetterboxdResults(results);
    } catch (err: unknown) {
      setLetterboxdError(
        err instanceof Error ? err.message : "Letterboxd import failed"
      );
    } finally {
      setLetterboxdImporting(false);
    }
  };

  return (
    <>
      {/* ─── Import from Letterboxd URL ───────────────────────────────────── */}
      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-xs dark:border-gray-700 dark:bg-gray-800">
        <h2 className="mb-2 text-lg font-semibold text-gray-900 dark:text-gray-100">
          Import from a Letterboxd URL
        </h2>
        <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
          Paste a public Letterboxd profile or list URL — no Letterboxd login or
          cookies needed. The profile must be public. This copies the list once;
          it is not a live sync.
        </p>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleLetterboxdImport(["watchlist"]);
          }}
          className="space-y-4"
        >
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-300">
              Letterboxd profile or list URL
            </label>
            <input
              type="text"
              inputMode="url"
              value={letterboxdUrl}
              onChange={(e) => {
                setLetterboxdUrl(e.target.value);
                setLetterboxdError("");
              }}
              placeholder="https://letterboxd.com/username/"
              className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
            />
            <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
              Works with https://letterboxd.com/username/,
              .../username/watchlist/ and .../username/films/watched/
            </p>
          </div>

          <div className="flex flex-wrap justify-end gap-3">
            <button
              type="button"
              onClick={() => handleLetterboxdImport(["watchlist"])}
              disabled={!letterboxdUrl.trim() || letterboxdImporting}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-xs hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-blue-500 dark:hover:bg-blue-600"
            >
              {letterboxdImporting ? "Importing…" : "Import Watchlist"}
            </button>
            <button
              type="button"
              onClick={() => handleLetterboxdImport(["watched"])}
              disabled={!letterboxdUrl.trim() || letterboxdImporting}
              className="rounded-lg bg-purple-600 px-4 py-2 text-sm font-medium text-white shadow-xs hover:bg-purple-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-purple-500 dark:hover:bg-purple-600"
            >
              Import Watched
            </button>
            <button
              type="button"
              onClick={() => handleLetterboxdImport(["watchlist", "watched"])}
              disabled={!letterboxdUrl.trim() || letterboxdImporting}
              className="rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white shadow-xs hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-green-500 dark:hover:bg-green-600"
            >
              Import Both
            </button>
          </div>
        </form>

        {letterboxdError && (
          <div className="mt-4 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
            {letterboxdError}
          </div>
        )}

        {letterboxdResults.length > 0 &&
          letterboxdResults.every((r) => r.rows_found === 0) && (
            <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-700 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
              No entries found at this URL — check that the profile is public and
              that the list contains films.
            </div>
          )}
      </section>

      {letterboxdResults.length > 0 &&
        letterboxdResults.some((r) => r.rows_found > 0) &&
        (() => {
          const ambiguousItems = letterboxdResults.flatMap((r) => r.ambiguous);
          return (
            <section className="space-y-4">
              <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800/50">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                  Letterboxd Import Summary
                </h3>
                {letterboxdResults.map((result) => (
                  <div
                    key={`${result.username}-${result.import_type}`}
                    className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs font-medium last:mb-0"
                  >
                    <span className="font-semibold text-gray-700 dark:text-gray-200">
                      {result.username} · {result.import_type}
                    </span>
                    <span className="text-gray-500 dark:text-gray-400">
                      Pages: {result.pages_fetched}
                    </span>
                    <span className="text-gray-500 dark:text-gray-400">
                      Rows: {result.rows_found}
                    </span>
                    <span className="text-green-600 dark:text-green-400">
                      Resolved: {result.resolved.length}
                    </span>
                    <span className="text-amber-600 dark:text-amber-400">
                      Ambiguous: {result.ambiguous.length}
                    </span>
                    <span className="text-gray-500 dark:text-gray-400">
                      Skipped: {result.skipped.length}
                    </span>
                    {result.import_type === "watched" && (
                      <span className="text-purple-600 dark:text-purple-400">
                        Marked watched: {result.watched_count}
                      </span>
                    )}
                  </div>
                ))}
              </div>

              {ambiguousItems.length > 0 && (
                <div className="flex items-center justify-between rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-700 dark:bg-amber-900/30">
                  <div>
                    <h4 className="font-semibold text-amber-800 dark:text-amber-300">
                      {ambiguousItems.length} Ambiguous Match
                      {ambiguousItems.length > 1 ? "es" : ""}
                    </h4>
                    <p className="text-xs text-amber-700 dark:text-amber-400">
                      Some titles could not be automatically matched to TMDb.
                    </p>
                  </div>

                  <AmbiguousReviewLink
                    items={ambiguousItems}
                    className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white shadow-xs hover:bg-amber-700 dark:bg-amber-500 dark:hover:bg-amber-600"
                  >
                    Review ambiguous matches
                  </AmbiguousReviewLink>
                </div>
              )}
            </section>
          );
        })()}
    </>
  );
}
