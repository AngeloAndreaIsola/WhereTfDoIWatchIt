"use client";

import { useEffect, useState } from "react";
import { ProviderFilter, ProviderItem } from "./ProviderFilter";

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
}

interface FilterBarProps {
  filters: FilterState;
  onChange: (newFilters: FilterState) => void;
  showStatusFilter?: boolean;
}

export function FilterBar({ filters, onChange, showStatusFilter = true }: FilterBarProps) {
  const [genres, setGenres] = useState<{ id: number; name: string }[]>([]);
  const [providers, setProviders] = useState<ProviderItem[]>([]);
  const [actorSearch, setActorSearch] = useState("");
  const [actorSuggestions, setActorSuggestions] = useState<{ id: number; name: string }[]>([]);
  const [showActorDropdown, setShowActorSuggestions] = useState(false);

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
    if (!filters.actors.some((a) => a.id === actor.id)) {
      onChange({ ...filters, actors: [...filters.actors, actor] });
    }
    setActorSearch("");
    setShowActorSuggestions(false);
  };

  const removeActor = (id: number) => {
    onChange({
      ...filters,
      actors: filters.actors.filter((a) => a.id !== id),
    });
  };

  return (
    <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-4 shadow-sm mb-6 space-y-4">
      {/* Top Search & Content Type */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="md:col-span-2">
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Search Title / Overview
          </label>
          <input
            type="text"
            placeholder="Search by title..."
            value={filters.q}
            onChange={(e) => onChange({ ...filters, q: e.target.value })}
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
                onClick={() => onChange({ ...filters, content_type: t.id })}
                className={`flex-1 py-1.5 text-center font-medium transition-colors ${
                  filters.content_type === t.id
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
        {filters.actors.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-2">
            {filters.actors.map((actor) => (
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

      {/* Secondary Row: Genre, Provider, Year Range, Status */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">
        <div>
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Genre
          </label>
          <select
            value={filters.genre}
            onChange={(e) => onChange({ ...filters, genre: e.target.value })}
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
            selectedProviders={filters.providers || []}
            onChange={(selectedSlugs) =>
              onChange({ ...filters, providers: selectedSlugs })
            }
          />
        </div>

        <div>
          <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
            Min Year
          </label>
          <input
            type="number"
            placeholder="e.g. 2020"
            value={filters.year_min}
            onChange={(e) => onChange({ ...filters, year_min: e.target.value })}
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
            value={filters.year_max}
            onChange={(e) => onChange({ ...filters, year_max: e.target.value })}
            className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
          />
        </div>

        {showStatusFilter && (
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              Watchability Status
            </label>
            <select
              value={filters.status}
              onChange={(e) => onChange({ ...filters, status: e.target.value })}
              className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
            >
              <option value="">All Statuses</option>
              <option value="watchable">🟢 Watchable (Any)</option>
              <option value="watchable_here">🟢 Watchable here</option>
              <option value="watchable_vpn">🟡 Watchable with VPN</option>
              <option value="not_watchable">⚫ Not watchable</option>
            </select>
          </div>
        )}
      </div>
    </div>
  );
}
