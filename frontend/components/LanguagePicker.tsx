"use client";

import React, { useState, useMemo, useRef, useEffect } from "react";

export interface LanguageOption {
  code: string;
  name: string;
}

export const COMMON_LANGUAGES: LanguageOption[] = [
  { code: "en", name: "English" },
  { code: "es", name: "Spanish" },
  { code: "fr", name: "French" },
  { code: "de", name: "German" },
  { code: "it", name: "Italian" },
  { code: "ja", name: "Japanese" },
  { code: "ko", name: "Korean" },
  { code: "zh", name: "Chinese" },
  { code: "pt", name: "Portuguese" },
  { code: "ru", name: "Russian" },
  { code: "hi", name: "Hindi" },
  { code: "ar", name: "Arabic" },
  { code: "cs", name: "Czech" },
  { code: "da", name: "Danish" },
  { code: "nl", name: "Dutch" },
  { code: "fi", name: "Finnish" },
  { code: "el", name: "Greek" },
  { code: "he", name: "Hebrew" },
  { code: "hu", name: "Hungarian" },
  { code: "id", name: "Indonesian" },
  { code: "no", name: "Norwegian" },
  { code: "pl", name: "Polish" },
  { code: "ro", name: "Romanian" },
  { code: "sv", name: "Swedish" },
  { code: "th", name: "Thai" },
  { code: "tr", name: "Turkish" },
  { code: "uk", name: "Ukrainian" },
  { code: "vi", name: "Vietnamese" },
];

const LANG_MAP = new Map<string, string>(
  COMMON_LANGUAGES.map((l) => [l.code, l.name])
);

export interface LanguagePickerProps {
  label: string;
  selectedCodes: string[];
  onChange: (codes: string[]) => void;
  quickAddCodes?: string[];
  placeholder?: string;
}

export default function LanguagePicker({
  label,
  selectedCodes,
  onChange,
  quickAddCodes = ["en", "ja", "es", "fr", "de", "it"],
  placeholder = "Search language or code (e.g. ja, Japanese)...",
}: LanguagePickerProps) {
  const [query, setQuery] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const filteredOptions = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return COMMON_LANGUAGES;
    return COMMON_LANGUAGES.filter(
      (lang) =>
        lang.code.toLowerCase().includes(q) ||
        lang.name.toLowerCase().includes(q)
    );
  }, [query]);

  const handleAddCode = (code: string) => {
    const cleanCode = code.trim().toLowerCase();
    if (!cleanCode) return;
    if (!selectedCodes.includes(cleanCode)) {
      onChange([...selectedCodes, cleanCode]);
    }
    setQuery("");
    setIsOpen(false);
  };

  const handleRemoveCode = (codeToRemove: string) => {
    onChange(selectedCodes.filter((c) => c !== codeToRemove));
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const q = query.trim().toLowerCase();
      if (!q) return;

      if (filteredOptions.length > 0) {
        handleAddCode(filteredOptions[0].code);
      } else if (/^[a-z]{2}$/.test(q)) {
        handleAddCode(q);
      }
    }
  };

  return (
    <div className="space-y-2">
      <label className="block text-xs font-medium text-gray-700 dark:text-gray-300">
        {label}
      </label>

      {/* Quick Add Buttons */}
      {quickAddCodes.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-[11px] text-gray-500 dark:text-gray-400 font-medium">
            Quick Add:
          </span>
          {quickAddCodes.map((code) => {
            const name = LANG_MAP.get(code) ?? code;
            const isSelected = selectedCodes.includes(code);
            return (
              <button
                key={code}
                type="button"
                onClick={() =>
                  isSelected ? handleRemoveCode(code) : handleAddCode(code)
                }
                className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
                  isSelected
                    ? "bg-blue-100 text-blue-800 dark:bg-blue-900/50 dark:text-blue-300 border border-blue-300 dark:border-blue-700"
                    : "bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600"
                }`}
                title={isSelected ? `Remove ${name}` : `Add ${name} (${code})`}
              >
                + {code.toUpperCase()} ({name})
              </button>
            );
          })}
        </div>
      )}

      {/* Selected Tag Badges */}
      <div className="flex flex-wrap gap-1.5 min-h-[32px] p-1.5 rounded-md border border-gray-300 bg-white dark:border-gray-600 dark:bg-gray-700/50">
        {selectedCodes.length === 0 ? (
          <span className="text-xs text-gray-400 self-center px-1">
            No languages selected
          </span>
        ) : (
          selectedCodes.map((code) => {
            const name = LANG_MAP.get(code);
            const labelText = name ? `${name} (${code})` : code;
            return (
              <span
                key={code}
                className="inline-flex items-center gap-1 rounded bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 border border-blue-200 dark:bg-blue-900/40 dark:text-blue-300 dark:border-blue-800"
              >
                {labelText}
                <button
                  type="button"
                  onClick={() => handleRemoveCode(code)}
                  className="rounded hover:bg-blue-200 dark:hover:bg-blue-800 p-0.5 text-blue-600 dark:text-blue-300"
                  aria-label={`Remove ${labelText}`}
                >
                  ✕
                </button>
              </span>
            );
          })
        )}
      </div>

      {/* Searchable Input Dropdown */}
      <div ref={containerRef} className="relative">
        <input
          type="text"
          placeholder={placeholder}
          value={query}
          onFocus={() => setIsOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            setIsOpen(true);
          }}
          onKeyDown={handleKeyDown}
          className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
        />

        {isOpen && (
          <div className="absolute z-50 mt-1 max-h-48 w-full overflow-auto rounded-md border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800">
            {filteredOptions.length > 0 ? (
              filteredOptions.map((lang) => {
                const isSelected = selectedCodes.includes(lang.code);
                return (
                  <button
                    key={lang.code}
                    type="button"
                    onClick={() => handleAddCode(lang.code)}
                    disabled={isSelected}
                    className={`flex w-full items-center justify-between px-3 py-1.5 text-xs text-left transition-colors ${
                      isSelected
                        ? "opacity-50 cursor-not-allowed bg-gray-50 dark:bg-gray-800/50 text-gray-400"
                        : "hover:bg-blue-50 dark:hover:bg-gray-700 text-gray-800 dark:text-gray-200"
                    }`}
                  >
                    <span className="font-medium">
                      {lang.name}{" "}
                      <span className="text-gray-400 dark:text-gray-500">
                        ({lang.code})
                      </span>
                    </span>
                    {isSelected && <span className="text-[10px]">Selected</span>}
                  </button>
                );
              })
            ) : (
              <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
                {/^[a-z]{2}$/i.test(query.trim()) ? (
                  <button
                    type="button"
                    onClick={() => handleAddCode(query.trim())}
                    className="w-full text-left font-medium text-blue-600 hover:underline dark:text-blue-400"
                  >
                    + Add custom code &quot;{query.trim().toLowerCase()}&quot;
                  </button>
                ) : (
                  "No languages match query. Enter a 2-letter ISO code to add."
                )}
              </div>
            )}
          </div>
        )}
      </div>
      <span className="block text-[11px] text-gray-400">
        ISO 639-1 2-letter language codes
      </span>
    </div>
  );
}
