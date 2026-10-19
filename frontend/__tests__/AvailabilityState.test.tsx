/**
 * P9 — the detail pages must not claim "not available" for a title they never
 * checked.
 *
 * The defect this pins: whenever `offers` was empty the page rendered one fixed
 * sentence —
 *
 *     Not currently available on your subscribed streaming services.
 *
 * — which is a claim about the world. Measured over 400 tracked titles with no
 * offers, every one was `unknown` (375 `not_checked`, 21 `not_applicable`,
 * 4 `title_not_found`) and none were `unavailable`.
 *
 * The component's contract, in one line: an empty offer list is not evidence of
 * absence, and a missing verdict is not evidence of anything.
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import AvailabilityState, {
  AvailabilityStateMessage,
} from "@/components/AvailabilityState";
import type {
  AvailabilityResolutionReason,
  TitleAvailabilitySource,
  TitleAvailabilityVerdict,
} from "@/lib/api";

/** A title-level source row, exactly as the endpoint emits it. */
function sourceRow(
  source: string,
  overrides: Partial<TitleAvailabilitySource> = {},
): TitleAvailabilitySource {
  return {
    source,
    label: source,
    status: "not_run",
    reason: "not_checked",
    available: null,
    checked_at: null,
    ...overrides,
  };
}

const FALSE_UNAVAILABLE = /not currently available on your subscribed/i;

function verdict(
  state: TitleAvailabilityVerdict["state"],
  reason: AvailabilityResolutionReason | null = null,
  overrides: Partial<TitleAvailabilityVerdict> = {},
): TitleAvailabilityVerdict {
  return {
    state,
    reason,
    resolved_sources: state === "available" ? ["justwatch"] : [],
    unresolved_sources: state === "unknown" ? ["watchmode"] : [],
    sources: [],
    ...overrides,
  };
}

describe("AvailabilityState", () => {
  describe("unavailable", () => {
    it("says so plainly when the backend says unavailable", () => {
      render(<AvailabilityState availability={verdict("unavailable")} />);

      expect(screen.getByText(FALSE_UNAVAILABLE)).toBeInTheDocument();
    });

    it("is the only state that may render that sentence", () => {
      render(<AvailabilityState availability={verdict("unknown", "not_checked")} />);

      expect(screen.queryByText(FALSE_UNAVAILABLE)).not.toBeInTheDocument();
    });
  });

  describe("unknown — the regression", () => {
    it("must not claim the title is unavailable", () => {
      render(<AvailabilityState availability={verdict("unknown", "not_checked")} />);

      expect(screen.queryByText(FALSE_UNAVAILABLE)).not.toBeInTheDocument();
    });

    it("exposes the unknown state for styling and tests", () => {
      render(<AvailabilityState availability={verdict("unknown", "not_checked")} />);

      expect(screen.getByTestId("availability-state")).toHaveAttribute(
        "data-state",
        "unknown",
      );
    });

    it("explains the unresolved title in human terms", () => {
      render(<AvailabilityState availability={verdict("unknown", "not_checked")} />);

      expect(
        screen.getByText(/not known yet|not been able to confirm/i),
      ).toBeInTheDocument();
    });

    const REASON_CASES: [AvailabilityResolutionReason, RegExp][] = [
      ["not_checked", /have not checked/i],
      ["not_applicable", /do not cover this title/i],
      ["title_not_found", /do not recognise this title/i],
      ["quota_exhausted", /out of requests/i],
      ["source_unavailable", /could not be reached/i],
      ["not_supported", /do not support this title/i],
      ["collector_not_run", /have not run for this title/i],
    ];

    it.each(REASON_CASES)(
      "explains the %s reason without leaking enum names",
      (reason, pattern) => {
        render(<AvailabilityState availability={verdict("unknown", reason)} />);

        expect(screen.getByText(pattern)).toBeInTheDocument();
        expect(screen.queryByText(new RegExp(reason))).not.toBeInTheDocument();
      },
    );

    it("degrades gracefully for an unregistered reason", () => {
      // A reason the backend adds after this build must still render rather
      // than crash, and must still not read as "unavailable".
      render(
        <AvailabilityState
          availability={
            verdict("unknown", "brand_new" as AvailabilityResolutionReason)
          }
        />,
      );

      expect(screen.getByText(/brand_new/)).toBeInTheDocument();
      expect(screen.queryByText(FALSE_UNAVAILABLE)).not.toBeInTheDocument();
    });
  });

  describe("backward compatibility", () => {
    it("treats a missing verdict as unknown, never unavailable", () => {
      render(<AvailabilityState />);

      expect(screen.queryByText(FALSE_UNAVAILABLE)).not.toBeInTheDocument();
      expect(screen.getByTestId("availability-state")).toHaveAttribute(
        "data-state",
        "unknown",
      );
    });

    it("still renders something useful when the field is absent", () => {
      render(<AvailabilityState />);

      expect(
        screen.getByText(/not known yet|not been able to confirm/i),
      ).toBeInTheDocument();
    });
  });

  describe("available", () => {
    it("does not show the unavailable sentence", () => {
      render(<AvailabilityState availability={verdict("available", null)} />);

      expect(screen.queryByText(FALSE_UNAVAILABLE)).not.toBeInTheDocument();
    });

    it("does not turn an available-with-no-offers into unavailable", () => {
      // A backend/UI discrepancy, not a frontend judgement: the classifier says
      // available but no displayable offer exists. The conservative reading is
      // "available, but not on a service we can show", never "not available".
      render(
        <AvailabilityState
          availability={verdict("available", null, { available_on: ["max", "netflix"] })}
        />,
      );

      expect(screen.queryByText(FALSE_UNAVAILABLE)).not.toBeInTheDocument();
      expect(screen.getByText(/not on a service we can show/i)).toBeInTheDocument();
      // Names where the source says it is, so the line is actionable.
      expect(screen.getByText(/max, netflix/i)).toBeInTheDocument();
    });
  });

  describe("refreshing", () => {
    it("does not contradict an active refresh by saying unavailable", () => {
      render(
        <AvailabilityStateMessage
          availability={verdict("unavailable")}
          refreshing
        />,
      );

      expect(screen.queryByText(FALSE_UNAVAILABLE)).not.toBeInTheDocument();
    });

    it("still reports unavailable once the refresh settles", () => {
      render(
        <AvailabilityStateMessage
          availability={verdict("unavailable")}
          refreshing={false}
        />,
      );

      expect(screen.getByText(FALSE_UNAVAILABLE)).toBeInTheDocument();
    });
  });

  describe("source provenance", () => {
    it("lists unresolved sources so an incomplete answer is visible", () => {
      render(
        <AvailabilityState
          availability={verdict("unknown", "not_checked", {
            unresolved_sources: ["watchmode", "youtube"],
          })}
        />,
      );

      expect(screen.getByText(/watchmode, youtube/)).toBeInTheDocument();
    });

    it("renders one row per source with its human label", () => {
      render(
        <AvailabilityState
          availability={verdict("unknown", "not_checked", {
            sources: [
              sourceRow("justwatch", { label: "JustWatch" }),
              sourceRow("watchmode", {
                label: "Watchmode",
                status: "failed",
                reason: "source_unavailable",
              }),
            ],
          })}
        />,
      );

      expect(screen.getByTestId("availability-source-justwatch")).toHaveTextContent(
        "JustWatch",
      );
      expect(
        screen.getByTestId("availability-source-justwatch"),
      ).toHaveTextContent(/have not checked/i);
      expect(screen.getByTestId("availability-source-watchmode")).toHaveTextContent(
        /could not be reached/i,
      );
    });

    it("does not dump the internal enum name of a source row", () => {
      render(
        <AvailabilityState
          availability={verdict("unknown", "not_checked", {
            sources: [
              sourceRow("streaming_availability", {
                label: "Streaming Availability",
              }),
            ],
          })}
        />,
      );

      expect(
        screen.getByTestId("availability-source-streaming_availability"),
      ).toHaveTextContent("Streaming Availability");
      // The slug is a data attribute, not user-facing text.
      expect(
        screen.queryByText("streaming_availability", { exact: false }),
      ).toBeNull();
    });

    it("omits the reason line for a resolved source", () => {
      render(
        <AvailabilityState
          availability={verdict("available", null, {
            resolved_sources: ["justwatch"],
            sources: [
              sourceRow("justwatch", {
                label: "JustWatch",
                status: "completed",
                reason: "resolved",
                available: true,
                checked_at: "2026-10-06T00:00:00Z",
              }),
            ],
          })}
        />,
      );

      const row = screen.getByTestId("availability-source-justwatch");
      expect(row).toHaveTextContent("JustWatch");
      expect(row).not.toHaveTextContent(/checked\)$/);
    });
  });
});