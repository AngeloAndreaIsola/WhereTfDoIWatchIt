"use client";

import React, { useState, useEffect, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import AmbiguousMatchCard, { Candidate } from "@/components/AmbiguousMatchCard";
import { readStashedAmbiguousItems } from "@/lib/ambiguousReview";

interface AmbiguousItem {
  title: string;
  year: number | null;
  uri?: string | null;
  candidates: Candidate[];
  reason?: string;
}

function AmbiguousClient() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const itemsParam = searchParams.get("items");

  let queryItems: AmbiguousItem[] = [];
  let parseError = "";

  if (itemsParam) {
    try {
      const parsed = JSON.parse(itemsParam);
      queryItems = Array.isArray(parsed) ? parsed : [];
    } catch {
      parseError = "Failed to parse ambiguous items parameter.";
    }
  }

  // Items are handed over through sessionStorage (see lib/ambiguousReview.ts)
  // so large ambiguity queues don't produce a 431 from the query string.
  const [stashedItems, setStashedItems] = useState<AmbiguousItem[] | null>(
    null
  );

  useEffect(() => {
    if (itemsParam || stashedItems !== null) return;
    setStashedItems(readStashedAmbiguousItems<AmbiguousItem>() ?? []);
  }, [itemsParam, stashedItems]);

  const items = itemsParam ? queryItems : stashedItems ?? [];
  const ready = Boolean(itemsParam) || stashedItems !== null;

  // selections map: itemIndex -> selected tmdb_id (or null if skip)
  const [selections, setSelections] = useState<Record<number, number | null>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");

  const handleRadioChange = (itemIndex: number, tmdbId: number | null) => {
    setSelections((prev) => ({ ...prev, [itemIndex]: tmdbId }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError("");

    // Build payload of selected items
    const selectedPayload = items
      .map((item, index) => {
        const selectedId = selections[index];
        if (selectedId === null || selectedId === undefined) return null;
        return {
          original_title: item.title,
          year: item.year,
          tmdb_id: selectedId,
        };
      })
      .filter((entry): entry is NonNullable<typeof entry> => entry !== null);

    try {
      const res = await fetch("/api/watchlist/resolve/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selections: selectedPayload }),
      });

      if (res.status === 404) {
        setSubmitError(
          "Resolving matches is not available yet. The backend resolve endpoint has not been implemented."
        );
        return;
      }

      if (res.status === 429) {
        const body = await res.json().catch(() => ({}));
        setSubmitError(body.error ?? "Rate limited — try again later.");
        return;
      }

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error ?? `Failed to resolve items (${res.status})`);
      }

      // On success, redirect to watchlist
      router.push("/watchlist");
    } catch (err: unknown) {
      setSubmitError(
        err instanceof Error ? err.message : "Failed to resolve items."
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (parseError) {
    return (
      <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
        {parseError}
      </div>
    );
  }

  if (!ready) {
    return (
      <p className="py-6 text-center text-sm text-gray-500 dark:text-gray-400">
        Loading ambiguous items…
      </p>
    );
  }

  if (items.length === 0) {
    return (
      <div className="text-center py-8">
        <p className="text-sm text-gray-500 dark:text-gray-400">
          No ambiguous items provided for review.
        </p>
        <Link
          href="/watchlist"
          className="mt-4 inline-block text-sm font-medium text-blue-600 hover:underline dark:text-blue-400"
        >
          ← Back to Watchlist
        </Link>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {submitError && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
          {submitError}
        </div>
      )}

      {items.map((item, itemIdx) => {
        const selectedValue = selections[itemIdx] ?? null;

        return (
          <div
            key={itemIdx}
            className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-700 dark:bg-gray-800"
          >
            <div className="mb-3">
              <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
                {item.title}
                {item.year && (
                  <span className="ml-1 text-sm font-normal text-gray-500 dark:text-gray-400">
                    ({item.year})
                  </span>
                )}
              </h3>
              {item.reason && (
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Reason: {item.reason}
                </p>
              )}
            </div>

            <div className="space-y-2.5">
              <p className="text-xs font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">
                Select matching TMDb title:
              </p>

              {/* Skip option */}
              <label
                className={`flex items-center gap-3 rounded-lg border p-3 text-sm cursor-pointer transition-colors ${
                  selectedValue === null
                    ? "border-blue-500 bg-blue-50/60 ring-1 ring-blue-500 dark:border-blue-500 dark:bg-blue-950/30"
                    : "border-gray-200 bg-white hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800/60 dark:hover:bg-gray-700/50"
                }`}
              >
                <input
                  type="radio"
                  name={`item-${itemIdx}`}
                  checked={selectedValue === null}
                  onChange={() => handleRadioChange(itemIdx, null)}
                  className="h-4 w-4 text-blue-600 focus:ring-blue-500"
                />
                <span className="text-gray-600 dark:text-gray-400 font-medium">
                  Skip (don&apos;t match)
                </span>
              </label>

              {/* Candidates */}
              {item.candidates.map((cand) => (
                <AmbiguousMatchCard
                  key={cand.tmdb_id}
                  candidate={cand}
                  selected={selectedValue === cand.tmdb_id}
                  onSelect={() => handleRadioChange(itemIdx, cand.tmdb_id)}
                  name={`item-${itemIdx}`}
                />
              ))}
            </div>
          </div>
        );
      })}

      <div className="flex items-center justify-between pt-4">
        <Link
          href="/watchlist"
          className="text-sm text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-200"
        >
          Cancel & Return
        </Link>
        <button
          type="submit"
          disabled={submitting}
          className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-blue-500 dark:hover:bg-blue-600"
        >
          {submitting ? "Saving Selections…" : "Confirm & Save Selections"}
        </button>
      </div>
    </form>
  );
}

export default function AmbiguousPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-bold tracking-tight">
          Resolve Ambiguous Titles
        </h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Match ambiguous Letterboxd entries to their correct TMDb IDs
        </p>
      </div>

      <Suspense
        fallback={
          <p className="py-6 text-center text-sm text-gray-500 dark:text-gray-400">
            Loading ambiguous items…
          </p>
        }
      >
        <AmbiguousClient />
      </Suspense>
    </main>
  );
}
