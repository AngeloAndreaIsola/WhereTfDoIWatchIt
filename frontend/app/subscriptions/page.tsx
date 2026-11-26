"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import ProviderGrid, { ProviderItem } from "@/components/ProviderGrid";
import CountrySelect from "@/components/CountrySelect";
import {
  fetchProviderMarketCatalogue,
  refreshProvider,
  saveMarketSelection,
  setProviderAccessMode,
  syncProviderCatalogue,
  ProviderAccessMode,
  MarketSelectionInput,
} from "@/lib/api";

/** Fast cadence for re-reading the list right after a press. */
const POLL_INTERVAL_MS = 2500;
/** Slower cadence once a run has been going for a while — catalogue syncs take minutes. */
const SLOW_POLL_INTERVAL_MS = 10000;
/** Never poll forever: ~11 minutes of "still refreshing" is treated as stuck. */
const MAX_POLLS = 84;
/** Polls spent on the fast cadence (~1 minute) before backing off. */
const FAST_POLLS = 24;

const BILLING_COUNTRY_KEY = "wtdw.billingCountry";

function initialBillingCountry(): string {
  if (typeof window === "undefined") return "NL";
  try {
    const saved = window.localStorage.getItem(BILLING_COUNTRY_KEY);
    if (saved && /^[a-zA-Z]{2}$/.test(saved)) return saved.toUpperCase();
  } catch {
    // Storage is optional; the default market still yields a valid catalogue.
  }
  return "NL";
}

export default function SubscriptionsPage() {
  const [providers, setProviders] = useState<ProviderItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState("");
  const [billingCountry, setBillingCountry] = useState<string>(initialBillingCountry);

  const [importUrl, setImportUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const [importSuccess, setImportSuccess] = useState("");

  const [refreshingFree, setRefreshingFree] = useState(false);
  const [refreshingScrapers, setRefreshingScrapers] = useState(false);
  const [refreshResults, setRefreshResults] = useState<Record<string, string>>({});

  const [syncingCatalogue, setSyncingCatalogue] = useState(false);
  const [syncResult, setSyncResult] = useState("");
  const [syncError, setSyncError] = useState("");

  // Refresh state that the backend cannot know about yet: the press happened,
  // the queue marker / scraper run is still landing. Server-side
  // `provider.refreshing` takes over as soon as a fetch reflects it.
  const [refreshPending, setRefreshPending] = useState<ReadonlySet<string>>(new Set());
  const [refreshErrors, setRefreshErrors] = useState<Record<string, string>>({});

  const pollsRef = useRef(0);

  const fetchProviders = useCallback(async () => {
    setLoading(true);
    setFetchError("");
    try {
      setProviders(await fetchProviderMarketCatalogue(billingCountry));
    } catch (err: unknown) {
      setFetchError(err instanceof Error ? err.message : "Failed to load providers");
    } finally {
      setLoading(false);
    }
  }, [billingCountry]);

  /**
   * Re-read the list without the loading state (never scroll/reset the page
   * mid-refresh) and retire any press whose source is no longer refreshing.
   * A failed poll keeps the current data: the next tick retries.
   */
  const silentFetchProviders = useCallback(async () => {
    try {
      const data: ProviderItem[] = await fetchProviderMarketCatalogue(billingCountry);
      setProviders(data);
      setRefreshPending((prev) => {
        if (prev.size === 0) return prev;
        const stillRunning = new Set<string>();
        prev.forEach((slug) => {
          const provider = data.find((p) => p.slug === slug);
          if (provider?.refreshing) stillRunning.add(slug);
        });
        return stillRunning;
      });
    } catch {
      // Transient network failure while polling: keep the last known state.
    }
  }, [billingCountry]);

  const anyRefreshing = providers.some((p) => p.refreshing);
  const shouldPoll = anyRefreshing || refreshPending.size > 0;

  // Poll the single list endpoint while anything is in flight: queued
  // catalogue sources flip `refreshing` only when the worker starts/finishes
  // them, and a scraper press needs one final read for its new counts/last run.
  useEffect(() => {
    if (!shouldPoll) {
      pollsRef.current = 0;
      return;
    }
    if (pollsRef.current >= MAX_POLLS) return;
    const interval =
      pollsRef.current < FAST_POLLS ? POLL_INTERVAL_MS : SLOW_POLL_INTERVAL_MS;
    const timer = setTimeout(() => {
      pollsRef.current += 1;
      void silentFetchProviders();
    }, interval);
    return () => clearTimeout(timer);
  }, [shouldPoll, providers, silentFetchProviders]);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  const handleToggleProvider = async (slug: string) => {
    try {
      const res = await fetch(`/api/providers/${slug}/toggle/`, { method: "POST" });
      if (res.ok) {
        // Update just this provider's subscription flag in place so the page
        // doesn't flash back to the loading state (which jumps the scroll to top).
        setProviders((prev) =>
          prev.map((p) =>
            p.slug === slug ? { ...p, is_subscribed: !p.is_subscribed } : p
          )
        );
      }
    } catch (err) {
      console.error(`Failed to toggle provider ${slug}:`, err);
    }
  };

  /**
   * Declare the user's access to a service (included / add-on / free /
   * rent·buy / not owned). Updates the card in place so the page never resets.
   */
  const handleSetAccessMode = async (slug: string, mode: ProviderAccessMode) => {
    const result = await setProviderAccessMode(slug, mode);
    setProviders((prev) =>
      prev.map((p) =>
        p.slug === slug
          ? { ...p, is_subscribed: result.is_subscribed, access_mode: result.access_mode }
          : p
      )
    );
  };

  /** Switching billing market re-reads the whole catalogue for that market. */
  const handleBillingCountryChange = (country: string) => {
    const normalized = country.toUpperCase();
    setBillingCountry(normalized);
    try {
      window.localStorage.setItem(BILLING_COUNTRY_KEY, normalized);
    } catch {
      // Persisting the choice is a convenience, not a requirement.
    }
  };

  /**
   * Save a market plan/channel declaration. A conflict is rethrown to the
   * selector so it can keep the user's draft visible; only a successful write
   * refreshes the saved state from the server.
   */
  const handleSaveMarketSelection = async (slug: string, input: MarketSelectionInput) => {
    await saveMarketSelection(slug, billingCountry, input);
    await silentFetchProviders();
  };

  /** Pull the current provider catalogue in from JustWatch (idempotent). */
  const handleSyncCatalogue = async () => {
    setSyncingCatalogue(true);
    setSyncError("");
    setSyncResult("");
    try {
      const report = await syncProviderCatalogue();
      const created = report.created_providers?.length ?? 0;
      const mappings = report.mappings_created?.length ?? 0;
      const unmapped = report.unmapped_count ?? 0;
      setSyncResult(
        `Catalogue synced across ${report.countries?.length ?? 0} countries: ` +
          `${mappings} mapping(s) refreshed, ${created} new service(s) added ` +
          `(disabled — enable them below), ${unmapped} unmapped package(s) skipped.`
      );
      await fetchProviders();
    } catch (err: unknown) {
      setSyncError(err instanceof Error ? err.message : "Catalogue sync failed");
    } finally {
      setSyncingCatalogue(false);
    }
  };

  /**
   * Press one card's refresh control: exactly one source's collector, via the
   * endpoint that resolves it. Returns the failure message (or null) so the
   * bulk actions below can aggregate, and always retires the local pending
   * state on failure so a stuck card stays pressable.
   */
  const refreshOne = useCallback(
    async (slug: string): Promise<string | null> => {
      setRefreshErrors((prev) => {
        if (!(slug in prev)) return prev;
        const next = { ...prev };
        delete next[slug];
        return next;
      });
      setRefreshPending((prev) => new Set(prev).add(slug));
      try {
        await refreshProvider(slug);
        // Catalogue sources: `refreshing` takes over from here via polling.
        // Scrapers: synchronous, so this read already carries the new state.
        await silentFetchProviders();
        return null;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setRefreshErrors((prev) => ({ ...prev, [slug]: message }));
        setRefreshPending((prev) => {
          const next = new Set(prev);
          next.delete(slug);
          return next;
        });
        return message;
      }
    },
    [silentFetchProviders]
  );

  const handleRefreshFreeProviders = async () => {
    setRefreshingFree(true);
    setRefreshResults((prev) => ({ ...prev, __free__: "Refreshing…" }));
    const targets = bvodProviders.filter((p) => p.refresh_kind);
    let lastError: string | null = null;
    for (const provider of targets) {
      const message = await refreshOne(provider.slug);
      if (message) lastError = message;
    }
    setRefreshingFree(false);
    if (lastError) {
      setRefreshResults((prev) => ({ ...prev, __free__: `Finished with errors: ${lastError}` }));
    } else {
      setRefreshResults((prev) => ({ ...prev, __free__: "All free providers refreshed" }));
    }
    await silentFetchProviders();
  };

  const handleRefreshScraperProviders = async () => {
    setRefreshingScrapers(true);
    setRefreshResults((prev) => ({ ...prev, __scrapers__: "Refreshing…" }));
    const targets = scraperProviders.filter((p) => p.refresh_kind);
    let lastError: string | null = null;
    for (const provider of targets) {
      const message = await refreshOne(provider.slug);
      if (message) lastError = message;
    }
    setRefreshingScrapers(false);
    if (lastError) {
      setRefreshResults((prev) => ({ ...prev, __scrapers__: `Finished with errors: ${lastError}` }));
    } else {
      setRefreshResults((prev) => ({ ...prev, __scrapers__: "All imported sources refreshed" }));
    }
    await silentFetchProviders();
  };

  const handleDeleteProvider = async (slug: string) => {
    if (!confirm(`Are you sure you want to remove source '${slug}'?`)) return;
    try {
      const res = await fetch(`/api/providers/${slug}/`, { method: "DELETE" });
      if (res.ok || res.status === 204) {
        await fetchProviders();
      }
    } catch (err) {
      console.error(`Failed to delete provider ${slug}:`, err);
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

  const hostNames = Object.fromEntries(providers.map((p) => [p.slug, p.name]));
  const majorProviders = providers.filter((p) => p.is_major);
  const bvodProviders = providers.filter((p) => p.category === "bvod");
  // Public broadcasters are BVOD providers, so every scraper-category provider
  // is an imported source by construction — no slug allowlist needed here.
  const scraperProviders = providers.filter(
    (p) => !p.is_major && p.category === "scraper"
  );
  // Everything else the catalogue sources can cover: services discovered from
  // JustWatch that are not majors, broadcasters or imported scrapers. They are
  // listed so the user can declare access to them (which activates the card);
  // until then they sit inactive rather than being hidden.
  const otherProviders = providers.filter(
    (p) => !p.is_major && p.category !== "bvod" && p.category !== "scraper"
  );

  return (
    <main className="mx-auto max-w-4xl px-4 py-10 sm:px-6">
      {/* Top navigation link */}
      <div className="mb-6">
        <Link href="/preferences" className="text-sm text-blue-600 hover:underline dark:text-blue-400">
          ← Back to preferences
        </Link>
      </div>

      {/* Header */}
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
            Subscriptions &amp; Sources
          </h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Declare how you access each service — an included subscription, an
            add-on channel, a free service, or a rent/buy account — plus free
            public broadcasters &amp; imported scraper sources.
          </p>
        </div>
        {!loading && !fetchError && (
          <button
            type="button"
            onClick={handleSyncCatalogue}
            disabled={syncingCatalogue}
            title="Reconcile the service list from the JustWatch provider catalogue"
            className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-600 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
          >
            <svg
              className={`h-4 w-4 ${syncingCatalogue ? "animate-spin" : ""}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            {syncingCatalogue ? "Syncing catalogue…" : "Sync service catalogue"}
          </button>
        )}
      </div>

      {!loading && !fetchError && (
        <div className="mb-6 rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
          <p className="block text-sm font-semibold text-gray-800 dark:text-gray-200">
            Billing country
          </p>
          <p className="mb-2 text-xs text-gray-500 dark:text-gray-400">
            Plans and channel add-ons are sold per billing market. The current
            location is kept separate — a plan held elsewhere is not confirmed
            here without reviewed portability.
          </p>
          <div className="max-w-xs">
            <CountrySelect
              country={billingCountry}
              onChange={handleBillingCountryChange}
              label="Billing country"
            />
          </div>
        </div>
      )}

      {syncResult && (
        <div className="mb-6 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-2.5 text-xs text-emerald-800 dark:border-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300">
          {syncResult}
        </div>
      )}
      {syncError && (
        <div className="mb-6 rounded-lg border border-red-300 bg-red-50 px-4 py-2.5 text-xs text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {syncError}
        </div>
      )}

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
                SVOD - Subscription Video on Demand
              </h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Fixed canonical catalog. Click a provider card to toggle your active subscription status.
              </p>
            </div>
            <ProviderGrid
              providers={majorProviders}
              onToggle={handleToggleProvider}
              onSetAccessMode={handleSetAccessMode}
              billingCountry={billingCountry}
              onSaveMarketSelection={handleSaveMarketSelection}
              hostNames={hostNames}
              onRefresh={refreshOne}
              refreshPending={refreshPending}
              refreshErrors={refreshErrors}
              loading={loading}
            />
          </section>

          {/* Section 2: Free Streaming Providers (European Public Broadcasters & Global Free Services) */}
          <section className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-800 dark:bg-gray-900">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                  BVOD - Broadcaster Video on Demand
                </h2>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  European public broadcasters and free ad-supported streaming services across 20+ countries. Click a provider card to enable or disable it for watch availability matching.
                </p>
              </div>
              {bvodProviders.some((p) => p.refresh_kind) && (
                <button
                  type="button"
                  onClick={handleRefreshFreeProviders}
                  disabled={refreshingFree}
                  title="Refresh all free provider catalogs"
                  className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
                >
                  <svg
                    className={`h-4 w-4 ${refreshingFree ? "animate-spin" : ""}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  {refreshingFree ? "Refreshing…" : "Refresh"}
                </button>
              )}
            </div>
            {bvodProviders.length === 0 ? (
              <p className="text-xs text-gray-400 italic">
                No free providers configured yet.
              </p>
            ) : (
              <ProviderGrid
                providers={bvodProviders}
                onToggle={handleToggleProvider}
                onSetAccessMode={handleSetAccessMode}
                billingCountry={billingCountry}
                onSaveMarketSelection={handleSaveMarketSelection}
                hostNames={hostNames}
                onRefresh={refreshOne}
                refreshPending={refreshPending}
                refreshErrors={refreshErrors}
                loading={loading}
              />
            )}
            {refreshResults.__free__ && (
              <div
                className={`mt-3 rounded-lg border px-4 py-2.5 text-xs ${
                  refreshResults.__free__.startsWith("Error")
                    ? "border-red-300 bg-red-50 text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400"
                    : "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
                }`}
              >
                {refreshResults.__free__}
              </div>
            )}
          </section>

          {/* Section 3: Other catalogue services (mapped, not majors/broadcasters) */}
          <section className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-800 dark:bg-gray-900">
            <div className="mb-4">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                Other Services (from the JustWatch catalogue)
              </h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Services our sources can cover. Pick how you access one to activate
                it — until then it stays inactive and is not counted as yours.
              </p>
            </div>
            {otherProviders.length === 0 ? (
              <p className="text-xs text-gray-400 italic">
                Nothing else discovered yet — run &ldquo;Sync service catalogue&rdquo;.
              </p>
            ) : (
              <ProviderGrid
                providers={otherProviders}
                onToggle={handleToggleProvider}
                onSetAccessMode={handleSetAccessMode}
                billingCountry={billingCountry}
                onSaveMarketSelection={handleSaveMarketSelection}
                hostNames={hostNames}
                onRefresh={refreshOne}
                refreshPending={refreshPending}
                refreshErrors={refreshErrors}
                loading={loading}
              />
            )}
          </section>

          {/* Section 4: Imported Small Providers & Sources */}
          <section className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-800 dark:bg-gray-900">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                  Imported Small Providers & Sources
                </h2>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Import custom sources by URL (e.g., KissKH, HydraHD, AnimeWorld, or any custom domain) to trigger automated scrapers.
                </p>
              </div>
              {scraperProviders.some((p) => p.refresh_kind) && (
                <button
                  type="button"
                  onClick={handleRefreshScraperProviders}
                  disabled={refreshingScrapers}
                  title="Refresh all imported custom scraper sources"
                  className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
                >
                  <svg
                    className={`h-4 w-4 ${refreshingScrapers ? "animate-spin" : ""}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  {refreshingScrapers ? "Refreshing…" : "Refresh All"}
                </button>
              )}
            </div>

            {refreshResults.__scrapers__ && (
              <div
                className={`mb-4 rounded-lg border px-4 py-2.5 text-xs ${
                  refreshResults.__scrapers__.startsWith("Error")
                    ? "border-red-300 bg-red-50 text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400"
                    : "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
                }`}
              >
                {refreshResults.__scrapers__}
              </div>
            )}

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

            {/* Imported sources: same square cards as the other sections —
                the section only adds a delete control on top. */}
            <div className="space-y-3">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                Imported Sources ({scraperProviders.length})
              </h3>

              {scraperProviders.length === 0 ? (
                <p className="text-xs text-gray-400 italic">No custom sources imported yet.</p>
              ) : (
                <ProviderGrid
                  providers={scraperProviders}
                  onToggle={handleToggleProvider}
                  onSetAccessMode={handleSetAccessMode}
                  billingCountry={billingCountry}
                  onSaveMarketSelection={handleSaveMarketSelection}
                  hostNames={hostNames}
                  onRefresh={refreshOne}
                  onRemove={handleDeleteProvider}
                  useFavicons
                  refreshPending={refreshPending}
                  refreshErrors={refreshErrors}
                  loading={loading}
                />
              )}
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
