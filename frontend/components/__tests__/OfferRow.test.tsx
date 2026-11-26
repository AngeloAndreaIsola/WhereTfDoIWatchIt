import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import OfferRow, { formatPrice } from "../OfferRow";
import type { OfferEntitlement, PrimeOffer } from "@/lib/api";

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
const providerUrl = "https://www.primevideo.com/detail/example";
const included: OfferEntitlement = {
  verdict: "INCLUDED",
  reason: "PLAN_COVERS_TITLE",
  offer_country: "IT",
  offer_type: "subscription",
  provider: "prime",
  evidence_state: "verified",
  caveats: [],
};

describe("OfferRow", () => {
  it("keeps the Watch link for a verified offer", () => {
    render(<OfferRow offer={{ ...baseOffer, url: providerUrl, availability_evidence: "verified", is_accessible: true, entitlement: included }} />);
    expect(screen.queryByTestId("unverified-badge")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Watch" })).toHaveAttribute("href", providerUrl);
  });

  it.each(["stale", "unknown"] as const)(
    "labels a %s offer as unverified and keeps its provider link",
    (evidence) => {
      render(
        <OfferRow offer={{ ...baseOffer, url: providerUrl, availability_evidence: evidence }} />
      );
      const badge = screen.getByTestId("unverified-badge");
      expect(badge).toHaveTextContent("Unverified");
      expect(badge).toHaveAttribute("data-verification-state", evidence);
      expect(screen.getByRole("link", { name: "Check provider" })).toHaveAttribute("href", providerUrl);
      expect(screen.queryByRole("link", { name: /watch/i })).not.toBeInTheDocument();
    },
  );

  it("keeps the internal fallback link for an unverified offer without a provider URL", () => {
    render(<OfferRow offer={{ ...baseOffer, availability_evidence: "stale" }} tmdbId={238} />);
    expect(screen.getByRole("link", { name: "Verify availability" })).toHaveAttribute("href", "/watch/238");
  });

  it("shows Local badge for current country", () => {
    render(<OfferRow offer={baseOffer} currentCountry="IT" />);
    expect(screen.getByText("Local")).toBeInTheDocument();
  });

  it("shows VPN required badge for non-current country", () => {
    render(<OfferRow offer={baseOffer} currentCountry="IN" />);
    expect(screen.getByText("VPN required")).toBeInTheDocument();
  });

  it("labels an offer with unknown territory without claiming local access", () => {
    render(
      <OfferRow
        offer={{
          ...baseOffer,
          country: "",
          locality: "unknown",
          is_entitled: true,
          is_accessible: false,
          rejection_reasons: ["Territory information unknown"],
        }}
        currentCountry="NL"
      />,
    );
    expect(screen.getAllByText("Territory unknown").length).toBeGreaterThan(0);
    expect(screen.queryByText("Local")).not.toBeInTheDocument();
    expect(screen.queryByText("VPN required")).not.toBeInTheDocument();
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
      entitlement: { ...included, verdict: "BLOCKED", reason: "BASE_NOT_HELD" } as const,
      access_label: "Included with your subscription",
    };
    render(<OfferRow offer={unowned} currentCountry="NL" isBest={true} />);

    expect(screen.getByTestId("offer-not-subscribed"))
      .toHaveTextContent(/base subscription is marked as not held/i);
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
      entitlement: included,
      access_label: "Included with your subscription",
      rejection_reasons: ["Does not satisfy language rules"],
    };
    render(<OfferRow offer={languageBlocked} currentCountry="NL" />);

    expect(
      screen.getByText("Not available in your languages"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Not reachable from your settings")).not.toBeInTheDocument();
    // They do own the service, so the entitlement chip is accurate here.
    expect(screen.getByText("Included with your selected plan")).toBeInTheDocument();
  });

  it("still marks an accessible offer as Best Match", () => {
    const accessible = { ...baseOffer, locality: "local" as const, is_entitled: true, is_accessible: true, entitlement: included };
    render(<OfferRow offer={accessible} currentCountry="IT" isBest={true} />);
    expect(screen.getByText("Best Match")).toBeInTheDocument();
    expect(screen.queryByTestId("offer-not-subscribed")).not.toBeInTheDocument();
  });
});

const rentOffer = (overrides: Partial<PrimeOffer> = {}): PrimeOffer => ({
  ...baseOffer,
  offer_type: "rent",
  price: {
    amount: "2.99",
    currency: "EUR",
    source: "justwatch",
    is_current: true,
    is_from: false,
  },
  ...overrides,
});

describe("OfferRow price display", () => {
  it("formats the structured amount and currency for a rent offer", () => {
    render(<OfferRow offer={rentOffer()} currentCountry="NL" />);
    const price = screen.getByTestId("offer-price");
    expect(price).toHaveAttribute("data-amount", "2.99");
    expect(price).toHaveAttribute("data-currency", "EUR");
    expect(price.textContent).toContain("€");
    expect(price.textContent).toContain("2.99");
  });

  it("shows a distinct price for a buy offer", () => {
    render(
      <OfferRow
        offer={rentOffer({
          offer_type: "buy",
          price: { amount: "9.99", currency: "EUR", is_current: true },
        })}
        currentCountry="NL"
      />,
    );
    expect(screen.getByTestId("offer-price")).toHaveAttribute("data-amount", "9.99");
  });

  it("renders a different currency without converting it", () => {
    render(
      <OfferRow
        offer={rentOffer({
          price: { amount: "3.99", currency: "USD", is_current: true },
        })}
        currentCountry="US"
      />,
    );
    const price = screen.getByTestId("offer-price");
    expect(price).toHaveAttribute("data-currency", "USD");
    expect(price.textContent).toContain("3.99");
  });

  it("labels a multi-source disagreement as 'from' the cheapest price", () => {
    render(
      <OfferRow
        offer={rentOffer({
          price: { amount: "2.99", currency: "EUR", is_current: true, is_from: true },
        })}
        currentCountry="NL"
      />,
    );
    expect(screen.getByTestId("offer-price").textContent).toMatch(/^from /);
  });

  it("falls back to 'not confirmed' rather than implying a missing price is free", () => {
    render(
      <OfferRow offer={rentOffer({ price: null })} currentCountry="NL" />,
    );
    expect(screen.getByTestId("offer-price-missing")).toBeInTheDocument();
    expect(screen.queryByTestId("offer-price")).not.toBeInTheDocument();
    expect(screen.queryByText(/free/i)).not.toBeInTheDocument();
  });

  it("never presents a stale price as freshly confirmed", () => {
    render(
      <OfferRow
        offer={rentOffer({
          price: { amount: "2.99", currency: "EUR", is_current: false },
        })}
        currentCountry="NL"
      />,
    );
    expect(screen.queryByTestId("offer-price")).not.toBeInTheDocument();
    expect(screen.getByTestId("offer-price-missing")).toBeInTheDocument();
  });

  it("does not show a per-title price for subscription offers", () => {
    render(
      <OfferRow
        offer={{
          ...baseOffer,
          offer_type: "subscription",
          price: { amount: "9.99", currency: "EUR", is_current: true },
        }}
        currentCountry="IT"
      />,
    );
    expect(screen.queryByTestId("offer-price")).not.toBeInTheDocument();
    expect(screen.queryByTestId("offer-price-missing")).not.toBeInTheDocument();
  });

  it("shows a price on an unowned offer without implying entitlement", () => {
    render(
      <OfferRow
        offer={rentOffer({
          locality: "local" as const,
          is_entitled: false,
          is_accessible: false,
          access_label: "Purchase",
        })}
        currentCountry="NL"
        isBest={true}
      />,
    );
    // Price is visible...
    expect(screen.getByTestId("offer-price")).toBeInTheDocument();
    // ...but the offer is still clearly not playable, and never "Best Match".
    expect(screen.getByTestId("offer-not-subscribed")).toBeInTheDocument();
    expect(screen.queryByText("Best Match")).not.toBeInTheDocument();
  });

  it("keeps the add-on label when a price is present", () => {
    render(
      <OfferRow
        offer={rentOffer({
          offer_type: "addon",
          is_addon: true,
          required_base_display: "Prime Video",
          price: { amount: "4.99", currency: "EUR", is_current: true },
        })}
        currentCountry="NL"
      />,
    );
    expect(screen.getByText(/Add-on/)).toBeInTheDocument();
    expect(screen.getByTestId("offer-price")).toHaveAttribute("data-amount", "4.99");
  });
});

describe("formatPrice", () => {
  it("formats a decimal string with the requested currency", () => {
    expect(formatPrice("2.99", "EUR", "en-US")).toBe("€2.99");
  });

  it("preserves the currency's own precision rules", () => {
    // JPY has no minor unit; the formatter must not invent .00.
    expect(formatPrice("1200", "JPY", "en-US")).toBe("¥1,200");
  });

  it("falls back to a plain code label for a code Intl rejects", () => {
    expect(formatPrice("2.99", "EU", "en-US")).toBe("2.99 EU");
  });

  it("returns empty for a non-numeric amount", () => {
    expect(formatPrice("not-a-number", "EUR", "en-US")).toBe("");
  });
});

