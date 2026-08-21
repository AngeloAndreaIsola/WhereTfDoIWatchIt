"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { search, SearchResult } from "@/lib/api";

export default function SearchBar() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState<number>(-1);

  const containerRef = useRef<HTMLDivElement>(null);

  // Debounced search
  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setResults([]);
      setIsOpen(false);
      setError("");
      setLoading(false);
      return;
    }

    setLoading(true);
    setError("");

    const timer = setTimeout(async () => {
      try {
        const data = await search(trimmed);
        setResults(data);
        setIsOpen(true);
        setSelectedIndex(-1);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Search failed");
        setResults([]);
        setIsOpen(true);
      } finally {
        setLoading(false);
      }
    }, 300);

    return () => clearTimeout(timer);
  }, [query]);

  // Click outside to close
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleSelect = useCallback(
    (tmdbId: number) => {
      setIsOpen(false);
      router.push(`/movie/${tmdbId}`);
    },
    [router]
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!isOpen) {
      if (e.key === "ArrowDown" && results.length > 0) {
        setIsOpen(true);
      }
      return;
    }

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) =>
        prev < results.length - 1 ? prev + 1 : 0
      );
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) =>
        prev > 0 ? prev - 1 : results.length - 1
      );
    } else if (e.key === "Enter") {
      if (selectedIndex >= 0 && selectedIndex < results.length) {
        e.preventDefault();
        handleSelect(results[selectedIndex].tmdb_id);
      }
    } else if (e.key === "Escape") {
      setIsOpen(false);
      setSelectedIndex(-1);
    }
  };

  return (
    <div ref={containerRef} className="relative w-full">
      <div className="relative flex items-center">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => {
            if (query.trim() && (results.length > 0 || error || !loading)) {
              setIsOpen(true);
            }
          }}
          onKeyDown={handleKeyDown}
          placeholder="Search for a movie by title..."
          className="w-full rounded-xl border border-gray-300 bg-white px-4 py-3 pl-11 pr-10 text-sm text-gray-900 shadow-sm
                     placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20
                     dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100 dark:placeholder-gray-400 dark:focus:border-blue-400"
        />

        {/* Search icon */}
        <div className="pointer-events-none absolute left-3.5 flex items-center text-gray-400 dark:text-gray-500">
          <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        </div>

        {/* Spinner or clear indicator */}
        {loading && (
          <div className="absolute right-3.5 flex items-center text-gray-400">
            <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
            </svg>
          </div>
        )}
      </div>

      {/* Floating Dropdown Results */}
      {isOpen && query.trim() !== "" && (
        <div className="absolute left-0 right-0 z-50 mt-2 max-h-80 overflow-y-auto rounded-xl border border-gray-200 bg-white py-1.5 shadow-lg dark:border-gray-700 dark:bg-gray-800">
          {error && (
            <div className="px-4 py-3 text-sm text-red-600 dark:text-red-400">
              {error}
            </div>
          )}

          {!loading && !error && results.length === 0 && (
            <div className="px-4 py-3 text-sm text-gray-500 dark:text-gray-400">
              No movies found.
            </div>
          )}

          {!error &&
            results.map((item, index) => (
              <button
                key={item.tmdb_id}
                type="button"
                onClick={() => handleSelect(item.tmdb_id)}
                onMouseEnter={() => setSelectedIndex(index)}
                className={`flex w-full items-center justify-between px-4 py-2.5 text-left text-sm transition-colors
                           ${
                             index === selectedIndex
                               ? "bg-blue-50 text-blue-900 dark:bg-blue-900/30 dark:text-blue-200"
                               : "text-gray-800 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700/50"
                           }`}
              >
                <span className="font-medium">{item.title}</span>
                {item.year && (
                  <span className="ml-2 text-xs text-gray-400 dark:text-gray-500">
                    {item.year}
                  </span>
                )}
              </button>
            ))}
        </div>
      )}
    </div>
  );
}
