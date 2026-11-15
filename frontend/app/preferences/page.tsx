"use client";

import { useEffect, useState } from "react";

interface RankingPreferences {
  version: number;
  source_priority: string[];
  prefer_official: boolean;
  prefer_local: boolean;
  prefer_entitled: boolean;
  provider_priority: string[];
}

const DEFAULT_RANKING_PREFS: RankingPreferences = {
  version: 1,
  source_priority: ["official", "local", "unofficial"],
  prefer_official: true,
  prefer_local: true,
  prefer_entitled: true,
  provider_priority: [],
};

const SOURCE_LABELS: Record<string, { label: string; desc: string }> = {
  official: {
    label: "Official / Major Providers",
    desc: "Licensed commercial and broadcaster streaming platforms",
  },
  local: {
    label: "Local Region",
    desc: "Offers available in your active country without requiring VPN",
  },
  unofficial: {
    label: "Unofficial / Scraped Sources",
    desc: "Public and community scraped catalogues",
  },
};

export default function PreferencesPage() {
  const [countries, setCountries] = useState("");
  const [rankingPrefs, setRankingPrefs] = useState<RankingPreferences>(DEFAULT_RANKING_PREFS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savedMessage, setSavedMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/preferences/")
      .then(async (response) => {
        if (!response.ok) throw new Error(`Failed to load preferences (${response.status})`);
        return response.json() as Promise<{
          candidate_countries?: string[];
          ranking_preferences?: Partial<RankingPreferences>;
        }>;
      })
      .then((data) => {
        setCountries((data.candidate_countries || []).join(", "));
        if (data.ranking_preferences) {
          setRankingPrefs({
            ...DEFAULT_RANKING_PREFS,
            ...data.ranking_preferences,
            source_priority: data.ranking_preferences.source_priority || DEFAULT_RANKING_PREFS.source_priority,
          });
        }
      })
      .catch((loadError: unknown) => {
        setError(loadError instanceof Error ? loadError.message : "Failed to load preferences");
      })
      .finally(() => setLoading(false));
  }, []);

  const movePriority = (index: number, direction: "up" | "down") => {
    const list = [...rankingPrefs.source_priority];
    const targetIndex = direction === "up" ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= list.length) return;
    const temp = list[index];
    list[index] = list[targetIndex];
    list[targetIndex] = temp;
    setRankingPrefs({ ...rankingPrefs, source_priority: list });
  };

  const handleSave = async (prefsToSave?: RankingPreferences) => {
    const payload = prefsToSave || rankingPrefs;
    setSaving(true);
    setError("");
    setSavedMessage("");
    try {
      const res = await fetch("/api/preferences/", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ranking_preferences: payload }),
      });
      if (!res.ok) {
        throw new Error(`Failed to save preferences (${res.status})`);
      }
      const data = await res.json();
      if (data.ranking_preferences) {
        setRankingPrefs({
          ...DEFAULT_RANKING_PREFS,
          ...data.ranking_preferences,
        });
      }
      setSavedMessage("Ranking preferences saved successfully.");
      setTimeout(() => setSavedMessage(""), 4000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save preferences");
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    setSaving(true);
    setError("");
    setSavedMessage("");
    try {
      const res = await fetch("/api/preferences/", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ranking_preferences: { reset: true } }),
      });
      if (!res.ok) {
        throw new Error(`Failed to reset preferences (${res.status})`);
      }
      const data = await res.json();
      setRankingPrefs({
        ...DEFAULT_RANKING_PREFS,
        ...(data.ranking_preferences || {}),
      });
      setSavedMessage("Reset to default ranking preferences.");
      setTimeout(() => setSavedMessage(""), 4000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to reset preferences");
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
      <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">Preferences</h1>
      <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
        Adjust how source legality, local access, and entitlement rank feasible offers across Catalog and Watchlist.
      </p>

      {error && (
        <div className="mt-6 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {error}
        </div>
      )}

      {savedMessage && (
        <div className="mt-6 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:border-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300">
          {savedMessage}
        </div>
      )}

      {/* Source Ranking Preferences */}
      <section className="mt-8 rounded-xl border border-gray-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-800/60 shadow-xs">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Source Ranking Priority</h2>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          Reorder how feasible offers are prioritized. Factual availability, language rules, and subscription access are always preserved.
        </p>

        {/* Priority reorder list */}
        <div className="mt-4 space-y-2">
          {rankingPrefs.source_priority.map((sourceId, index) => {
            const info = SOURCE_LABELS[sourceId] || { label: sourceId, desc: "" };
            return (
              <div
                key={sourceId}
                data-testid={`source-priority-item-${sourceId}`}
                className="flex items-center justify-between rounded-lg border border-gray-200 bg-gray-50/70 p-3 dark:border-gray-700 dark:bg-gray-800"
              >
                <div className="flex items-center gap-3">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-blue-100 text-xs font-bold text-blue-800 dark:bg-blue-900/50 dark:text-blue-300">
                    {index + 1}
                  </span>
                  <div>
                    <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      {info.label}
                    </div>
                    {info.desc && (
                      <div className="text-xs text-gray-500 dark:text-gray-400">
                        {info.desc}
                      </div>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    aria-label={`Move ${info.label} up`}
                    disabled={index === 0}
                    onClick={() => movePriority(index, "up")}
                    className="rounded p-1 text-gray-500 hover:bg-gray-200 disabled:opacity-30 dark:text-gray-400 dark:hover:bg-gray-700"
                  >
                    ▲
                  </button>
                  <button
                    type="button"
                    aria-label={`Move ${info.label} down`}
                    disabled={index === rankingPrefs.source_priority.length - 1}
                    onClick={() => movePriority(index, "down")}
                    className="rounded p-1 text-gray-500 hover:bg-gray-200 disabled:opacity-30 dark:text-gray-400 dark:hover:bg-gray-700"
                  >
                    ▼
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        {/* Independent booleans */}
        <div className="mt-6 space-y-3 border-t border-gray-100 pt-4 dark:border-gray-700">
          <label className="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
            <input
              type="checkbox"
              checked={rankingPrefs.prefer_official}
              onChange={(e) =>
                setRankingPrefs({ ...rankingPrefs, prefer_official: e.target.checked })
              }
              className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-700"
            />
            <span>
              <strong>Prefer official sources</strong> over scraped/community sources
            </span>
          </label>

          <label className="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
            <input
              type="checkbox"
              checked={rankingPrefs.prefer_local}
              onChange={(e) =>
                setRankingPrefs({ ...rankingPrefs, prefer_local: e.target.checked })
              }
              className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-700"
            />
            <span>
              <strong>Prefer local access</strong> before VPN-required regions
            </span>
          </label>

          <label className="flex items-center gap-3 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
            <input
              type="checkbox"
              checked={rankingPrefs.prefer_entitled}
              onChange={(e) =>
                setRankingPrefs({ ...rankingPrefs, prefer_entitled: e.target.checked })
              }
              className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-700"
            />
            <span>
              <strong>Prefer subscribed/free offers</strong> ahead of unowned services
            </span>
          </label>
        </div>

        {/* Action buttons */}
        <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 pt-4 dark:border-gray-700">
          <button
            type="button"
            onClick={handleReset}
            disabled={saving || loading}
            className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200 dark:hover:bg-gray-600 transition-all"
          >
            Reset to defaults
          </button>

          <button
            type="button"
            onClick={() => handleSave()}
            disabled={saving || loading}
            className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white shadow-xs hover:bg-blue-700 disabled:opacity-50 dark:bg-blue-500 dark:hover:bg-blue-600 transition-all"
          >
            {saving ? "Saving…" : "Save Ranking Preferences"}
          </button>
        </div>
      </section>

      {/* Candidate Countries Section */}
      <div className="mt-8 rounded-xl border border-gray-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-800/60 shadow-xs">
        <label htmlFor="candidate-countries" className="block text-sm font-semibold text-gray-900 dark:text-gray-100">
          Candidate Countries
        </label>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          Regions consulted for availability checks.
        </p>
        <input
          id="candidate-countries"
          type="text"
          value={countries}
          readOnly
          className="mt-3 w-full rounded-lg border border-gray-300 bg-gray-100 px-4 py-2 text-sm text-gray-600 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-300"
        />
      </div>
    </main>
  );
}
