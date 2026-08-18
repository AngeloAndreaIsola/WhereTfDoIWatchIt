"use client";

import { useEffect } from "react";
import ErrorCard from "@/components/ErrorBoundary";

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="mx-auto max-w-2xl px-4 py-10 sm:px-6">
      <ErrorCard error={error} reset={reset} />
    </main>
  );
}
