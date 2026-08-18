export default function Loading() {
  return (
    <main className="mx-auto max-w-2xl px-4 py-10 sm:px-6">
      <div className="animate-pulse space-y-6">
        {/* Header Skeleton */}
        <div className="flex flex-col items-center space-y-2 text-center">
          <div className="h-8 w-64 rounded-md bg-gray-200 dark:bg-gray-800" />
          <div className="h-4 w-80 rounded-md bg-gray-200 dark:bg-gray-800" />
        </div>

        {/* Search Bar Skeleton */}
        <div className="flex gap-2">
          <div className="h-10 flex-1 rounded-lg bg-gray-200 dark:bg-gray-800" />
          <div className="h-10 w-24 rounded-lg bg-gray-200 dark:bg-gray-800" />
        </div>

        {/* Content List Skeleton */}
        <div className="mt-8 space-y-3">
          <div className="h-4 w-20 rounded bg-gray-200 dark:bg-gray-800" />
          <div className="rounded-lg border border-gray-200 p-4 space-y-3 dark:border-gray-800">
            <div className="flex justify-between items-center">
              <div className="h-5 w-40 rounded bg-gray-200 dark:bg-gray-800" />
              <div className="h-4 w-12 rounded bg-gray-200 dark:bg-gray-800" />
            </div>
            <div className="h-4 w-full rounded bg-gray-200 dark:bg-gray-800" />
            <div className="h-4 w-3/4 rounded bg-gray-200 dark:bg-gray-800" />
          </div>
        </div>
      </div>
    </main>
  );
}
