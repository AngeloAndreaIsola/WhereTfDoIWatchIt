"use client";

import { useEffect, useState, useRef } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { Pagination } from "@/components/Pagination";
import { fetchWatched, type WatchedItem, type WatchedListResponse } from "@/lib/api";

const SORTS = [
  { value: "last_watched", label: "Recently watched" },
  { value: "title", label: "Title" },
  { value: "rating", label: "Rating" },
];

export default function WatchedPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [search, setSearch] = useState(searchParams.get("search") || "");
  const [contentType, setContentType] = useState(searchParams.get("content_type") || "all");
  const [sort, setSort] = useState(searchParams.get("sort") || "last_watched");
  const [watched, setWatched] = useState(searchParams.get("watched") || "all");
  const [page, setPage] = useState(Number(searchParams.get("page") || "1"));

  const [data, setData] = useState<WatchedListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // Restore filters and page when browser navigation changes the URL.
  useEffect(() => {
    setSearch(searchParams.get("search") || "");
    setContentType(searchParams.get("content_type") || "all");
    setSort(searchParams.get("sort") || "last_watched");
    setWatched(searchParams.get("watched") || "all");
    setPage(Number(searchParams.get("page") || "1"));
  }, [searchParams]);

  // Sync state -> URL
  useEffect(() => {
    const params = new URLSearchParams(searchParams.toString());
    if (search) params.set("search", search); else params.delete("search");
    if (contentType !== "all") params.set("content_type", contentType); else params.delete("content_type");
    if (sort && sort !== "last_watched") params.set("sort", sort); else if (sort === "last_watched") params.delete("sort");
    if (watched && watched !== "all") params.set("watched", watched); else params.delete("watched");
    if (page > 1) params.set("page", String(page)); else params.delete("page");
    router.replace(`/watched?${params.toString()}`);
  }, [search, contentType, sort, watched, page]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch with abort
  useEffect(() => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    const params: Record<string, string> = {};
    if (search) params.search = search;
    if (contentType !== "all") params.content_type = contentType;
    if (sort) params.sort = sort;
    if (watched && watched !== "all") params.watched = watched;
    if (page > 1) params.page = String(page);
    setLoading(true);
    setError(null);
    fetchWatched(params)
      .then((d) => {
        if (!ac.signal.aborted) setData(d);
      })
      .catch((e) => {
        if (!ac.signal.aborted) setError(String(e));
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
  }, [search, contentType, sort, watched, page]);

  const detailHref = (r: WatchedItem) =>
    r.movie.content_type === "series" ? `/tv/${r.movie.tmdb_id}` : `/movie/${r.movie.tmdb_id}`;

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-4">Watched</h1>
      <div className="flex flex-wrap gap-3 mb-4">
        <input value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} placeholder="Search title…" className="border rounded px-3 py-1.5 text-sm" />
        <select value={contentType} onChange={(e) => { setContentType(e.target.value); setPage(1); }} className="border rounded px-3 py-1.5 text-sm">
          <option value="all">All</option>
          <option value="movie">Movies</option>
          <option value="series">Series</option>
        </select>
        <select value={sort} onChange={(e) => { setSort(e.target.value); setPage(1); }} className="border rounded px-3 py-1.5 text-sm">
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
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
                <div className="text-sm text-gray-500">{r.movie.year} · {r.movie.content_type}</div>
                {r.user_data?.rating != null && <div className="text-sm">★ {r.user_data.rating}</div>}
                {r.last_watched_at && <div className="text-xs text-gray-400">{r.last_watched_at}</div>}
                <div className="text-xs text-gray-400">watches: {r.watch_count}</div>
              </Link>
            ))}
          </div>
          <Pagination page={data.pagination.page} totalPages={data.pagination.total_pages} hasNext={data.pagination.has_next} hasPrevious={data.pagination.has_previous} onPageChange={setPage} />
        </>
      )}
    </div>
  );
}
