"use client";

import React, { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import ImportSection from "@/components/watchlist/ImportSection";

function ImportContent() {
  const searchParams = useSearchParams();
  const qs = searchParams?.toString() || "";
  const backHref = qs ? `/watchlist/?${qs}` : "/watchlist";

  return (
    <>
      <div>
        <Link
          href={backHref}
          className="inline-flex items-center gap-1 text-sm font-medium text-blue-600 hover:underline dark:text-blue-400"
        >
          ← Back to Watchlist
        </Link>
        <h1 className="mt-2 text-2xl font-bold text-gray-900 dark:text-gray-100">
          Import
        </h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Bring titles into your watchlist from Letterboxd, Google Interests, or
          an export file.
        </p>
      </div>

      <ImportSection />
    </>
  );
}

export default function WatchlistImportPage() {
  return (
    <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
      <Suspense
        fallback={
          <p className="py-6 text-center text-sm text-gray-500 dark:text-gray-400">
            Loading import…
          </p>
        }
      >
        <ImportContent />
      </Suspense>
    </div>
  );
}
