"use client";

import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  CANONICAL_LANGUAGES,
  CANONICAL_LANGUAGE_MAP,
  isCanonicalLanguageCode,
  normalizeLanguageCode,
} from "@/lib/languages";

export interface InlineLanguagePickerProps {
  selectedCodes: string[];
  onChange: (codes: string[]) => void;
  ariaLabel?: string;
  placeholder?: string;
}

let englishNames: Intl.DisplayNames | null = null;
const englishNameCache = new Map<string, string>();

function getEnglishName(code: string): string {
  const cached = englishNameCache.get(code);
  if (cached !== undefined) return cached;
  let name = code;
  try {
    englishNames ??= new Intl.DisplayNames(["en"], { type: "language" });
    name = englishNames.of(code) ?? code;
  } catch {
    name = code;
  }
  englishNameCache.set(code, name);
  return name;
}

export default function InlineLanguagePicker({
  selectedCodes,
  onChange,
  ariaLabel = "Languages",
  placeholder = "Search language or code…",
}: InlineLanguagePickerProps) {
  const [query, setQuery] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [announcement, setAnnouncement] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listboxId = useId();

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
    if (!q) {
      return CANONICAL_LANGUAGES.filter(
        (lang) => !selectedCodes.includes(lang.code)
      );
    }

    const normalizedQuery = normalizeLanguageCode(query);
    const exactCode = isCanonicalLanguageCode(normalizedQuery)
      ? normalizedQuery
      : null;

    // An exact code match that is already selected must not fall through to a
    // different language that merely contains the query as a substring.
    if (exactCode && selectedCodes.includes(exactCode)) return [];

    const ranked = CANONICAL_LANGUAGES.filter((lang) => {
      if (selectedCodes.includes(lang.code)) return false;
      const haystack = `${lang.code} ${lang.name} ${getEnglishName(lang.code)}`.toLowerCase();
      return haystack.includes(q) || lang.code === normalizedQuery;
    }).map((lang) => {
      let tier = 2;
      if (exactCode && lang.code === exactCode) {
        tier = 0;
      } else {
        const fields = [lang.code, lang.name, getEnglishName(lang.code)];
        if (fields.some((f) => f.toLowerCase().startsWith(q))) tier = 1;
      }
      return { lang, tier };
    });

    // Array.prototype.sort is stable, so ties keep canonical list order.
    ranked.sort((a, b) => a.tier - b.tier);
    return ranked.map((r) => r.lang);
  }, [query, selectedCodes]);

  const safeActiveIndex =
    filteredOptions.length === 0
      ? -1
      : Math.min(activeIndex, filteredOptions.length - 1);

  useEffect(() => {
    if (!isOpen || safeActiveIndex < 0) return;
    const option = listRef.current?.children[
      safeActiveIndex
    ] as HTMLElement | undefined;
    option?.scrollIntoView?.({ block: "nearest" });
  }, [isOpen, safeActiveIndex]);

  const addCode = (code: string) => {
    if (selectedCodes.includes(code)) return;
    const name = CANONICAL_LANGUAGE_MAP[code] ?? code;
    onChange([...selectedCodes, code]);
    setQuery("");
    setIsOpen(true);
    setActiveIndex(0);
    setAnnouncement(`${name} (${code}) added.`);
    inputRef.current?.focus();
  };

  const removeCode = (code: string) => {
    const name = CANONICAL_LANGUAGE_MAP[code] ?? code;
    onChange(selectedCodes.filter((c) => c !== code));
    setAnnouncement(`${name} (${code}) removed.`);
    inputRef.current?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        if (!isOpen) {
          setIsOpen(true);
        } else if (filteredOptions.length > 0) {
          setActiveIndex((i) => (i + 1) % filteredOptions.length);
        }
        break;
      case "ArrowUp":
        e.preventDefault();
        if (filteredOptions.length > 0) {
          setActiveIndex(
            (i) => (i - 1 + filteredOptions.length) % filteredOptions.length
          );
        }
        break;
      case "Enter":
        e.preventDefault();
        if (isOpen && safeActiveIndex >= 0) {
          addCode(filteredOptions[safeActiveIndex].code);
        }
        break;
      case "Escape":
        setIsOpen(false);
        break;
      case "Backspace":
        if (query === "" && selectedCodes.length > 0) {
          removeCode(selectedCodes[selectedCodes.length - 1]);
        }
        break;
      default:
        break;
    }
  };

  const activeOptionId =
    isOpen && safeActiveIndex >= 0
      ? `${listboxId}-option-${safeActiveIndex}`
      : undefined;

  return (
    <div
      ref={containerRef}
      className="relative"
      onBlur={(e) => {
        if (!containerRef.current?.contains(e.relatedTarget as Node)) {
          setIsOpen(false);
        }
      }}
    >
      <span aria-live="polite" className="sr-only">
        {announcement}
      </span>

      <div
        className="flex min-h-[34px] w-full cursor-text flex-wrap items-center gap-1 rounded-md border border-gray-300 bg-white px-2 py-1 shadow-xs focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500 dark:border-gray-600 dark:bg-gray-800"
        onClick={() => inputRef.current?.focus()}
      >
        {selectedCodes.map((rawCode) => {
          const code = rawCode.trim().toUpperCase();
          const name = CANONICAL_LANGUAGE_MAP[code] ?? code;
          const labelText = `${name} (${code})`;
          return (
            <span
              key={code}
              className="inline-flex items-center gap-1 rounded bg-blue-50 px-1.5 py-0.5 text-[11px] font-medium text-blue-700 border border-blue-200 dark:bg-blue-900/40 dark:text-blue-300 dark:border-blue-800"
            >
              {labelText}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  removeCode(code);
                }}
                className="rounded p-0.5 text-blue-600 hover:bg-blue-200 dark:text-blue-300 dark:hover:bg-blue-800"
                aria-label={`Remove ${labelText}`}
              >
                ✕
              </button>
            </span>
          );
        })}

        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={isOpen}
          aria-controls={listboxId}
          aria-activedescendant={activeOptionId}
          aria-autocomplete="list"
          aria-label={ariaLabel}
          placeholder={selectedCodes.length === 0 ? placeholder : ""}
          value={query}
          onFocus={() => setIsOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            setActiveIndex(0);
            setIsOpen(true);
          }}
          onKeyDown={handleKeyDown}
          className="min-w-[7rem] flex-1 bg-transparent px-1 py-0.5 text-xs text-gray-900 placeholder-gray-400 focus:outline-none dark:text-gray-100"
        />
      </div>

      {isOpen && (
        <ul
          ref={listRef}
          id={listboxId}
          role="listbox"
          aria-label={ariaLabel}
          className="absolute left-0 right-0 z-50 mt-1 max-h-48 overflow-auto rounded-md border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800"
        >
          {filteredOptions.length > 0 ? (
            filteredOptions.map((lang, index) => (
              <li
                key={lang.code}
                id={`${listboxId}-option-${index}`}
                role="option"
                aria-selected={index === safeActiveIndex}
                onMouseEnter={() => setActiveIndex(index)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => addCode(lang.code)}
                className={`flex cursor-pointer items-center justify-between px-3 py-1.5 text-xs ${
                  index === safeActiveIndex
                    ? "bg-blue-50 text-gray-900 dark:bg-gray-700 dark:text-gray-100"
                    : "text-gray-800 dark:text-gray-200"
                }`}
              >
                <span className="font-medium">
                  {lang.name}{" "}
                  <span className="text-gray-400 dark:text-gray-500">
                    ({lang.code})
                  </span>
                </span>
              </li>
            ))
          ) : (
            <li className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
              No matching languages found.
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
