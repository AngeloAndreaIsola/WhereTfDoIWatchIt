"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html>
      <body>
        <main className="mx-auto max-w-2xl px-4 py-10 text-center">
          <h2 className="text-2xl font-bold">Something went wrong!</h2>
          <button
            type="button"
            onClick={() => reset()}
            className="mt-4 rounded-lg bg-blue-600 px-4 py-2 text-white"
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
