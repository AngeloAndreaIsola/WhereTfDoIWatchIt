"use client";

import React, { useState, useEffect, useCallback } from "react";
import {
  ProviderContentItem,
  TMDbCandidate,
  fetchCandidateQueue,
  fetchProviderContentCandidates,
  acceptCandidate,
  rejectCandidate,
  ApiError,
} from "@/lib/candidateReviewApi";
import { ReviewQueue } from "@/components/candidate-review/ReviewQueue";
import { CandidateComparison } from "@/components/candidate-review/CandidateComparison";
import { MappingAvailabilitySummary } from "@/components/candidate-review/MappingAvailabilitySummary";
import { ReviewActionModal } from "@/components/candidate-review/ReviewActionModal";

export default function CandidateReviewPage() {
  const [queue, setQueue] = useState<ProviderContentItem[]>([]);
  const [queueLoading, setQueueLoading] = useState(true);
  const [queueError, setQueueError] = useState<string | null>(null);

  // Pagination state
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [totalItems, setTotalItems] = useState(0);

  // Filters mapped to Wave 2A backend query params: provider, status, content_type, search
  const [filterProvider, setFilterProvider] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterContentType, setFilterContentType] = useState("");
  const [searchQuery, setSearchQuery] = useState("");

  const [selectedItem, setSelectedItem] = useState<ProviderContentItem | null>(null);
  const [candidates, setCandidates] = useState<TMDbCandidate[]>([]);
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [candidatesError, setCandidatesError] = useState<string | null>(null);

  // Modal mutation state
  const [actionModal, setActionModal] = useState<{
    isOpen: boolean;
    action: "accept" | "reject";
    candidate: TMDbCandidate | null;
  }>({
    isOpen: false,
    action: "accept",
    candidate: null,
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Feedback notifications
  const [notification, setNotification] = useState<{
    type: "success" | "error" | "info";
    message: string;
  } | null>(null);

  const loadQueue = useCallback(async () => {
    setQueueLoading(true);
    setQueueError(null);
    try {
      const data = await fetchCandidateQueue({
        provider: filterProvider || undefined,
        status: filterStatus || undefined,
        content_type: filterContentType || undefined,
        search: searchQuery.trim() || undefined,
        page,
        page_size: 25,
      });

      const items = data.items || [];
      setQueue(items);
      setPages(data.pages || 1);
      setTotalItems(data.total_items ?? items.length);

      if (items.length > 0) {
        setSelectedItem((prev) => {
          if (prev && items.some((i) => i.id === prev.id)) {
            return items.find((i) => i.id === prev.id) || items[0];
          }
          return items[0];
        });
      } else {
        setSelectedItem(null);
      }
    } catch (err: unknown) {
      if (err instanceof ApiError && err.isBackendUnavailable) {
        setQueueError("Candidate review backend endpoint is currently unavailable (404/503/offline).");
      } else {
        setQueueError(err instanceof Error ? err.message : "Failed to load candidate review queue.");
      }
    } finally {
      setQueueLoading(false);
    }
  }, [filterProvider, filterStatus, filterContentType, searchQuery, page]);

  useEffect(() => {
    loadQueue();
  }, [loadQueue]);

  const loadCandidates = useCallback(async (contentItem: ProviderContentItem) => {
    setCandidatesLoading(true);
    setCandidatesError(null);
    try {
      const data = await fetchProviderContentCandidates(contentItem.id);
      setCandidates(data.candidates || []);
      if (data.provider_content) {
        setSelectedItem(data.provider_content);
      }
    } catch (err: unknown) {
      if (err instanceof ApiError && err.isBackendUnavailable) {
        setCandidatesError("Candidates endpoint unavailable. Backend mapping-acceptance service is not reachable.");
      } else {
        setCandidatesError(err instanceof Error ? err.message : "Failed to load candidate details.");
      }
    } finally {
      setCandidatesLoading(false);
    }
  }, []);

  const onSelectItem = (item: ProviderContentItem) => {
    setSelectedItem(item);
  };

  useEffect(() => {
    if (selectedItem) {
      loadCandidates(selectedItem);
    } else {
      setCandidates([]);
    }
  }, [selectedItem?.id, loadCandidates]); // eslint-disable-line react-hooks/exhaustive-deps

  // Filter setters that reset page to 1
  const handleFilterProviderChange = (provider: string) => {
    setFilterProvider(provider);
    setPage(1);
  };

  const handleFilterStatusChange = (status: string) => {
    setFilterStatus(status);
    setPage(1);
  };

  const handleFilterContentTypeChange = (contentType: string) => {
    setFilterContentType(contentType);
    setPage(1);
  };

  const handleSearchQueryChange = (query: string) => {
    setSearchQuery(query);
    setPage(1);
  };

  const handleInitiateAction = (candidate: TMDbCandidate, action: "accept" | "reject") => {
    setActionError(null);
    setActionModal({
      isOpen: true,
      action,
      candidate,
    });
  };

  const handleCloseModal = () => {
    if (!isSubmitting) {
      setActionModal((prev) => ({ ...prev, isOpen: false }));
      setActionError(null);
    }
  };

  const handleConfirmAction = async (data: {
    reason: string;
    expectedRevision: number | string | null;
    authorizeNetwork?: boolean;
    maxRequests?: number;
  }) => {
    if (!actionModal.candidate || !selectedItem) return;

    setIsSubmitting(true);
    setActionError(null);

    try {
      if (actionModal.action === "accept") {
        const resp = await acceptCandidate(selectedItem.id, actionModal.candidate.tmdb_id, {
          reason: data.reason,
          expected_revision: data.expectedRevision,
          authorize_network: data.authorizeNetwork,
          max_http_requests: data.maxRequests,
        });

        const statusMsg = resp.status === "already_accepted" ? "Candidate was already accepted." : "Successfully accepted candidate.";
        setNotification({
          type: "success",
          message: `${statusMsg} TMDb ID: ${resp.tmdb_id}${resp.candidate_title ? ` (${resp.candidate_title})` : ""}. Mapping updated: ${
            resp.mapping_updated ? "yes" : "no"
          }.`,
        });
      } else {
        const resp = await rejectCandidate(selectedItem.id, actionModal.candidate.tmdb_id, {
          reason: data.reason,
        });

        setNotification({
          type: "info",
          message: `Candidate TMDb ID: ${resp.tmdb_id}${resp.candidate_title ? ` (${resp.candidate_title})` : ""} rejected. Reason: "${resp.rejection_reason}".`,
        });
      }

      // Close modal
      setActionModal((prev) => ({ ...prev, isOpen: false }));

      // Refresh authoritative backend state
      await loadCandidates(selectedItem);
      await loadQueue();
    } catch (err: unknown) {
      if (err instanceof ApiError && err.isStaleState) {
        setActionError(
          "Concurrent or stale review conflict (409): The mapping revision or review status has changed on the server. Please close this modal, refresh, and review again."
        );
      } else {
        setActionError(
          err instanceof Error
            ? err.message
            : "Operation failed due to an unexpected error. No changes applied."
        );
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col min-h-[calc(100vh-65px)]">
      {/* Top Header */}
      <div className="border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 px-4 py-3 sm:px-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-gray-900 dark:text-gray-100 flex items-center gap-2">
              <span>TMDb Candidate Review Interface</span>
              <span className="text-[10px] uppercase font-semibold px-2 py-0.5 rounded bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-300">
                Operator Tool
              </span>
            </h1>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              Review and confirm provider-native catalogue identities and prospective TMDb matches.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <a href="/candidate-review/legacy" className="text-xs underline">Saved scraper observations</a>
            <button
              type="button"
              onClick={() => {
                loadQueue();
                if (selectedItem) loadCandidates(selectedItem);
              }}
              disabled={queueLoading || candidatesLoading}
              className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-1.5 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-750 disabled:opacity-50 shadow-sm"
            >
              Refresh Data
            </button>
          </div>
        </div>

        {/* Global Feedback notification */}
        {notification && (
          <div
            className={`mt-3 p-2.5 rounded-md text-xs flex items-center justify-between ${
              notification.type === "success"
                ? "bg-emerald-50 dark:bg-emerald-950/50 text-emerald-800 dark:text-emerald-200 border border-emerald-200 dark:border-emerald-800"
                : notification.type === "error"
                ? "bg-red-50 dark:bg-red-950/50 text-red-800 dark:text-red-200 border border-red-200 dark:border-red-800"
                : "bg-blue-50 dark:bg-blue-950/50 text-blue-800 dark:text-blue-200 border border-blue-200 dark:border-blue-800"
            }`}
          >
            <span>{notification.message}</span>
            <button
              type="button"
              onClick={() => setNotification(null)}
              className="font-bold ml-4 text-gray-500 hover:text-gray-700"
            >
              ✕
            </button>
          </div>
        )}
      </div>

      {/* Main Workspace Layout */}
      <div className="flex-1 flex flex-col md:flex-row">
        {/* Left: Unresolved Queue */}
        <ReviewQueue
          items={queue}
          selectedId={selectedItem?.id ?? null}
          onSelectItem={onSelectItem}
          loading={queueLoading}
          filterProvider={filterProvider}
          onFilterProviderChange={handleFilterProviderChange}
          filterStatus={filterStatus}
          onFilterStatusChange={handleFilterStatusChange}
          filterContentType={filterContentType}
          onFilterContentTypeChange={handleFilterContentTypeChange}
          searchQuery={searchQuery}
          onSearchQueryChange={handleSearchQueryChange}
          page={page}
          pages={pages}
          totalItems={totalItems}
          onPageChange={setPage}
        />

        {/* Right: Inspection, Evidence & Candidates Area */}
        <main className="flex-1 p-4 sm:p-6 overflow-y-auto space-y-6 bg-gray-50/20 dark:bg-gray-950/20">
          {queueError && (
            <div className="rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-4 text-xs text-red-800 dark:text-red-300">
              <h3 className="font-semibold text-sm">Failed to load review queue</h3>
              <p className="mt-1">{queueError}</p>
            </div>
          )}

          {!selectedItem && !queueLoading && (
            <div className="rounded-lg border border-dashed border-gray-300 dark:border-gray-700 p-12 text-center">
              <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">
                No Provider Item Selected
              </h3>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                Select an item from the unresolved queue on the left to inspect candidate matches.
              </p>
            </div>
          )}

          {selectedItem && (
            <>
              {/* Selected Item Header */}
              <div className="rounded-lg border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-850 p-4 shadow-sm">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300">
                        {selectedItem.provider_name}
                      </span>
                      <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
                        {selectedItem.native_title}
                      </h2>
                      {selectedItem.release_year && (
                        <span className="text-sm text-gray-500 dark:text-gray-400">
                          ({selectedItem.release_year})
                        </span>
                      )}
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        [{selectedItem.content_type}]
                      </span>
                    </div>

                    <div className="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400 mt-1.5 flex-wrap">
                      <span>Provider ID: <strong className="font-mono">{selectedItem.provider_content_id}</strong></span>
                      {selectedItem.provider_url && (
                        <a
                          href={selectedItem.provider_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-blue-600 dark:text-blue-400 hover:underline inline-flex items-center gap-1"
                        >
                          Provider Page ↗
                        </a>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              {/* Mapping vs Offer Availability Summary */}
              <MappingAvailabilitySummary item={selectedItem} />

              {/* Candidate Loading / Error / Comparison */}
              {candidatesLoading ? (
                <div className="py-12 text-center text-xs text-gray-500">
                  Loading candidate identities and scoring evidence…
                </div>
              ) : candidatesError ? (
                <div className="rounded-md border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4 text-xs text-amber-900 dark:text-amber-200">
                  <h4 className="font-semibold text-sm">Candidate details unavailable</h4>
                  <p className="mt-1">{candidatesError}</p>
                </div>
              ) : (
                <CandidateComparison
                  candidates={candidates}
                  providerContent={selectedItem}
                  onInitiateAction={handleInitiateAction}
                  isActionPending={isSubmitting}
                />
              )}
            </>
          )}
        </main>
      </div>

      {/* Review Action Confirmation Modal */}
      {selectedItem && (
        <ReviewActionModal
          isOpen={actionModal.isOpen}
          action={actionModal.action}
          candidate={actionModal.candidate}
          providerContent={selectedItem}
          onClose={handleCloseModal}
          onConfirm={handleConfirmAction}
          isSubmitting={isSubmitting}
          error={actionError}
        />
      )}
    </div>
  );
}
