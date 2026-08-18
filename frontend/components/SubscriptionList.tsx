"use client";

import { useState } from "react";

export interface SubscriptionItem {
  id: number;
  name: string;
}

interface SubscriptionListProps {
  subscriptions: SubscriptionItem[];
  onDeleteSuccess: () => void;
}

export function SubscriptionList({ subscriptions, onDeleteSuccess }: SubscriptionListProps) {
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [deleteError, setDeleteError] = useState("");

  const handleDelete = async (id: number) => {
    setDeletingId(id);
    setDeleteError("");

    try {
      const res = await fetch(`/api/subscriptions/${id}/`, {
        method: "DELETE",
      });

      if (!res.ok && res.status !== 204) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || body.error || `Failed to remove subscription (${res.status})`);
      }

      onDeleteSuccess();
    } catch (err: unknown) {
      setDeleteError(err instanceof Error ? err.message : "Failed to remove subscription.");
    } finally {
      setDeletingId(null);
    }
  };

  if (subscriptions.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-gray-300 p-8 text-center text-sm text-gray-500 dark:border-gray-700 dark:text-gray-400">
        No active subscriptions added yet.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {deleteError && (
        <div className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {deleteError}
        </div>
      )}

      <ul className="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white dark:divide-gray-800 dark:border-gray-800 dark:bg-gray-900">
        {subscriptions.map((sub) => (
          <li
            key={sub.id}
            className="flex items-center justify-between px-4 py-3 text-sm"
          >
            <span className="font-medium text-gray-900 dark:text-gray-100">
              {sub.name}
            </span>
            <button
              type="button"
              onClick={() => handleDelete(sub.id)}
              disabled={deletingId === sub.id}
              className="rounded px-2.5 py-1 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-50 dark:text-red-400 dark:hover:bg-red-950/50"
            >
              {deletingId === sub.id ? "Removing…" : "Remove"}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
