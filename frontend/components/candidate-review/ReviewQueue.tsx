"use client";

import React from "react";
import { ProviderContentItem } from "@/lib/candidateReviewApi";

interface ReviewQueueProps {
  items: ProviderContentItem[];
  selectedId: string | number | null;
  onSelectItem: (item: ProviderContentItem) => void;
  loading: boolean;
  filterProvider: string;
  onFilterProviderChange: (provider: string) => void;
  filterStatus: string;
  onFilterStatusChange: (status: string) => void;
  filterContentType: string;
  onFilterContentTypeChange: (contentType: string) => void;
  searchQuery: string;
  onSearchQueryChange: (query: string) => void;
  page: number;
  pages: number;
  totalItems: number;
  onPageChange: (newPage: number) => void;
}

export function ReviewQueue({
  items,
  selectedId,
  onSelectItem,
  loading,
  filterProvider,
  onFilterProviderChange,
  filterStatus,
  onFilterStatusChange,
  filterContentType,
  onFilterContentTypeChange,
  searchQuery,
  onSearchQueryChange,
  page,
  pages,
  totalItems,
  onPageChange,
}: ReviewQueueProps) {
  // Extract unique providers for filter dropdown
  const providers = Array.from(new Set(items.map((i) => i.provider_slug))).filter(Boolean);

  return (
    <aside className="w-full md:w-80 flex-shrink-0 flex flex-col border-r border-gray-200 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-900/40 p-4 space-y-4">
      <div>
        <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
          Unresolved Queue ({totalItems})
        </h2>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
          Provider items requiring TMDb identity verification
        </p>
      </div>

      {/* Filters & Search - mapped to Wave 2A query params: search, provider, status, content_type */}
      <div className="space-y-2">
        <input
          type="text"
          placeholder="Search title or ID…"
          value={searchQuery}
          onChange={(e) => onSearchQueryChange(e.target.value)}
          aria-label="Filter queue items"
          className="w-full rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-1.5 text-xs text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />

        <div className="flex gap-2">
          <select
            value={filterProvider}
            onChange={(e) => onFilterProviderChange(e.target.value)}
            aria-label="Filter by provider"
            className="w-1/2 rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1 text-xs text-gray-700 dark:text-gray-300 focus:outline-none"
          >
            <option value="">All Providers</option>
            {providers.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>

          <select
            value={filterStatus}
            onChange={(e) => onFilterStatusChange(e.target.value)}
            aria-label="Filter by review state"
            className="w-1/2 rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1 text-xs text-gray-700 dark:text-gray-300 focus:outline-none"
          >
            <option value="">All Review States</option>
            <option value="pending">Pending</option>
            <option value="accepted">Accepted</option>
            <option value="rejected">Rejected</option>
            <option value="unmapped">Unmapped</option>
            <option value="mapped">Mapped</option>
          </select>
        </div>

        <div className="flex gap-2">
          <select
            value={filterContentType}
            onChange={(e) => onFilterContentTypeChange(e.target.value)}
            aria-label="Filter by content type"
            className="w-full rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1 text-xs text-gray-700 dark:text-gray-300 focus:outline-none"
          >
            <option value="">All Types (Movies & Series)</option>
            <option value="movie">Movies Only</option>
            <option value="series">Series Only</option>
          </select>
        </div>
      </div>

      {/* Queue items list */}
      <div className="flex-1 overflow-y-auto space-y-2 max-h-[520px] pr-1">
        {loading && (
          <p className="text-xs text-center py-6 text-gray-500">Loading queue items…</p>
        )}

        {!loading && items.length === 0 && (
          <div className="text-center py-8 text-xs text-gray-500 dark:text-gray-400">
            Queue is empty. No review needed.
          </div>
        )}

        {!loading &&
          items.map((item) => {
            const isSelected = selectedId === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onSelectItem(item)}
                aria-pressed={isSelected}
                data-testid={`queue-item-${item.id}`}
                className={`w-full text-left p-3 rounded-lg border transition-all ${
                  isSelected
                    ? "border-blue-500 bg-blue-50/80 dark:bg-blue-950/40 ring-1 ring-blue-500 shadow-sm"
                    : "border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-800/80 hover:border-gray-300 dark:hover:border-gray-700"
                }`}
              >
                <div className="flex items-center justify-between gap-1 mb-1">
                  <span className="text-[10px] font-semibold tracking-wide uppercase px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
                    {item.provider_name || item.provider_slug}
                  </span>
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                      item.resolution_category === "ambiguous"
                        ? "bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
                        : item.resolution_category === "conflict"
                        ? "bg-red-100 text-red-800 dark:bg-red-950/60 dark:text-red-300"
                        : "bg-blue-100 text-blue-800 dark:bg-blue-950/60 dark:text-blue-300"
                    }`}
                  >
                    {item.resolution_category}
                  </span>
                </div>

                <div className="font-medium text-sm text-gray-900 dark:text-gray-100 truncate">
                  {item.native_title}
                </div>

                <div className="text-[11px] font-mono text-gray-500 dark:text-gray-400 truncate">
                  ID: {item.provider_content_id}
                </div>

                <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 mt-1">
                  <span>
                    {item.content_type} {item.release_year ? `• ${item.release_year}` : ""}
                  </span>
                  <span>{item.candidate_count} candidates</span>
                </div>

                {/* Offer notice indicator */}
                <div className="mt-1.5 text-[10px] text-gray-400 dark:text-gray-500 truncate">
                  Status: {item.availability_summary?.status_label ?? "Unknown"}
                </div>
              </button>
            );
          })}
      </div>

      {/* Pagination Controls */}
      <div className="pt-3 border-t border-gray-200 dark:border-gray-800 flex items-center justify-between text-xs text-gray-600 dark:text-gray-400">
        <button
          type="button"
          disabled={loading || page <= 1}
          onClick={() => onPageChange(page - 1)}
          className="px-2.5 py-1 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-750"
        >
          Previous
        </button>

        <span className="font-medium">
          Page {page} of {Math.max(pages, 1)}
        </span>

        <button
          type="button"
          disabled={loading || page >= pages}
          onClick={() => onPageChange(page + 1)}
          className="px-2.5 py-1 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-750"
        >
          Next
        </button>
      </div>
    </aside>
  );
}
