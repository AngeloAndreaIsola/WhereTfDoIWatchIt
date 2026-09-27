"use client";

import React, { useState } from "react";
import AmbiguousReviewLink from "@/components/AmbiguousReviewLink";
import {
  GoogleInterestsImportResult,
  importGoogleInterests,
} from "@/lib/api";

export default function GoogleInterestsImport() {
  // URL import state
  const [urlImportUrls, setUrlImportUrls] = useState("");
  const [urlImportCookies, setUrlImportCookies] = useState("");
  const [urlImporting, setUrlImporting] = useState(false);
  const [urlImportError, setUrlImportError] = useState("");
  const [urlImportResult, setUrlImportResult] = useState<
    GoogleInterestsImportResult | null
  >(null);

  const clearUrlImport = () => {
    setUrlImportUrls("");
    setUrlImportCookies("");
    setUrlImportError("");
    setUrlImportResult(null);
  };

  const handleUrlImport = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!urlImportUrls.trim()) return;

    setUrlImporting(true);
    setUrlImportError("");
    setUrlImportResult(null);

    let cookies: Record<string, string> = {};
    if (urlImportCookies.trim()) {
      try {
        cookies = JSON.parse(urlImportCookies) as Record<string, string>;
      } catch {
        setUrlImportError("Cookies field must be valid JSON (e.g. {\"CONSENT\":\"...\"}).");
        setUrlImporting(false);
        return;
      }
    }

    const urls = urlImportUrls
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l.startsWith("http"));

    if (urls.length === 0) {
      setUrlImportError("No valid URLs found. Paste one or more Google Interests / Google TV list URLs, one per line.");
      setUrlImporting(false);
      return;
    }

    try {
      const data = await importGoogleInterests({ cookies, urls });
      setUrlImportResult(data);
    } catch (err: unknown) {
      setUrlImportError(
        err instanceof Error ? err.message : "Import from URL failed"
      );
    } finally {
      setUrlImporting(false);
    }
  };

  return (
    <>
      {/* ─── Import from URL ───────────────────────────────────────────────── */}
      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-xs dark:border-gray-700 dark:bg-gray-800">
        <h2 className="mb-2 text-lg font-semibold text-gray-900 dark:text-gray-100">
          Import from URL
        </h2>
        <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
          Paste one or more Google Interests / Google TV list page URLs (one per
          line). Interests and Google TV lists sit behind your Google account,
          so they need cookies — public Google Collections pages work without
          them. Multi-page lists import every page automatically.
        </p>

        <form onSubmit={handleUrlImport} className="space-y-4">
          <div className="space-y-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-300">
                Google Interests list URLs
              </label>
              <textarea
                value={urlImportUrls}
                onChange={(e) => setUrlImportUrls(e.target.value)}
                rows={4}
                placeholder="https://www.google.com/interests/saved/list/...
https://www.google.com/interests/saved/list/...&pageNumber=2"
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-300">
                Google cookies{" "}
                <span className="text-gray-400 dark:text-gray-500">(JSON, optional)</span>
              </label>
              <textarea
                value={urlImportCookies}
                onChange={(e) => setUrlImportCookies(e.target.value)}
                rows={3}
                placeholder='{"CONSENT": "...", "NID": "...", "SOCS": "..."} (leave empty for public pages)'
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-mono focus:border-blue-500 focus:outline-none dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
              />
              <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
                Get these from Chrome DevTools → Application → Cookies → www.google.com
              </p>
            </div>
          </div>

          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={clearUrlImport}
              className="rounded-lg px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
            >
              Clear
            </button>
            <button
              type="submit"
              disabled={
                !urlImportUrls.trim() || urlImporting
              }
              className="rounded-lg bg-green-600 px-5 py-2.5 text-sm font-medium text-white shadow-xs hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-green-500 dark:hover:bg-green-600"
            >
              {urlImporting ? "Importing…" : "Import from URL"}
            </button>
          </div>
        </form>

        {urlImportError && (
          <div className="mt-4 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
            {urlImportError}
          </div>
        )}
      </section>

      {urlImportResult && (
        <section className="space-y-4">
          <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800/50">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              Import Summary
            </h3>
            <div className="flex flex-wrap gap-4 text-xs font-medium">
              <span className="text-green-600 dark:text-green-400">
                Resolved Watchlist: {urlImportResult.resolved.length}
              </span>
              <span className="text-amber-600 dark:text-amber-400">
                Ambiguous: {urlImportResult.ambiguous.length}
              </span>
              <span className="text-gray-500 dark:text-gray-400">
                Skipped: {urlImportResult.skipped.length}
              </span>
            </div>
          </div>

          {urlImportResult.skipped.length > 0 && (
            <div className="rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-800 dark:bg-red-900/20">
              <h4 className="font-semibold text-red-800 dark:text-red-300">
                {urlImportResult.skipped.length} Skipped
              </h4>
              <p className="text-xs text-red-700 dark:text-red-400">
                These entries could not be imported. Expired or missing cookies
                are the usual cause for Google Interests / Google TV lists.
              </p>
              <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto text-xs text-red-700 dark:text-red-400">
                {urlImportResult.skipped.slice(0, 25).map((item, index) => (
                  <li key={index} className="break-words">
                    <span className="font-medium">
                      {item.title || item.url || "Entry"}
                    </span>
                    {item.reason ? ` — ${item.reason}` : ""}
                  </li>
                ))}
                {urlImportResult.skipped.length > 25 && (
                  <li>…and {urlImportResult.skipped.length - 25} more</li>
                )}
              </ul>
            </div>
          )}

          {urlImportResult.ambiguous.length > 0 && (
            <div className="flex items-center justify-between rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-700 dark:bg-amber-900/30">
              <div>
                <h4 className="font-semibold text-amber-800 dark:text-amber-300">
                  {urlImportResult.ambiguous.length} Ambiguous Match
                  {urlImportResult.ambiguous.length > 1 ? "es" : ""}
                </h4>
                <p className="text-xs text-amber-700 dark:text-amber-400">
                  Some titles could not be automatically matched to TMDb.
                </p>
              </div>

              <AmbiguousReviewLink
                items={urlImportResult.ambiguous}
                className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white shadow-xs hover:bg-amber-700 dark:bg-amber-500 dark:hover:bg-amber-600"
              >
                Review ambiguous matches
              </AmbiguousReviewLink>
            </div>
          )}
        </section>
      )}
    </>
  );
}
