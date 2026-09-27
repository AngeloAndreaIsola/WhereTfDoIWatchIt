"use client";

import FileImport from "@/components/watchlist/FileImport";
import LetterboxdImport from "@/components/watchlist/LetterboxdImport";
import GoogleInterestsImport from "@/components/watchlist/GoogleInterestsImport";

/**
 * The complete import workflow (file export, Letterboxd URL, Google
 * Interests). Single source of truth rendered by `/watchlist/import`.
 */
export default function ImportSection() {
  return (
    <>
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
          Import
        </h2>
        <div className="h-px flex-1 bg-gray-200 dark:bg-gray-700" />
      </div>
      <FileImport />
      <LetterboxdImport />
      <GoogleInterestsImport />
    </>
  );
}
