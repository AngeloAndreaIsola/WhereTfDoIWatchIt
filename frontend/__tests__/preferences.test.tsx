import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import PreferencesPage from "../app/preferences/page";

describe("PreferencesPage - Ranking Preferences", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders source ranking priority and allows moving priority up/down", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        candidate_countries: ["US", "IT"],
        ranking_preferences: {
          version: 1,
          source_priority: ["official", "local", "unofficial"],
          prefer_official: true,
          prefer_local: true,
          prefer_entitled: true,
          provider_priority: [],
        },
      }),
    } as Response);

    render(<PreferencesPage />);

    expect(await screen.findByText("Source Ranking Priority")).toBeInTheDocument();
    expect(screen.getByText("Official / Major Providers")).toBeInTheDocument();
    expect(screen.getByText("Local Region")).toBeInTheDocument();
    expect(screen.getByText("Unofficial / Scraped Sources")).toBeInTheDocument();

    const downBtn = screen.getByLabelText("Move Official / Major Providers down");
    fireEvent.click(downBtn);

    // Save
    const saveMock = vi.spyOn(global, "fetch").mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ranking_preferences: {
          version: 1,
          source_priority: ["local", "official", "unofficial"],
          prefer_official: true,
          prefer_local: true,
          prefer_entitled: true,
          provider_priority: [],
        },
      }),
    } as Response);

    const saveBtn = screen.getByText("Save Ranking Preferences");
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(saveMock).toHaveBeenCalledWith(
        "/api/preferences/",
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"source_priority":["local","official","unofficial"]'),
        })
      );
    });

    expect(await screen.findByText("Ranking preferences saved successfully.")).toBeInTheDocument();
  });

  it("handles reset to defaults action", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        candidate_countries: ["US"],
        ranking_preferences: {
          version: 1,
          source_priority: ["unofficial", "local", "official"],
          prefer_official: false,
          prefer_local: false,
          prefer_entitled: false,
          provider_priority: [],
        },
      }),
    } as Response);

    render(<PreferencesPage />);
    expect(await screen.findByText("Source Ranking Priority")).toBeInTheDocument();

    const resetMock = vi.spyOn(global, "fetch").mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ranking_preferences: {
          version: 1,
          source_priority: ["official", "local", "unofficial"],
          prefer_official: true,
          prefer_local: true,
          prefer_entitled: true,
          provider_priority: [],
        },
      }),
    } as Response);

    const resetBtn = screen.getByText("Reset to defaults");
    fireEvent.click(resetBtn);

    await waitFor(() => {
      expect(resetMock).toHaveBeenCalledWith(
        "/api/preferences/",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ ranking_preferences: { reset: true } }),
        })
      );
    });

    expect(await screen.findByText("Reset to default ranking preferences.")).toBeInTheDocument();
  });
});
