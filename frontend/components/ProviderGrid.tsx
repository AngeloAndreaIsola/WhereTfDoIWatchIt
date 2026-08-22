"use client";

import React, { useState } from "react";
import ProviderIcon from "./ProviderIcon";

export interface ProviderItem {
  id: number;
  slug: string;
  name: string;
  category: string;
  is_major: boolean;
  is_enabled: boolean;
  icon_name: string;
  domain?: string;
  last_scraped_at?: string | null;
  scraped_content_count?: number;
  is_subscribed: boolean;
}

interface ProviderGridProps {
  providers: ProviderItem[];
  onToggle: (slug: string) => Promise<void>;
  loading?: boolean;
}

export default function ProviderGrid({ providers, onToggle, loading = false }: ProviderGridProps) {
  const [togglingSlug, setTogglingSlug] = useState<string | null>(null);

  const handleToggle = async (slug: string) => {
    if (togglingSlug) return;
    setTogglingSlug(slug);
    try {
      await onToggle(slug);
    } finally {
      setTogglingSlug(null);
    }
  };

  const majorProviders = providers.filter((p) => p.is_major);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          Streaming Providers
        </h2>
        {loading && <span className="text-xs text-gray-500 dark:text-gray-400">Syncing…</span>}
      </div>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Click a provider to toggle your active subscription status.
      </p>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {majorProviders.map((provider) => {
          const isSelected = provider.is_subscribed;
          const isPending = togglingSlug === provider.slug;

          return (
            <button
              key={provider.slug}
              type="button"
              onClick={() => handleToggle(provider.slug)}
              disabled={isPending}
              aria-pressed={isSelected}
              className={`relative flex flex-col items-center justify-center rounded-xl border p-4 text-center transition-all duration-150 focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                isSelected
                  ? "border-blue-600 bg-blue-50/80 shadow-sm dark:border-blue-500 dark:bg-blue-950/40"
                  : "border-gray-200 bg-white hover:border-gray-300 dark:border-gray-800 dark:bg-gray-900 dark:hover:border-gray-700"
              }`}
            >
              {/* Checkmark badge */}
              {isSelected && (
                <div className="absolute right-2 top-2 flex h-5 w-5 items-center justify-center rounded-full bg-blue-600 text-white dark:bg-blue-500">
                  <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                </div>
              )}

              {/* Icon */}
              <div className="mb-2.5">
                <ProviderIcon slug={provider.slug} name={provider.name} className="h-10 w-10" />
              </div>

              {/* Name */}
              <span
                className={`text-xs font-semibold ${
                  isSelected
                    ? "text-blue-900 dark:text-blue-200"
                    : "text-gray-700 dark:text-gray-300"
                }`}
              >
                {provider.name}
              </span>

              {/* Status pill */}
              <span
                className={`mt-1.5 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                  isSelected
                    ? "bg-blue-200/80 text-blue-800 dark:bg-blue-900/60 dark:text-blue-300"
                    : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"
                }`}
              >
                {isPending ? "Updating…" : isSelected ? "Active" : "Inactive"}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
