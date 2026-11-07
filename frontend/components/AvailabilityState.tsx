"use client";

import React from "react";
import type { TitleAvailabilityVerdict } from "@/lib/api";

/**
 * Renders "why there is nothing here" without ever claiming more than the
 * evidence supports.
 *
 * The bug this exists to prevent: `offers: []` used to render one fixed
 * sentence, "Not currently available on your subscribed streaming services."
 * Measured over 400 tracked movies with no local offers, every single one was
 * `unknown` (375 `not_checked`, 21 `not_applicable`, 4 `title_not_found`) and
 * **none** were genuinely `unavailable`. The old copy therefore asserted
 * something false about the large majority of them.
 *
 * Three states, three meanings:
 *
 * - `unavailable` — sources were checked and none offered it. It really is
 *   not available.
 * - `unknown`     — the evidence never resolved (not checked, failed,
 *   blocked on quota, not applicable). Saying "not available" here is a lie;
 *   saying "not known yet" is not.
 * - missing       — an older backend sent no verdict at all. Treated as
 *   `unknown`, never as `unavailable`, so a missing field can never make a
 *   title look definitively negative.
 */

/** Human wording for the backend's resolution reasons. */
const REASON_COPY: Record<string, string> = {
  not_checked: "we have not checked these sources for this title yet",
  collector_not_run: "these sources have not run for this title yet",
  queued: "a check for this title is queued",
  not_applicable: "these sources do not cover this title",
  not_supported: "these sources do not support this title",
  title_not_found: "the sources do not recognise this title",
  source_unavailable: "the source could not be reached",
  quota_exhausted: "the source is out of requests for now",
  resolved: "checked",
};

function reasonCopy(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return REASON_COPY[reason] ?? `reason: ${reason}`;
}

export interface AvailabilityStateProps {
  /** Absent when the backend predates the verdict field. */
  availability?: TitleAvailabilityVerdict;
  /** True while a refresh is queued or running. */
  refreshing?: boolean;
  /** Label used in the "still not known" sentence. */
  titleLabel?: string;
}

/**
 * The headline state. Exported separately so the detail pages can render the
 * same state in their own layout without duplicating the wording.
 */
export function AvailabilityStateMessage({
  availability,
  refreshing,
}: {
  availability?: TitleAvailabilityVerdict;
  refreshing?: boolean;
}): React.ReactElement {
  // Absence is unknown, never unavailable. This is the whole point.
  const state = availability?.state ?? "unknown";
  const reason = reasonCopy(availability?.reason);
  const evidence = availability?.evidence;
  const titleSignals = (availability?.title_signals ?? []).filter((s) => s.provider);

  // Retain historical provenance, but only current trusted signals support
  // a present-tense claim. Neither kind of signal is a playable offer.
  if (evidence === "title_signal" && state !== "unavailable" && !refreshing) {
    const trustedSignals = titleSignals.filter((s) => s.trusted === true);
    const availableOn = Array.from(new Set([
      ...(availability?.available_on ?? []),
      ...trustedSignals.map((s) => s.provider),
    ].filter(Boolean)));
    const trusted = availableOn.length > 0;
    return (
      <div>
        <p className="font-medium text-amber-700 dark:text-amber-300">
          {trusted
            ? "Reported available — no current offer to show."
            : "Historical availability report — current availability unverified."}
        </p>
        <p className="mt-1 text-gray-600 dark:text-gray-400">
          {trusted
            ? `A source reports this title on ${availableOn.join(", ")}, but no concrete offer could be matched to a service in our catalogue yet.`
            : `An earlier source report${titleSignals.length ? ` for ${Array.from(new Set(titleSignals.map((s) => s.provider))).join(", ")}` : ""} has not been verified as current availability.`}{" "}
          <span className="text-gray-500 dark:text-gray-500">
            That is a report, not yet a playable option.
          </span>
        </p>
      </div>
    );
  }

  if (state === "unavailable" && !refreshing) {
    return (
      <p className="text-gray-500 dark:text-gray-400">
        Not currently available on your subscribed streaming services.
      </p>
    );
  }

  if (state === "unknown") {
    return (
      <div>
        <p className="font-medium text-amber-700 dark:text-amber-300">
          Availability not known yet.
        </p>
        <p className="mt-1 text-gray-600 dark:text-gray-400">
          {reason
            ? `This title is unresolved because ${reason}.`
            : "No source has been able to confirm availability for this title yet."}
          {" "}
          <span className="text-gray-500 dark:text-gray-500">
            That is not the same as being unavailable.
          </span>
        </p>
      </div>
    );
  }

  // state === "available" with no offers is contradictory; treat it honestly
  // rather than claiming a positive we cannot show. Name where the source says
  // it is available so the line is actionable instead of a dead end.
  const availableOn = (availability?.available_on ?? []).filter(Boolean);
  return (
    <div>
      <p className="font-medium text-amber-700 dark:text-amber-300">
        Available — but not on a service we can show.
      </p>
      <p className="mt-1 text-gray-600 dark:text-gray-400">
        {availableOn.length > 0
          ? `A source reports this title on ${availableOn.join(", ")}, but no offer could be matched to a service in our catalogue.`
          : "A source reported this title as available, but no current offer could be displayed."}
      </p>
    </div>
  );
}

export default function AvailabilityState({
  availability,
  refreshing,
}: AvailabilityStateProps): React.ReactElement {
  const state = availability?.state ?? "unknown";
  const isUnknown = state !== "unavailable";

  const unresolved = availability?.unresolved_sources ?? [];
  const rows = availability?.sources ?? [];

  return (
    <div
      data-testid="availability-state"
      data-state={state}
      className={`rounded-xl border p-6 text-center text-sm ${
        isUnknown
          ? "border-amber-300 bg-amber-50/60 dark:border-amber-700 dark:bg-amber-900/20"
          : "border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800"
      }`}
    >
      <AvailabilityStateMessage
        availability={availability}
        refreshing={refreshing}
      />

      {rows.length > 0 && (
        <ul className="mt-4 space-y-1 text-left text-xs text-gray-600 dark:text-gray-400">
          {rows.map((row) => (
            <li key={row.source} data-testid={`availability-source-${row.source}`}>
              <span className="font-medium">{row.label || row.source}</span>
              {" — "}
              {row.status}
              {row.reason && row.reason !== "resolved"
                ? ` (${REASON_COPY[row.reason] ?? row.reason})`
                : ""}
            </li>
          ))}
        </ul>
      )}

      {unresolved.length > 0 && (
        <p className="mt-3 text-xs text-gray-500 dark:text-gray-500">
          Still unresolved: {unresolved.join(", ")}
        </p>
      )}
    </div>
  );
}