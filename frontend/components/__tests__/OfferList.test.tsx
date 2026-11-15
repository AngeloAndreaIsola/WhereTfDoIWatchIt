import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import OfferList, { Offer } from "../OfferList";

describe("OfferList Component with LanguagePicker UX", () => {
  const mockOffer: Offer = {
    country: "US",
    service: "Netflix",
    offer_type: "flatrate",
    quality: "hd",
    audio_languages: ["EN"],
    subtitle_languages: ["ES"],
    ranking: {
      audio_rank: 0,
      subtitle_rank: 1,
      quality_rank: 0,
    },
  };

  const mockOnRefresh = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders offer details and opens language edit modal prefilled with languages", async () => {
    render(
      <OfferList
        tmdbId={100}
        offers={[mockOffer]}
        onRefreshAvailability={mockOnRefresh}
      />
    );

    expect(screen.getByText("Netflix")).toBeInTheDocument();
    expect(screen.getByText("US")).toBeInTheDocument();

    const editBtn = screen.getByRole("button", { name: /Edit Language Info/i });
    fireEvent.click(editBtn);

    expect(screen.getByText("Edit Language Info")).toBeInTheDocument();
    expect(screen.getByText("English (EN)")).toBeInTheDocument();
    expect(screen.getByText("Español (ES)")).toBeInTheDocument();
  });

  it("allows quick-add language selection and tag removal", async () => {
    render(
      <OfferList
        tmdbId={100}
        offers={[mockOffer]}
        onRefreshAvailability={mockOnRefresh}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /Edit Language Info/i }));

    // Find quick add button for Japanese (JA) in Audio Language Codes section
    const jaButtons = screen.getAllByRole("button", { name: /\+ JA \(日本語\)/i });
    fireEvent.click(jaButtons[0]);

    // Check that Japanese tag badge appeared under Audio
    expect(screen.getByText("日本語 (JA)")).toBeInTheDocument();

    // Remove Spanish tag badge from Subtitles
    const removeEsButton = screen.getByRole("button", {
      name: /Remove Español \(ES\)/i,
    });
    fireEvent.click(removeEsButton);

    expect(screen.queryByText("Español (ES)")).not.toBeInTheDocument();
  });

  it("submits selected languages via PUT fetch request", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: "ok" }),
    } as Response);

    render(
      <OfferList
        tmdbId={12345}
        offers={[mockOffer]}
        onRefreshAvailability={mockOnRefresh}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /Edit Language Info/i }));

    // Add French (FR) to Subtitles
    const frButtons = screen.getAllByRole("button", { name: /\+ FR \(Français\)/i });
    fireEvent.click(frButtons[1]); // second picker is Subtitles

    const saveButton = screen.getByRole("button", { name: /Save Language Info/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/movie/12345/language-info/",
        expect.objectContaining({
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider: "Netflix",
            country: "US",
            audio_languages: ["EN"],
            subtitle_languages: ["ES", "FR"],
          }),
        })
      );
    });
  });

  it("handles fetch errors gracefully", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({ error: "Invalid language payload" }),
    } as Response);

    render(
      <OfferList
        tmdbId={100}
        offers={[mockOffer]}
        onRefreshAvailability={mockOnRefresh}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /Edit Language Info/i }));
    fireEvent.click(screen.getByRole("button", { name: /Save Language Info/i }));

    await waitFor(() => {
      expect(screen.getByText("Invalid language payload")).toBeInTheDocument();
    });
  });
});
