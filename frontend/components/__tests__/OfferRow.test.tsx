import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import OfferRow from "../OfferRow";

const baseOffer = {
  country: "IT",
  service: "Prime Video",
  offer_type: "subscription" as const,
  quality: "4k",
  audio_languages: ["EN"],
  subtitle_languages: ["EN"],
  ranking: {
    audio_rank: 0,
    subtitle_rank: 0,
    quality_rank: 0,
    is_language_match: true,
  },
};

describe("OfferRow", () => {
  it("shows Local badge for current country", () => {
    render(<OfferRow offer={baseOffer} currentCountry="IT" />);
    expect(screen.getByText("Local")).toBeInTheDocument();
  });

  it("shows VPN required badge for non-current country", () => {
    render(<OfferRow offer={baseOffer} currentCountry="IN" />);
    expect(screen.getByText("VPN required")).toBeInTheDocument();
  });

  it("does not render locality badge when currentCountry is not provided", () => {
    render(<OfferRow offer={baseOffer} />);
    expect(screen.queryByText("Local")).not.toBeInTheDocument();
    expect(screen.queryByText("VPN required")).not.toBeInTheDocument();
  });
});
