"use client";

import { useState } from "react";

interface SubscriptionFormProps {
  onSuccess: () => void;
}

export function SubscriptionForm({ onSuccess }: SubscriptionFormProps) {
  const [name, setName] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Please enter a subscription name.");
      return;
    }

    setAdding(true);
    setError("");

    try {
      const res = await fetch("/api/subscriptions/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ name: trimmed }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        let errMsg = "Failed to add subscription.";
        if (body.name && Array.isArray(body.name) && body.name.length > 0) {
          errMsg = body.name[0];
        } else if (body.detail || body.error) {
          errMsg = body.detail || body.error;
        }
        throw new Error(errMsg);
      }

      setName("");
      onSuccess();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to add subscription.");
    } finally {
      setAdding(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      {error && (
        <div className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/30 dark:text-red-400">
          {error}
        </div>
      )}

      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          type="text"
          placeholder="Service name (e.g. Netflix, Disney+, Max)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="flex-1 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm
                     placeholder-gray-400 shadow-sm
                     focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20
                     dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100
                     dark:placeholder-gray-500 dark:focus:border-blue-400"
        />
        <button
          type="submit"
          disabled={adding}
          className="rounded-lg bg-blue-600 px-5 py-2 text-sm font-medium text-white shadow-sm
                     hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50
                     dark:bg-blue-500 dark:hover:bg-blue-600"
        >
          {adding ? "Adding…" : "Add Subscription"}
        </button>
      </div>
    </form>
  );
}
