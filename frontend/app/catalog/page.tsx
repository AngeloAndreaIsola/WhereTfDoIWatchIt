"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";

export interface ScrapedCatalogItem {
  id: number;
  scraped_title: string;
  scraped_year: number | null;
  scraped_url: string;
  is_confirmed: boolean;
  confidence_score: number;
  created_at: string;
  source_indicator?: string;
  provider_category?: string;
  provider: {
    id: number;
    slug: string;
    name: string;
    category: string;
  };
  movie?: {
    id: number;
    tmdb_id: number;
    title: string;
    year: number | null;
  } | null;
}

const SOURCE_INDICATOR_COLORS: Record<string, string> = {
  Official: "bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-300",
  Free: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/60 dark:text-emerald-300",
  Unofficial: "bg-amber-100 text-amber-800 dark:bg-amber-900/60 dark:text-amber-300",
  Scraper: "bg-purple-100 text-purple-800 dark:bg-purple-900/60 dark:text-purple-300",
};

function SourceBadge({ source, category }: { source?: string; category?: string }) {
  const label = source || (category === "unofficial" ? "Unofficial" : category === "free" ? "Free" : category === "official" ? "Official" : "Scraper");
  const colorClass = SOURCE_INDICATOR_COLORS[label] || SOURCE_INDICATOR_COLORS["Scraper"];
  return (
    <span
      className={`inline-flex items-center rounded px-2 py-0.5 text-[10px] font-bold ${colorClass}`}
      title={`Source: ${label}`}
    >
      {label}
    </span>
  );
}

export default function CatalogPage() {
  const [items, setItems] = useState<ScrapedCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");

  const fetchCatalog = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (searchQuery.trim()) {
        params.set("q", searchQuery.trim());
      }
      const url = `/api/v2/unofficial/${params.toString() ? `?${params.toString()}` : ""}`;
      const res = await fetch(url);
      if (!res.ok) {
        throw new Error(`Failed to load catalog (${res.status})`);
      }
      const data: { results?: ScrapedCatalogItem[] } = await res.json();
      setItems(data.results || []);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load catalog");
    } finally {
      setLoading(false);
    }
  }, [searchQuery]);

  useEffect(() => {
    fetchCatalog();
  }, [fetchCatalog]);

  return (
    <main className="mx-auto max-w-4xl px-4 py-10 sm:px-6">
      {/* Top navigation link */}
      <div className="mb-6">
        <Link href="/" className="text-sm text-blue-600 hover:underline dark:text-blue-400">
          ← Back to home
        </Link>
      </div>

      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
          Scraped Content Catalog
        </h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Browse movies and series discovered across custom scraper sources and
          small providers. Results include unofficial sources (RaiPlay, BBC,
          NHK, KissKH, etc.) with source indicator badges and ranking by
          language match quality.
        </p>
      </div>

      {/* Search Input */}
      <div className="mb-6 flex gap-2">
        <input
          type="text"
          placeholder="Filter catalog by title..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="flex-1 rounded-lg border border-gray-300 px-3.5 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
        />
        <button
          onClick={fetchCatalog}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-500 dark:bg-blue-500 dark:hover:bg-blue-400"
        >
          Search
        </button>
      </div>

      {/* Loading state */}
      {loading && (
        <div className="flex items-center gap-2 py-8 text-sm text-gray-500 dark:text-gray-400">
          <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
            />
          </svg>
          Loading catalog…
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="mb-6 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Empty State */}
      {!loading && !error && items.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-300 p-8 text-center dark:border-gray-700">
          <p className="text-sm font-medium text-gray-500 dark:text-gray-400">
            No scraped content found in catalog.
          </p>
          <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
            Import custom provider URLs on the Subscriptions page to run scrapers.
          </p>
          <Link
            href="/subscriptions"
            className="mt-4 inline-block rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-blue-500 dark:bg-blue-500 dark:hover:bg-blue-400"
          >
            Go to Subscriptions & Sources →
          </Link>
        </div>
      )}

      {/* Catalog Grid — with source indicator badges and ranked results */}
      {!loading && !error && items.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2">
          {items.map((item) => (
            <div
              key={item.id}
              className="flex flex-col justify-between rounded-xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-800 dark:bg-gray-900"
            >
              <div>
                <div className="flex items-start justify-between gap-2">
                  <h3 className="font-semibold text-gray-900 dark:text-gray-100">
                    {item.scraped_title}{" "}
                    {item.scraped_year && (
                      <span className="font-normal text-gray-500 dark:text-gray-400">
                        ({item.scraped_year})
                      </span>
                    )}
                  </h3>
                  <div className="flex flex-col items-end gap-1">
                    <SourceBadge
                      source={item.source_indicator}
                      category={item.provider_category || item.provider?.category}
                    />
                    <span className="rounded bg-gray-100 px-2 py-0.5 text-[10px] font-bold text-gray-600 dark:bg-gray-800 dark:text-gray-400">
                      {item.provider?.name || "Scraper"}
                    </span>
                  </div>
                </div>

                {item.scraped_url && (
                  <a
                    href={item.scraped_url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-1 block truncate text-xs text-blue-600 hover:underline dark:text-blue-400"
                  >
                    {item.scraped_url}
                  </a>
                )}
              </div>

              <div className="mt-4 flex items-center justify-between border-t border-gray-100 pt-3 text-xs text-gray-500 dark:border-gray-800 dark:text-gray-400">
                <span>
                  Match:{" "}
                  <strong
                    className={
                      item.movie
                        ? "text-emerald-600 dark:text-emerald-400"
                        : "text-amber-600 dark:text-amber-400"
                    }
                  >
                    {item.movie ? item.movie.title : "Unconfirmed"}
                  </strong>
                </span>
                <span>Score: {Math.round(item.confidence_score * 100)}%</span>
              </div>
            </div>
          ))}
        </div>
      )}{" "}
    </main>
  );
}
