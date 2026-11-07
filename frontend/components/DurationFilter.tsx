"use client";

import { useState, useRef, useEffect } from "react";

export interface DurationPreset {
  id: string;
  label: string;
  min: string;
  max: string;
}

export const DURATION_PRESETS: DurationPreset[] = [
  { id: "any", label: "Any duration", min: "", max: "" },
  { id: "under_30", label: "Under 30 min", min: "", max: "30" },
  { id: "30_60", label: "30–60 min", min: "31", max: "60" },
  { id: "1_2_hours", label: "1–2 hours", min: "61", max: "120" },
  { id: "2_3_hours", label: "2–3 hours", min: "121", max: "180" },
  { id: "over_3_hours", label: "Over 3 hours", min: "181", max: "" },
];

export function formatDurationMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || isNaN(minutes) || minutes < 0) {
    return "";
  }
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h > 0 && m > 0) return `${h}h ${m}m`;
  if (h > 0) return `${h}h`;
  return `${m}m`;
}

export function getDurationSummary(min: string, max: string): string {
  const minVal = min ? parseInt(min, 10) : null;
  const maxVal = max ? parseInt(max, 10) : null;

  // Check presets
  for (const p of DURATION_PRESETS) {
    if (p.id === "any") continue;
    if (p.min === min && p.max === max) {
      return p.label;
    }
  }

  if (minVal !== null && maxVal !== null) {
    return `${formatDurationMinutes(minVal)} – ${formatDurationMinutes(maxVal)}`;
  }
  if (minVal !== null) {
    return `≥ ${formatDurationMinutes(minVal)}`;
  }
  if (maxVal !== null) {
    return `≤ ${formatDurationMinutes(maxVal)}`;
  }
  return "Any duration";
}

interface DurationFilterProps {
  durationMin: string;
  durationMax: string;
  onChange: (min: string, max: string) => void;
}

export function DurationFilter({
  durationMin,
  durationMax,
  onChange,
}: DurationFilterProps) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isOpen]);

  const isActive = Boolean(durationMin || durationMax);
  const summary = getDurationSummary(durationMin, durationMax);

  const handleSelectPreset = (preset: DurationPreset) => {
    onChange(preset.min, preset.max);
    setIsOpen(false);
  };

  const handleCustomMinChange = (val: string) => {
    onChange(val, durationMax);
  };

  const handleCustomMaxChange = (val: string) => {
    onChange(durationMin, val);
  };

  const handleClear = () => {
    onChange("", "");
    setIsOpen(false);
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={isOpen}
        aria-haspopup="true"
        data-testid="duration-filter-toggle"
        className={`w-full flex items-center justify-between px-3 py-1.5 text-sm rounded-lg border transition-colors ${
          isActive
            ? "border-indigo-500 bg-indigo-50 text-indigo-900 dark:bg-indigo-900/30 dark:text-indigo-200 dark:border-indigo-600 font-medium"
            : "border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
        }`}
      >
        <span className="truncate">{summary}</span>
        <span className="text-xs text-gray-400 ml-1">{isOpen ? "▲" : "▼"}</span>
      </button>

      {isOpen && (
        <div
          data-testid="duration-filter-dropdown"
          className="absolute z-30 mt-2 w-72 left-0 sm:left-auto sm:right-0 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl p-3 space-y-3"
        >
          <div className="flex items-center justify-between border-b pb-2 border-gray-200 dark:border-gray-700">
            <span className="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
              Duration Presets
            </span>
            {isActive && (
              <button
                type="button"
                onClick={handleClear}
                className="text-xs text-red-600 dark:text-red-400 hover:underline font-medium"
              >
                Reset
              </button>
            )}
          </div>

          <div className="space-y-1">
            {DURATION_PRESETS.map((preset) => {
              const selected =
                (preset.id === "any" && !durationMin && !durationMax) ||
                (preset.min === durationMin && preset.max === durationMax);

              return (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => handleSelectPreset(preset)}
                  className={`w-full text-left px-2.5 py-1.5 text-sm rounded-lg transition-colors flex items-center justify-between ${
                    selected
                      ? "bg-indigo-600 text-white font-medium"
                      : "text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                  }`}
                >
                  <span>{preset.label}</span>
                  {selected && <span className="text-xs">✓</span>}
                </button>
              );
            })}
          </div>

          <div className="border-t pt-3 border-gray-200 dark:border-gray-700">
            <span className="block text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2">
              Custom Duration (mins)
            </span>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block text-[11px] text-gray-500 dark:text-gray-400 mb-0.5">
                  Min (m)
                </label>
                <input
                  type="number"
                  min="0"
                  placeholder="Min"
                  value={durationMin}
                  onChange={(e) => handleCustomMinChange(e.target.value)}
                  className="w-full px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100"
                />
                {durationMin && parseInt(durationMin, 10) > 0 && (
                  <span className="text-[10px] text-gray-400 mt-0.5 block">
                    {formatDurationMinutes(parseInt(durationMin, 10))}
                  </span>
                )}
              </div>
              <div>
                <label className="block text-[11px] text-gray-500 dark:text-gray-400 mb-0.5">
                  Max (m)
                </label>
                <input
                  type="number"
                  min="0"
                  placeholder="Max"
                  value={durationMax}
                  onChange={(e) => handleCustomMaxChange(e.target.value)}
                  className="w-full px-2 py-1 text-sm rounded border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100"
                />
                {durationMax && parseInt(durationMax, 10) > 0 && (
                  <span className="text-[10px] text-gray-400 mt-0.5 block">
                    {formatDurationMinutes(parseInt(durationMax, 10))}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
