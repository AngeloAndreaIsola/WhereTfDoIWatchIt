/**
 * Typed API client for TMDb Candidate Review interface (Local Operator).
 *
 * Backend integration endpoints owned by parallel mapping-acceptance backend:
 * - GET /api/admin/provider-content/candidates/ (Paginated review queue)
 * - GET /api/admin/provider-content/{id}/candidates/ (Details & candidates)
 * - POST /api/admin/provider-content/{id}/candidates/{tmdb_id}/accept/
 * - POST /api/admin/provider-content/{id}/candidates/{tmdb_id}/reject/
 *
 * STRICT GUARDRAILS:
 * - Do not invent alternative mutation endpoints.
 * - If an endpoint is unavailable during development, return structured error or handle cleanly.
 * - Never claim that an acceptance succeeded without backend confirmation.
 * - Mapped does not imply watchable.
 * - Explicit backend-unavailable state when collection endpoint is absent (do not use fake mock fallback or silent 404->empty).
 */

export type ReviewState = "pending" | "accepted" | "rejected" | "conflict" | "ambiguous";

export type ResolutionCategory =
  | "unique_candidate"
  | "ambiguous"
  | "conflict"
  | "no_results"
  | "rejected";

export type AvailabilityOfferStatus =
  | "no_offer"
  | "active_offer"
  | "unavailable"
  | "unknown";

export interface ExistingMapping {
  id?: number;
  tmdb_id: number;
  title?: string;
  year?: number | null;
  content_type?: string;
  match_method: string;
  confidence: number;
  is_manual: boolean;
  source?: string;
  first_seen?: string | null;
  last_verified?: string | null;
  revision?: number | string | null;
}

export interface AvailabilityOffersSummary {
  status: AvailabilityOfferStatus;
  status_label: string;
  total_offers: number;
  active_offers: number;
  countries: string[];
  services: string[];
  disclaimer: string;
}

export interface CandidateScoringEvidence {
  content_title?: string;
  content_type?: string;
  candidate_title?: string;
  candidate_original_title?: string;
  candidate_tmdb_id?: number;
  title_relation?: "exact" | "contains" | "overlap" | "weak_overlap" | "none" | string;
  matched_target?: "primary_title" | "original_title" | "localized_title" | string;
  content_year?: number | null;
  candidate_year?: number | null;
  year_diff?: number;
  year_match?: "exact" | "adjacent" | "conflict" | "discrepancy" | "unknown_content_year" | "unknown_candidate_year" | string;
  final_score?: number;
  supporting_evidence?: string[];
  conflicting_evidence?: string[];
  warnings?: string[];
}

export interface TMDbCandidate {
  id?: number;
  tmdb_id: number;
  title: string;
  original_title?: string;
  content_type: "movie" | "series";
  year: number | null;
  poster_path?: string | null;
  confidence_score: number;
  scoring_evidence: CandidateScoringEvidence;
  review_state: ReviewState;
  resolution_category: ResolutionCategory;
  rejection_reason?: string;
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  /** True only if an operator explicitly confirmed this match */
  is_human_reviewed?: boolean;
  /** Competing manual mapping on the same TMDb ID if detected */
  competing_manual_mapping?: {
    mapping_id: number;
    provider_slug: string;
    provider_content_id: string;
    detail: string;
  } | null;
}

export interface ProviderContentItem {
  id: string | number;
  provider_slug: string;
  provider_name: string;
  provider_content_id: string;
  native_title: string;
  content_type: "movie" | "series";
  release_year: number | null;
  provider_url?: string;
  metadata?: Record<string, unknown>;
  existing_mapping?: ExistingMapping | null;
  availability_summary: AvailabilityOffersSummary;
  candidate_count: number;
  top_confidence?: number;
  resolution_category: ResolutionCategory;
  last_seen?: string | null;
  revision?: number | string | null;
}

export interface ProviderContentQueueResponse {
  count: number;
  results: ProviderContentItem[];
}

export interface ProviderContentCandidatesResponse {
  provider_content: ProviderContentItem;
  candidates: TMDbCandidate[];
  summary: {
    total_candidates: number;
    accepted_count: number;
    rejected_count: number;
    pending_count: number;
    has_manual_conflict: boolean;
  };
}

export interface ReviewAcceptPayload {
  reason: string;
  expected_revision?: number | string | null;
  authorize_network?: boolean;
  max_http_requests?: number;
}

export interface ReviewRejectPayload {
  reason: string;
}

export interface ReviewMutationResponse {
  success: boolean;
  message: string;
  tmdb_id: number;
  provider_content_id: string | number;
  review_state: ReviewState;
  rejection_reason?: string;
  mapping_updated: boolean;
  reviewed_at: string;
}

export class ApiError extends Error {
  status: number;
  isBackendUnavailable: boolean;
  isStaleState: boolean;

  constructor(message: string, status: number, isStale: boolean = false) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.isBackendUnavailable = status === 404 || status === 502 || status === 503 || status === 504 || status === 0;
    this.isStaleState = isStale || status === 409;
  }
}

/**
 * Fetch the queue of unresolved or review-pending provider contents.
 * Target endpoint: GET /api/admin/provider-content/candidates/
 * Fallback endpoint: GET /api/admin/provider-content/unresolved/
 */
export async function fetchCandidateQueue(
  params?: { provider?: string; category?: string; q?: string }
): Promise<ProviderContentQueueResponse> {
  const query = new URLSearchParams();
  if (params?.provider) query.set("provider", params.provider);
  if (params?.category) query.set("category", params.category);
  if (params?.q) query.set("q", params.q);

  const queryString = query.toString() ? `?${query.toString()}` : "";
  let res: Response;
  try {
    const primaryRes = await fetch(`/api/admin/provider-content/candidates/${queryString}`);
    if (primaryRes.ok) {
      res = primaryRes;
    } else if (primaryRes.status === 404) {
      // Check secondary fallback endpoint
      const fallbackRes = await fetch(`/api/admin/provider-content/unresolved/${queryString}`);
      if (fallbackRes.ok) {
        res = fallbackRes;
      } else {
        res = primaryRes;
      }
    } else {
      res = primaryRes;
    }
  } catch (err: unknown) {
    throw new ApiError(
      err instanceof Error ? err.message : "Network failure connecting to candidate review backend.",
      0
    );
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(
      body.error ?? body.detail ?? `Failed to fetch candidate queue (${res.status})`,
      res.status
    );
  }

  return res.json();
}

/**
 * Fetch candidate details and evidence for a specific provider content.
 * Endpoint: GET /api/admin/provider-content/{id}/candidates/
 */
export async function fetchProviderContentCandidates(
  id: string | number
): Promise<ProviderContentCandidatesResponse> {
  let res: Response;
  try {
    res = await fetch(`/api/admin/provider-content/${encodeURIComponent(id)}/candidates/`);
  } catch (err: unknown) {
    throw new ApiError(
      err instanceof Error ? err.message : "Network failure fetching candidates.",
      0
    );
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(
      body.error ?? body.detail ?? `Failed to fetch candidates for provider content ${id} (${res.status})`,
      res.status
    );
  }

  return res.json();
}

/**
 * Explicitly accept a candidate TMDb identity for a provider content.
 * Endpoint: POST /api/admin/provider-content/{id}/candidates/{tmdb_id}/accept/
 *
 * Requirements:
 * - Mandatory non-empty reason.
 * - Explicit expected_revision (null when unmapped).
 * - Confirmation required before invocation.
 * - Does not claim success without backend confirmation.
 */
export async function acceptCandidate(
  id: string | number,
  tmdbId: number,
  payload: ReviewAcceptPayload
): Promise<ReviewMutationResponse> {
  const trimmedReason = payload.reason?.trim();
  if (!trimmedReason) {
    throw new ApiError("A valid, non-empty reason is mandatory for acceptance.", 400);
  }

  const bodyData: Record<string, unknown> = {
    reason: trimmedReason,
    expected_revision: payload.expected_revision ?? null,
  };
  if (payload.authorize_network) {
    bodyData.authorize_network = true;
    if (payload.max_http_requests !== undefined) {
      bodyData.max_http_requests = payload.max_http_requests;
    }
  }

  let res: Response;
  try {
    res = await fetch(
      `/api/admin/provider-content/${encodeURIComponent(id)}/candidates/${encodeURIComponent(tmdbId)}/accept/`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(bodyData),
      }
    );
  } catch (err: unknown) {
    throw new ApiError(
      err instanceof Error ? err.message : "Network failure during candidate acceptance.",
      0
    );
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const message = body.error ?? body.detail ?? `Candidate acceptance failed (${res.status})`;
    throw new ApiError(message, res.status, res.status === 409);
  }

  return res.json();
}

/**
 * Explicitly reject a candidate TMDb identity for a provider content.
 * Endpoint: POST /api/admin/provider-content/{id}/candidates/{tmdb_id}/reject/
 *
 * Requirements:
 * - Mandatory non-empty reason.
 * - Confirmation required before invocation.
 * - Does not claim success without backend confirmation.
 */
export async function rejectCandidate(
  id: string | number,
  tmdbId: number,
  payload: ReviewRejectPayload
): Promise<ReviewMutationResponse> {
  const trimmedReason = payload.reason?.trim();
  if (!trimmedReason) {
    throw new ApiError("A valid, non-empty reason is mandatory for rejection.", 400);
  }

  let res: Response;
  try {
    res = await fetch(
      `/api/admin/provider-content/${encodeURIComponent(id)}/candidates/${encodeURIComponent(tmdbId)}/reject/`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: trimmedReason }),
      }
    );
  } catch (err: unknown) {
    throw new ApiError(
      err instanceof Error ? err.message : "Network failure during candidate rejection.",
      0
    );
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const message = body.error ?? body.detail ?? `Candidate rejection failed (${res.status})`;
    throw new ApiError(message, res.status, res.status === 409);
  }

  return res.json();
}
