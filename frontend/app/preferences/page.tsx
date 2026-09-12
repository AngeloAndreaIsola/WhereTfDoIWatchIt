"use client";

import { useEffect, useState } from "react";

export default function PreferencesPage() {
  const [countries, setCountries] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/preferences/")
      .then(async (response) => {
        if (!response.ok) throw new Error(`Failed to load preferences (${response.status})`);
        return response.json() as Promise<{ candidate_countries?: string[] }>;
      })
      .then((data) => setCountries((data.candidate_countries || []).join(", ")))
      .catch((loadError: unknown) => {
        setError(loadError instanceof Error ? loadError.message : "Failed to load preferences");
      });
  }, []);

  return (
    <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
      <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">Preferences</h1>
      <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
        Viewer language preferences are now available in the collapsible filters on Catalog and Watchlist.
      </p>
      {error ? (
        <div className="mt-6 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {error}
        </div>
      ) : (
        <div className="mt-6">
          <label htmlFor="candidate-countries" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
            Candidate Countries
          </label>
          <input
            id="candidate-countries"
            type="text"
            value={countries}
            readOnly
            className="mt-1 w-full rounded-lg border border-gray-300 bg-gray-100 px-4 py-2 text-sm text-gray-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-400"
          />
        </div>
      )}
    </main>
  );
}
