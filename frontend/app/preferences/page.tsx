"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import ViewerLanguageBuilder, { ViewersRuleNode } from "@/components/ViewerLanguageBuilder";

interface PreferencesData {
  audio_languages: string[];
  subtitle_languages: string[];
  candidate_countries: string[];
  language_rule?: ViewersRuleNode | unknown;
}

function formatArrayToCommaString(arr: string[] | undefined): string {
  if (!arr || !Array.isArray(arr)) return "";
  return arr.join(", ");
}

function extractViewerLanguages(rule: ViewersRuleNode): string[] {
  if (!rule || !Array.isArray(rule.viewers)) return [];
  const seen = new Set<string>();
  const res: string[] = [];
  for (const v of rule.viewers) {
    if (Array.isArray(v.languages)) {
      for (const lang of v.languages) {
        const clean = lang.trim().toUpperCase();
        if (clean && !seen.has(clean)) {
          seen.add(clean);
          res.push(clean);
        }
      }
    }
  }
  return res;
}

const DEFAULT_VIEWERS_RULE: ViewersRuleNode = {
  type: "viewers",
  viewers: [
    { name: "Andrea", languages: ["EN", "IT"] },
    { name: "Mayu", languages: ["JA"] },
  ],
};

export default function PreferencesPage() {
  const [countries, setCountries] = useState("");
  const [viewersRule, setViewersRule] = useState<ViewersRuleNode>(DEFAULT_VIEWERS_RULE);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [fetchError, setFetchError] = useState("");
  const [saveSuccess, setSaveSuccess] = useState(false);

  const fetchPreferences = useCallback(async () => {
    setLoading(true);
    setFetchError("");
    try {
      const res = await fetch("/api/preferences/");
      if (!res.ok) {
        throw new Error(`Failed to load preferences (${res.status})`);
      }
      const data: PreferencesData = await res.json();
      setCountries(formatArrayToCommaString(data.candidate_countries));
      if (data.language_rule && typeof data.language_rule === "object" && (data.language_rule as ViewersRuleNode).type === "viewers") {
        setViewersRule(data.language_rule as ViewersRuleNode);
      }
    } catch (err: unknown) {
      setFetchError(err instanceof Error ? err.message : "Failed to load preferences");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPreferences();
  }, [fetchPreferences]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSaveSuccess(false);
    setFetchError("");

    const viewerLangs = extractViewerLanguages(viewersRule);
    const parsedCountries = countries
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

    try {
      const res = await fetch("/api/preferences/", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          audio_languages: viewerLangs,
          subtitle_languages: viewerLangs,
          candidate_countries: parsedCountries,
          language_rule: viewersRule,
        }),
      });

      if (!res.ok) {
        throw new Error(`Failed to save preferences (${res.status})`);
      }

      const updated: PreferencesData = await res.json();
      setCountries(formatArrayToCommaString(updated.candidate_countries));
      if (updated.language_rule && typeof updated.language_rule === "object" && (updated.language_rule as ViewersRuleNode).type === "viewers") {
        setViewersRule(updated.language_rule as ViewersRuleNode);
      }
      setSaveSuccess(true);
    } catch (err: unknown) {
      setFetchError(err instanceof Error ? err.message : "Failed to save preferences");
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
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
          Configure your boolean language rules, language preferences, and candidate countries.
        </p>
      </div>

      {/* Link banner to Subscriptions */}
      <div className="mb-8 rounded-xl border border-blue-200 bg-blue-50/50 p-4 dark:border-blue-900/50 dark:bg-blue-950/20">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-blue-900 dark:text-blue-200">
              Streaming Subscriptions
            </h3>
            <p className="text-xs text-blue-700 dark:text-blue-300">
              Manage major streaming providers and import custom scraper sources on the Subscriptions page.
            </p>
          </div>
          <Link
            href="/subscriptions"
            className="rounded-lg bg-blue-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-500 dark:bg-blue-500 dark:hover:bg-blue-400"
          >
            Manage Subscriptions →
          </Link>
        </div>
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

      {/* Save success banner */}
      {saveSuccess && (
        <div className="mb-6 rounded-lg border border-green-300 bg-green-50 px-4 py-3 text-sm text-green-700 dark:border-green-700 dark:bg-green-900/30 dark:text-green-400">
          Preferences saved successfully!
        </div>
      )}

      {/* Interactive Preferences Form */}
      {!loading && !fetchError && (
        <form onSubmit={handleSave} className="space-y-8">
          {/* V2 Viewer Language Rule Section */}
          <div>
            <ViewerLanguageBuilder rule={viewersRule} onChange={setViewersRule} />
          </div>

          <hr className="border-gray-200 dark:border-gray-800" />

          {/* Candidate Countries Section */}
          <div className="space-y-4">
            <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
              Candidate VPN Regions & Countries
            </h2>

            <div>
              <label htmlFor="candidate-countries" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                Candidate Countries
              </label>
              <p className="mb-1.5 text-xs text-gray-500 dark:text-gray-400">
                Comma-separated ISO 3166-1 country codes (e.g. <code className="font-mono">us, gb, nl, it, th</code>)
              </p>
              <input
                id="candidate-countries"
                type="text"
                placeholder="us, gb, nl, it, th"
                value={countries}
                onChange={(e) => setCountries(e.target.value)}
                className="w-full rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm shadow-sm placeholder-gray-400 focus:border-blue-500 focus:outline-none dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
              />
            </div>
          </div>

          {/* Save button */}
          <div className="pt-2">
            <button
              type="submit"
              disabled={saving}
              className="rounded-lg bg-blue-600 px-6 py-2.5 text-sm font-medium text-white shadow-sm hover:bg-blue-500 disabled:opacity-50 dark:bg-blue-500 dark:hover:bg-blue-400"
            >
              {saving ? "Saving Preferences…" : "Save Preferences"}
            </button>
          </div>
        </form>
      )}
    </main>
  );
}
