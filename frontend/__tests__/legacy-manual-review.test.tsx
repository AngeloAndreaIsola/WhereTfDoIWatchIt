import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LegacyManualReviewPage from "@/app/candidate-review/legacy/page";

const row = {
  id: 8, scraped_title: "Fixture Identity", scraped_year: 2020, provider: { name: "NHK" },
  candidate_state: "cached", expected_updated_at: "2026-11-19T12:00:00Z",
  candidates: [{ tmdb_id: 420, title: "Fixture Identity", year: 2020, content_type: "series" }],
  manual_review: { allowed: true, code: null }, candidate_search: { allowed: true, code: null },
};
function response(data: unknown, ok = true) { return { ok, json: async () => data } as Response; }
afterEach(() => vi.restoreAllMocks());

describe("Legacy saved-evidence review", () => {
  it("loads saved candidates with GET only, without automatic lookup or confirmation", async () => {
    const fetch = vi.spyOn(global, "fetch").mockResolvedValue(response([row]));
    render(<LegacyManualReviewPage />);
    await screen.findByRole("button", { name: "Review Fixture Identity" });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith("/api/scrapers/unresolved/");
    expect(screen.getByText(/Suggestions from a saved TMDb search/)).toBeInTheDocument();
  });

  it("makes candidate lookup an explicit user action", async () => {
    const fetch = vi.spyOn(global, "fetch").mockResolvedValue(response([row]));
    render(<LegacyManualReviewPage />);
    const button = await screen.findByRole("button", { name: "Search TMDb for Fixture Identity" });
    expect(fetch).toHaveBeenCalledTimes(1);
    await userEvent.click(button);
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/scrapers/candidates/lookup/", expect.objectContaining({
      method: "POST", body: JSON.stringify({ mapping_id: 8 }),
    })));
  });

  it("confirms the complete typed identity only after review and a second click", async () => {
    const fetch = vi.spyOn(global, "fetch").mockImplementation(async (_url, init) => response(init?.method === "POST" ? {} : [row]));
    render(<LegacyManualReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Review Fixture Identity" }));
    expect(screen.getByRole("dialog", { name: "Confirm saved identity" })).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Confirm saved identity" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/scrapers/confirm-match/", expect.objectContaining({
      method: "POST", body: JSON.stringify({ mapping_id: 8, tmdb_id: 420, content_type: "series", expected_updated_at: row.expected_updated_at }),
    })));
    expect(fetch.mock.calls.some(([url]) => String(url).includes("refresh"))).toBe(false);
  });

  it("keeps cached suggestions readable while permission blocks lookup and confirmation", async () => {
    const fetch = vi.spyOn(global, "fetch").mockResolvedValue(response([{
      ...row, manual_review: { allowed: false, code: "source_permission_unverified" },
      candidate_search: { allowed: false, code: "source_permission_unverified" },
    }]));
    render(<LegacyManualReviewPage />);
    expect(await screen.findByRole("button", { name: "Review Fixture Identity" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Search TMDb for Fixture Identity" })).toBeDisabled();
    expect(screen.getByText(/Candidate lookup blocked/)).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("distinguishes missing, empty and stale evidence and surfaces conflicts", async () => {
    const fetch = vi.spyOn(global, "fetch").mockImplementation(async (_url, init) => init?.method === "POST"
      ? response({ error: "The saved observation changed; review again." }, false)
      : response([row, { ...row, id: 9, candidate_state: "not_searched", candidates: [] },
        { ...row, id: 10, candidate_state: "no_results", candidates: [] },
        { ...row, id: 11, candidate_state: "stale", candidates: [] }]));
    render(<LegacyManualReviewPage />);
    await screen.findByText("Candidates have not been searched.");
    expect(screen.getByText("The saved search found no candidates.")).toBeInTheDocument();
    expect(screen.getByText(/Saved candidates are stale/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Review Fixture Identity" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm saved identity" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The saved observation changed; review again.");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });
});
