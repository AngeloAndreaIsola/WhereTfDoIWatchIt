"use client";

import { useState } from "react";

export interface ProviderItem {
  id?: number;
  slug: string;
  name: string;
  category?: string;
  is_major?: boolean;
}

interface ProviderFilterProps {
  providers: ProviderItem[];
  selectedProviders: string[];
  onChange: (selectedSlugs: string[]) => void;
}

export function ProviderFilter({
  providers,
  selectedProviders,
  onChange,
}: ProviderFilterProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [allowMixed, setAllowMixed] = useState(true);

  // Categorize providers
  const majorProviders = providers.filter((p) => p.is_major);
  const bvodProviders = providers.filter((p) => p.category === "bvod" && !p.is_major);
  const scraperProviders = providers.filter(
    (p) => p.category === "scraper" && !p.is_major
  );

  const majorSlugs = majorProviders.map((p) => p.slug);
  const bvodSlugs = bvodProviders.map((p) => p.slug);
  const scraperSlugs = scraperProviders.map((p) => p.slug);

  const isAllMajorSelected =
    majorSlugs.length > 0 && majorSlugs.every((s) => selectedProviders.includes(s));
  const isAllBvodSelected =
    bvodSlugs.length > 0 && bvodSlugs.every((s) => selectedProviders.includes(s));
  const isAllScraperSelected =
    scraperSlugs.length > 0 &&
    scraperSlugs.every((s) => selectedProviders.includes(s));

  const handleTogglePreset = (presetSlugs: string[]) => {
    const isCurrentlyAllSelected = presetSlugs.every((s) =>
      selectedProviders.includes(s)
    );

    if (allowMixed) {
      if (isCurrentlyAllSelected) {
        // Remove presetSlugs
        onChange(selectedProviders.filter((s) => !presetSlugs.includes(s)));
      } else {
        // Add remaining presetSlugs
        const newSet = new Set([...selectedProviders, ...presetSlugs]);
        onChange(Array.from(newSet));
      }
    } else {
      // Single category selection
      if (isCurrentlyAllSelected) {
        onChange([]);
      } else {
        onChange([...presetSlugs]);
      }
    }
  };

  const handleToggleIndividual = (slug: string) => {
    const isSelected = selectedProviders.includes(slug);
    if (allowMixed) {
      if (isSelected) {
        onChange(selectedProviders.filter((s) => s !== slug));
      } else {
        onChange([...selectedProviders, slug]);
      }
    } else {
      if (isSelected) {
        onChange([]);
      } else {
        onChange([slug]);
      }
    }
  };

  const handleClear = () => {
    onChange([]);
  };

  const selectedCount = selectedProviders.length;

  return (
    <div className="relative">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          className="w-full flex items-center justify-between px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100 font-medium"
        >
          <span>
            {selectedCount === 0
              ? "All Providers"
              : `${selectedCount} Provider${selectedCount > 1 ? "s" : ""} Selected`}
          </span>
          <span className="text-xs text-gray-400">{isOpen ? "▲" : "▼"}</span>
        </button>

        {selectedCount > 0 && (
          <button
            type="button"
            onClick={handleClear}
            className="px-2.5 py-1.5 text-xs font-semibold text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30 rounded-lg border border-red-200 dark:border-red-800 transition-colors whitespace-nowrap"
            title="Clear provider filter"
          >
            Clear Filter
          </button>
        )}
      </div>

      {isOpen && (
        <div className="absolute z-30 mt-2 w-full sm:w-96 right-0 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl p-4 space-y-4 max-h-96 overflow-y-auto">
          {/* Header controls: Mixed Selection checkbox & Clear button */}
          <div className="flex items-center justify-between border-b pb-2 border-gray-200 dark:border-gray-700">
            <label className="flex items-center gap-2 text-xs font-semibold text-gray-700 dark:text-gray-300 cursor-pointer">
              <input
                type="checkbox"
                checked={allowMixed}
                onChange={(e) => setAllowMixed(e.target.checked)}
                className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
              />
              Allow mixed selection
            </label>

            <button
              type="button"
              onClick={handleClear}
              className="text-xs text-red-600 dark:text-red-400 hover:underline font-medium"
            >
              Clear Filter
            </button>
          </div>

          {/* Major Providers */}
          {majorProviders.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-between bg-gray-50 dark:bg-gray-700/50 px-2 py-1 rounded">
                <span className="text-xs font-bold text-gray-700 dark:text-gray-300 uppercase tracking-wider">
                  Major Providers
                </span>
                <label className="flex items-center gap-1.5 text-xs font-semibold text-indigo-600 dark:text-indigo-400 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={isAllMajorSelected}
                    onChange={() => handleTogglePreset(majorSlugs)}
                    className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                  />
                  All Major
                </label>
              </div>
              <div className="grid grid-cols-2 gap-1.5 pl-2">
                {majorProviders.map((p) => (
                  <label
                    key={p.slug}
                    className="flex items-center gap-2 text-xs text-gray-700 dark:text-gray-300 cursor-pointer hover:text-indigo-600"
                  >
                    <input
                      type="checkbox"
                      checked={selectedProviders.includes(p.slug)}
                      onChange={() => handleToggleIndividual(p.slug)}
                      className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                    />
                    <span className="truncate">{p.name}</span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* BVOD Providers (Free Broadcasters) */}
          {bvodProviders.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-between bg-gray-50 dark:bg-gray-700/50 px-2 py-1 rounded">
                <span className="text-xs font-bold text-gray-700 dark:text-gray-300 uppercase tracking-wider">
                  BVOD / Free
                </span>
                <label className="flex items-center gap-1.5 text-xs font-semibold text-emerald-600 dark:text-emerald-400 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={isAllBvodSelected}
                    onChange={() => handleTogglePreset(bvodSlugs)}
                    className="rounded border-gray-300 text-emerald-600 focus:ring-emerald-500"
                  />
                  All BVOD
                </label>
              </div>
              <div className="grid grid-cols-2 gap-1.5 pl-2">
                {bvodProviders.map((p) => (
                  <label
                    key={p.slug}
                    className="flex items-center gap-2 text-xs text-gray-700 dark:text-gray-300 cursor-pointer hover:text-emerald-600"
                  >
                    <input
                      type="checkbox"
                      checked={selectedProviders.includes(p.slug)}
                      onChange={() => handleToggleIndividual(p.slug)}
                      className="rounded border-gray-300 text-emerald-600 focus:ring-emerald-500"
                    />
                    <span className="truncate">{p.name}</span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Scraper Providers */}
          {scraperProviders.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-between bg-gray-50 dark:bg-gray-700/50 px-2 py-1 rounded">
                <span className="text-xs font-bold text-gray-700 dark:text-gray-300 uppercase tracking-wider">
                  Scraper Sources
                </span>
                <label className="flex items-center gap-1.5 text-xs font-semibold text-purple-600 dark:text-purple-400 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={isAllScraperSelected}
                    onChange={() => handleTogglePreset(scraperSlugs)}
                    className="rounded border-gray-300 text-purple-600 focus:ring-purple-500"
                  />
                  All Scrapers
                </label>
              </div>
              <div className="grid grid-cols-2 gap-1.5 pl-2">
                {scraperProviders.map((p) => (
                  <label
                    key={p.slug}
                    className="flex items-center gap-2 text-xs text-gray-700 dark:text-gray-300 cursor-pointer hover:text-purple-600"
                  >
                    <input
                      type="checkbox"
                      checked={selectedProviders.includes(p.slug)}
                      onChange={() => handleToggleIndividual(p.slug)}
                      className="rounded border-gray-300 text-purple-600 focus:ring-purple-500"
                    />
                    <span className="truncate">{p.name}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
