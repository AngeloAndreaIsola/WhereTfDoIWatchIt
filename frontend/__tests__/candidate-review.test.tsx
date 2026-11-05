import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CandidateReviewPage from "@/app/candidate-review/page";
import {
  mockCandidateQueue,
  mockQueueEmpty,
  mockAmbiguousCandidatesResponse,
  mockConflictCandidatesResponse,
  mockManualConflictCandidatesResponse,
  mockQueueItemMappedNoOffer,
} from "@/lib/candidateReviewFixtures";

describe("Candidate Review Interface", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders loading and empty queue states", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/") || u.includes("/api/admin/provider-content/unresolved/")) {
        return new Response(JSON.stringify(mockQueueEmpty), { status: 200 });
      }
      return new Response("Not found", { status: 404 });
    });

    render(<CandidateReviewPage />);

    expect(screen.getByText(/TMDb Candidate Review Interface/i)).toBeInTheDocument();
    expect(screen.getByText(/Loading queue items…/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText(/Queue is empty. No review needed./i)).toBeInTheDocument();
    });
  });

  it("handles unavailable backend gracefully without crashing", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async () => {
      return new Response(JSON.stringify({ detail: "Service Unavailable" }), { status: 503 });
    });

    render(<CandidateReviewPage />);

    await waitFor(() => {
      expect(
        screen.getByText(/Candidate review backend endpoint is currently unavailable/i)
      ).toBeInTheDocument();
    });
  });

  it("renders unresolved queue items and selects the first item", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/") || u.includes("/api/admin/provider-content/unresolved/")) {
        return new Response(JSON.stringify(mockCandidateQueue), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-101/candidates/")) {
        return new Response(JSON.stringify(mockAmbiguousCandidatesResponse), { status: 200 });
      }
      return new Response("Not found", { status: 404 });
    });

    render(<CandidateReviewPage />);

    await waitFor(() => {
      expect(screen.getByText(/Unresolved Queue \(4\)/i)).toBeInTheDocument();
    });

    // Verify first item is rendered and selected
    expect(screen.getByRole("heading", { name: "Pinocchio" })).toBeInTheDocument();
    expect(screen.getAllByText(/Tubi/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/pinocchio-2022-stream/i).length).toBeGreaterThan(0);
  });

  it("displays multiple ambiguous candidates and evidence comparison", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/") || u.includes("/api/admin/provider-content/unresolved/")) {
        return new Response(JSON.stringify(mockCandidateQueue), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-101/candidates/")) {
        return new Response(JSON.stringify(mockAmbiguousCandidatesResponse), { status: 200 });
      }
      return new Response("Not found", { status: 404 });
    });

    render(<CandidateReviewPage />);

    await waitFor(() => {
      expect(screen.getByText(/Discovered TMDb Candidates \(3\)/i)).toBeInTheDocument();
    });

    // Check candidate cards
    expect(screen.getByRole("heading", { name: "Pinocchio: A True Story" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Guillermo del Toro's Pinocchio" })).toBeInTheDocument();

    // Verify supporting evidence
    expect(screen.getByText("Exact title match ('Pinocchio')")).toBeInTheDocument();
    expect(screen.getAllByText("Exact release year match (2022)").length).toBeGreaterThan(0);

    // Verify ambiguity warning
    expect(
      screen.getByText(/Ambiguity Warning: Another candidate has an identical high confidence score/i)
    ).toBeInTheDocument();
  });

  it("renders movie/year conflicts and existing mapping details", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/") || u.includes("/api/admin/provider-content/unresolved/")) {
        return new Response(JSON.stringify(mockCandidateQueue), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-102/candidates/")) {
        return new Response(JSON.stringify(mockConflictCandidatesResponse), { status: 200 });
      }
      return new Response("Not found", { status: 404 });
    });

    const user = userEvent.setup();
    render(<CandidateReviewPage />);

    // Wait for queue to load and initial item to be selected
    await waitFor(() => {
      expect(screen.getByTestId("queue-item-pc-101")).toHaveAttribute("aria-pressed", "true");
    });

    // Select second queue item (The Thing, pc-102)
    const thingButton = screen.getByTestId("queue-item-pc-102");
    await user.click(thingButton);

    await waitFor(() => {
      expect(screen.getAllByRole("heading", { name: "The Thing" }).length).toBeGreaterThan(0);
    });

    // Check existing mapping display
    expect(screen.getAllByText(/AUTOMATED MAPPING/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/TMDb ID:/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/60935/i).length).toBeGreaterThan(0);

    // Check year conflict warning
    await waitFor(() => {
      expect(
        screen.getByText((_, element) =>
          element?.tagName.toLowerCase() === "li" &&
          (element?.textContent?.includes("MOVIE_TYPE_CONFLICT: Release year conflict: candidate is 2011, provider native year is 1982") ?? false)
        )
      ).toBeInTheDocument();
    });

    // Check pre-rejected candidate displays recorded rejection reason
    expect(
      screen.getByText(/Release year 2011 does not match provider broadcast of the 1982 classic/i)
    ).toBeInTheDocument();
  });

  it("displays manual mapping conflict warnings clearly and disables acceptance", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/") || u.includes("/api/admin/provider-content/unresolved/")) {
        return new Response(JSON.stringify(mockCandidateQueue), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-103/candidates/")) {
        return new Response(JSON.stringify(mockManualConflictCandidatesResponse), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-101/candidates/")) {
        return new Response(JSON.stringify(mockAmbiguousCandidatesResponse), { status: 200 });
      }
      return new Response("Not found", { status: 404 });
    });

    const user = userEvent.setup();
    render(<CandidateReviewPage />);

    // Wait for queue to load and initial item to be selected
    await waitFor(() => {
      expect(screen.getByTestId("queue-item-pc-101")).toHaveAttribute("aria-pressed", "true");
    });

    const duneButton = screen.getByTestId("queue-item-pc-103");
    await user.click(duneButton);

    await waitFor(() => {
      expect(
        screen.getByText((_, element) =>
          element?.tagName.toLowerCase() === "li" &&
          (element?.textContent?.includes("MANUAL MAPPING CONFLICT: Another manual mapping 55 exists for provider prime_video:dune-4k-uhd claiming proposed TMDb 438631") ?? false)
        )
      ).toBeInTheDocument();
    });

    // Acceptance button should be disabled for the candidate with manual conflict
    const acceptButtons = screen.getAllByRole("button", { name: /Accept Candidate…/i });
    expect(acceptButtons[0]).toBeDisabled();
  });

  it("distinguishes mapping versus offer availability and enforces 'Mapped ≠ Watchable'", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/") || u.includes("/api/admin/provider-content/unresolved/")) {
        return new Response(JSON.stringify(mockCandidateQueue), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-104/candidates/")) {
        return new Response(
          JSON.stringify({
            provider_content: mockQueueItemMappedNoOffer,
            candidates: [],
            summary: {
              total_candidates: 0,
              accepted_count: 0,
              rejected_count: 0,
              pending_count: 0,
              has_manual_conflict: false,
            },
          }),
          { status: 200 }
        );
      }
      return new Response("Not found", { status: 404 });
    });

    const user = userEvent.setup();
    render(<CandidateReviewPage />);

    // Wait for queue to load and initial item to be selected
    await waitFor(() => {
      expect(screen.getByTestId("queue-item-pc-101")).toHaveAttribute("aria-pressed", "true");
    });

    const drWhoButton = screen.getByTestId("queue-item-pc-104");
    await user.click(drWhoButton);

    await waitFor(() => {
      expect(screen.getByText(/MANUAL MAPPING/i)).toBeInTheDocument();
    });

    // Verify critical operational rule disclaimer
    expect(screen.getByText(/Mapping does NOT mean watchable/i)).toBeInTheDocument();
    expect(
      screen.getByText(/This content is mapped to TMDb 239770, but has ZERO active playback offers/i)
    ).toBeInTheDocument();
  });

  it("validates mandatory non-empty reason for accept/reject mutations", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/") || u.includes("/api/admin/provider-content/unresolved/")) {
        return new Response(JSON.stringify(mockCandidateQueue), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-101/candidates/")) {
        return new Response(JSON.stringify(mockAmbiguousCandidatesResponse), { status: 200 });
      }
      return new Response("Not found", { status: 404 });
    });

    const user = userEvent.setup();
    render(<CandidateReviewPage />);

    await waitFor(() => {
      expect(screen.getByText(/Discovered TMDb Candidates \(3\)/i)).toBeInTheDocument();
    });

    // Click Accept on first candidate
    const acceptButtons = screen.getAllByRole("button", { name: /Accept Candidate…/i });
    await user.click(acceptButtons[0]);

    // Confirmation modal should be visible
    expect(screen.getByRole("heading", { name: /Confirm Candidate Acceptance/i })).toBeInTheDocument();

    const submitConfirmButton = screen.getByRole("button", { name: /Confirm & Accept Candidate/i });

    // Attempt submitting without reason
    await user.click(submitConfirmButton);

    expect(
      screen.getByText(/A mandatory reason is required to accept this candidate/i)
    ).toBeInTheDocument();

    // Type spaces only
    const reasonTextarea = screen.getByPlaceholderText(/Explain why this candidate is the verified match/i);
    await user.type(reasonTextarea, "     ");
    await user.click(submitConfirmButton);

    expect(
      screen.getByText(/A mandatory reason is required to accept this candidate/i)
    ).toBeInTheDocument();
  });

  it("performs accept mutation after confirmation and sends expected_revision", async () => {
    let acceptCalled = false;
    let acceptPayload: unknown = null;

    vi.spyOn(global, "fetch").mockImplementation(async (url, init) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/") || u.includes("/api/admin/provider-content/unresolved/")) {
        return new Response(JSON.stringify(mockCandidateQueue), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-101/candidates/532639/accept/")) {
        acceptCalled = true;
        acceptPayload = JSON.parse(String(init?.body));
        return new Response(
          JSON.stringify({
            success: true,
            message: "Candidate accepted",
            tmdb_id: 532639,
            provider_content_id: "pc-101",
            review_state: "accepted",
            mapping_updated: true,
            reviewed_at: new Date().toISOString(),
          }),
          { status: 200 }
        );
      }
      if (u.includes("/api/admin/provider-content/pc-101/candidates/")) {
        return new Response(JSON.stringify(mockAmbiguousCandidatesResponse), { status: 200 });
      }
      return new Response("Not found", { status: 404 });
    });

    const user = userEvent.setup();
    render(<CandidateReviewPage />);

    await waitFor(() => {
      expect(screen.getByText(/Discovered TMDb Candidates \(3\)/i)).toBeInTheDocument();
    });

    const acceptButtons = screen.getAllByRole("button", { name: /Accept Candidate…/i });
    await user.click(acceptButtons[0]);

    const reasonTextarea = screen.getByPlaceholderText(/Explain why this candidate is the verified match/i);
    await user.type(reasonTextarea, "Verified director and Italian release year 2022");

    const submitConfirmButton = screen.getByRole("button", { name: /Confirm & Accept Candidate/i });
    await user.click(submitConfirmButton);

    await waitFor(() => {
      expect(acceptCalled).toBe(true);
      expect(acceptPayload).toEqual({
        reason: "Verified director and Italian release year 2022",
        expected_revision: null,
      });
    });

    // Check success notification
    await waitFor(() => {
      expect(screen.getByText(/Successfully accepted candidate TMDb 532639/i)).toBeInTheDocument();
    });
  });

  it("handles concurrent / stale conflict errors (409) cleanly", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/") || u.includes("/api/admin/provider-content/unresolved/")) {
        return new Response(JSON.stringify(mockCandidateQueue), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-101/candidates/532639/reject/")) {
        return new Response(
          JSON.stringify({
            error: "State conflict: candidate was already reviewed by another operator.",
          }),
          { status: 409 }
        );
      }
      if (u.includes("/api/admin/provider-content/pc-101/candidates/")) {
        return new Response(JSON.stringify(mockAmbiguousCandidatesResponse), { status: 200 });
      }
      return new Response("Not found", { status: 404 });
    });

    const user = userEvent.setup();
    render(<CandidateReviewPage />);

    await waitFor(() => {
      expect(screen.getByText(/Discovered TMDb Candidates \(3\)/i)).toBeInTheDocument();
    });

    const rejectButtons = screen.getAllByRole("button", { name: /Reject Candidate…/i });
    await user.click(rejectButtons[0]);

    const reasonTextarea = screen.getByPlaceholderText(/Explain why this candidate was rejected/i);
    await user.type(reasonTextarea, "Wrong candidate");

    const confirmRejectButton = screen.getByRole("button", { name: /Confirm & Reject Candidate/i });
    await user.click(confirmRejectButton);

    await waitFor(() => {
      expect(
        screen.getByText(/Concurrent or stale review conflict \(409\)/i)
      ).toBeInTheDocument();
    });
  });
});
