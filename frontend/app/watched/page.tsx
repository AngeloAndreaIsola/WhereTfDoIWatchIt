"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchWatched, type WatchedItem, type WatchedListResponse } from "@/lib/api";

export default function WatchedPage() {
  const [data, setData] = useState<WatchedListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchWatched()
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-6">Loading watched titles…</div>;
  if (error) return <div className="p-6 text-red-600">Error: {error}</div>;
  if (!data || !data.results || data.results.length === 0)
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-2">Watched</h1>
        <p>No watched titles yet. Add from Watchlist, title detail pages, or imports.</p>
      </div>
    );

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-4">Watched</h1>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {data.results.map((r: WatchedItem) => (
          <Link key={r.movie.tmdb_id} href={`/movie/${r.movie.tmdb_id}`} className="border rounded p-2">
            <div className="font-semibold">{r.movie.title}</div>
            <div className="text-sm text-gray-500">{r.movie.year} · {r.movie.content_type}</div>
            {r.user_data?.rating != null && <div className="text-sm">★ {r.user_data.rating}</div>}
            {r.last_watched_at && <div className="text-xs text-gray-400">{r.last_watched_at}</div>}
          </Link>
        ))}
      </div>
    </div>
  );
}
