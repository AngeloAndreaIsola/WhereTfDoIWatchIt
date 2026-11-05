"use client";

import React from "react";
import { TMDbCandidate, ProviderContentItem } from "@/lib/candidateReviewApi";

interface CandidateComparisonProps {
  candidates: TMDbCandidate[];
  providerContent: ProviderContentItem;
  onInitiateAction: (candidate: TMDbCandidate, action: "accept" | "reject") => void;
  isActionPending?: boolean;
}

export function CandidateComparison({
  candidates,
  providerContent,
  onInitiateAction,
  isActionPending = false,
}: CandidateComparisonProps) {
  if (candidates.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-gray-300 dark:border-gray-700 p-8 text-center bg-gray-50/50 dark:bg-gray-900/30">
        <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300">
          No Prospective TMDb Candidates Found
        </h4>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
          Automated discovery returned no matching TMDb candidates for &ldquo;{providerContent.native_title}&rdquo;.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-gray-600 dark:text-gray-300">
          Discovered TMDb Candidates ({candidates.length})
        </h3>
        <span className="text-xs text-gray-500 dark:text-gray-400">
          Ranked by scoring confidence
        </span>
      </div>

      <div className="space-y-4">
        {candidates.map((cand) => {
          const evidence = cand.scoring_evidence || {};
          const isAccepted = cand.review_state === "accepted";
          const isRejected = cand.review_state === "rejected";
          const isConflict = cand.review_state === "conflict";
          const isAmbiguous = cand.review_state === "ambiguous";

          const hasTypeConflict = cand.content_type !== providerContent.content_type;
          const hasYearConflict = evidence.year_match === "conflict";
          const hasManualConflict = Boolean(cand.competing_manual_mapping);

          const confidencePct = Math.round(cand.confidence_score * 100);

          return (
            <div
              key={cand.tmdb_id}
              className={`rounded-lg border p-4 transition-all ${
                isAccepted
                  ? "border-emerald-500 bg-emerald-50/30 dark:bg-emerald-950/20"
                  : isRejected
                  ? "border-red-300 dark:border-red-900/60 bg-red-50/20 dark:bg-red-950/10 opacity-75"
                  : isConflict
                  ? "border-amber-400 dark:border-amber-700 bg-amber-50/20 dark:bg-amber-950/10"
                  : "border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-800"
              }`}
            >
              {/* Header: Title, Year, TMDb ID & State Badge */}
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-3 border-b border-gray-100 dark:border-gray-700/60">
                <div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <h4 className="text-base font-bold text-gray-900 dark:text-gray-100">
                      {cand.title}
                    </h4>
                    {cand.year && (
                      <span className="text-sm font-normal text-gray-500 dark:text-gray-400">
                        ({cand.year})
                      </span>
                    )}
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
                      TMDb ID: {cand.tmdb_id}
                    </span>
                    <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
                      {cand.content_type}
                    </span>
                  </div>

                  {cand.original_title && cand.original_title !== cand.title && (
                    <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                      Original title: <em>{cand.original_title}</em>
                    </div>
                  )}
                </div>

                {/* Score & Review State */}
                <div className="flex items-center gap-2">
                  <div className="text-right">
                    <div className="text-xs text-gray-500 dark:text-gray-400 font-medium">Confidence</div>
                    <div
                      className={`text-sm font-bold font-mono ${
                        confidencePct >= 80
                          ? "text-emerald-600 dark:text-emerald-400"
                          : confidencePct >= 60
                          ? "text-blue-600 dark:text-blue-400"
                          : "text-amber-600 dark:text-amber-400"
                      }`}
                    >
                      {confidencePct}%
                    </div>
                  </div>

                  <div className="flex flex-col items-end gap-1">
                    <span
                      className={`text-xs font-semibold uppercase tracking-wider px-2.5 py-1 rounded-full ${
                        isAccepted
                          ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300"
                          : isRejected
                          ? "bg-red-100 text-red-800 dark:bg-red-950/60 dark:text-red-300"
                          : isConflict
                          ? "bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
                          : isAmbiguous
                          ? "bg-purple-100 text-purple-800 dark:bg-purple-950/60 dark:text-purple-300"
                          : "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300"
                      }`}
                    >
                      {cand.review_state}
                    </span>
                    {/* Machine generated vs human verified distinction / legacy unverified */}
                    {(cand.legacy_unverified || (!cand.is_human_reviewed && isAccepted)) && (
                      <span className="text-[9px] uppercase font-semibold text-amber-600 dark:text-amber-400">
                        Unverified Machine Match
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* Warnings Box */}
              {(hasTypeConflict || hasYearConflict || hasManualConflict || (evidence.warnings && evidence.warnings.length > 0)) && (
                <div className="mt-3 rounded-md border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 p-2.5 text-xs text-amber-900 dark:text-amber-200 space-y-1">
                  <span className="font-bold flex items-center gap-1">
                    ⚠️ Discrepancies & Warnings:
                  </span>
                  <ul className="list-disc pl-4 space-y-0.5">
                    {hasTypeConflict && (
                      <li>
                        <strong>MOVIE_TYPE_CONFLICT:</strong> candidate is <strong>{cand.content_type}</strong>, provider native item is <strong>{providerContent.content_type}</strong>.
                      </li>
                    )}
                    {hasYearConflict && (
                      <li>
                        <strong>MOVIE_TYPE_CONFLICT:</strong> Release year conflict: candidate is <strong>{cand.year}</strong>, provider native year is <strong>{providerContent.release_year}</strong>.
                      </li>
                    )}
                    {cand.competing_manual_mapping && (
                      <li>
                        <strong>MANUAL MAPPING CONFLICT:</strong> {cand.competing_manual_mapping.detail} (Acceptance disabled)
                      </li>
                    )}
                    {evidence.warnings?.map((warning, wIdx) => (
                      <li key={wIdx}>{warning}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Evidence Details */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3 text-xs">
                {/* Supporting Evidence */}
                <div className="rounded border border-gray-100 dark:border-gray-800 bg-gray-50/60 dark:bg-gray-900/40 p-2.5 space-y-1">
                  <div className="text-[11px] font-semibold uppercase tracking-wider text-emerald-700 dark:text-emerald-400">
                    Supporting Evidence
                  </div>
                  {evidence.supporting_evidence && evidence.supporting_evidence.length > 0 ? (
                    <ul className="list-disc pl-4 space-y-0.5 text-gray-700 dark:text-gray-300">
                      {evidence.supporting_evidence.map((s, idx) => (
                        <li key={idx}>{s}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-gray-500 dark:text-gray-400 italic text-[11px]">
                      Title relation: {evidence.title_relation ?? "none"}
                    </p>
                  )}
                </div>

                {/* Conflicting Evidence */}
                <div className="rounded border border-gray-100 dark:border-gray-800 bg-gray-50/60 dark:bg-gray-900/40 p-2.5 space-y-1">
                  <div className="text-[11px] font-semibold uppercase tracking-wider text-red-700 dark:text-red-400">
                    Conflicting Evidence
                  </div>
                  {evidence.conflicting_evidence && evidence.conflicting_evidence.length > 0 ? (
                    <ul className="list-disc pl-4 space-y-0.5 text-gray-700 dark:text-gray-300">
                      {evidence.conflicting_evidence.map((c, idx) => (
                        <li key={idx}>{c}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-gray-500 dark:text-gray-400 italic text-[11px]">
                      No conflicting evidence identified.
                    </p>
                  )}
                </div>
              </div>

              {/* Rejection reason display if rejected */}
              {isRejected && cand.rejection_reason && (
                <div className="mt-3 rounded border border-red-200 dark:border-red-900/40 bg-red-50/60 dark:bg-red-950/30 p-2.5 text-xs text-red-900 dark:text-red-300">
                  <span className="font-semibold">Recorded Rejection Reason:</span> {cand.rejection_reason}
                </div>
              )}

              {/* Action Buttons */}
              <div className="mt-4 flex items-center justify-end gap-2 pt-2 border-t border-gray-100 dark:border-gray-700/60">
                <button
                  type="button"
                  disabled={isActionPending}
                  onClick={() => onInitiateAction(cand, "reject")}
                  className="rounded px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50 border border-red-200 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950/50 disabled:opacity-50 transition-colors"
                >
                  Reject Candidate…
                </button>

                <button
                  type="button"
                  disabled={isActionPending || hasManualConflict}
                  onClick={() => onInitiateAction(cand, "accept")}
                  title={hasManualConflict ? "Cannot accept candidate due to competing manual mapping conflict" : undefined}
                  className="rounded px-3 py-1.5 text-xs font-medium text-white bg-emerald-600 hover:bg-emerald-700 dark:bg-emerald-500 dark:hover:bg-emerald-600 disabled:opacity-50 transition-colors shadow-sm disabled:cursor-not-allowed"
                >
                  Accept Candidate…
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
