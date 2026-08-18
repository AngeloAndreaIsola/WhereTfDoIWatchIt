"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { SubscriptionForm } from "@/components/SubscriptionForm";
import { SubscriptionList, SubscriptionItem } from "@/components/SubscriptionList";

export default function SubscriptionsPage() {
  const [subscriptions, setSubscriptions] = useState<SubscriptionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState("");

  const fetchSubscriptions = useCallback(async () => {
    setLoading(true);
    setFetchError("");
    try {
      const res = await fetch("/api/subscriptions/");
      if (!res.ok) {
        throw new Error(`Failed to load subscriptions (${res.status})`);
      }
      const data: SubscriptionItem[] = await res.json();
      setSubscriptions(data);
    } catch (err: unknown) {
      setFetchError(err instanceof Error ? err.message : "Failed to load subscriptions");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSubscriptions();
  }, [fetchSubscriptions]);

  return (
    <main className="mx-auto max-w-2xl px-4 py-10 sm:px-6">
      {/* Top navigation link */}
      <div className="mb-6">
        <Link href="/preferences" className="text-sm text-blue-600 hover:underline dark:text-blue-400">
          ← Back to preferences
        </Link>
      </div>

      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">Subscriptions</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Manage your active streaming service subscriptions used for availability ranking.
        </p>
      </div>

      {/* Loading state */}
      {loading && (
        <div className="flex items-center gap-2 py-8 text-sm text-gray-500 dark:text-gray-400">
          <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
          </svg>
          Loading subscriptions…
        </div>
      )}

      {/* Initial load error */}
      {fetchError && (
        <div className="mb-6 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {fetchError}
        </div>
      )}

      {!loading && !fetchError && (
        <div className="space-y-8">
          <section>
            <h2 className="mb-3 text-lg font-semibold tracking-tight">Add New Subscription</h2>
            <SubscriptionForm onSuccess={fetchSubscriptions} />
          </section>

          <section>
            <h2 className="mb-3 text-lg font-semibold tracking-tight">Your Subscriptions</h2>
            <SubscriptionList
              subscriptions={subscriptions}
              onDeleteSuccess={fetchSubscriptions}
            />
          </section>
        </div>
      )}
    </main>
  );
}
