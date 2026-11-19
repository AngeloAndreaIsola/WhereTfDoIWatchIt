"use client";

import React, { useEffect, useState } from "react";
import { getMutationHeaders } from "@/lib/candidateReviewApi";

type Candidate = { tmdb_id: number; title: string; year: number | null; content_type: "movie" | "series" };
type Review = {
  id: number; scraped_title: string; scraped_year: number | null;
  provider: { name: string }; candidates: Candidate[]; candidate_state: string;
  expected_updated_at: string;
  manual_review: { allowed: boolean; code: string | null };
  candidate_search: { allowed: boolean; code: string | null };
};
const states: Record<string, string> = {
  not_searched: "Candidates have not been searched.", no_results: "The saved search found no candidates.",
  stale: "Saved candidates are stale or no longer match this observation.",
  cached: "Suggestions from a saved TMDb search. Identity still needs review.",
  persisted: "Saved candidate evidence. Identity still needs review.",
};

const reasons: Record<string, string> = {
  source_permission_unverified: "This source has no approved manual matching permission.",
  tmdb_review_permission_missing: "Separate TMDb lookup authorization is required.",
  tmdb_review_permission_expired: "The TMDb lookup authorization has expired.",
  tmdb_review_permission_denied: "TMDb lookup permission was denied.",
  manual_review_permission_missing: "Manual review is not authorized for this provider.",
};
function blockedReason(code: string | null) {
  return (code && reasons[code]) || "The required permission or shared request controls are unavailable.";
}

export default function LegacyManualReviewPage() {
  const [rows, setRows] = useState<Review[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<{ row: Review; candidate: Candidate } | null>(null);
  async function load() {
    const response = await fetch("/api/scrapers/unresolved/");
    if (!response.ok) throw new Error("Could not load saved observations.");
    setRows(await response.json());
  }
  useEffect(() => { load().catch((err: Error) => setError(err.message)); }, []);
  async function mutate(path: string, data: object) {
    setBusy(true); setError("");
    try {
      const response = await fetch(path, { method: "POST", headers: getMutationHeaders(), body: JSON.stringify(data) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || result.code || "Review action blocked.");
      setPending(null);
      await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Review action failed."); }
    finally { setBusy(false); }
  }
  return <main className="mx-auto max-w-4xl space-y-5 p-6">
    <a href="/candidate-review" className="underline">Back to catalogue candidate review</a>
    <h1 className="text-xl font-semibold">Saved scraper observations</h1>
    <p>Opening this list uses stored evidence only. A confirmed identity does not establish playback availability.</p>
    {error && <p role="alert">{error}</p>}
    {rows.map((row) => <section key={row.id} className="space-y-3 rounded border p-4">
      <h2 className="font-semibold">{row.scraped_title} ({row.scraped_year ?? "year unknown"}) · {row.provider.name}</h2>
      <p>{states[row.candidate_state] || "Candidate evidence is unavailable."}</p>
      {!row.candidate_search.allowed && <p>Candidate lookup blocked: {blockedReason(row.candidate_search.code)}</p>}
      {!row.manual_review.allowed && <p>Manual confirmation blocked: {blockedReason(row.manual_review.code)}</p>}
      <button disabled={busy || !row.candidate_search.allowed} onClick={() => mutate(
        "/api/scrapers/candidates/lookup/", { mapping_id: row.id },
      )} className="rounded border px-3 py-1 disabled:opacity-50">Search TMDb for {row.scraped_title}</button>
      <ul className="space-y-2">{row.candidates.map((candidate) => <li key={`${candidate.content_type}:${candidate.tmdb_id}`}>
        {candidate.title} · {candidate.year ?? "year unknown"} · {candidate.content_type} · TMDb {candidate.tmdb_id}{" "}
        <button disabled={busy || !row.candidate_search.allowed} onClick={() => mutate(
          "/api/scrapers/candidates/lookup/", { mapping_id: row.id, tmdb_id: candidate.tmdb_id },
        )}>Cache details for {candidate.title}</button>{" "}
        <button disabled={busy || !row.manual_review.allowed} onClick={() => setPending({ row, candidate })}>Review {candidate.title}</button>
      </li>)}</ul>
    </section>)}
    {pending && <section role="dialog" aria-label="Confirm saved identity" className="space-y-3 rounded border p-4">
      <p>Confirm {pending.row.scraped_title} as {pending.candidate.title} ({pending.candidate.content_type}, TMDb {pending.candidate.tmdb_id}) using fresh saved details? No availability refresh will run.</p>
      <button disabled={busy} onClick={() => mutate("/api/scrapers/confirm-match/", {
        mapping_id: pending.row.id, tmdb_id: pending.candidate.tmdb_id,
        content_type: pending.candidate.content_type, expected_updated_at: pending.row.expected_updated_at,
      })}>Confirm saved identity</button>{" "}
      <button disabled={busy} onClick={() => setPending(null)}>Cancel</button>
    </section>}
  </main>;
}
