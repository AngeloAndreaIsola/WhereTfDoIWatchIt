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
      <FileImport />
      <LetterboxdImport />
      <GoogleInterestsImport />
    </>
  );
}
