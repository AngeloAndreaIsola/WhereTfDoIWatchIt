"use client";

interface RefreshAvailabilityButtonProps {
  /** A refresh is queued or being enqueued — spin, label, and disable the button. */
  inProgress: boolean;
  /** Non-blocking failure note (enqueue error, worker gave up, poll timed out). */
  error?: string | null;
  onRefresh: () => void;
}

/**
 * Manual "Refresh availability" action for the movie/series detail pages.
 *
 * Clicking enqueues an explicit refresh (high-priority queue request, never a
 * direct provider call); the page polls the detail endpoint until the worker
 * drains it and swaps in the new canonical offers.
 */
export default function RefreshAvailabilityButton({
  inProgress,
  error,
  onRefresh,
}: RefreshAvailabilityButtonProps) {
  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={onRefresh}
        disabled={inProgress}
        title="Check the streaming catalogues again now"
        className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
      >
        <svg
          className={`h-4 w-4 ${inProgress ? "animate-spin" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"
          />
        </svg>
        {inProgress ? "Refreshing…" : "Refresh availability"}
      </button>
      {error && (
        <p role="alert" className="text-xs text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  );
}
