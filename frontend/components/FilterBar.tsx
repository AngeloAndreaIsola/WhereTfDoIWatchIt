"use client";

import { useEffect, useRef, useState } from "react";
import ViewerLanguageBuilder from "./ViewerLanguageBuilder";
import { useViewerPreferences } from "./ViewerPreferencesProvider";
import { ProviderFilter, ProviderItem } from "./ProviderFilter";
import { AccessTypeFilter } from "./AccessTypeFilter";
import CountrySelect, { countryName } from "./CountrySelect";
import { useCurrentLocation } from "./CurrentLocationProvider";
import { DurationFilter } from "./DurationFilter";
import { CANONICAL_LANGUAGES } from "@/lib/languages";

export interface FilterState {
  q: string;
  content_type: string; // "all", "movie", "series"
  genre: string;
  year_min: string;
  year_max: string;
  actors: { id: number; name: string }[];
  providers: string[]; // array of provider slugs
  country: string;
  status: string; // "", "watchable_here", "watchable_vpn", "not_watchable"
  quality: string; // "", "4k,uhd", "hdr", "hd", "sd"
  score_min: string; // "", "8.0", "7.0", "6.0", "5.0"
  sort_by: string; // "", "rating_desc", "year_desc", "year_asc", "title_asc", "title_desc"
  /** Access model filter: "", "all", or "subscription,addon,free,free_with_ads,rent,buy". */
  access: string;
  duration_min: string;
  duration_max: string;
  watched: string; // "all", "watched", "unwatched"
  audio_languages: string;
  subtitle_languages: string;
  provider_categories: string[];
}

export const DEFAULT_FILTER_STATE: FilterState = {
  q: "",
  content_type: "all",
  genre: "",
  year_min: "",
  year_max: "",
  actors: [],
  providers: [],
  country: "",
  status: "",
  quality: "",
  score_min: "",
  sort_by: "",
  access: "",
  duration_min: "",
  duration_max: "",
  watched: "all",
  audio_languages: "",
  subtitle_languages: "",
  provider_categories: [],
};

/** The all-defaults value used by "Reset". content_type/watched are enums. */
const EMPTY_FILTER_STATE: FilterState = {
  q: "",
  content_type: "all",
  genre: "",
  year_min: "",
  year_max: "",
  actors: [],
  providers: [],
  country: "",
  status: "",
  quality: "",
  score_min: "",
  sort_by: "",
  access: "",
  duration_min: "",
  duration_max: "",
  watched: "all",
  audio_languages: "",
  subtitle_languages: "",
  provider_categories: [],
};

/** Field-by-field comparison; `actors` needs an id-set compare, not `===`. */
function sameFilters(a: FilterState, b: FilterState): boolean {
  if (a === b) return true;
  const actorIdsA = a.actors.map((x) => x.id).sort((x, y) => x - y);
  const actorIdsB = b.actors.map((x) => x.id).sort((x, y) => x - y);
  return (
    a.q === b.q &&
    a.content_type === b.content_type &&
    a.genre === b.genre &&
    a.year_min === b.year_min &&
    a.year_max === b.year_max &&
    a.providers.length === b.providers.length &&
    a.providers.every((p) => b.providers.includes(p)) &&
    a.country === b.country &&
    a.status === b.status &&
    a.quality === b.quality &&
    a.score_min === b.score_min &&
    a.sort_by === b.sort_by &&
    a.access === b.access &&
    a.duration_min === b.duration_min &&
    a.duration_max === b.duration_max &&
    (a.watched || "all") === (b.watched || "all") &&
    a.audio_languages === b.audio_languages &&
    a.subtitle_languages === b.subtitle_languages &&
    a.provider_categories.length === b.provider_categories.length &&
    a.provider_categories.every((category) => b.provider_categories.includes(category)) &&
    actorIdsA.length === actorIdsB.length &&
    actorIdsA.every((id, i) => id === actorIdsB[i])
  );
}

interface FilterBarProps {
  filters: FilterState;
  onChange: (newFilters: FilterState) => void;
  showStatusFilter?: boolean;
  showLanguageFilters?: boolean;
  showProviderCategories?: boolean;
}

export function FilterBar({
  filters,
  onChange,
  showStatusFilter = true,
  showLanguageFilters = false,
  showProviderCategories = false,
}: FilterBarProps) {
  // Controls edit a local draft; nothing reaches the parent (and so no request
  // is issued) until Apply is pressed. `filters` is the last applied value, so
  // an external reset or URL-driven change re-seeds the draft.
  const [draft, setDraft] = useState<FilterState>(filters);
  const [applied, setApplied] = useState<FilterState>(filters);
  const draftRef = useRef(draft);
  draftRef.current = draft;

  const [genres, setGenres] = useState<{ id: number; name: string }[]>([]);
  const [providers, setProviders] = useState<ProviderItem[]>([]);
  const [actorSearch, setActorSearch] = useState("");
  const [actorSuggestions, setActorSuggestions] = useState<{ id: number; name: string }[]>([]);
  const [showActorDropdown, setShowActorSuggestions] = useState(false);
  const [showViewerLanguages, setShowViewerLanguages] = useState(false);
  const { viewersRule, setViewersRule, loading: viewerPreferencesLoading, error: viewerPreferencesError } = useViewerPreferences();
  const { currentCountry, loading: currentLocationLoading, error: currentLocationError, setCurrentCountry } = useCurrentLocation();
  const [currentLocationSaving, setCurrentLocationSaving] = useState(false);
  const [vpnEnabled, setVpnEnabled] = useState(false);
  const [vpnSaving, setVpnSaving] = useState(false);
  const viewerSummary = viewersRule.viewers
    .map((viewer) => `${viewer.name} (${viewer.languages.join(", ") || "No languages"})`)
    .join(" · ");

  // Re-seed the draft only when the parent genuinely pushes a different applied
  // value (external reset, "Clear filters", a URL-driven change). A parent that
  // merely re-sends the same value — or echoes a new object with equal fields —
  // must NOT clobber edits the user has staged but not yet applied, so the
  // comparison is against the last observed prop, not against the draft.
  const lastPropRef = useRef(filters);
  useEffect(() => {
    if (sameFilters(lastPropRef.current, filters)) return;
    lastPropRef.current = filters;
    setDraft(filters);
    setApplied(filters);
  }, [filters]);

  const isDirty = !sameFilters(draft, applied);

  const handleApply = () => {
    setApplied(draftRef.current);
    onChange(draftRef.current);
  };

  const handleReset = () => {
    const cleared: FilterState = { ...EMPTY_FILTER_STATE, watched: "all" };
    setDraft(cleared);
    setApplied(cleared);
    setActorSearch("");
    onChange(cleared);
  };

  const updateDraft = (patch: Partial<FilterState>) =>
    setDraft((current) => ({ ...current, ...patch }));

  const handleCurrentLocationChange = async (country: string) => {
    setCurrentLocationSaving(true);
    try {
      await setCurrentCountry(country);
    } finally {
      setCurrentLocationSaving(false);
    }
  };

  // VPN reach is the user's own declaration, never auto-detected: the switch
  // says "I can reach other countries' catalogues", and the candidate-country
  // list (edited on Preferences) is where they apply.
  const handleVpnToggle = async (enabled: boolean) => {
    setVpnEnabled(enabled);
    setVpnSaving(true);
    try {
      const res = await fetch("/api/preferences/", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vpn_enabled: enabled }),
      });
      if (!res.ok) throw new Error(`Failed to save VPN setting (${res.status})`);
    } catch {
      setVpnEnabled(!enabled); // revert on failure
    } finally {
      setVpnSaving(false);
    }
  };

  useEffect(() => {
    fetch("/api/genres/")
      .then((res) => (res.ok ? res.json() : { results: [] }))
      .then((data) => setGenres(data.results || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetch("/api/providers/")
      .then((res) => (res.ok ? res.json() : []))
      .then((data) => {
        if (Array.isArray(data)) {
          setProviders(data);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetch("/api/preferences/")
      .then((res) => (res.ok ? res.json() : {}))
      .then((data: { vpn_enabled?: boolean }) => setVpnEnabled(Boolean(data.vpn_enabled)))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (actorSearch.trim().length < 2) {
      setActorSuggestions([]);
      return;
    }
    const timer = setTimeout(() => {
      fetch(`/api/search/person/?q=${encodeURIComponent(actorSearch)}`)
        .then((res) => (res.ok ? res.json() : { results: [] }))
        .then((data) => setActorSuggestions(data.results || []))
        .catch(() => {});
    }, 300);
    return () => clearTimeout(timer);
  }, [actorSearch]);

  const addActor = (actor: { id: number; name: string }) => {
    if (!draft.actors.some((a) => a.id === actor.id)) {
      updateDraft({ actors: [...draft.actors, actor] });
    }
    setActorSearch("");
    setShowActorSuggestions(false);
  };

  const removeActor = (id: number) => {
    updateDraft({ actors: draft.actors.filter((a) => a.id !== id) });
  };

  const hasActiveFilters = Boolean(
    draft.q ||
      (draft.content_type && draft.content_type !== "all") ||
      draft.genre ||
      draft.year_min ||
      draft.year_max ||
      (draft.actors && draft.actors.length > 0) ||
      (draft.providers && draft.providers.length > 0) ||
      draft.country ||
      draft.status ||
      draft.quality ||
      draft.score_min ||
      draft.sort_by ||
      draft.access ||
      draft.duration_min ||
      draft.duration_max ||
      (draft.watched && draft.watched !== "all") ||
      draft.audio_languages ||
      draft.subtitle_languages ||
      draft.provider_categories.length > 0
  );

  const handleClearAllFilters = () => {
    const cleared: FilterState = { ...EMPTY_FILTER_STATE, watched: "all" };
    setDraft(cleared);
    setApplied(cleared);
    setActorSearch("");
    onChange(cleared);
  };

  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-4 shadow-sm mb-6 space-y-4">
      <div className="overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700">
        <button
          type="button"
          onClick={() => setShowViewerLanguages((visible) => !visible)}
          aria-expanded={showViewerLanguages}
          className="flex w-full items-center justify-between gap-3 bg-gray-50 px-3 py-2.5 text-left hover:bg-gray-100 dark:bg-gray-800/60 dark:hover:bg-gray-800"
        >
          <span>
            <span className="block text-sm font-semibold text-gray-900 dark:text-gray-100">Viewer Languages</span>
            <span className="block text-xs text-gray-500 dark:text-gray-400">
              {viewerPreferencesLoading ? "Loading preferences…" : viewerSummary || "No viewers configured"}
            </span>
          </span>
          <svg
            className={`h-4 w-4 shrink-0 text-gray-500 transition-transform ${showViewerLanguages ? "rotate-180" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="m6 9 6 6 6-6" />
          </svg>
        </button>
        {showViewerLanguages && (
          <div className="border-t border-gray-200 p-3 dark:border-gray-700">
            {viewerPreferencesError && (
              <p className="mb-3 text-xs text-red-600 dark:text-red-400">{viewerPreferencesError}</p>
            )}
            <ViewerLanguageBuilder rule={viewersRule} onChange={setViewersRule} />
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div>
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Current Location
          </label>
          <CountrySelect
            country={currentCountry}
            onChange={handleCurrentLocationChange}
            disabled={currentLocationLoading || currentLocationSaving}
          />
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            {currentLocationLoading ? "Determining location…" : currentCountry ? `Local results: ${countryName(currentCountry)}` : "Choose a country to prioritize local results"}
          </p>
          {currentLocationError && <p className="mt-1 text-xs text-red-600 dark:text-red-400">{currentLocationError}</p>}
        </div>

        <div>
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            VPN Access
          </label>
          <label
            className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm transition-colors ${
              vpnEnabled
                ? "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
                : "border-gray-300 bg-gray-50 text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300"
            } ${vpnSaving ? "opacity-60" : ""}`}
          >
            <input
              type="checkbox"
              checked={vpnEnabled}
              disabled={vpnSaving}
              onChange={(e) => handleVpnToggle(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-emerald-600 focus:ring-emerald-500"
            />
            I can use a VPN
          </label>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            {vpnEnabled
              ? "Other countries are treated as reachable; add regions on Preferences."
              : "Other countries' catalogues are ignored."}
          </p>
        </div>

        <div>
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Access Type
          </label>
          <AccessTypeFilter
            access={draft.access || ""}
            onChange={(access) => updateDraft({ access })}
          />
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            Filter offers by how they are delivered. Manage which services you own on Subscriptions.
          </p>
        </div>
      </div>

      {/* Top Search & Content Type */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="md:col-span-2">
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Search Title / Overview
          </label>
          <input
            type="text"
            placeholder="Search by title..."
            value={draft.q}
            onChange={(e) => updateDraft({ q: e.target.value })}
            className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
        </div>

        <div>
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Type
          </label>
          <div className="flex rounded-lg border border-gray-300 dark:border-gray-700 overflow-hidden text-sm">
            {[
              { id: "all", label: "All" },
              { id: "movie", label: "Movies" },
              { id: "series", label: "Series" },
            ].map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => updateDraft({ content_type: t.id })}
                className={`flex-1 py-1.5 text-center font-medium transition-colors ${
                  draft.content_type === t.id
                    ? "bg-indigo-600 text-white"
                    : "bg-gray-50 dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-100"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Actor Autocomplete & Selected Chips */}
      <div>
        <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
          Cast / Actors (AND matching)
        </label>
        <div className="relative">
          <input
            type="text"
            placeholder="Type actor name (e.g. Ryan Gosling)..."
            value={actorSearch}
            onChange={(e) => {
              setActorSearch(e.target.value);
              setShowActorSuggestions(true);
            }}
            onFocus={() => setShowActorSuggestions(true)}
            className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
          {showActorDropdown && actorSuggestions.length > 0 && (
            <div className="absolute z-20 top-full left-0 right-0 mt-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg max-h-48 overflow-y-auto">
              {actorSuggestions.map((person) => (
                <button
                  key={person.id}
                  type="button"
                  onClick={() => addActor(person)}
                  className="w-full text-left px-3 py-2 text-sm hover:bg-indigo-50 dark:hover:bg-indigo-900/40 text-gray-900 dark:text-gray-100 flex items-center justify-between"
                >
                  <span>{person.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        {draft.actors.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-2">
            {draft.actors.map((actor) => (
              <span
                key={actor.id}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-indigo-100 text-indigo-800 dark:bg-indigo-900/60 dark:text-indigo-200"
              >
                {actor.name}
                <button
                  type="button"
                  onClick={() => removeActor(actor.id)}
                  className="hover:text-red-500 font-bold"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Secondary Row: Genre, Provider, Year Range, Quality, Min Score, Sort, Status */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-8 gap-3">
        {showProviderCategories && (
          <fieldset className="col-span-full rounded-lg border border-gray-200 p-3 dark:border-gray-700">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-gray-500">
              Provider categories
            </legend>
            <div className="flex flex-wrap gap-x-5 gap-y-2">
              {[
                ["major", "Major services"],
                ["bvod", "Broadcast (BVOD)"],
                ["avod", "Ad-supported (AVOD)"],
                ["tvod", "Rent and buy (TVOD)"],
                ["fast", "Free ad-supported (FAST)"],
                ["scraper", "Unofficial sources"],
              ].map(([value, label]) => (
                <label key={value} className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                  <input
                    type="checkbox"
                    checked={draft.provider_categories.includes(value)}
                    onChange={(event) => {
                      const selected = new Set(draft.provider_categories);
                      if (event.target.checked) selected.add(value);
                      else selected.delete(value);
                      updateDraft({ provider_categories: Array.from(selected) });
                    }}
                  />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
        )}

        <div>
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Genre
          </label>
          <select
            value={draft.genre}
            onChange={(e) => updateDraft({ genre: e.target.value })}
            className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
          >
            <option value="">All Genres</option>
            {genres.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Provider Filter
          </label>
          <ProviderFilter
            providers={providers}
            selectedProviders={draft.providers || []}
            onChange={(selectedSlugs) => updateDraft({ providers: selectedSlugs })}
          />
        </div>

        <div>
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Min Year
          </label>
          <input
            type="number"
            placeholder="e.g. 2020"
            value={draft.year_min}
            onChange={(e) => updateDraft({ year_min: e.target.value })}
            className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
          />
        </div>

        <div>
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Max Year
          </label>
          <input
            type="number"
            placeholder="e.g. 2026"
            value={draft.year_max}
            onChange={(e) => updateDraft({ year_max: e.target.value })}
            className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
          />
        </div>

        <div>
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Quality
          </label>
          <select
            value={draft.quality}
            onChange={(e) => updateDraft({ quality: e.target.value })}
            className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
          >
            <option value="">All Qualities</option>
            <option value="4k,uhd">4K / UHD</option>
            <option value="hdr">HDR</option>
            <option value="hd">HD</option>
            <option value="sd">SD</option>
          </select>
        </div>

        <div>
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Min Rating
          </label>
          <select
            value={draft.score_min}
            onChange={(e) => updateDraft({ score_min: e.target.value })}
            className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
          >
            <option value="">All Ratings</option>
            <option value="8.0">★ 8.0+</option>
            <option value="7.0">★ 7.0+</option>
            <option value="6.0">★ 6.0+</option>
            <option value="5.0">★ 5.0+</option>
          </select>
        </div>

        <div>
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Sort By
          </label>
          <select
            value={draft.sort_by}
            onChange={(e) => updateDraft({ sort_by: e.target.value })}
            className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
          >
            <option value="">Default / Newest</option>
            <option value="rating_desc">Rating (High to Low)</option>
            <option value="year_desc">Year (Newest First)</option>
            <option value="year_asc">Year (Oldest First)</option>
            <option value="title_asc">Title (A-Z)</option>
            <option value="title_desc">Title (Z-A)</option>
          </select>
        </div>

        <div>
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Duration
          </label>
          <DurationFilter
            durationMin={draft.duration_min || ""}
            durationMax={draft.duration_max || ""}
            onChange={(duration_min, duration_max) =>
              updateDraft({ duration_min, duration_max })
            }
          />
        </div>

        {showStatusFilter && (
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              Watchability Status
            </label>
            <select
              value={draft.status}
              onChange={(e) => updateDraft({ status: e.target.value })}
              className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
            >
              <option value="">All Statuses</option>
              <option value="watchable">🟢 Watchable (Any)</option>
              <option value="watchable_here">🟢 Watchable here</option>
              <option value="watchable_vpn">🟡 Watchable with VPN</option>
              <option value="needs_recheck">🕓 Needs recheck (last seen)</option>
              <option value="not_watchable">⚫ Not watchable</option>
            </select>
          </div>
        )}
      </div>

      {/* Watched Filter */}
      <div>
        <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
          Watched
        </label>
        <select
          value={draft.watched || "all"}
          onChange={(e) => updateDraft({ watched: e.target.value })}
          aria-label="Watched status"
          className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
        >
          <option value="all">All</option>
          <option value="watched">✓ Watched</option>
          <option value="unwatched">○ Unwatched</option>
        </select>
      </div>

      {showLanguageFilters && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {([
            ["audio_languages", "Audio language"],
            ["subtitle_languages", "Subtitle language"],
          ] as const).map(([field, label]) => (
            <div key={field}>
              <label htmlFor={field} className="mb-1 block text-xs font-semibold uppercase tracking-wider text-gray-500">
                {label}
              </label>
              <select
                id={field}
                aria-label={label}
                value={draft[field]}
                onChange={(event) => {
                  const value = event.target.value;
                  updateDraft(field === "audio_languages"
                    ? { audio_languages: value }
                    : { subtitle_languages: value });
                }}
                className="w-full rounded-lg border border-gray-300 bg-gray-50 px-3 py-1.5 text-sm text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
              >
                <option value="">Any</option>
                {CANONICAL_LANGUAGES.map(({ code, name }) => (
                  <option key={code} value={code}>{name}</option>
                ))}
              </select>
            </div>
          ))}
        </div>
      )}

      {/* Apply / Reset — filters are staged locally until Apply is pressed */}
      <div className="flex items-center justify-end gap-2 border-t border-gray-200 pt-3 dark:border-gray-800">
        {isDirty && (
          <span className="mr-auto text-xs text-gray-500 dark:text-gray-400">
            Unapplied filter changes
          </span>
        )}
        {hasActiveFilters && (
          <button
            type="button"
            onClick={handleClearAllFilters}
            data-testid="clear-all-filters-btn"
            className="rounded-lg border border-red-200 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950"
          >
            Clear all filters
          </button>
        )}
        <button
          type="button"
          onClick={handleReset}
          disabled={!isDirty}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
        >
          Reset
        </button>
        <button
          type="button"
          onClick={handleApply}
          disabled={!isDirty}
          aria-label="Apply filters"
          className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Apply filters
        </button>
      </div>
    </div>
  );
}
