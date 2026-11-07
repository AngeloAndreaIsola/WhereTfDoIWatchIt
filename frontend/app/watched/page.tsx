"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Pagination } from "@/components/Pagination";
import { fetchWatched, type WatchedItem, type WatchedListResponse } from "@/lib/api";

const SORTS = [
  { value: "last_watched", label: "Recently watched" },
  { value: "title", label: "Title" },
  { value: "rating", label: "Rating" },
];

export default function WatchedPage() {
  const searchParams = useSearchParams();

  const [search, setSearch] = useState(searchParams.get("search") || "");
  const [contentType, setContentType] = useState(searchParams.get("content_type") || "all");
  const [sort, setSort] = useState(searchParams.get("sort") || "last_watched");
  const [page, setPage] = useState(Number(searchParams.get("page") || "1"));

  const [data, setData] = useState<WatchedListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params: Record<string, string> = {};
    if (search) params.search = search;
    if (contentType !== "all") params.content_type = contentType;
    if (sort) params.sort = sort;
    if (page > 1) params.page = String(page);
    setLoading(true);
    setError(null);
    fetchWatched(params)
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [search, contentType, sort, page]);

  // reset to page 1 on filter change
  useEffect(() => {
    setPage(1);
  }, [search, contentType, sort]);

  const detailHref = (r: WatchedItem) =>
    r.movie.content_type === "series"
      ? `/tv/${r.movie.tmdb_id}`
      : `/movie/${r.movie.tmdb_id}`;

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-4">Watched</h1>

      <div className="flex flex-wrap gap-3 mb-4">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search title…"
          className="border rounded px-3 py-1.5 text-sm"
        />
        <select value={contentType} onChange={(e) => setContentType(e.target.value)} className="border rounded px-3 py-1.5 text-sm">
          <option value="all">All</option>
          <option value="movie">Movies</option>
          <option value="series">Series</option>
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)} className="border rounded px-3 py-1.5 text-sm">
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </div>

      {loading && <div className="p-6">Loading watched titles…</div>}
      {error && <div className="p-6 text-red-600">Error: {error}</div>}
      {!loading && !error && (!data || !data.results || data.results.length === 0) && (
        <p>No watched titles yet. Add from Watchlist, title detail pages, or imports.</p>
      )}

      {!loading && !error && data && data.results.length > 0 && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {data.results.map((r: WatchedItem) => (
              <Link key={r.movie.tmdb_id} href={detailHref(r)} className="border rounded p-2">
                <div className="font-semibold">{r.movie.title}</div>
                <div className="text-sm text-gray-500">
                  {r.movie.year} · {r.movie.content_type}
                </div>
                {r.user_data?.rating != null && <div className="text-sm">★ {r.user_data.rating}</div>}
                {r.last_watched_at && <div className="text-xs text-gray-400">{r.last_watched_at}</div>}
                <div className="text-xs text-gray-400">watches: {r.watch_count}</div>
              </Link>
            ))}
          </div>
          <Pagination
            page={data.pagination.page}
            totalPages={data.pagination.total_pages}
            hasNext={data.pagination.has_next}
            hasPrevious={data.pagination.has_previous}
            onPageChange={setPage}
          />
        </>
      )}
    </div>
  );
}
