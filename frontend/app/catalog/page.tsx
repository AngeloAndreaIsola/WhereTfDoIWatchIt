"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { FilterBar, FilterState } from "@/components/FilterBar";
import { VpnWatchModal } from "@/components/VpnWatchModal";
import { WhyCantIWatchModal, ExcludedOffer } from "@/components/WhyCantIWatchModal";
import { Pagination } from "@/components/Pagination";

interface BestOffer {
  country: string;
  service: string;
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

  // Reset to page 1 whenever filters change
  useEffect(() => {
    setPage(1);
  }, [filters]);

  useEffect(() => {
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
      .then((res) => (res.ok ? res.json() : { results: [], total_pages: 1, has_next: false, has_previous: false }))
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

  return (
    <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
          Catalog
        </h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Browse titles known to the application and filter by availability.
        </p>
      </div>

      <FilterBar filters={filters} onChange={setFilters} showStatusFilter={true} />

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

              const isWatchable = status === "watchable_here" || status === "watchable_vpn";

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
                        {item.content_type === "series" ? "TV Series" : "Movie"} {item.year && `• ${item.year}`}
                      </p>
                    </div>

                    {isWatchable && bestOffer ? (
                      <div className="space-y-2">
                        <div className="bg-gray-50 dark:bg-gray-800/60 p-2.5 rounded-lg text-xs space-y-1">
                          <p className="font-bold text-gray-700 dark:text-gray-300">
                            Best way to watch:
                          </p>
                          <p className="text-indigo-600 dark:text-indigo-400 font-semibold">
                            {bestOffer.service} ({bestOffer.country})
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
