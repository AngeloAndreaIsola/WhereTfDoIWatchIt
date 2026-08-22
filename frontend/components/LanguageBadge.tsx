"use client";

import React from "react";
import { getLanguageDisplayName } from "@/lib/languages";

interface LanguageBadgeProps {
  code: string;
  type: "audio" | "subtitle";
  isPreferred: boolean;
  compact?: boolean;
}

export default function LanguageBadge({
  code,
  type,
  isPreferred,
  compact = false,
}: LanguageBadgeProps) {
  const displayName = getLanguageDisplayName(code);

  if (isPreferred) {
    const colorClasses =
      type === "audio"
        ? "bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-900/40 dark:text-emerald-300 dark:border-emerald-700"
        : "bg-blue-100 text-blue-800 border-blue-300 dark:bg-blue-900/40 dark:text-blue-300 dark:border-blue-700";

    return (
      <span
        className={`inline-flex items-center gap-1 rounded-md border font-semibold shadow-xs ${colorClasses} ${
          compact ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-xs"
        }`}
        title={`Preferred ${type} language match (${code})`}
      >
        <span>{compact ? code : displayName}</span>
        <span className="text-[10px] text-emerald-600 dark:text-emerald-400 font-bold">✓</span>
      </span>
    );
  }

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md bg-gray-100 font-normal text-gray-600 dark:bg-gray-700/60 dark:text-gray-400 ${
        compact ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-xs"
      }`}
    >
      <span>{compact ? code : displayName}</span>
    </span>
  );
}
