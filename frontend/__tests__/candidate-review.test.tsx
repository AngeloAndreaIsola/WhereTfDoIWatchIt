import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CandidateReviewPage from "@/app/candidate-review/page";
import {
  mockBackendQueueResponse,
  mockBackendQueueEmpty,
  mockBackendDetailAmbiguous,
  mockBackendDetailConflict,
  mockBackendDetailManualConflict,
  mockBackendDetailMappedNoOffer,
} from "@/lib/candidateReviewFixtures";

describe("Candidate Review Interface (Wave 2A Backend Compatibility)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders loading and empty queue states from Wave 2A {items, total_items} format", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/")) {
        return new Response(JSON.stringify(mockBackendQueueEmpty), { status: 200 });
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

  it("handles 404 from documented queue endpoint by showing backend unavailable (no fallback)", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/")) {
        return new Response(JSON.stringify({ detail: "Not found" }), { status: 404 });
      }
      return new Response("Not found", { status: 404 });
    });

    render(<CandidateReviewPage />);

    await waitFor(() => {
      expect(
        screen.getByText(/Candidate review backend endpoint is currently unavailable/i)
      ).toBeInTheDocument();
    });
  });

  it("renders unresolved queue items, pagination controls, and selects the first item", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/")) {
        return new Response(
          JSON.stringify({
            ...mockBackendQueueResponse,
            page: 1,
            pages: 3,
            total_items: 65,
          }),
          { status: 200 }
        );
      }
      if (u.includes("/api/admin/provider-content/pc-101/candidates/")) {
        return new Response(JSON.stringify(mockBackendDetailAmbiguous), { status: 200 });
      }
      return new Response("Not found", { status: 404 });
    });

    render(<CandidateReviewPage />);

    await waitFor(() => {
      expect(screen.getByText(/Unresolved Queue \(65\)/i)).toBeInTheDocument();
    });

    // Check pagination controls
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).not.toBeDisabled();

    // Verify first item is rendered and selected
    expect(screen.getByRole("heading", { name: "Pinocchio" })).toBeInTheDocument();
    expect(screen.getAllByText(/Tubi/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/pinocchio-2022-stream/i).length).toBeGreaterThan(0);
  });

  it("displays multiple ambiguous candidates and evidence comparison from flat backend response", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/")) {
        return new Response(JSON.stringify(mockBackendQueueResponse), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-101/candidates/")) {
        return new Response(JSON.stringify(mockBackendDetailAmbiguous), { status: 200 });
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

  it("renders movie/year conflicts, legacy unverified status, and existing mapping details", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/")) {
        return new Response(JSON.stringify(mockBackendQueueResponse), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-102/candidates/")) {
        return new Response(JSON.stringify(mockBackendDetailConflict), { status: 200 });
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

    // Verify legacy_unverified is displayed as Unverified Machine Match
    expect(screen.getByText(/Unverified Machine Match/i)).toBeInTheDocument();
  });

  it("displays manual mapping conflict warnings clearly and disables acceptance", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/")) {
        return new Response(JSON.stringify(mockBackendQueueResponse), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-103/candidates/")) {
        return new Response(JSON.stringify(mockBackendDetailManualConflict), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-101/candidates/")) {
        return new Response(JSON.stringify(mockBackendDetailAmbiguous), { status: 200 });
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

  it("derives manual mapping from match_method and disables unsafe candidate acceptance", async () => {
    const itemWithManualMapping = {
      ...mockBackendDetailMappedNoOffer,
      existing_mapping: {
        id: 99,
        tmdb_id: 11111,
        title: "Original Manually Mapped Movie",
        match_method: "manual",
        revision: 2,
      },
      candidates: [
        {
          id: 50,
          tmdb_id: 22222, // Diverging target TMDb ID
          title: "Conflicting Candidate Movie",
          content_type: "movie",
          year: 2020,
          confidence_score: 0.9,
          scoring_evidence: {
            title_relation: "exact",
          },
          status: "pending",
        },
      ],
    };

    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/")) {
        return new Response(
          JSON.stringify({
            items: [itemWithManualMapping],
            page: 1,
            pages: 1,
            total_items: 1,
          }),
          { status: 200 }
        );
      }
      if (u.includes("/api/admin/provider-content/pc-104/candidates/")) {
        return new Response(JSON.stringify(itemWithManualMapping), { status: 200 });
      }
      return new Response("Not found", { status: 404 });
    });

    render(<CandidateReviewPage />);

    await waitFor(() => {
      expect(screen.getByText(/MANUAL MAPPING OVERRIDE CONFLICT/i)).toBeInTheDocument();
    });

    // Acceptance should be disabled before reaching the server
    const acceptButton = screen.getByRole("button", { name: /Accept Candidate…/i });
    expect(acceptButton).toBeDisabled();
  });

  it("distinguishes mapping versus offer availability and enforces 'Mapped ≠ Watchable'", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/")) {
        return new Response(JSON.stringify(mockBackendQueueResponse), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-104/candidates/")) {
        return new Response(JSON.stringify(mockBackendDetailMappedNoOffer), { status: 200 });
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

  it("sends operator-guard headers (anti-CSRF) and handles 403 operator guard failure", async () => {
    let capturedHeaders: HeadersInit | undefined;

    vi.spyOn(global, "fetch").mockImplementation(async (url, init) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/")) {
        return new Response(JSON.stringify(mockBackendQueueResponse), { status: 200 });
      }
      if (u.includes("/api/admin/provider-content/pc-101/candidates/532639/accept/")) {
        capturedHeaders = init?.headers;
        // Verify mock operator guard: fail with 403 if operator guard header is missing
        const headersObj = init?.headers as Record<string, string>;
        if (headersObj?.["X-Operator-Context"] !== "local-operator") {
          return new Response(JSON.stringify({ detail: "Forbidden: operator guard check failed" }), { status: 403 });
        }
        return new Response(
          JSON.stringify({
            status: "accepted",
            mapping: { id: 1, tmdb_id: 532639 },
            candidate: { id: 1, tmdb_id: 532639, title: "Pinocchio" },
          }),
          { status: 200 }
        );
      }
      if (u.includes("/api/admin/provider-content/pc-101/candidates/")) {
        return new Response(JSON.stringify(mockBackendDetailAmbiguous), { status: 200 });
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
    await user.type(reasonTextarea, "Verified via director and Italian release year 2022");

    const submitConfirmButton = screen.getByRole("button", { name: /Confirm & Accept Candidate/i });
    await user.click(submitConfirmButton);

    await waitFor(() => {
      expect(screen.getByText(/Successfully accepted candidate\. TMDb ID: 532639/i)).toBeInTheDocument();
    });

    // Assert that operator mutation guard headers were present on the request
    expect(capturedHeaders).toBeDefined();
    const headersObj = capturedHeaders as Record<string, string>;
    expect(headersObj["X-Operator-Context"]).toBe("local-operator");
    expect(headersObj["X-Requested-With"]).toBe("XMLHttpRequest");
  });

  it("handles PR #50 reject mutation response and concurrent / stale conflict errors (409) cleanly", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      const u = String(url);
      if (u.includes("/api/admin/provider-content/candidates/")) {
        return new Response(JSON.stringify(mockBackendQueueResponse), { status: 200 });
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
        return new Response(JSON.stringify(mockBackendDetailAmbiguous), { status: 200 });
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
