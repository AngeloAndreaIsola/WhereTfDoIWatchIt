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

  it("labels an unowned-service offer instead of hiding it", () => {
    // Movie 194: the offer is real availability the user cannot play. It must
    // stay visible, muted, and clearly labelled — never silently dropped.
    const unowned = {
      ...baseOffer,
      locality: "vpn" as const,
      is_entitled: false,
      is_accessible: false,
      access_label: "Included with your subscription",
    };
    render(<OfferRow offer={unowned} currentCountry="NL" isBest={true} />);

    expect(screen.getByTestId("offer-not-subscribed")).toBeInTheDocument();
    expect(
      screen.getByText(/not in your subscriptions/i),
    ).toBeInTheDocument();
    // An unplayable offer must never be crowned "Best Match".
    expect(screen.queryByText("Best Match")).not.toBeInTheDocument();
    // The entitlement chip must not contradict the muted message: this offer
    // is NOT included in the user's subscriptions.
    expect(
      screen.queryByText("Included with your subscription"),
    ).not.toBeInTheDocument();
    // Locality is shown from the verdict, not guessed from the country.
    expect(screen.getByText("VPN required")).toBeInTheDocument();
  });

  it("names the real blocker when an owned offer is blocked by language", () => {
    // Both offers are the user's service; this one is blocked by the language
    // rule, not by reach. Saying "not reachable" would mislabel it.
    const languageBlocked = {
      ...baseOffer,
      country: "DE",
      locality: "vpn" as const,
      is_entitled: true,
      is_accessible: false,
      access_label: "Included with your subscription",
      rejection_reasons: ["Does not satisfy language rules"],
    };
    render(<OfferRow offer={languageBlocked} currentCountry="NL" />);

    expect(
      screen.getByText("Not available in your languages"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Not reachable from your settings")).not.toBeInTheDocument();
    // They do own the service, so the entitlement chip is accurate here.
    expect(screen.getByText("Included with your subscription")).toBeInTheDocument();
  });

  it("still marks an accessible offer as Best Match", () => {
    const accessible = { ...baseOffer, locality: "local" as const, is_entitled: true, is_accessible: true };
    render(<OfferRow offer={accessible} currentCountry="IT" isBest={true} />);
    expect(screen.getByText("Best Match")).toBeInTheDocument();
    expect(screen.queryByTestId("offer-not-subscribed")).not.toBeInTheDocument();
  });
});
