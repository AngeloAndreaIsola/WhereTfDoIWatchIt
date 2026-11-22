"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import TitlePoster from "@/components/TitlePoster";
import { useLocale } from "@/lib/i18n/LocaleProvider";
import {
  type LeavingSoonResponse,
  fetchLeavingSoon,
} from "@/lib/api";
import {
  groupLeavingOffers,
  formatDepartureDate,
  formatObservedDate,
  getAccessTypeLabel,
  type LeavingSoonDepartureEntry,
} from "@/lib/leavingSoon";
import { leavingSoonCache } from "@/lib/navigationCache";

interface LeavingSoonSectionProps {
  country?: string;
  refreshKey?: number;
}

export function LeavingSoonSection({
  country = "",
  refreshKey = 0,
}: LeavingSoonSectionProps) {
  const { t, locale } = useLocale();
  const normalizedCountry = country.trim().toUpperCase();

  const cacheKey = normalizedCountry ? `${normalizedCountry}:30` : "";
  const cachedData = cacheKey ? (leavingSoonCache.get(cacheKey) as LeavingSoonResponse | undefined) : undefined;

  const [data, setData] = useState<LeavingSoonResponse | null>(cachedData ?? null);
  const [loading, setLoading] = useState<boolean>(!cachedData && Boolean(normalizedCountry));
  const [error, setError] = useState<string | null>(null);
  const [isOffline, setIsOffline] = useState<boolean>(false);

  const abortRef = useRef<AbortController | null>(null);
  const fetchGenRef = useRef(0);
  const lastFetchedKeyRef = useRef<string>("");
  const dataRef = useRef<LeavingSoonResponse | null>(data);
  dataRef.current = data;

  const loadData = useCallback(
    async (force: boolean = false) => {
      if (!normalizedCountry) {
        setLoading(false);
        return;
      }

      const key = `${normalizedCountry}:30`;
      if (!force && lastFetchedKeyRef.current === key && dataRef.current) {
        return;
      }

      if (!force) {
        const cached = leavingSoonCache.get(key) as LeavingSoonResponse | undefined;
        if (cached) {
          setData(cached);
          setError(null);
          setIsOffline(false);
          setLoading(false);
          lastFetchedKeyRef.current = key;
          return;
        }
      }

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const gen = ++fetchGenRef.current;

      setLoading(true);
      setError(null);
      setIsOffline(false);

      if (typeof navigator !== "undefined" && !navigator.onLine) {
        setIsOffline(true);
        setLoading(false);
        return;
      }

      try {
        const rawResult = await fetchLeavingSoon(normalizedCountry, 30, controller.signal);
        if (controller.signal.aborted || gen !== fetchGenRef.current) {
          return;
        }
        // Normalize response shape in case mock returns partial/unexpected payload
        const result: LeavingSoonResponse = {
          country: rawResult.country || normalizedCountry,
          horizon: rawResult.horizon || {
            days: 30,
            as_of: new Date().toISOString(),
            until: new Date(Date.now() + 30 * 86_400_000).toISOString(),
          },
          items: Array.isArray(rawResult.items) ? rawResult.items : [],
          coverage: rawResult.coverage || {
            status: "no_confirmed_expiry_data",
            watchlist_titles: 0,
            titles_with_confirmed_expiry_data: 0,
            offers_with_confirmed_expiry_data: 0,
            scope: "selected_country_watchlist_only",
            completeness: "partial_not_all_providers_report_expiry",
          },
        };

        // Verify country matches current country (ignore stale responses)
        if (result.country && result.country.toUpperCase() !== normalizedCountry) {
          return;
        }

        setData(result);
        lastFetchedKeyRef.current = key;
        leavingSoonCache.set({
          key,
          items: [result],
          page: 1,
          paginationInfo: { totalPages: 1, hasNext: false, hasPrevious: false },
          filters: null,
          scrollY: 0,
          cachedAt: Date.now(),
        });
      } catch (err: unknown) {
        if (controller.signal.aborted || gen !== fetchGenRef.current) {
          return;
        }
        const isNetworkErr =
          (typeof navigator !== "undefined" && !navigator.onLine) ||
          (err instanceof Error &&
            (err.message.toLowerCase().includes("failed to fetch") ||
              err.message.toLowerCase().includes("network") ||
              err.message.toLowerCase().includes("offline")));

        if (isNetworkErr) {
          setIsOffline(true);
        } else {
          setError(err instanceof Error ? err.message : "Failed to load leaving soon titles");
        }
      } finally {
        if (!controller.signal.aborted && gen === fetchGenRef.current) {
          setLoading(false);
        }
      }
    },
    [normalizedCountry]
  );

  const prevKeyRef = useRef<string>("");
  const prevRefreshKeyRef = useRef<number>(refreshKey);

  useEffect(() => {
    const key = `${normalizedCountry}:30`;
    const isNewCountry = key !== prevKeyRef.current;
    const isRefresh = refreshKey !== prevRefreshKeyRef.current;
    prevKeyRef.current = key;
    prevRefreshKeyRef.current = refreshKey;

    if (isNewCountry || isRefresh) {
      loadData(isRefresh && refreshKey > 0);
    }
  }, [normalizedCountry, refreshKey, loadData]);

  const handleRetry = () => {
    if (cacheKey) {
      leavingSoonCache.delete(cacheKey);
    }
    loadData(true);
  };

  const grouped = useMemo(() => {
    if (!data) {
      return { next_7_days: [], days_8_14: [], days_15_30: [], totalDepartures: 0 };
    }
    const asOf = data.horizon?.as_of || new Date().toISOString();
    return groupLeavingOffers(data.items, asOf);
  }, [data]);

  if (!normalizedCountry) {
    return null;
  }

  const renderCard = (entry: LeavingSoonDepartureEntry) => {
    const { movie, offer } = entry;
    const href = movie.content_type === "series" ? `/tv/${movie.tmdb_id}` : `/movie/${movie.tmdb_id}`;
    const formattedDate = formatDepartureDate(offer.available_until, locale);
    const observedDate = offer.last_verified_at ? formatObservedDate(offer.last_verified_at, locale) : null;
    const cardKey = `${movie.tmdb_id}-${offer.provider}-${offer.season_number ?? "all"}-${offer.episode_number ?? "all"}-${offer.available_until}`;

    return (
      <li
        key={cardKey}
        data-testid="leaving-soon-card"
        className="flex gap-3 rounded-lg border border-gray-200 bg-white p-3 shadow-2xs dark:border-gray-700 dark:bg-gray-900 transition-shadow hover:shadow-xs"
      >
        <Link
          href={href}
          className="relative aspect-[2/3] w-14 shrink-0 overflow-hidden rounded bg-gray-100 dark:bg-gray-800 focus:outline-hidden focus:ring-2 focus:ring-blue-500"
          tabIndex={0}
          aria-label={`${movie.title} poster`}
        >
          <TitlePoster
            tmdbId={movie.tmdb_id}
            contentType={movie.content_type}
            posterPath={movie.poster_path}
            title={movie.title || "Poster"}
          />
        </Link>

        <div className="flex flex-1 flex-col justify-between min-w-0">
          <div className="min-w-0">
            <Link
              href={href}
              className="font-semibold text-xs text-gray-900 dark:text-gray-100 hover:underline truncate block focus:outline-hidden focus:ring-1 focus:ring-blue-500 rounded-xs"
            >
              {movie.title}
              {movie.year ? ` (${movie.year})` : ""}
            </Link>

            {/* Provider and Country tag */}
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400">
              <span className="font-medium text-gray-800 dark:text-gray-200">
                {offer.provider_display || offer.provider}
              </span>
              <span className="rounded bg-gray-100 px-1 py-0.2 text-[10px] font-mono text-gray-600 dark:bg-gray-800 dark:text-gray-300">
                {offer.country}
              </span>
              {offer.season_number != null && (
                <span className="rounded bg-gray-100 px-1 py-0.2 text-[10px] text-gray-600 dark:bg-gray-800 dark:text-gray-300">
                  {offer.episode_number != null
                    ? t("leavingSoon.seasonEpisodeLabel", { season: offer.season_number, episode: offer.episode_number })
                    : t("leavingSoon.seasonLabel", { season: offer.season_number })}
                </span>
              )}
            </div>

            {/* Departure kind and Access model */}
            <div className="mt-1.5 flex flex-wrap items-center gap-1">
              {offer.departure_kind === "last_option" ? (
                <span
                  data-testid="last-option-badge"
                  className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold bg-amber-100 text-amber-900 dark:bg-amber-950/60 dark:text-amber-200 border border-amber-300 dark:border-amber-700"
                >
                  <span aria-hidden="true">⚠️</span>
                  <span>{t("leavingSoon.lastOption", { country: offer.country })}</span>
                </span>
              ) : (
                <span
                  data-testid="leaving-provider-badge"
                  className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300"
                >
                  {t("leavingSoon.leavingProvider", { provider: offer.provider_display || offer.provider, country: offer.country })}
                </span>
              )}
              <span
                data-testid="access-type-chip"
                className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] text-gray-600 dark:text-gray-400 border border-gray-200 dark:border-gray-700"
              >
                {getAccessTypeLabel(offer.access_type, t)}
              </span>
            </div>
          </div>

          {/* Expiration date and verified date */}
          <div className="mt-2 pt-1 border-t border-gray-100 dark:border-gray-800 flex flex-wrap items-center justify-between gap-1 text-[11px]">
            <div>
              <span className="sr-only">
                {t("leavingSoon.srDepartureDate", { date: formattedDate })}
              </span>
              <time dateTime={offer.available_until} className="font-semibold text-red-600 dark:text-red-400">
                {t("leavingSoon.leavingDate", { date: formattedDate })}
              </time>
            </div>
            {observedDate && (
              <span className="text-[10px] text-gray-400 dark:text-gray-500">
                {t("leavingSoon.lastVerified", { date: observedDate })}
              </span>
            )}
          </div>
        </div>
      </li>
    );
  };

  const coverage = data?.coverage;
  const watchlistTitles = coverage?.watchlist_titles ?? 0;
  const coverageStatus = coverage?.status;

  return (
    <section
      aria-labelledby="leaving-soon-heading"
      className="rounded-xl border border-gray-200 bg-white p-4 shadow-xs dark:border-gray-700 dark:bg-gray-800"
    >
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between mb-4">
        <div>
          <h2
            id="leaving-soon-heading"
            className="text-sm font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-1.5"
          >
            <span aria-hidden="true">⏳</span>
            <span>{t("leavingSoon.title")} — {normalizedCountry}</span>
          </h2>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
            {t("leavingSoon.description", { country: normalizedCountry })}
          </p>
        </div>
      </div>

      {/* State 1: Loading */}
      {loading && (
        <div
          role="status"
          aria-live="polite"
          data-testid="leaving-soon-loading"
          className="py-8 text-center text-xs text-gray-500 dark:text-gray-400 animate-pulse"
        >
          {t("leavingSoon.stateLoading")}
        </div>
      )}

      {/* State 7: Offline */}
      {!loading && isOffline && (
        <div
          role="status"
          data-testid="leaving-soon-offline"
          className="p-4 bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-800 rounded-lg text-amber-800 dark:text-amber-200 text-xs"
        >
          <p className="font-medium">{t("leavingSoon.stateOffline")}</p>
          <button
            type="button"
            onClick={handleRetry}
            className="mt-2 inline-flex items-center px-3 py-1 rounded bg-amber-100 hover:bg-amber-200 text-amber-800 dark:bg-amber-800 dark:hover:bg-amber-700 dark:text-amber-100 font-medium transition-colors cursor-pointer"
          >
            {t("leavingSoon.retry")}
          </button>
        </div>
      )}

      {/* State 6: API Error */}
      {!loading && !isOffline && error && (
        <div
          role="alert"
          data-testid="leaving-soon-error"
          className="p-4 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-lg text-red-800 dark:text-red-200 text-xs"
        >
          <p className="font-medium">
            {t("leavingSoon.stateError")}: {error}
          </p>
          <button
            type="button"
            onClick={handleRetry}
            className="mt-2 inline-flex items-center px-3 py-1 rounded bg-red-100 hover:bg-red-200 text-red-800 dark:bg-red-800 dark:hover:bg-red-700 dark:text-red-100 font-medium transition-colors cursor-pointer"
          >
            {t("leavingSoon.retry")}
          </button>
        </div>
      )}

      {/* State 5: Empty Watchlist */}
      {!loading && !isOffline && !error && data && watchlistTitles === 0 && (
        <div
          role="status"
          data-testid="leaving-soon-empty-watchlist"
          className="py-6 text-center border border-dashed border-gray-200 dark:border-gray-700 rounded-lg p-4"
        >
          <p className="text-xs font-medium text-gray-700 dark:text-gray-300">
            {t("leavingSoon.stateEmptyWatchlist")}
          </p>
          <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1">
            {t("leavingSoon.stateEmptyWatchlistDesc")}
          </p>
        </div>
      )}

      {/* State 3: No confirmed expiry evidence */}
      {!loading &&
        !isOffline &&
        !error &&
        data &&
        watchlistTitles > 0 &&
        coverageStatus === "no_confirmed_expiry_data" && (
          <div
            role="status"
            data-testid="leaving-soon-no-evidence"
            className="py-6 text-center border border-dashed border-gray-200 dark:border-gray-700 rounded-lg p-4"
          >
            <p className="text-xs font-semibold text-gray-700 dark:text-gray-300">
              {t("leavingSoon.stateNoEvidence")}
            </p>
            <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1">
              {t("leavingSoon.stateNoEvidenceDesc", { country: normalizedCountry })}
            </p>
            <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-2">
              {t("leavingSoon.coverageNoticeSparse")}
            </p>
          </div>
        )}

      {/* State 4: Confirmed evidence but nothing in horizon */}
      {!loading &&
        !isOffline &&
        !error &&
        data &&
        watchlistTitles > 0 &&
        (coverageStatus === "no_departures_within_horizon" ||
          (coverageStatus === "partial_confirmed_departures" && grouped.totalDepartures === 0)) && (
          <div
            role="status"
            data-testid="leaving-soon-none-in-horizon"
            className="py-6 text-center border border-dashed border-gray-200 dark:border-gray-700 rounded-lg p-4"
          >
            <p className="text-xs font-semibold text-gray-700 dark:text-gray-300">
              {t("leavingSoon.stateNoneInHorizon")}
            </p>
            <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1">
              {t("leavingSoon.stateNoneInHorizonDesc", { country: normalizedCountry })}
            </p>
            <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-2">
              {t("leavingSoon.coverageNoticeSparse")}
            </p>
          </div>
        )}

      {/* State 2: Confirmed departures */}
      {!loading && !isOffline && !error && data && grouped.totalDepartures > 0 && (
        <div data-testid="leaving-soon-departures" className="space-y-6">
          {grouped.next_7_days.length > 0 && (
            <div data-testid="group-next-7-days" className="space-y-2">
              <h3 className="text-xs font-bold uppercase tracking-wider text-red-700 dark:text-red-400 flex items-center gap-1.5">
                <span aria-hidden="true" className="w-2 h-2 rounded-full bg-red-500 inline-block" />
                <span>{t("leavingSoon.groupNext7Days")} ({grouped.next_7_days.length})</span>
              </h3>
              <ul role="list" className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {grouped.next_7_days.map((entry) => renderCard(entry))}
              </ul>
            </div>
          )}

          {grouped.days_8_14.length > 0 && (
            <div data-testid="group-days-8-14" className="space-y-2">
              <h3 className="text-xs font-bold uppercase tracking-wider text-amber-700 dark:text-amber-400 flex items-center gap-1.5">
                <span aria-hidden="true" className="w-2 h-2 rounded-full bg-amber-500 inline-block" />
                <span>{t("leavingSoon.groupDays8To14")} ({grouped.days_8_14.length})</span>
              </h3>
              <ul role="list" className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {grouped.days_8_14.map((entry) => renderCard(entry))}
              </ul>
            </div>
          )}

          {grouped.days_15_30.length > 0 && (
            <div data-testid="group-days-15-30" className="space-y-2">
              <h3 className="text-xs font-bold uppercase tracking-wider text-blue-700 dark:text-blue-400 flex items-center gap-1.5">
                <span aria-hidden="true" className="w-2 h-2 rounded-full bg-blue-500 inline-block" />
                <span>{t("leavingSoon.groupDays15To30")} ({grouped.days_15_30.length})</span>
              </h3>
              <ul role="list" className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {grouped.days_15_30.map((entry) => renderCard(entry))}
              </ul>
            </div>
          )}

          {/* Honest Coverage note */}
          <div
            data-testid="leaving-soon-coverage-note"
            className="mt-4 pt-3 border-t border-gray-100 dark:border-gray-700/60 text-xs text-gray-500 dark:text-gray-400 flex items-start gap-2"
          >
            <span aria-hidden="true">ℹ️</span>
            <span>{t("leavingSoon.coverageNoticeSparse")}</span>
          </div>
        </div>
      )}
    </section>
  );
}

export default LeavingSoonSection;
