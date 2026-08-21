export interface SearchResult {
  tmdb_id: number;
  title: string;
  year: number | null;
}

export async function search(query: string): Promise<SearchResult[]> {
  const trimmed = query.trim();
  if (!trimmed) {
    return [];
  }

  const res = await fetch(`/api/search/?q=${encodeURIComponent(trimmed)}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error ?? `Search failed (${res.status})`);
  }

  const data = await res.json();
  return data.results ?? [];
}
