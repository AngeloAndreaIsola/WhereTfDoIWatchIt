"use client";

import React from "react";
import { ProviderContentItem } from "@/lib/candidateReviewApi";

interface MappingAvailabilitySummaryProps {
  item: ProviderContentItem;
}

export function MappingAvailabilitySummary({ item }: MappingAvailabilitySummaryProps) {
  const existingMapping = item.existing_mapping;
  const availability = item.availability_summary;

  const isMapped = Boolean(existingMapping && existingMapping.tmdb_id);
  const hasActiveOffers = availability.active_offers > 0;
  const isMappedWithoutOffers = isMapped && !hasActiveOffers;

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-850 p-4 space-y-4">
      {/* Critical Operational Rule Header: Mapped ≠ Watchable */}
      {isMappedWithoutOffers && (
        <div className="rounded-md border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-950/40 p-3 text-xs text-amber-900 dark:text-amber-200">
          <div className="font-semibold flex items-center gap-1.5">
            <span>⚠️</span>
            <span>CRITICAL: Mapping does NOT mean watchable (Mapped ≠ Watchable)</span>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-amber-800 dark:text-amber-300">
            This content is mapped to TMDb {existingMapping?.tmdb_id}, but has ZERO active playback offers. Mapping records identity, while offers track actual streaming availability.
          </p>
        </div>
      )}

      {/* Grid: Existing Mapping vs Availability */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
        {/* Mapping Status */}
        <div className="rounded border border-gray-100 dark:border-gray-800 bg-gray-50/70 dark:bg-gray-900/40 p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 text-[10px]">
              Mapping Record (Identity)
            </span>
            {isMapped ? (
              <span
                className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                  existingMapping?.is_manual
                    ? "bg-purple-100 text-purple-800 dark:bg-purple-950/70 dark:text-purple-300"
                    : "bg-blue-100 text-blue-800 dark:bg-blue-950/70 dark:text-blue-300"
                }`}
              >
                {existingMapping?.is_manual ? "MANUAL MAPPING" : "AUTOMATED MAPPING"}
              </span>
            ) : (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300">
                UNMAPPED
              </span>
            )}
          </div>

          {isMapped && existingMapping ? (
            <div className="space-y-1 text-gray-700 dark:text-gray-300">
              <div className="font-medium text-sm text-gray-900 dark:text-gray-100">
                {existingMapping.title || item.native_title}{" "}
                {existingMapping.year ? `(${existingMapping.year})` : ""}
              </div>
              <div className="flex items-center gap-2 font-mono text-[11px] text-gray-500 dark:text-gray-400">
                <span>TMDb ID: {existingMapping.tmdb_id}</span>
                <span>• Method: {existingMapping.match_method}</span>
                {existingMapping.revision !== undefined && (
                  <span>• Rev: {existingMapping.revision ?? "null"}</span>
                )}
              </div>
              <div className="text-[11px] text-gray-500 dark:text-gray-400">
                Confidence: {Math.round(existingMapping.confidence * 100)}%
              </div>
            </div>
          ) : (
            <p className="text-gray-500 dark:text-gray-400 italic">
              No existing mapping record found. This item has never been resolved to a TMDb title.
            </p>
          )}
        </div>

        {/* Current Availability / Offer Status */}
        <div className="rounded border border-gray-100 dark:border-gray-800 bg-gray-50/70 dark:bg-gray-900/40 p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 text-[10px]">
              Actual Offer Availability (Catalog)
            </span>
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                availability.status === "active_offer"
                  ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/70 dark:text-emerald-300"
                  : availability.status === "no_offer"
                  ? "bg-amber-100 text-amber-800 dark:bg-amber-950/70 dark:text-amber-300"
                  : "bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300"
              }`}
            >
              {availability.status_label || availability.status}
            </span>
          </div>

          <div className="space-y-1 text-gray-700 dark:text-gray-300">
            <div className="flex items-center gap-2 font-medium">
              <span>Active Offers: {availability.active_offers}</span>
              <span>(Total: {availability.total_offers})</span>
            </div>

            {availability.countries && availability.countries.length > 0 && (
              <div className="text-[11px] text-gray-600 dark:text-gray-300">
                Regions: {availability.countries.join(", ")}
              </div>
            )}

            {availability.services && availability.services.length > 0 && (
              <div className="text-[11px] text-gray-600 dark:text-gray-300">
                Providers: {availability.services.join(", ")}
              </div>
            )}

            {availability.disclaimer && (
              <div className="text-[11px] text-gray-500 dark:text-gray-400 italic">
                {availability.disclaimer}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
