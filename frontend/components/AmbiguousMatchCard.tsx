/* eslint-disable @next/next/no-img-element */
"use client";

import React, { useState } from "react";

export interface Candidate {
  tmdb_id: number;
  content_type?: "movie" | "series";
  title: string;
  year: number | null;
  poster_path?: string | null;
  overview?: string | null;
}

interface AmbiguousMatchCardProps {
  candidate: Candidate;
  selected: boolean;
  onSelect: () => void;
  name: string;
}

export default function AmbiguousMatchCard({
  candidate,
  selected,
  onSelect,
  name,
}: AmbiguousMatchCardProps) {
  const [imageError, setImageError] = useState(false);

  const hasPoster = candidate.poster_path && !imageError;
  const posterUrl = hasPoster
    ? `https://image.tmdb.org/t/p/w185${candidate.poster_path}`
    : null;

  return (
    <label
      className={`flex items-start gap-3.5 rounded-lg border p-3 text-sm cursor-pointer transition-colors ${
        selected
          ? "border-blue-500 bg-blue-50/60 ring-1 ring-blue-500 dark:border-blue-500 dark:bg-blue-950/30"
          : "border-gray-200 bg-white hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800/60 dark:hover:bg-gray-700/50"
      }`}
    >
      <input
        type="radio"
        name={name}
        checked={selected}
        onChange={onSelect}
        className="mt-1 h-4 w-4 text-blue-600 focus:ring-blue-500 flex-shrink-0"
      />

      {posterUrl ? (
        <img
          src={posterUrl}
          alt={candidate.title}
          onError={() => setImageError(true)}
          className="h-24 w-16 flex-shrink-0 rounded object-cover shadow-sm bg-gray-100 dark:bg-gray-700"
        />
      ) : (
        <div className="flex h-24 w-16 flex-shrink-0 flex-col items-center justify-center rounded bg-gray-100 p-1 text-center text-gray-400 shadow-sm dark:bg-gray-700 dark:text-gray-500">
          <svg
            className="h-6 w-6 mb-1 opacity-75"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M7 4v16M17 4v16M3 8h18M3 16h18"
            />
          </svg>
          <span className="text-[10px] font-medium leading-tight line-clamp-2">
            No Poster
          </span>
        </div>
      )}

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
          <span className="font-semibold text-gray-900 dark:text-gray-100">
            {candidate.title}
          </span>
          {candidate.year && (
            <span className="text-xs text-gray-500 dark:text-gray-400">
              ({candidate.year})
            </span>
          )}
          {candidate.content_type && (
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {candidate.content_type === "series" ? "TV series" : "Movie"}
            </span>
          )}
          <span className="font-mono text-xs text-gray-400 dark:text-gray-500">
            ID: {candidate.tmdb_id}
          </span>
        </div>

        <p className="mt-1.5 text-xs text-gray-600 dark:text-gray-300 line-clamp-2 leading-relaxed">
          {candidate.overview?.trim() ? (
            candidate.overview.trim()
          ) : (
            <span className="italic text-gray-400 dark:text-gray-500">
              No overview available.
            </span>
          )}
        </p>
      </div>
    </label>
  );
}
