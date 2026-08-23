import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import WatchPage from "@/app/watch/[tmdb_id]/page";
import LanguageBadge from "@/components/LanguageBadge";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode; [key: string]: unknown }) =>
    React.createElement("a", { href, ...props }, children),
}));

function mockFetch(response: { ok?: boolean; status?: number; body: unknown }) {
  const isOk = response.ok ?? (response.status ? response.status >= 200 && response.status < 300 : true);
  const fetchMock = vi.fn().mockResolvedValue({
    ok: isOk,
    status: response.status ?? 200,
    json: async () => response.body,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Watch Flow and Language Badges", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  describe("Watch Page /watch/[tmdb_id]", () => {
    it("renders fallback UI when no watch destination is found", async () => {
      mockFetch({
        ok: true,
        status: 200,
        body: {
          found: false,
          movie: { tmdb_id: 1001, title: "Unavailable Movie", year: 2021 },
          message: "Not currently available to watch on your subscribed services.",
        },
      });

      render(React.createElement(WatchPage, { params: { tmdb_id: "1001" } }));

      expect(await screen.findByText("Not currently available to watch")).toBeInTheDocument();
      expect(screen.getByText("Not currently available to watch on your subscribed services.")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "← Return to Movie Details" })).toHaveAttribute("href", "/movie/1001");
    });

    it("renders redirect UI when valid watch URL is found", async () => {
      mockFetch({
        ok: true,
        status: 200,
        body: {
          found: true,
          watch_url: "https://www.netflix.com/title/123",
          movie: { tmdb_id: 1001, title: "Inception", year: 2010 },
          offer: { service: "Netflix", country: "US" },
        },
      });

      render(React.createElement(WatchPage, { params: { tmdb_id: "1001" } }));

      expect(await screen.findByText(/Redirecting to watch Inception/)).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /Click here if you are not redirected automatically/ })).toHaveAttribute("href", "https://www.netflix.com/title/123");
    });
  });

  describe("LanguageBadge Component", () => {
    it("renders preferred audio badge in emerald style with checkmark", () => {
      render(React.createElement(LanguageBadge, { code: "JA", type: "audio", isPreferred: true }));
      expect(screen.getByText("日本語")).toBeInTheDocument();
      expect(screen.getByText("✓")).toBeInTheDocument();
    });

    it("renders preferred subtitle badge in blue style with checkmark", () => {
      render(React.createElement(LanguageBadge, { code: "EN", type: "subtitle", isPreferred: true }));
      expect(screen.getByText("English")).toBeInTheDocument();
      expect(screen.getByText("✓")).toBeInTheDocument();
    });

    it("renders non-preferred language badge without checkmark", () => {
      render(React.createElement(LanguageBadge, { code: "DE", type: "subtitle", isPreferred: false }));
      expect(screen.getByText("Deutsch")).toBeInTheDocument();
      expect(screen.queryByText("✓")).toBeNull();
    });
  });
});
