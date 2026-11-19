"use client";

import React, { useState } from "react";
import ProviderIcon from "./ProviderIcon";
import { formatRelativeTime } from "@/lib/time";
import type { ProviderAccessMode } from "@/lib/api";

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
  /** How the user holds this service (null when not owned). */
  access_mode?: ProviderAccessMode | null;
  has_scraper?: boolean;
  /** Which collector refreshes this card: a catalogue source, its scraper, or `null` when none is registered (the refresh control stays hidden). */
  refresh_kind?: "catalogue" | "scraper" | null;
  /** Slug of the availability source behind `refresh_kind: "catalogue"`. */
  source_slug?: string | null;
  /** When that collector last ran; `null`/missing renders "Never run". */
  last_run?: string | null;
  /** Error recorded by the last finished run of that collector. */
  last_run_error?: string | null;
  /** Items the collector owns: catalogue/scraped rows, or active offers for plain providers. */
  available_items?: number;
  /** Queue/worker state read server-side — the card must not guess it. */
  refreshing?: boolean;
  catalogue_sync_status?: string;
  catalogue_sync_report?: { pages_scanned?: number; pages_remaining?: number | null; items_unique?: number; last_error?: string };
  /** Pending discovery observations, separate from canonical availability. */
  catalogue_discovery?: {
    inventory_count?: number;
    pending_classification_count?: number;
    last_successful_sync?: string | null;
    historical_checkpoint?: Record<string, number>;
    incremental_checkpoint?: Record<string, number>;
    coverage_status?: string;
    error?: string;
    new_discoveries_last_run?: number;
    duplicate_identities_last_run?: number;
    failed_pages_last_run?: Array<{ partition?: string; page?: number; status?: number; evidence?: string }>;
  };
  /** Site host used to fetch this card's favicon. Absent for catalogue providers, which use the brand badges. */
  favicon_domain?: string;
  /** True when this service is an add-on channel sold through a base platform. */
  is_addon?: boolean;
  /** Base platform slug this add-on requires (only for `is_addon` rows). */
  parent?: string | null;
  /** Display name of the base platform this add-on requires. */
  parent_name?: string | null;
}

interface ProviderGridProps {
  providers: ProviderItem[];
  onToggle: (slug: string) => Promise<void>;
  /**
   * Declare *how* the user holds a service. When provided, each card renders
   * an access-mode selector (included / add-on / free / rent·buy / not owned)
   * instead of relying on the binary subscribe/unsubscribe toggle.
   */
  onSetAccessMode?: (slug: string, mode: ProviderAccessMode) => Promise<void>;
  /** Per-source refresh. Rendered only when the card resolves to a registered collector. Resolves with an optional failure message (ignored here, surfaced by `refreshErrors`). */
  onRefresh?: (slug: string) => Promise<unknown>;
  /** Delete control, offered by the imported-sources section only. */
  onRemove?: (slug: string) => void;
  /**
   * Draw each card's icon from the source's own favicon instead of the brand
   * badges. Set by the imported-sources section: those sites are arbitrary, so
   * only the site itself has a recognisable icon.
   */
  useFavicons?: boolean;
  /** Slugs whose refresh was pressed in this session but is not yet reflected by `provider.refreshing`. */
  refreshPending?: ReadonlySet<string>;
  /** Per-slug failure text from the last press (backend `last_run_error` is the fallback). */
  refreshErrors?: Record<string, string>;
  loading?: boolean;
}

/** How each access mode is shown on a card, in selector order. */
const ACCESS_MODE_OPTIONS: Array<{ value: ProviderAccessMode; label: string }> = [
  { value: "included", label: "Included subscription" },
  { value: "addon", label: "Add-on channel" },
  { value: "free", label: "Free access" },
  { value: "rent_buy", label: "Rent / buy account" },
  { value: "none", label: "Not owned" },
];

const ACCESS_MODE_LABELS: Record<string, string> = {
  included: "Included",
  addon: "Add-on",
  free: "Free",
  rent_buy: "Rent / buy",
};

export default function ProviderGrid({
  providers,
  onToggle,
  onSetAccessMode,
  onRefresh,
  onRemove,
  useFavicons = false,
  refreshPending,
  refreshErrors,
  loading = false,
}: ProviderGridProps) {
  const [togglingSlug, setTogglingSlug] = useState<string | null>(null);
  const [modeSaving, setModeSaving] = useState<string | null>(null);

  const handleToggle = async (slug: string) => {
    if (togglingSlug) return;
    setTogglingSlug(slug);
    try {
      await onToggle(slug);
    } finally {
      setTogglingSlug(null);
    }
  };

  const handleSetMode = async (slug: string, mode: ProviderAccessMode) => {
    if (!onSetAccessMode || modeSaving) return;
    setModeSaving(slug);
    try {
      await onSetAccessMode(slug, mode);
    } finally {
      setModeSaving(null);
    }
  };

  const handleRefresh = async (slug: string) => {
    if (!onRefresh) return;
    await onRefresh(slug);
  };

  const handleRemove = (slug: string) => {
    if (!onRemove) return;
    onRemove(slug);
  };

  return (
    <div className="space-y-4">
      {loading && (
        <div className="flex justify-end">
          <span className="text-xs text-gray-500 dark:text-gray-400">Syncing…</span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {providers.map((provider) => {
          const isSelected = provider.is_subscribed;
          const isPending = togglingSlug === provider.slug || modeSaving === provider.slug;
          const effectiveMode: ProviderAccessMode =
            provider.access_mode ?? (isSelected ? "included" : "none");
          const modeLabel = ACCESS_MODE_LABELS[effectiveMode] ?? "Inactive";
          const isRefreshing =
            Boolean(provider.refreshing) || Boolean(refreshPending?.has(provider.slug));
          const canRefresh = Boolean(onRefresh && provider.refresh_kind);
          const errorText = refreshErrors?.[provider.slug] || provider.last_run_error || "";
          // Favicons need a domain to resolve; sources without one keep the badge.
          const faviconDomain = useFavicons ? provider.domain || undefined : undefined;

          return (
            <div key={provider.slug} className="relative">
              {/* The card itself is the activation control: clicking anywhere
                  that is not one of the sibling buttons toggles the source. */}
              <button
                type="button"
                onClick={() => handleToggle(provider.slug)}
                disabled={isPending}
                aria-pressed={isSelected}
                className={`flex w-full flex-col items-center justify-center rounded-xl border px-3 pb-4 pt-6 text-center transition-all duration-150 focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                  isSelected
                    ? "border-blue-600 bg-blue-50/80 shadow-sm dark:border-blue-500 dark:bg-blue-950/40"
                    : "border-gray-200 bg-white hover:border-gray-300 dark:border-gray-800 dark:bg-gray-900 dark:hover:border-gray-700"
                }`}
              >
                {/* Icon */}
                <div className="mb-2.5">
                  <ProviderIcon
                    slug={provider.slug}
                    name={provider.name}
                    className="h-10 w-10"
                    faviconDomain={faviconDomain}
                  />
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

                {/* Status pill — active state lives here so the corner slots
                    stay free for the refresh/remove controls. */}
                <span
                  className={`mt-1.5 flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                    isSelected
                      ? "bg-blue-200/80 text-blue-800 dark:bg-blue-900/60 dark:text-blue-300"
                      : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"
                  }`}
                >
                  {isSelected && !isPending && (
                    <svg
                      className="h-2.5 w-2.5"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={3}
                      aria-hidden="true"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                  {isPending ? "Updating…" : isSelected ? modeLabel : "Inactive"}
                </span>

                {/* Add-on channels are selectable on their own, but the card
                    must say what base platform they require — a channel the
                    user buys is not the same as the platform underneath it. */}
                {provider.is_addon && (
                  <span className="mt-1 text-[10px] font-medium text-amber-700 dark:text-amber-400">
                    Add-on{provider.parent_name ? ` · needs ${provider.parent_name}` : ""}
                  </span>
                )}

                {/* Operational meta: counts and last run come batched with the
                    list response, so the card never fetches its own data. */}
                <span className="mt-2 text-[10px] text-gray-500 dark:text-gray-400">
                  {provider.available_items ?? 0} {(provider.category === "scraper" || provider.refresh_kind === "scraper" || (!provider.refresh_kind && provider.has_scraper)) ? "active mappings" : "available"}
                </span>
                <span className="text-[10px] text-gray-400 dark:text-gray-500">
                  {isRefreshing ? (
                    <span className="font-medium text-blue-600 dark:text-blue-400">
                      Refreshing…
                    </span>
                  ) : errorText && !provider.last_run ? (
                    <span className="font-medium text-red-500 dark:text-red-400">
                      Last run failed
                    </span>
                  ) : (
                    <>Last run {formatRelativeTime(provider.last_run)}</>
                  )}
                </span>
                {provider.catalogue_sync_status && provider.catalogue_sync_status !== "IDLE" && (
                  <span className="text-[10px] text-gray-400 dark:text-gray-500" role="status">
                    Catalogue {provider.catalogue_sync_status.toLowerCase()}
                    {provider.catalogue_sync_report?.pages_scanned != null &&
                      ` · ${provider.catalogue_sync_report.pages_scanned} pages scanned`}
                    {provider.catalogue_sync_report?.pages_remaining != null &&
                      ` · ${provider.catalogue_sync_report.pages_remaining} remaining`}
                  </span>
                )}
                {provider.catalogue_discovery &&
                  (provider.catalogue_discovery.inventory_count != null ||
                    provider.catalogue_discovery.coverage_status) && (
                  <span className="text-[10px] text-amber-700 dark:text-amber-400" role="status">
                    Pending classification {provider.catalogue_discovery.pending_classification_count ?? 0}
                    {provider.catalogue_discovery.inventory_count != null &&
                      ` · ${provider.catalogue_discovery.inventory_count} discovered`}
                  </span>
                )}
                {provider.catalogue_discovery?.historical_checkpoint &&
                  Object.keys(provider.catalogue_discovery.historical_checkpoint).length > 0 && (
                    <span className="text-[10px] text-gray-400 dark:text-gray-500">
                      Historical {Object.entries(provider.catalogue_discovery.historical_checkpoint)
                        .map(([partition, page]) => `${partition} ${page}`)
                        .join(" · ")}
                    </span>
                  )}
                {provider.catalogue_discovery?.incremental_checkpoint &&
                  Object.keys(provider.catalogue_discovery.incremental_checkpoint).length > 0 && (
                    <span className="text-[10px] text-gray-400 dark:text-gray-500">
                      Incremental {Object.entries(provider.catalogue_discovery.incremental_checkpoint)
                        .map(([partition, page]) => `${partition} ${page}`)
                        .join(" · ")}
                    </span>
                  )}
                {(provider.catalogue_discovery?.new_discoveries_last_run != null ||
                  provider.catalogue_discovery?.duplicate_identities_last_run != null) && (
                  <span className="text-[10px] text-gray-400 dark:text-gray-500">
                    Last run +{provider.catalogue_discovery.new_discoveries_last_run ?? 0}
                    {` · ${provider.catalogue_discovery.duplicate_identities_last_run ?? 0} repeated identities`}
                    {` · ${provider.catalogue_discovery.failed_pages_last_run?.length ?? 0} failed pages`}
                  </span>
                )}
                {provider.catalogue_discovery?.last_successful_sync && (
                  <span className="text-[10px] text-gray-400 dark:text-gray-500">
                    Last successful checkpoint {formatRelativeTime(provider.catalogue_discovery.last_successful_sync)}
                  </span>
                )}
                {provider.catalogue_discovery?.coverage_status && (
                  <span className="text-[10px] text-gray-400 dark:text-gray-500">
                    Coverage {provider.catalogue_discovery.coverage_status.toLowerCase()}
                  </span>
                )}
                {provider.catalogue_discovery?.error && (
                  <span className="text-[10px] font-medium text-amber-700 dark:text-amber-400" role="status">
                    {provider.catalogue_discovery.error}
                  </span>
                )}

                {errorText && !isRefreshing && (
                  <span
                    className="mt-1 line-clamp-2 text-[10px] font-medium text-red-500 dark:text-red-400"
                    role="status"
                  >
                    {errorText}
                  </span>
                )}
              </button>

              {/* Access-mode selector: a sibling of the card so it never
                  triggers the subscribe toggle. It records *how* the user
                  holds the service — included, add-on, free or a rent/buy
                  account — instead of a binary on/off. */}
              {onSetAccessMode && (
                <select
                  value={effectiveMode}
                  onChange={(e) =>
                    handleSetMode(provider.slug, e.target.value as ProviderAccessMode)
                  }
                  disabled={isPending}
                  aria-label={`Access mode for ${provider.name}`}
                  title="How do you access this service?"
                  className={`mt-1.5 w-full rounded-lg border px-2 py-1 text-[11px] focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50 ${
                    isSelected
                      ? "border-blue-300 bg-blue-50 text-blue-900 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-200"
                      : "border-gray-200 bg-white text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300"
                  }`}
                >
                  {ACCESS_MODE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              )}

              {/* Refresh: a sibling of the card, never a child of it, so the
                  press runs one source without toggling the subscription. */}
              {canRefresh && (
                <button
                  type="button"
                  onClick={() => handleRefresh(provider.slug)}
                  disabled={isRefreshing}
                  aria-label={`Refresh ${provider.name}`}
                  title="Refresh this source"
                  className={`absolute left-2 top-2 z-10 rounded-full border p-1.5 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed ${
                    isRefreshing
                      ? "border-blue-300 bg-blue-50 text-blue-500 dark:border-blue-800 dark:bg-blue-950/60 dark:text-blue-400"
                      : "border-gray-200 bg-white text-gray-500 hover:border-blue-300 hover:text-blue-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400 dark:hover:border-blue-700 dark:hover:text-blue-400"
                  }`}
                >
                  <svg
                    className={`h-3.5 w-3.5 ${isRefreshing ? "animate-spin" : ""}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                    aria-hidden="true"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                    />
                  </svg>
                </button>
              )}

              {/* Remove: same sibling pattern, offered only where the caller
                  (imported sources) allows deletion. */}
              {onRemove && (
                <button
                  type="button"
                  onClick={() => handleRemove(provider.slug)}
                  disabled={isPending}
                  aria-label={`Remove ${provider.name}`}
                  title="Remove source"
                  className="absolute right-2 top-2 z-10 rounded-full border border-gray-200 bg-white p-1.5 text-gray-400 transition-colors hover:border-red-300 hover:text-red-500 focus:outline-none focus:ring-2 focus:ring-red-400 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-500 dark:hover:border-red-800 dark:hover:text-red-400"
                >
                  <svg
                    className="h-3.5 w-3.5"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2.5}
                    aria-hidden="true"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
