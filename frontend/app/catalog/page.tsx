"use client";

import { useEffect, useState, useCallback, useMemo, Suspense } from "react";
import Link from "next/link";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { FilterBar, FilterState } from "@/components/FilterBar";
import { VpnWatchModal } from "@/components/VpnWatchModal";
import { WhyCantIWatchModal, ExcludedOffer } from "@/components/WhyCantIWatchModal";
import { Pagination } from "@/components/Pagination";
import LanguageBadge from "@/components/LanguageBadge";
import { formatLastSeen } from "@/lib/time";
import { extractViewerLanguages, useViewerPreferences } from "@/components/ViewerPreferencesProvider";
import { useCurrentLocation } from "@/components/CurrentLocationProvider";
import type {
  UnifiedCatalogItem,
  UnofficialProvider,
} from "@/lib/api";
import {
  fetchUnifiedCatalog,
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

function CatalogPageClient() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { currentCountry } = useCurrentLocation();

  const initialUrlState = useMemo(() => {
    const q = searchParams.get("q") || "";
    const content_type = searchParams.get("content_type") || "all";
    const genre = searchParams.get("genre") || "";
    const year_min = searchParams.get("year_min") || "";
    const year_max = searchParams.get("year_max") || "";
    const country = searchParams.get("country") || "";
    const status = searchParams.get("status") || "";
    const quality = searchParams.get("quality") || "";
    const score_min = searchParams.get("score_min") || "";
    const sort_by = searchParams.get("sort_by") || "";
    const access = searchParams.get("access") || "";
    const rawProviders = searchParams.get("providers") || "";
    const providers = rawProviders ? rawProviders.split(",").map((s) => s.trim()).filter(Boolean) : [];

    let actors: { id: number; name: string }[] = [];
    const rawActors = searchParams.get("actors");
    if (rawActors) {
      try {
        actors = JSON.parse(rawActors);
      } catch {
        actors = [];
      }
    }

    const initialPage = parseInt(searchParams.get("page") || "1", 10) || 1;

    return {
      filters: {
        q,
        content_type,
        genre,
        year_min,
        year_max,
        actors,
        providers,
        country,
        status,
        quality,
        score_min,
        sort_by,
        access,
      },
      page: initialPage,
    };
  }, [searchParams]);

  const [filters, setFilters] = useState<FilterState>(initialUrlState.filters);
  const [items, setItems] = useState<UnifiedCatalogItem[]>([]);
  const [page, setPage] = useState(initialUrlState.page);
  const [paginationInfo, setPaginationInfo] = useState({
    totalPages: 1,
    hasNext: false,
    hasPrevious: false,
  });
  const [loading, setLoading] = useState(true);

  // Unofficial scrape panel state
  const [providers, setProviders] = useState<UnofficialProvider[]>([]);
  const [scrapeProvider, setScrapeProvider] = useState("");
  const [scrapeQuery, setScrapeQuery] = useState("");
  const [scraping, setScraping] = useState(false);
  const [scrapeResult, setScrapeResult] = useState<string | null>(null);
  const [showScrapePanel, setShowScrapePanel] = useState(false);

  const { viewersRule } = useViewerPreferences();
  const preferredLanguages = useMemo(() => extractViewerLanguages(viewersRule), [viewersRule]);

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

  // Load providers for scrape panel once
  useEffect(() => {
    fetchUnofficialProviders()
      .then((data) => setProviders(data.results || []))
      .catch(() => {});
  }, []);

  // Sync state to URL search params whenever filters or page change
  useEffect(() => {
    const params = new URLSearchParams();
    if (filters.q) params.set("q", filters.q);
    if (filters.content_type && filters.content_type !== "all") {
      params.set("content_type", filters.content_type);
    }
    if (filters.genre) params.set("genre", filters.genre);
    if (filters.year_min) params.set("year_min", filters.year_min);
    if (filters.year_max) params.set("year_max", filters.year_max);
    if (filters.providers && filters.providers.length > 0) {
      params.set("providers", filters.providers.join(","));
    }
    if (filters.country) params.set("country", filters.country);
    if (filters.status) params.set("status", filters.status);
    if (filters.quality) params.set("quality", filters.quality);
    if (filters.score_min) params.set("score_min", filters.score_min);
    if (filters.sort_by) params.set("sort_by", filters.sort_by);
    if (filters.access) params.set("access", filters.access);
    if (filters.actors && filters.actors.length > 0) {
      params.set("actors", JSON.stringify(filters.actors));
    }
    if (page > 1) {
      params.set("page", page.toString());
    }

    const queryString = params.toString();
    const newUrl = queryString ? `${pathname}?${queryString}` : pathname;
    router.replace(newUrl, { scroll: false });
  }, [filters, page, pathname, router]);

  const handleFilterChange = (newFilters: FilterState) => {
    setFilters(newFilters);
    setPage(1);
  };

  const loadCatalog = useCallback(() => {
    setLoading(true);
    const params: Record<string, string> = {
      page: page.toString(),
      page_size: "24",
    };
    if (filters.q) params.q = filters.q;
    if (filters.content_type !== "all") params.content_type = filters.content_type;
    if (filters.genre) params.genre = filters.genre;
    if (filters.year_min) params.year_min = filters.year_min;
    if (filters.year_max) params.year_max = filters.year_max;
    if (filters.providers && filters.providers.length > 0) {
      params.providers = filters.providers.join(",");
    }
    if (filters.status) params.status = filters.status;
    if (filters.quality) params.quality = filters.quality;
    if (filters.score_min) params.score_min = filters.score_min;
    if (filters.sort_by) params.sort_by = filters.sort_by;
    if (filters.access) params.access = filters.access;
    if (filters.country) params.country = filters.country;
    if (filters.actors && filters.actors.length > 0) {
      params.actor_id = filters.actors.map((a) => a.id).join(",");
    }
    if (currentCountry) params.current_country = currentCountry;

    fetchUnifiedCatalog(params)
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
  }, [filters, page]);

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

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

  const openWhyModal = (item: UnifiedCatalogItem) => {
    const watchRes = item.watchability || {};
    setWhyModalState({
      isOpen: true,
      title: item.title,
      explanations: watchRes.explanation || ["No availability data found."],
      excludedOffers: (watchRes.excluded_offers as ExcludedOffer[]) || [],
    });
  };

  const handleScrape = async () => {
    if (!scrapeProvider) return;
    setScraping(true);
    setScrapeResult(null);
    try {
      const data = await triggerUnofficialScrape(
        scrapeProvider,
        scrapeQuery || undefined
      );
      setScrapeResult(`Scraped ${data.count} item(s) from ${scrapeProvider}`);
      loadCatalog();
    } catch (err) {
      setScrapeResult(
        `Error: ${err instanceof Error ? err.message : String(err)}`
      );
    } finally {
      setScraping(false);
    }
  };

  const renderBadge = (sourceIndicator: string) => {
    const indicator = sourceIndicator.toLowerCase();
    if (indicator === "official" || indicator === "svod") {
      return (
        <span className="px-2 py-0.5 text-xs font-bold rounded-md bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-200">
          {sourceIndicator}
        </span>
      );
    }
    if (indicator === "bvod" || indicator === "avod" || indicator === "fast") {
      return (
        <span className="px-2 py-0.5 text-xs font-bold rounded-md bg-emerald-100 text-emerald-800 dark:bg-emerald-900/60 dark:text-emerald-200">
          {sourceIndicator}
        </span>
      );
    }
    if (indicator === "tvod") {
      return (
        <span className="px-2 py-0.5 text-xs font-bold rounded-md bg-indigo-100 text-indigo-800 dark:bg-indigo-900/60 dark:text-indigo-200">
          {sourceIndicator}
        </span>
      );
    }
    return (
      <span className="px-2 py-0.5 text-xs font-bold rounded-md bg-purple-100 text-purple-800 dark:bg-purple-900/60 dark:text-purple-200">
        {sourceIndicator || "Scraper"}
      </span>
    );
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
          Catalog
        </h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Browse all titles across major, bvod, and scraper streaming providers in one place.
        </p>
      </div>

      <FilterBar filters={filters} onChange={handleFilterChange} showStatusFilter={true} />

      {/* Scrape panel */}
      <div className="space-y-3">
        <button
          type="button"
          onClick={() => setShowScrapePanel((v) => !v)}
          className="text-sm font-medium text-purple-600 hover:text-purple-700 dark:text-purple-400 dark:hover:text-purple-300"
        >
          {showScrapePanel ? "Hide Scrape Panel" : "Scrape Sources"}
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

      {/* Catalog items grid */}
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

              const isOfficial = item.source === "official";
              const unofficialDetails = item.unofficial_details;

              return (
                <div
                  key={item.id}
                  className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-shadow flex flex-col"
                >
                  {item.tmdb_id ? (
                    <Link
                      href={
                        item.content_type === "series"
                          ? `/tv/${item.tmdb_id}`
                          : `/movie/${item.tmdb_id}`
                      }
                    >
                      <div className="aspect-[2/3] bg-gray-100 dark:bg-gray-800 relative">
                        {item.poster_path ? (
                          <img
                            src={
                              item.poster_path.startsWith("http")
                                ? item.poster_path
                                : `https://image.tmdb.org/t/p/w500${item.poster_path}`
                            }
                            alt={item.title}
                            className="w-full h-full object-cover"
                          />
                        ) : (
                          <div className="w-full h-full flex items-center justify-center text-gray-400 text-xs">
                            No Poster
                          </div>
                        )}
                        <div className="absolute top-2 left-2 pointer-events-none z-10">
                          <span className="px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider rounded-md bg-gray-900/80 text-white backdrop-blur-xs">
                            {item.content_type === "series" ? "SERIES" : "MOVIE"}
                          </span>
                        </div>
                        <div className="absolute top-2 right-2 flex flex-col items-end gap-1">
                          {renderBadge(item.source_indicator)}
                          {isOfficial && (
                            <span className="px-2 py-0.5 rounded-md text-xs font-bold bg-black/70 text-white backdrop-blur-sm">
                              {symbol} {label}
                            </span>
                          )}
                          {isOfficial && status === "needs_recheck" && (
                            <span
                              data-testid="last-seen"
                              className="px-2 py-0.5 rounded-md text-[10px] font-semibold bg-black/60 text-white"
                            >
                              {formatLastSeen(watchRes.last_seen_at)}
                            </span>
                          )}
                        </div>
                      </div>
                    </Link>
                  ) : (
                    <div className="aspect-[2/3] bg-gray-100 dark:bg-gray-800 relative">
                      {item.poster_path ? (
                        <img
                          src={
                            item.poster_path.startsWith("http")
                              ? item.poster_path
                              : `https://image.tmdb.org/t/p/w500${item.poster_path}`
                          }
                          alt={item.title}
                          className="w-full h-full object-cover"
                        />
                      ) : (
                        <div className="w-full h-full flex items-center justify-center text-gray-400 text-xs">
                          No Poster
                        </div>
                      )}
                      <div className="absolute top-2 left-2 pointer-events-none z-10">
                        <span className="px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider rounded-md bg-gray-900/80 text-white backdrop-blur-xs">
                          {item.content_type === "series" ? "SERIES" : "MOVIE"}
                        </span>
                      </div>
                      <div className="absolute top-2 right-2">
                        {renderBadge(item.source_indicator)}
                      </div>
                    </div>
                  )}

                  <div className="p-4 flex-1 flex flex-col justify-between space-y-3">
                    <div>
                      <h3 className="font-bold text-gray-900 dark:text-gray-100 line-clamp-1">
                        {item.title}
                      </h3>
                      <p className="text-xs text-gray-500">
                        {item.content_type === "series" ? "TV Series" : "Movie"}{" "}
                        {item.year && `• ${item.year}`}
                        {item.provider && ` • ${item.provider.name}`}
                      </p>

                      {/* Languages for scraper items */}
                      {!isOfficial && unofficialDetails && (
                        <div className="mt-2 flex flex-wrap items-center gap-1">
                          {unofficialDetails.audio_languages.map((lang) => (
                            <LanguageBadge
                              key={`audio-${lang}`}
                              code={lang}
                              type="audio"
                              isPreferred={preferredLanguages.includes(lang.toUpperCase())}
                              compact
                            />
                          ))}
                          {unofficialDetails.subtitle_languages.map((lang) => (
                            <LanguageBadge
                              key={`sub-${lang}`}
                              code={lang}
                              type="subtitle"
                              isPreferred={preferredLanguages.includes(lang.toUpperCase())}
                              compact
                            />
                          ))}
                        </div>
                      )}
                    </div>

                    {isOfficial ? (
                      isWatchable && bestOffer ? (
                        <div className="space-y-2">
                          <div className="bg-gray-50 dark:bg-gray-800/60 p-2.5 rounded-lg text-xs space-y-1">
                            <p className="font-bold text-gray-700 dark:text-gray-300">
                              Best way to watch:
                            </p>
                            <p className="text-indigo-600 dark:text-indigo-400 font-semibold">
                              {bestOffer.country
                                ? `${bestOffer.service_display || bestOffer.service} (${bestOffer.country})`
                                : bestOffer.service_display || bestOffer.service}
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
                      )
                    ) : (
                      item.watch_url && (
                        <a
                          href={item.watch_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="w-full block text-center py-2 text-xs font-semibold rounded-lg bg-purple-600 hover:bg-purple-700 text-white transition-colors"
                        >
                          Watch on {item.provider?.name || "Source"}
                        </a>
                      )
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

export default function CatalogPage() {
  return (
    <Suspense fallback={<div className="py-12 text-center text-gray-500">Loading catalog...</div>}>
      <CatalogPageClient />
    </Suspense>
  );
}
