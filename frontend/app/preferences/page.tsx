"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";

interface PreferencesData {
  audio_languages: string[];
  subtitle_languages: string[];
  candidate_countries: string[];
}

function parseCommaSeparated(input: string): string[] {
  const items = input
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item.length > 0);

  // Deduplicate while preserving order
  return Array.from(new Set(items));
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
  const [saving, setSaving] = useState(false);
  const [fetchError, setFetchError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [successMessage, setSuccessMessage] = useState("");

  /* --- Fetch Preferences --- */
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

  /* --- Save Preferences --- */
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSaveError("");
    setSuccessMessage("");

    const payload: PreferencesData = {
      audio_languages: parseCommaSeparated(audioLangs),
      subtitle_languages: parseCommaSeparated(subtitleLangs),
      candidate_countries: parseCommaSeparated(countries),
    };

    try {
      const res = await fetch("/api/preferences/", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || body.error || `Failed to save preferences (${res.status})`);
      }

      const updatedData: PreferencesData = await res.json();
      setAudioLangs(formatArrayToCommaString(updatedData.audio_languages));
      setSubtitleLangs(formatArrayToCommaString(updatedData.subtitle_languages));
      setCountries(formatArrayToCommaString(updatedData.candidate_countries));
      setSuccessMessage("Preferences saved successfully.");
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : "Failed to save preferences");
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="mx-auto max-w-2xl px-4 py-10 sm:px-6">
      {/* Top navigation link */}
      <div className="mb-6">
        <Link href="/" className="text-sm text-blue-600 hover:underline dark:text-blue-400">
          ← Back to search
        </Link>
      </div>

      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">Preferences</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Configure your streaming availability and ranking preferences.
        </p>
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

      {/* Main form */}
      {!loading && !fetchError && (
        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Success banner */}
          {successMessage && (
            <div className="rounded-lg border border-green-300 bg-green-50 px-4 py-3 text-sm text-green-800 dark:border-green-700 dark:bg-green-900/30 dark:text-green-400">
              {successMessage}
            </div>
          )}

          {/* Save error banner */}
          {saveError && (
            <div className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
              {saveError}
            </div>
          )}

          {/* Audio Languages */}
          <div>
            <label htmlFor="audio-languages" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Audio Languages
            </label>
            <p className="mb-1.5 text-xs text-gray-500 dark:text-gray-400">
              Comma-separated ISO 639-1 language codes (e.g. <code className="font-mono">en, it, fr</code>)
            </p>
            <input
              id="audio-languages"
              type="text"
              placeholder="en, it, fr"
              value={audioLangs}
              onChange={(e) => setAudioLangs(e.target.value)}
              className="w-full rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm
                         placeholder-gray-400 shadow-sm
                         focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20
                         dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100
                         dark:placeholder-gray-500 dark:focus:border-blue-400"
            />
          </div>

          {/* Subtitle Languages */}
          <div>
            <label htmlFor="subtitle-languages" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Subtitle Languages
            </label>
            <p className="mb-1.5 text-xs text-gray-500 dark:text-gray-400">
              Comma-separated ISO 639-1 language codes (e.g. <code className="font-mono">en, it</code>)
            </p>
            <input
              id="subtitle-languages"
              type="text"
              placeholder="en, it"
              value={subtitleLangs}
              onChange={(e) => setSubtitleLangs(e.target.value)}
              className="w-full rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm
                         placeholder-gray-400 shadow-sm
                         focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20
                         dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100
                         dark:placeholder-gray-500 dark:focus:border-blue-400"
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
              className="w-full rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm
                         placeholder-gray-400 shadow-sm
                         focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20
                         dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100
                         dark:placeholder-gray-500 dark:focus:border-blue-400"
            />
          </div>

          {/* Subscriptions section */}
          <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm dark:border-gray-700 dark:bg-gray-800/50">
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

          {/* Submit button */}
          <div className="pt-2">
            <button
              type="submit"
              disabled={saving}
              className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm
                         hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50
                         dark:bg-blue-500 dark:hover:bg-blue-600"
            >
              {saving ? "Saving…" : "Save Preferences"}
            </button>
          </div>
        </form>
      )}
    </main>
  );
}
