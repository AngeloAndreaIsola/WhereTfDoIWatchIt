import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import LastKnownOffers from "../LastKnownOffers";
import { formatLastSeen } from "@/lib/time";
import type { PrimeOffer } from "@/lib/api";

const offer = (overrides: Partial<PrimeOffer> = {}): PrimeOffer => ({
  country: "US",
  service: "Netflix",
  offer_type: "subscription",
  quality: "hd",
  audio_languages: ["EN"],
  subtitle_languages: ["EN"],
  ranking: { audio_rank: 0, subtitle_rank: 0, quality_rank: 0 },
  is_accessible: true,
  is_entitled: true,
  ...overrides,
});

describe("LastKnownOffers", () => {
  it("renders nothing without last-known offers", () => {
    const { container } = render(<LastKnownOffers offers={[]} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId("last-known-offers")).not.toBeInTheDocument();
  });

  it("labels stale offers as needing a recheck with their last-seen date", () => {
    const seen = "2026-10-01T12:00:00+00:00";
    render(
      <LastKnownOffers
        offers={[offer({ availability_evidence: "stale", last_seen_at: seen })]}
      />
    );
    expect(screen.getByText(/Last known availability — needs recheck/)).toBeInTheDocument();
    expect(screen.getByTestId("last-known-label")).toHaveTextContent(
      `Unverified · last seen — ${formatLastSeen(seen)}`
    );
    expect(screen.getByTestId("unverified-badge")).toHaveTextContent("Unverified");
    expect(screen.getByText(/not counted as watchable now/)).toBeInTheDocument();
  });

  it("labels uncorroborated offers as unverified", () => {
    render(
      <LastKnownOffers offers={[offer({ availability_evidence: "unknown", last_seen_at: null })]} />
    );
    expect(screen.getByTestId("last-known-label")).toHaveTextContent(
      "Unverified · Last seen: date unknown"
    );
    expect(screen.getByTestId("unverified-badge")).toHaveAttribute(
      "data-verification-state", "unknown"
    );
  });

  it("keeps a stale legacy offer muted without interpreting its subscription flags", () => {
    render(
      <LastKnownOffers
        offers={[
          offer({
            availability_evidence: "stale",
            last_seen_at: "2026-10-01T12:00:00+00:00",
            is_accessible: false,
            is_entitled: false,
          }),
        ]}
      />
    );
    expect(screen.getByTestId("offer-not-subscribed")).toHaveTextContent(
      "Availability needs a recheck"
    );
    expect(screen.getByTestId("offer-row")).toHaveAttribute("data-entitled", "unknown");
  });
});

describe("formatLastSeen", () => {
  it("never renders a missing timestamp as a date", () => {
    expect(formatLastSeen(undefined)).toBe("Last seen: date unknown");
    expect(formatLastSeen("not-a-date")).toBe("Last seen: date unknown");
    expect(formatLastSeen("2026-10-01T12:00:00Z")).toMatch(/^Last seen /);
  });
});
