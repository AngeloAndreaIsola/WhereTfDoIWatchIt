import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-[60vh] max-w-2xl flex-col items-center justify-center px-4 text-center sm:px-6">
      <div className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm dark:border-gray-800 dark:bg-gray-900/50">
        <h1 className="text-6xl font-extrabold tracking-tight text-gray-900 dark:text-gray-100">
          404
        </h1>
        <h2 className="mt-4 text-xl font-semibold tracking-tight text-gray-800 dark:text-gray-200">
          Page Not Found
        </h2>
        <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
          Sorry, we couldn&apos;t find the page you&apos;re looking for.
        </p>
        <div className="mt-6">
          <Link
            href="/"
            className="inline-flex items-center justify-center rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500/20 dark:bg-blue-500 dark:hover:bg-blue-600"
          >
            Return Home
          </Link>
        </div>
      </div>
    </main>
  );
}
