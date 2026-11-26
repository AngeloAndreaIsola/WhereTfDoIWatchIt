import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import OfferRow from "@/components/OfferRow";
import type { OfferEntitlement, PrimeOffer } from "@/lib/api";

const baseOffer: Omit<PrimeOffer, "ranking"> & Partial<Pick<PrimeOffer, "ranking">> = {
  country: "NL",
  service: "Aster",
  offer_type: "subscription",
  quality: "hd",
  audio_languages: ["EN"],
  subtitle_languages: ["EN"],
  is_accessible: false,
  is_entitled: false,
  availability_evidence: "verified",
};

function entitlement(overrides: Partial<OfferEntitlement>): OfferEntitlement {
  return {
    verdict: "UNKNOWN",
    reason: "NO_APPLICABLE_RULE",
    provider: "aster",
    offer_type: "subscription",
    offer_country: "NL",
    evidence_state: "verified",
    caveats: [],
    ...overrides,
  };
}

function verdictRow() {
  return screen.getByTestId("entitlement-verdict");
}

describe("OfferRow entitlement verdicts (#103)", () => {
  it.each([
    ["INCLUDED", "PLAN_COVERS_TITLE", "Included with your selected plan"],
    ["REQUIRES_ADDON", "CHANNEL_NOT_HELD", "Requires channel add-on"],
    ["RENT_OR_BUY", "PURCHASE", "Buy"],
    ["FREE_WITH_ADS", "PUBLIC_AD_FUNDED", "Free with ads"],
    ["BLOCKED", "PLAN_EXCLUDED", "Blocked for your selection"],
    ["UNKNOWN", "PLAN_UNSPECIFIED", "Plan access unconfirmed"],
  ] as const)("renders %s / %s as %s", (verdict, reason, label) => {
    render(<OfferRow offer={{ ...baseOffer, entitlement: entitlement({ verdict, reason }) }} />);
    expect(verdictRow()).toHaveAttribute("data-verdict", verdict);
    expect(verdictRow()).toHaveAttribute("data-reason", reason);
    expect(verdictRow()).toHaveTextContent(label);
  });

  it("renders public free separately from a paid ads tier", () => {
    const { unmount } = render(
      <OfferRow
        offer={{
          ...baseOffer,
          offer_type: "free",
          entitlement: entitlement({ verdict: "INCLUDED", reason: "PUBLIC_FREE" }),
        }}
      />
    );
    expect(verdictRow()).toHaveTextContent("Free");
    expect(verdictRow()).not.toHaveTextContent("with ads");
    unmount();

    render(
      <OfferRow
        offer={{
          ...baseOffer,
          entitlement: entitlement({
            verdict: "INCLUDED",
            reason: "PLAN_COVERS_TITLE",
            plan_id: "ads",
          }),
        }}
      />
    );
    expect(verdictRow()).toHaveTextContent("Included with your selected plan");
    expect(verdictRow()).not.toHaveTextContent("Free with ads");
    expect(screen.getByText(/selected plan: ads/i)).toBeInTheDocument();
  });

  it("explains a missing channel with its host and names the channel", () => {
    render(
      <OfferRow
        offer={{
          ...baseOffer,
          offer_type: "addon",
          is_addon: true,
          is_accessible: false,
          entitlement: entitlement({
            verdict: "REQUIRES_ADDON",
            reason: "CHANNEL_NOT_HELD",
            required_channel: "orbit",
            required_base: "aster",
          }),
        }}
      />
    );
    expect(screen.getByTestId("entitlement-reason")).toHaveTextContent(/channel is not marked as held/i);
    expect(screen.getByTestId("offer-not-subscribed")).toHaveTextContent(/orbit.*aster/i);
  });

  it("keeps a separate paid action visible without implying entitlement", () => {
    render(
      <OfferRow
        offer={{
          ...baseOffer,
          offer_type: "buy",
          requires_payment: true,
          price: { amount: "9.99", currency: "EUR", is_current: true },
          entitlement: entitlement({
            verdict: "RENT_OR_BUY",
            reason: "PURCHASE",
            offer_type: "buy",
          }),
        }}
      />
    );
    expect(verdictRow()).toHaveTextContent("Buy");
    expect(screen.getByTestId("offer-price")).toHaveAttribute("data-amount", "9.99");
    expect(screen.getByTestId("entitlement-reason")).toHaveTextContent(/separate purchase action/i);
    expect(screen.getByTestId("offer-row")).toHaveAttribute("data-entitled", "false");
  });

  it("never marks an unconfirmed offer as confirmed watchability", () => {
    render(
      <OfferRow
        offer={{
          ...baseOffer,
          is_entitled: false,
          is_accessible: false,
          entitlement: entitlement({ reason: "PLAN_UNSPECIFIED" }),
        }}
        isBest={true}
        tmdbId={238}
      />
    );
    expect(verdictRow()).toHaveTextContent("Plan access unconfirmed");
    expect(screen.getByTestId("entitlement-reason")).toHaveTextContent(/confirm your exact plan/i);
    expect(screen.getByTestId("offer-row")).toHaveAttribute("data-entitled", "unknown");
    expect(screen.queryByText("Best Match")).toBeNull();
    expect(screen.queryByText("Included with your selected plan")).toBeNull();
    expect(screen.getByRole("link")).toHaveTextContent("Why can't I watch?");
  });

  it("treats an older payload without an entitlement object as unconfirmed", () => {
    render(<OfferRow offer={{
      ...baseOffer,
      offer_type: "subscription",
      is_entitled: true,
      is_accessible: true,
      access_label: "Included with your subscription",
    }} isBest tmdbId={238} />);
    expect(verdictRow()).toHaveTextContent("Plan access unconfirmed");
    expect(screen.queryByText("Included with your subscription")).toBeNull();
    expect(screen.queryByText("Best Match")).toBeNull();
    expect(screen.getByTestId("offer-row")).toHaveAttribute("data-entitlement-verdict", "missing");
    expect(screen.getByTestId("offer-row")).toHaveAttribute("data-entitled", "unknown");
  });

  it("shows the declared-selection caveat on a positive verdict", () => {
    render(
      <OfferRow
        offer={{
          ...baseOffer,
          is_entitled: true,
          is_accessible: true,
          locality: "local",
          entitlement: entitlement({
            verdict: "INCLUDED",
            reason: "PLAN_COVERS_TITLE",
            caveats: ["Coverage applies to your declared selection. Account status and playback are unverified."],
          }),
        }}
        isBest={true}
      />
    );
    expect(screen.getByTestId("entitlement-caveat")).toHaveTextContent(/account status and playback are unverified/i);
    expect(screen.getByText("Best Match")).toBeInTheDocument();
  });

  it("explains a billing/offer market mismatch and a region exclusion", () => {
    const { unmount } = render(
      <OfferRow
        offer={{
          ...baseOffer,
          entitlement: entitlement({
            reason: "PORTABILITY_UNVERIFIED",
            billing_country: "NL",
            offer_country: "DE",
          }),
        }}
      />
    );
    expect(screen.getAllByTestId("region-caveat").length).toBeGreaterThan(0);
    expect(screen.getByTestId("entitlement-reason")).toHaveTextContent(/portability/i);
    unmount();

    render(
      <OfferRow
        offer={{
          ...baseOffer,
          entitlement: entitlement({ verdict: "BLOCKED", reason: "REGION_EXCLUDED" }),
        }}
      />
    );
    expect(screen.getByTestId("region-caveat")).toHaveTextContent(/not confirmed by your selected plan/i);
  });

  it("preserves the source-reported evidence label and never upgrades it", () => {
    render(
      <OfferRow
        offer={{
          ...baseOffer,
          availability_evidence: "source_reported",
          reporting_source: "justwatch",
          entitlement: entitlement({ reason: "SOURCE_REPORTED", evidence_state: "source_reported" }),
        }}
        tmdbId={238}
      />
    );
    expect(screen.getByTestId("unverified-badge")).toHaveTextContent("Source-reported");
    expect(verdictRow()).toHaveTextContent("Plan access unconfirmed");
    expect(screen.getByText(/a source reported this offer/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Watch" })).toBeNull();
  });

  it("preserves the last-known evidence label with a stale entitlement", () => {
    render(
      <OfferRow
        offer={{
          ...baseOffer,
          availability_evidence: "stale",
          is_accessible: false,
          entitlement: entitlement({ reason: "STALE_EVIDENCE", evidence_state: "stale" }),
        }}
        tmdbId={238}
      />
    );
    expect(screen.getByTestId("unverified-badge")).toHaveAttribute("data-verification-state", "stale");
    expect(screen.getByRole("link", { name: "Verify availability" })).toBeInTheDocument();
    expect(screen.getByTestId("entitlement-reason")).toHaveTextContent(/needs a recheck/i);
  });
});
