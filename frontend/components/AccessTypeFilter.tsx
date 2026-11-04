"use client";

import { useState } from "react";

/**
 * Offer-type filter rendered as a multi-select checkbox dropdown.
 *
 * The parent stores the selection as the same comma-separated string the API
 * already understands: "" means "no filter (any access)", "all" means every
 * type is selected, and anything else is the selected subset. Keeping that
 * encoding means no backend or query-string change is needed.
 */
export const ACCESS_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "subscription", label: "Included subscription" },
  { value: "addon", label: "Add-on channel" },
  { value: "free", label: "Free" },
  { value: "free_with_ads", label: "Free with ads" },
  { value: "rent", label: "Available to rent" },
  { value: "buy", label: "Available to buy" },
];

const ALL_VALUES = ACCESS_TYPE_OPTIONS.map((o) => o.value);

export function parseAccessTypes(access: string): string[] {
  const raw = (access || "").trim().toLowerCase();
  if (!raw) return [];
  if (raw === "all") return [...ALL_VALUES];
  return ALL_VALUES.filter((v) => raw.split(",").includes(v));
}

export function serialiseAccessTypes(selected: string[]): string {
  const unique = ALL_VALUES.filter((v) => selected.includes(v));
  if (unique.length === 0) return "";
  if (unique.length === ALL_VALUES.length) return "all";
  return unique.join(",");
}

function summarise(selected: string[]): string {
  if (selected.length === 0) return "Any access";
  if (selected.length === ALL_VALUES.length) return "All access types";
  const labels = ACCESS_TYPE_OPTIONS.filter((o) => selected.includes(o.value)).map(
    (o) => o.label
  );
  if (labels.length <= 2) return labels.join(", ");
  return `${labels.length} access types`;
}

interface AccessTypeFilterProps {
  access: string;
  onChange: (access: string) => void;
}

export function AccessTypeFilter({ access, onChange }: AccessTypeFilterProps) {
  const [isOpen, setIsOpen] = useState(false);
  const selected = parseAccessTypes(access);

  const toggle = (value: string) => {
    const next = selected.includes(value)
      ? selected.filter((v) => v !== value)
      : [...selected, value];
    onChange(serialiseAccessTypes(next));
  };

  const selectAll = () => onChange(serialiseAccessTypes([...ALL_VALUES]));
  const clear = () => onChange("");

  const allSelected = selected.length === ALL_VALUES.length;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        aria-expanded={isOpen}
        aria-haspopup="true"
        data-testid="access-type-toggle"
        className="w-full flex items-center justify-between px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100 font-medium"
      >
        <span className="truncate">{summarise(selected)}</span>
        <span className="text-xs text-gray-400">{isOpen ? "▲" : "▼"}</span>
      </button>

      {isOpen && (
        <div
          data-testid="access-type-dropdown"
          className="absolute z-30 mt-2 w-full sm:w-72 right-0 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl p-3 space-y-2 max-h-80 overflow-y-auto"
        >
          <div className="flex items-center justify-between border-b pb-2 border-gray-200 dark:border-gray-700">
            <label className="flex items-center gap-2 text-xs font-semibold text-gray-700 dark:text-gray-300 cursor-pointer">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={() => (allSelected ? clear() : selectAll())}
                className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
              />
              All access types
            </label>
            <button
              type="button"
              onClick={clear}
              className="text-xs text-red-600 dark:text-red-400 hover:underline font-medium"
            >
              Clear
            </button>
          </div>

          <div className="space-y-1.5">
            {ACCESS_TYPE_OPTIONS.map((option) => (
              <label
                key={option.value}
                className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300 cursor-pointer hover:text-indigo-600 dark:hover:text-indigo-400"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(option.value)}
                  onChange={() => toggle(option.value)}
                  className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                />
                <span>{option.label}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
