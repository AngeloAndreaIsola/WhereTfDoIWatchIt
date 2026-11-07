"use client";

import React, { useState } from "react";
import { TMDbCandidate, ProviderContentItem } from "@/lib/candidateReviewApi";

interface ReviewActionModalProps {
  isOpen: boolean;
  action: "accept" | "reject";
  candidate: TMDbCandidate | null;
  providerContent: ProviderContentItem;
  onClose: () => void;
  onConfirm: (data: {
    reason: string;
    expectedRevision: number | string | null;
    authorizeNetwork?: boolean;
    maxRequests?: number;
  }) => Promise<void>;
  isSubmitting: boolean;
  error?: string | null;
}

export function ReviewActionModal({
  isOpen,
  action,
  candidate,
  providerContent,
  onClose,
  onConfirm,
  isSubmitting,
  error,
}: ReviewActionModalProps) {
  const [reason, setReason] = useState("");
  const [authorizeNetwork, setAuthorizeNetwork] = useState(false);
  const [maxRequests, setMaxRequests] = useState(5);
  const [validationError, setValidationError] = useState<string | null>(null);

  if (!isOpen || !candidate) return null;

  const isAccept = action === "accept";
  const expectedRev = providerContent.existing_mapping?.revision ?? null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = reason.trim();
    if (!trimmed) {
      setValidationError(
        `A mandatory reason is required to ${isAccept ? "accept" : "reject"} this candidate.`
      );
      return;
    }
    setValidationError(null);
    await onConfirm({
      reason: trimmed,
      expectedRevision: expectedRev,
      authorizeNetwork: isAccept ? authorizeNetwork : undefined,
      maxRequests: isAccept && authorizeNetwork ? maxRequests : undefined,
    });
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm"
    >
      <div className="w-full max-w-lg rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 shadow-xl overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div
          className={`px-5 py-4 border-b ${
            isAccept
              ? "border-emerald-200 bg-emerald-50/50 dark:border-emerald-950/60 dark:bg-emerald-950/30"
              : "border-red-200 bg-red-50/50 dark:border-red-950/60 dark:bg-red-950/30"
          }`}
        >
          <h3
            id="modal-title"
            className={`text-base font-bold ${
              isAccept
                ? "text-emerald-900 dark:text-emerald-200"
                : "text-red-900 dark:text-red-200"
            }`}
          >
            {isAccept ? "Confirm Candidate Acceptance" : "Confirm Candidate Rejection"}
          </h3>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            Explicit confirmation required before applying mapping mutations.
          </p>
        </div>

        {/* Content */}
        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          {/* Target comparison summary */}
          <div className="rounded-md border border-gray-100 dark:border-gray-800 bg-gray-50/70 dark:bg-gray-850 p-3 text-xs space-y-1.5">
            <div className="flex justify-between items-center text-gray-500 dark:text-gray-400">
              <span>Provider Content:</span>
              <span className="font-mono">{providerContent.provider_slug} ({providerContent.provider_content_id})</span>
            </div>
            <div className="font-semibold text-gray-900 dark:text-gray-100">
              {providerContent.native_title} {providerContent.release_year ? `(${providerContent.release_year})` : ""}
            </div>
            <div className="pt-1.5 border-t border-gray-200 dark:border-gray-700/60 flex justify-between items-center text-gray-500 dark:text-gray-400">
              <span>{isAccept ? "Proposed TMDb Identity:" : "Rejected Candidate:"}</span>
              <span className="font-mono">TMDb ID: {candidate.tmdb_id}</span>
            </div>
            <div className="font-semibold text-gray-900 dark:text-gray-100">
              {candidate.title} {candidate.year ? `(${candidate.year})` : ""} [{candidate.content_type}]
            </div>

            <div className="pt-1 text-[11px] text-gray-500 dark:text-gray-400">
              Expected Mapping Revision:{" "}
              <span className="font-mono font-semibold">
                {expectedRev === null ? "null (unmapped)" : expectedRev}
              </span>
            </div>
          </div>

          {/* Conflict warnings in modal */}
          {candidate.competing_manual_mapping && (
            <div className="rounded-md border border-red-300 dark:border-red-900 bg-red-50 dark:bg-red-950/40 p-3 text-xs text-red-900 dark:text-red-200">
              <strong>MANUAL MAPPING CONFLICT:</strong> {candidate.competing_manual_mapping.detail}
            </div>
          )}

          {/* Mandatory Reason Field */}
          <div>
            <label
              htmlFor="review-reason"
              className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1"
            >
              Mandatory Reason <span className="text-red-500">*</span>
            </label>
            <textarea
              id="review-reason"
              rows={3}
              disabled={isSubmitting}
              value={reason}
              onChange={(e) => {
                setReason(e.target.value);
                if (validationError) setValidationError(null);
              }}
              placeholder={
                isAccept
                  ? "Explain why this candidate is the verified match (e.g. verified director, cast, original title and release year matches)..."
                  : "Explain why this candidate was rejected (e.g. wrong release year, completely different movie with same title)..."
              }
              className="w-full rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 p-2 text-xs text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            {validationError && (
              <p className="mt-1 text-xs text-red-600 dark:text-red-400">{validationError}</p>
            )}
          </div>

          {/* Network verification options (Accept only) */}
          {isAccept && (
            <div className="rounded-md border border-gray-100 dark:border-gray-800 p-3 space-y-2 bg-gray-50/50 dark:bg-gray-850">
              <label className="flex items-center gap-2 cursor-pointer text-xs font-medium text-gray-700 dark:text-gray-300">
                <input
                  type="checkbox"
                  checked={authorizeNetwork}
                  onChange={(e) => setAuthorizeNetwork(e.target.checked)}
                  disabled={isSubmitting}
                  className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                />
                <span>Authorize external network verification (finite budget)</span>
              </label>

              {authorizeNetwork && (
                <div className="flex items-center gap-2 pt-1 pl-6 text-xs text-gray-600 dark:text-gray-400">
                  <label htmlFor="max-reqs">Max HTTP requests:</label>
                  <input
                    id="max-reqs"
                    type="number"
                    min={1}
                    max={10}
                    value={maxRequests}
                    onChange={(e) => setMaxRequests(parseInt(e.target.value, 10) || 1)}
                    disabled={isSubmitting}
                    className="w-16 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-0.5 text-xs text-gray-900 dark:text-gray-100"
                  />
                  <span className="text-[10px] text-gray-400">(capped to 10 max)</span>
                </div>
              )}
            </div>
          )}

          {/* Backend Error Display */}
          {error && (
            <div className="rounded-md border border-red-300 dark:border-red-900 bg-red-50 dark:bg-red-950/40 p-2.5 text-xs text-red-900 dark:text-red-200">
              {error}
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center justify-end gap-2 pt-3 border-t border-gray-100 dark:border-gray-800">
            <button
              type="button"
              disabled={isSubmitting}
              onClick={onClose}
              className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-1.5 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-750 disabled:opacity-50"
            >
              Cancel
            </button>

            <button
              type="submit"
              disabled={isSubmitting}
              className={`rounded-md px-3.5 py-1.5 text-xs font-medium text-white transition-colors shadow-sm disabled:opacity-50 flex items-center gap-1.5 ${
                isAccept
                  ? "bg-emerald-600 hover:bg-emerald-700 dark:bg-emerald-500 dark:hover:bg-emerald-600"
                  : "bg-red-600 hover:bg-red-700 dark:bg-red-500 dark:hover:bg-red-600"
              }`}
            >
              {isSubmitting && (
                <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-solid border-white border-r-transparent" />
              )}
              <span>{isAccept ? "Confirm & Accept Candidate" : "Confirm & Reject Candidate"}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
