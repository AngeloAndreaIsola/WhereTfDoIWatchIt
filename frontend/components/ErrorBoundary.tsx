"use client";

import React from "react";

export interface ErrorBoundaryProps {
  title?: string;
  error?: Error | string | null;
  reset?: () => void;
}

export function ErrorCard({
  title = "Something went wrong",
  error,
  reset,
}: ErrorBoundaryProps) {
  const errorMessage =
    typeof error === "string"
      ? error
      : error?.message || "An unexpected error occurred.";

  return (
    <div className="mx-auto my-8 max-w-2xl rounded-xl border border-red-200 bg-red-50 p-6 text-center shadow-sm dark:border-red-900/50 dark:bg-red-950/30">
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-400">
        <svg
          className="h-6 w-6"
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth="1.5"
          stroke="currentColor"
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
          />
        </svg>
      </div>
      <h2 className="text-lg font-semibold text-red-900 dark:text-red-200">
        {title}
      </h2>
      <p className="mt-2 text-sm text-red-700 dark:text-red-300">
        {errorMessage}
      </p>
      {reset && (
        <button
          onClick={reset}
          className="mt-6 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500/20 dark:bg-red-700 dark:hover:bg-red-600"
        >
          Try again
        </button>
      )}
    </div>
  );
}

export default ErrorCard;
