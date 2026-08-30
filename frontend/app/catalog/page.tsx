"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { FilterBar, FilterState } from "@/components/FilterBar";
import { VpnWatchModal } from "@/components/VpnWatchModal";
import { WhyCantIWatchModal, ExcludedOffer } from "@/components/WhyCantIWatchModal";
import { Pagination } from "@/components/Pagination";
import UnofficialCatalogItem from "@/components/UnofficialCatalogItem";
import type {
  UnofficialCatalogItem as UnofficialItem,
  UnofficialProvider,
} from "@/lib/api";
import {
  fetchUnofficialCatalog,
  fetchUnofficialProviders,
  triggerUnofficialScrape,
} from "@/lib/api";

interface BestOffer {
  country: string;
  service: string;
  service_display?: string;
  url: string;
  is_current_country: boolean;
}

interface CatalogItem {
  id?: number;
  tmdb_id: number;
  title: string;
  content_type?: string;
  year?: number;
  poster_path?: string;
  watchability?: {
    status?: string;
    status_symbol?: string;
    status_label?: string;
    best_offer?: BestOffer;
    explanation?: string[];
    excluded_offers?: ExcludedOffer[];
  };
}

type Tab = "official" | "unofficial";

export default function CatalogPage() {
  const [filters, setFilters] = useState<FilterState>({
    q: "",
    content_type: "all",
    genre: "",
    year_min: "",
    year_max: "",
    actors: [],
    provider: "",
    country: "",
    status: "",
  });

  const [items, setItems] = useState<CatalogItem[]>([]);
  const [page, setPage] = useState(1);
  const [paginationInfo, setPaginationInfo] = useState({
    totalPages: 1,
    hasNext: false,
    hasPrevious: false,
  });
  const [loading, setLoading] = useState(true);

  // Unofficial sources state
  const [activeTab, setActiveTab] = useState<Tab>("official");
  const [unofficialItems, setUnofficialItems] = useState<UnofficialItem[]>([]);
  const [unofficialLoading, setUnofficialLoading] = useState(false);
  const [unofficialCount, setUnofficialCount] = useState(0);
  const [providers, setProviders] = useState<UnofficialProvider[]>([]);
  const [scrapeProvider, setScrapeProvider] = useState("");
  const [scrapeQuery, setScrapeQuery] = useState("");
  const [scraping, setScraping] = useState(false);
  const [scrapeResult, setScrapeResult] = useState<string | null>(null);
  const [showScrapePanel, setShowScrapePanel] = useState(false);

  // Language preferences for badges
  const [preferredAudio, setPreferredAudio] = useState<string[]>([]);
  const [preferredSubtitles, setPreferredSubtitles] = useState<string[]>([]);

  const [vpnModalState, setVpnModalState] = useState<{
    isOpen: boolean;
    requiredCountry: string;
    serviceName: string;
    watchUrl: string;
  }>({
    isOpen: false,
    requiredCountry: "US",
    serviceName: "",
    watchUrl: "",
  });

  const [whyModalState, setWhyModalState] = useState<{
    isOpen: boolean;
    title: string;
    explanations: string[];
    excludedOffers: ExcludedOffer[];
  }>({
    isOpen: false,
    title: "",
    explanations: [],
    excludedOffers: [],
  });

  // Load preferences once
  useEffect(() => {
    fetch("/api/preferences/")
      .then((r) => (r.ok ? r.json() : null))
      .then((p) => {
        if (p) {
          setPreferredAudio(p.audio_languages || []);
          setPreferredSubtitles(p.subtitle_languages || []);
        }
      })
      .catch(() => {});
  }, []);

  // Load providers once
  useEffect(() => {
    fetchUnofficialProviders()
      .then((data) => setProviders(data.results || []))
      .catch(() => {});
  }, []);

  // Reset to page 1 whenever filters change
  useEffect(() => {
    setPage(1);
  }, [filters]);

  // Fetch official catalog
  useEffect(() => {
    if (activeTab !== "official") return;
    setLoading(true);
    const params = new URLSearchParams();
    params.set("page", page.toString());
    params.set("page_size", "24");
    if (filters.q) params.set("q", filters.q);
    if (filters.content_type !== "all") params.set("type", filters.content_type);
    if (filters.genre) params.set("genre", filters.genre);
    if (filters.year_min) params.set("year_min", filters.year_min);
    if (filters.year_max) params.set("year_max", filters.year_max);
    if (filters.provider) params.set("provider", filters.provider);
    if (filters.status) params.set("status", filters.status);
    filters.actors.forEach((a) => params.append("actor_id", a.id.toString()));

    fetch(`/api/catalog/?${params.toString()}`)
      .then((res) =>
        res.ok
          ? res.json()
          : { results: [], total_pages: 1, has_next: false, has_previous: false }
      )
      .then((data) => {
        setItems(data.results || []);
        setPaginationInfo({
          totalPages: data.total_pages || 1,
          hasNext: !!data.has_next,
          hasPrevious: !!data.has_previous,
        });
      })
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, [filters, page, activeTab]);

  // Fetch unofficial catalog
  useEffect(() => {
    if (activeTab !== "unofficial") return;
    setUnofficialLoading(true);
    const params: Record<string, string> = {};
    if (filters.q) params.q = filters.q;
    if (filters.provider) params.provider = filters.provider;
    if (filters.country) params.country = filters.country;
    fetchUnofficialCatalog(params)
      .then((data) => {
        setUnofficialItems(data.results || []);
        setUnofficialCount(data.count || 0);
      })
      .catch(() => {
        setUnofficialItems([]);
        setUnofficialCount(0);
      })
      .finally(() => setUnofficialLoading(false));
  }, [filters, activeTab]);

  const handleWatchClick = (bestOffer: BestOffer) => {
    if (!bestOffer) return;
    if (bestOffer.is_current_country) {
      window.open(bestOffer.url, "_blank", "noopener,noreferrer");
    } else {
      setVpnModalState({
        isOpen: true,
        requiredCountry: bestOffer.country,
        serviceName: bestOffer.service,
        watchUrl: bestOffer.url,
      });
    }
  };

  const openWhyModal = (item: CatalogItem) => {
    const watchRes = item.watchability || {};
    setWhyModalState({
      isOpen: true,
      title: item.title,
      explanations: watchRes.explanation || ["No availability data found."],
      excludedOffers: watchRes.excluded_offers || [],
    });
  };

  const handleScrape = async () => {
    if (!scrapeProvider) return;
    setScraping(true);
    setScrapeResult(null);
    try {
      const data = await triggerUnofficialScrape(scrapeProvider, scrapeQuery || undefined);
      setScrapeResult(`Scraped ${data.count} item(s) from ${scrapeProvider}`);
      // Refresh the unofficial catalog after scraping
      const refreshParams: Record<string, string> = {};
      if (filters.q) refreshParams.q = filters.q;
      if (filters.provider) refreshParams.provider = filters.provider;
      const resp = await fetchUnofficialCatalog(refreshParams);
      setUnofficialItems(resp.results || []);
      setUnofficialCount(resp.count || 0);
    } catch (err) {
      setScrapeResult(`Error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setScraping(false);
    }
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
          Catalog
        </h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Browse titles from official streaming services and free providers.
        </p>
      </div>

      {/* Tab navigation */}
      <div className="flex gap-1 border-b border-gray-200 dark:border-gray-800">
        <button
          type="button"
          onClick={() => setActiveTab("official")}
          className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
            activeTab === "official"
              ? "bg-white dark:bg-gray-900 border-b-2 border-indigo-600 text-indigo-600 dark:text-indigo-400"
              : "text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
          }`}
        >
          Official Sources
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("unofficial")}
          className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
            activeTab === "unofficial"
              ? "bg-white dark:bg-gray-900 border-b-2 border-purple-600 text-purple-600 dark:text-purple-400"
              : "text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
          }`}
        >
          Unofficial Sources
          {unofficialCount > 0 && (
            <span className="ml-2 inline-flex items-center justify-center px-2 py-0.5 text-xs font-bold rounded-full bg-purple-100 text-purple-800 dark:bg-purple-900/60 dark:text-purple-300">
              {unofficialCount}
            </span>
          )}
        </button>
      </div>

      <FilterBar filters={filters} onChange={setFilters} showStatusFilter={activeTab === "official"} tab={activeTab} />

      {/* Unofficial: Scrape panel */}
      {activeTab === "unofficial" && (
        <div className="space-y-3">
          <button
            type="button"
            onClick={() => setShowScrapePanel((v) => !v)}
            className="text-sm font-medium text-purple-600 hover:text-purple-700 dark:text-purple-400 dark:hover:text-purple-300"
          >
            {showScrapePanel ? "Hide Scrape Panel" : "Scrape Unofficial Sources"}
          </button>
          {showScrapePanel && (
            <div className="rounded-xl border border-purple-200 dark:border-purple-800 bg-purple-50 dark:bg-purple-900/20 p-4 space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div>
                  <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
                    Provider
                  </label>
                  <select
                    value={scrapeProvider}
                    onChange={(e) => setScrapeProvider(e.target.value)}
                    className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                  >
                    <option value="">Select provider…</option>
                    {providers.map((p) => (
                      <option key={p.slug} value={p.slug}>
                        {p.name} ({p.slug})
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
                    Search Query (optional)
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. Naruto"
                    value={scrapeQuery}
                    onChange={(e) => setScrapeQuery(e.target.value)}
                    className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                  />
                </div>
                <div className="flex items-end">
                  <button
                    type="button"
                    onClick={handleScrape}
                    disabled={scraping || !scrapeProvider}
                    className="w-full py-2 text-sm font-semibold rounded-lg bg-purple-600 hover:bg-purple-700 disabled:bg-gray-300 dark:disabled:bg-gray-700 text-white transition-colors"
                  >
                    {scraping ? "Scraping…" : "Run Scrape"}
                  </button>
                </div>
              </div>
              {scrapeResult && (
                <p
                  className={`text-sm ${
                    scrapeResult.startsWith("Error")
                      ? "text-red-600 dark:text-red-400"
                      : "text-emerald-600 dark:text-emerald-400"
                  }`}
                >
                  {scrapeResult}
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Official catalog */}
      {activeTab === "official" && (
        <>
          {loading ? (
            <div className="py-12 text-center text-gray-500 animate-pulse">
              Loading catalog items...
            </div>
          ) : items.length === 0 ? (
            <div className="py-12 text-center border border-dashed border-gray-300 dark:border-gray-800 rounded-xl p-8">
              <p className="text-gray-600 dark:text-gray-400 font-medium">
                No catalog titles found matching your search and filter criteria.
              </p>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
                {items.map((item) => {
                  const watchRes = item.watchability || {};
                  const bestOffer = watchRes.best_offer;
                  const status = watchRes.status || "unknown";
                  const symbol = watchRes.status_symbol || "⚪";
                  const label = watchRes.status_label || "Unknown";

                  const isWatchable =
                    status === "watchable_here" || status === "watchable_vpn";

                  return (
                    <div
                      key={item.id || item.tmdb_id}
                      className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-shadow flex flex-col"
                    >
                      <Link href={`/movie/${item.tmdb_id}`}>
                        <div className="aspect-[2/3] bg-gray-100 dark:bg-gray-800 relative">
                          {item.poster_path ? (
                            <img
                              src={`https://image.tmdb.org/t/p/w500${item.poster_path}`}
                              alt={item.title}
                              className="w-full h-full object-cover"
                            />
                          ) : (
                            <div className="w-full h-full flex items-center justify-center text-gray-400 text-xs">
                              No Poster
                            </div>
                          )}
                          <span className="absolute top-2 right-2 px-2 py-1 rounded-md text-xs font-bold bg-black/70 text-white backdrop-blur-sm">
                            {symbol} {label}
                          </span>
                        </div>
                      </Link>

                      <div className="p-4 flex-1 flex flex-col justify-between space-y-3">
                        <div>
                          <h3 className="font-bold text-gray-900 dark:text-gray-100 line-clamp-1">
                            {item.title}
                          </h3>
                          <p className="text-xs text-gray-500">
                            {item.content_type === "series" ? "TV Series" : "Movie"}{" "}
                            {item.year && `• ${item.year}`}
                          </p>
                        </div>

                        {isWatchable && bestOffer ? (
                          <div className="space-y-2">
                            <div className="bg-gray-50 dark:bg-gray-800/60 p-2.5 rounded-lg text-xs space-y-1">
                              <p className="font-bold text-gray-700 dark:text-gray-300">
                                Best way to watch:
                              </p>
                              <p className="text-indigo-600 dark:text-indigo-400 font-semibold">
                                {bestOffer.country
                                  ? `${bestOffer.service_display || bestOffer.service} (${bestOffer.country})`
                                  : (bestOffer.service_display || bestOffer.service)}
                              </p>
                            </div>
                            <button
                              onClick={() => handleWatchClick(bestOffer)}
                              className="w-full py-2 text-xs font-semibold rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white transition-colors"
                            >
                              Watch Now
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={() => openWhyModal(item)}
                            className="w-full py-2 text-xs font-medium rounded-lg border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
                          >
                            Why can&apos;t I watch this?
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              <Pagination
                page={page}
                totalPages={paginationInfo.totalPages}
                hasNext={paginationInfo.hasNext}
                hasPrevious={paginationInfo.hasPrevious}
                onPageChange={setPage}
              />
            </>
          )}
        </>
      )}

      {/* Unofficial catalog */}
      {activeTab === "unofficial" && (
        <>
          {unofficialLoading ? (
            <div className="py-12 text-center text-gray-500 animate-pulse">
              Loading unofficial sources…
            </div>
          ) : unofficialItems.length === 0 ? (
            <div className="py-12 text-center border border-dashed border-gray-300 dark:border-gray-800 rounded-xl p-8">
              <p className="text-gray-600 dark:text-gray-400 font-medium">
                No unofficial source results found. Try running a scrape or adjusting
                your filters.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
              {unofficialItems.map((item) => (
                <UnofficialCatalogItem
                  key={item.id}
                  item={item}
                  preferredAudio={preferredAudio}
                  preferredSubtitles={preferredSubtitles}
                />
              ))}
            </div>
          )}
        </>
      )}

      <VpnWatchModal
        isOpen={vpnModalState.isOpen}
        onClose={() => setVpnModalState({ ...vpnModalState, isOpen: false })}
        requiredCountry={vpnModalState.requiredCountry}
        serviceName={vpnModalState.serviceName}
        watchUrl={vpnModalState.watchUrl}
      />

      <WhyCantIWatchModal
        isOpen={whyModalState.isOpen}
        onClose={() => setWhyModalState({ ...whyModalState, isOpen: false })}
        title={whyModalState.title}
        explanations={whyModalState.explanations}
        excludedOffers={whyModalState.excludedOffers}
      />
    </div>
  );
}
