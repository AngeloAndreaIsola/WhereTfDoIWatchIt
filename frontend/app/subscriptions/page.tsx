"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import ProviderGrid, { ProviderItem } from "@/components/ProviderGrid";

export default function SubscriptionsPage() {
  const [providers, setProviders] = useState<ProviderItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState("");

  const [importUrl, setImportUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const [importSuccess, setImportSuccess] = useState("");

  const [actionSlug, setActionSlug] = useState<string | null>(null);

  const fetchProviders = useCallback(async () => {
    setLoading(true);
    setFetchError("");
    try {
      const res = await fetch("/api/providers/");
      if (!res.ok) {
        throw new Error(`Failed to load providers (${res.status})`);
      }
      const data: ProviderItem[] = await res.json();
      setProviders(data);
    } catch (err: unknown) {
      setFetchError(err instanceof Error ? err.message : "Failed to load providers");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  const handleToggleProvider = async (slug: string) => {
    try {
      const res = await fetch(`/api/providers/${slug}/toggle/`, { method: "POST" });
      if (res.ok) {
        await fetchProviders();
      }
    } catch (err) {
      console.error(`Failed to toggle provider ${slug}:`, err);
    }
  };

  const handleRunScraper = async (slug: string) => {
    setActionSlug(slug);
    try {
      const res = await fetch("/api/scrapers/run/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider_slug: slug, query: "Popular" }),
      });
      if (res.ok) {
        await fetchProviders();
      }
    } catch (err) {
      console.error(`Failed to run scraper for ${slug}:`, err);
    } finally {
      setActionSlug(null);
    }
  };

  const handleDeleteProvider = async (slug: string) => {
    if (!confirm(`Are you sure you want to remove source '${slug}'?`)) return;
    setActionSlug(slug);
    try {
      const res = await fetch(`/api/providers/${slug}/`, { method: "DELETE" });
      if (res.ok || res.status === 204) {
        await fetchProviders();
      }
    } catch (err) {
      console.error(`Failed to delete provider ${slug}:`, err);
    } finally {
      setActionSlug(null);
    }
  };

  const handleImportUrl = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!importUrl.trim() || importing) return;

    setImporting(true);
    setImportError("");
    setImportSuccess("");

    try {
      const res = await fetch("/api/scrapers/import-url/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: importUrl.trim() }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || "Failed to import URL");
      }

      setImportSuccess(
        `Successfully imported and registered source '${data.provider?.name || "Scraper Source"}'! Found ${data.count || 0} item(s).`
      );
      setImportUrl("");
      await fetchProviders();
    } catch (err: unknown) {
      setImportError(err instanceof Error ? err.message : "Import failed");
    } finally {
      setImporting(false);
    }
  };

  const majorProviders = providers.filter((p) => p.is_major);
  const scraperProviders = providers.filter((p) => !p.is_major && p.category === "scraper");

  return (
    <main className="mx-auto max-w-4xl px-4 py-10 sm:px-6">
      {/* Top navigation link */}
      <div className="mb-6">
        <Link href="/preferences" className="text-sm text-blue-600 hover:underline dark:text-blue-400">
          ← Back to preferences
        </Link>
      </div>

      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
          Subscriptions & Sources
        </h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Manage major streaming service subscriptions and import custom scraper sources.
        </p>
      </div>

      {/* Loading state */}
      {loading && (
        <div className="flex items-center gap-2 py-8 text-sm text-gray-500 dark:text-gray-400">
          <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
          </svg>
          Loading subscriptions…
        </div>
      )}

      {/* Initial load error */}
      {fetchError && (
        <div className="mb-6 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {fetchError}
        </div>
      )}

      {!loading && !fetchError && (
        <div className="space-y-10">
          {/* Section 1: Fixed Major Streaming Providers (Top) */}
          <section className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-800 dark:bg-gray-900">
            <div className="mb-4">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                Major Streaming Providers
              </h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Fixed canonical catalog. Click a provider card to toggle your active subscription status.
              </p>
            </div>
            <ProviderGrid
              providers={majorProviders}
              onToggle={handleToggleProvider}
              loading={loading}
            />
          </section>

          {/* Section 2: Imported Small / Scraper Providers */}
          <section className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-800 dark:bg-gray-900">
            <div className="mb-4">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                Imported Small Providers & Sources
              </h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Import custom sources by URL (e.g., KissKH, HydraHD, AnimeWorld, or any custom domain) to trigger automated scrapers.
              </p>
            </div>

            {/* URL Import Form */}
            <form onSubmit={handleImportUrl} className="mb-6 flex gap-2">
              <input
                type="url"
                required
                placeholder="https://example-source.com/watch/item..."
                value={importUrl}
                onChange={(e) => setImportUrl(e.target.value)}
                className="flex-1 rounded-lg border border-gray-300 px-3.5 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
              />
              <button
                type="submit"
                disabled={importing}
                className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-500 disabled:opacity-50 dark:bg-blue-500 dark:hover:bg-blue-400"
              >
                {importing ? "Importing…" : "Import URL & Scrape"}
              </button>
            </form>

            {importError && (
              <div className="mb-4 rounded-lg border border-red-300 bg-red-50 px-4 py-2.5 text-xs text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
                {importError}
              </div>
            )}

            {importSuccess && (
              <div className="mb-4 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-2.5 text-xs text-emerald-800 dark:border-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300">
                {importSuccess}
              </div>
            )}

            {/* Small / Scraper Providers List */}
            <div className="space-y-3">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                Imported Sources ({scraperProviders.length})
              </h3>

              {scraperProviders.length === 0 ? (
                <p className="text-xs text-gray-400 italic">No custom sources imported yet.</p>
              ) : (
                <div className="divide-y divide-gray-100 rounded-lg border border-gray-100 dark:divide-gray-800 dark:border-gray-800">
                  {scraperProviders.map((provider) => {
                    const isSubscribed = provider.is_subscribed;
                    const isPending = actionSlug === provider.slug;

                    return (
                      <div
                        key={provider.slug}
                        className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between"
                      >
                        <div>
                          <div className="flex items-center gap-2">
                            <p className="text-sm font-semibold text-gray-800 dark:text-gray-200">
                              {provider.name}
                            </p>
                            <span
                              className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                                isSubscribed
                                  ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-300"
                                  : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"
                              }`}
                            >
                              {isSubscribed ? "Active" : "Inactive"}
                            </span>
                          </div>
                          <p className="mt-0.5 text-[11px] text-gray-400">
                            Available items: <strong className="text-gray-700 dark:text-gray-300">{provider.scraped_content_count || 0}</strong> • Last run:{" "}
                            {provider.last_scraped_at
                              ? new Date(provider.last_scraped_at).toLocaleString()
                              : "Never"}
                          </p>
                        </div>

                        {/* Action buttons */}
                        <div className="flex items-center gap-2">
                          {/* Enable / Render Inactive */}
                          <button
                            type="button"
                            onClick={() => handleToggleProvider(provider.slug)}
                            disabled={isPending}
                            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors ${
                              isSubscribed
                                ? "bg-amber-100 text-amber-800 hover:bg-amber-200 dark:bg-amber-900/40 dark:text-amber-300"
                                : "bg-emerald-100 text-emerald-800 hover:bg-emerald-200 dark:bg-emerald-900/40 dark:text-emerald-300"
                            }`}
                          >
                            {isSubscribed ? "Render Inactive" : "Enable"}
                          </button>

                          {/* Run Scraper */}
                          <button
                            type="button"
                            onClick={() => handleRunScraper(provider.slug)}
                            disabled={isPending}
                            className="rounded-lg bg-blue-100 px-3 py-1.5 text-xs font-semibold text-blue-800 hover:bg-blue-200 dark:bg-blue-900/40 dark:text-blue-300"
                          >
                            {isPending ? "Running…" : "Run Scraper"}
                          </button>

                          {/* Remove / Delete */}
                          <button
                            type="button"
                            onClick={() => handleDeleteProvider(provider.slug)}
                            disabled={isPending}
                            className="rounded-lg bg-red-100 px-3 py-1.5 text-xs font-semibold text-red-800 hover:bg-red-200 dark:bg-red-900/40 dark:text-red-300"
                          >
                            Remove
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
