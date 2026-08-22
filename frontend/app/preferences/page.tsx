"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";

interface PreferencesData {
  audio_languages: string[];
  subtitle_languages: string[];
  candidate_countries: string[];
}

function formatArrayToCommaString(arr: string[] | undefined): string {
  if (!arr || !Array.isArray(arr)) return "";
  return arr.join(", ");
}

export default function PreferencesPage() {
  const [audioLangs, setAudioLangs] = useState("");
  const [subtitleLangs, setSubtitleLangs] = useState("");
  const [countries, setCountries] = useState("");
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState("");
  /* --- Fetch Preferences (read-only, server-side managed) --- */
  const fetchPreferences = useCallback(async () => {
    setLoading(true);
    setFetchError("");
    try {
      const res = await fetch("/api/preferences/");
      if (!res.ok) {
        throw new Error(`Failed to load preferences (${res.status})`);
      }
      const data: PreferencesData = await res.json();
      setAudioLangs(formatArrayToCommaString(data.audio_languages));
      setSubtitleLangs(formatArrayToCommaString(data.subtitle_languages));
      setCountries(formatArrayToCommaString(data.candidate_countries));
    } catch (err: unknown) {
      setFetchError(err instanceof Error ? err.message : "Failed to load preferences");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPreferences();
  }, [fetchPreferences]);

  return (
    <main className="mx-auto max-w-2xl px-4 py-10 sm:px-6">
      {/* Top navigation link */}
      <div className="mb-6">
        <Link href="/" className="text-sm text-blue-600 hover:underline dark:text-blue-400">
          ← Back to browse
        </Link>
      </div>

      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
          Preferences
        </h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Configure your streaming availability and ranking preferences.
        </p>
      </div>

      {/* Coming soon banner */}
      <div className="mb-6 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-300">
        <span className="font-medium">Coming soon</span> — Preferences are managed
        by the server for now. Language preferences are hardcoded to{" "}
        <code className="font-mono">JA, EN</code> (audio) and{" "}
        <code className="font-mono">EN, JA</code> (subtitles).
      </div>

      {/* Loading state */}
      {loading && (
        <div className="flex items-center gap-2 py-8 text-sm text-gray-500 dark:text-gray-400">
          <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
          </svg>
          Loading preferences…
        </div>
      )}

      {/* Initial load error */}
      {fetchError && (
        <div className="mb-6 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {fetchError}
        </div>
      )}

      {/* Greyed-out form */}
      {!loading && !fetchError && (
        <div className="space-y-6 opacity-60" title="Coming soon — preferences are managed by the server for now">
          {/* Audio Languages */}
          <div>
            <label htmlFor="audio-languages" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Audio Languages
            </label>
            <p className="mb-1.5 text-xs text-gray-500 dark:text-gray-400">
              Comma-separated canonical language codes (e.g. <code className="font-mono">EN, IT, FR</code>)
            </p>
            <input
              id="audio-languages"
              type="text"
              placeholder="EN, IT, FR"
              value={audioLangs}
              onChange={(e) => setAudioLangs(e.target.value)}
              disabled
              className="w-full rounded-lg border border-gray-300 bg-gray-100 px-4 py-2.5 text-sm shadow-sm placeholder-gray-400 opacity-60 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
            />
          </div>

          {/* Subtitle Languages */}
          <div>
            <label htmlFor="subtitle-languages" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Subtitle Languages
            </label>
            <p className="mb-1.5 text-xs text-gray-500 dark:text-gray-400">
              Comma-separated canonical language codes (e.g. <code className="font-mono">EN, IT</code>)
            </p>
            <input
              id="subtitle-languages"
              type="text"
              placeholder="EN, IT"
              value={subtitleLangs}
              onChange={(e) => setSubtitleLangs(e.target.value)}
              disabled
              className="w-full rounded-lg border border-gray-300 bg-gray-100 px-4 py-2.5 text-sm shadow-sm placeholder-gray-400 opacity-60 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
            />
          </div>

          {/* Candidate Countries */}
          <div>
            <label htmlFor="candidate-countries" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Candidate Countries
            </label>
            <p className="mb-1.5 text-xs text-gray-500 dark:text-gray-400">
              Comma-separated ISO 3166-1 country codes (e.g. <code className="font-mono">us, gb, nl, it</code>)
            </p>
            <input
              id="candidate-countries"
              type="text"
              placeholder="us, gb, nl, it"
              value={countries}
              onChange={(e) => setCountries(e.target.value)}
              disabled
              className="w-full rounded-lg border border-gray-300 bg-gray-100 px-4 py-2.5 text-sm shadow-sm placeholder-gray-400 opacity-60 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
            />
          </div>

          {/* Subscriptions section */}
          <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm dark:border-gray-700 dark:bg-gray-800/50 opacity-60">
            <h3 className="font-medium text-gray-700 dark:text-gray-300">Subscriptions</h3>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Manage your active streaming services on the{" "}
              <Link
                href="/subscriptions"
                className="text-blue-600 hover:underline dark:text-blue-400"
              >
                Subscriptions page
              </Link>
              .
            </p>
          </div>

          {/* Save button (disabled) */}
          <div className="pt-2">
            <button
              type="submit"
              disabled
              className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm opacity-60"
            >
              Save Preferences
            </button>
          </div>
        </div>
      )}
    </main>
  );
}
