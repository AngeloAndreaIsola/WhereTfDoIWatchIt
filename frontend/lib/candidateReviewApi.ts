/**
 * Typed API client and adapter for TMDb Candidate Review interface (Local Operator).
 *
 * Backend integration endpoints owned by Wave 2A mapping-acceptance backend:
 * - GET /api/admin/provider-content/candidates/ (Review queue with search, status, content_type, page, page_size filters)
 * - GET /api/admin/provider-content/{id}/candidates/ (Details & candidate list)
 * - POST /api/admin/provider-content/{id}/candidates/{tmdb_id}/accept/
 * - POST /api/admin/provider-content/{id}/candidates/{tmdb_id}/reject/
 *
 * STRICT GUARDRAILS & SECURITY CONTRACT:
 * - Anti-CSRF and operator context headers sent on mutations (X-Requested-With, X-Operator-Context, X-CSRFToken).
 * - Non-secret same-origin headers coordinate with verify_operator_mutation_guard when no Django staff session exists.
 * - Normalized legacy candidate provenance from `is_legacy_unverified`.
 * - Derived manual mapping from `match_method: "manual"`.
 * - Detection of manual mapping override conflicts (existingMapping.is_manual && candidate.tmdb_id !== existingMapping.tmdb_id).
 * - Mapped does NOT imply watchable.
 * - Server-side pagination support (page, pages, total_items).
 */

export type ReviewState = "pending" | "accepted" | "rejected" | "conflict" | "ambiguous";

export type ResolutionCategory =
  | "unique_candidate"
  | "ambiguous"
  | "conflict"
  | "no_results"
  | "rejected"
  | "unresolved";

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

export interface CompetingManualMapping {
  mapping_id: number;
  provider_slug: string;
  provider_content_id: string;
  detail: string;
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
  /** If imported as legacy or automated match without human review */
  legacy_unverified?: boolean;
  /** Competing manual mapping on the same TMDb ID if detected */
  competing_manual_mapping?: CompetingManualMapping | null;
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

export interface QueueSummary {
  total_unresolved?: number;
  ambiguous?: number;
  conflict?: number;
  [key: string]: unknown;
}

export interface ProviderContentQueueResponse {
  items: ProviderContentItem[];
  page: number;
  pages: number;
  total_items: number;
  summary?: QueueSummary;
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

export interface NormalizedMutationResponse {
  status: "accepted" | "already_accepted" | "rejected" | string;
  tmdb_id: number;
  provider_content_id: string | number;
  mapping_updated: boolean;
  rejection_reason?: string;
  reviewed_at?: string;
  candidate_title?: string;
  mapping_id?: number;
  revision?: number | string | null;
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

// ============================================================================
// Operator Security & CSRF Token Helper
// ============================================================================

export function getCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

export function getMutationHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    // Standard non-secret same-origin & operator context headers required by verify_operator_mutation_guard
    "X-Requested-With": "XMLHttpRequest",
    "X-Operator-Context": "local-operator",
  };

  const token = getCsrfToken();
  if (token) {
    headers["X-CSRFToken"] = token;
  }

  return headers;
}

// ============================================================================
// Normalization Helpers (Wave 2A Adapter)
// ============================================================================

export function normalizeExistingMapping(raw: Record<string, unknown> | null | undefined): ExistingMapping | null {
  if (!raw || typeof raw !== "object" || !raw.tmdb_id) return null;

  const matchMethod = (raw.match_method as string) || "";
  // Backend provides match_method: "manual" (or "manual_curation") rather than is_manual: true
  const isManual = Boolean(
    raw.is_manual === true ||
    matchMethod.toLowerCase().includes("manual")
  );

  return {
    id: typeof raw.id === "number" ? raw.id : undefined,
    tmdb_id: Number(raw.tmdb_id),
    title: (raw.title as string) || (raw.tmdb_title as string) || undefined,
    year: typeof raw.year === "number" ? raw.year : (typeof raw.tmdb_year === "number" ? raw.tmdb_year : null),
    content_type: (raw.content_type as string) || "movie",
    match_method: matchMethod || (isManual ? "manual" : "automated"),
    confidence: typeof raw.confidence === "number" ? raw.confidence : 1.0,
    is_manual: isManual,
    source: raw.source as string | undefined,
    first_seen: (raw.first_seen as string) ?? null,
    last_verified: (raw.last_verified as string) ?? null,
    revision: (raw.revision as number | string) ?? null,
  };
}

export function normalizeAvailabilitySummary(
  raw: Record<string, unknown> | null | undefined,
  existingMapping: ExistingMapping | null
): AvailabilityOffersSummary {
  const rawAvailability = raw?.availability_summary as Record<string, unknown> | undefined;
  const activeOffers = Number(raw?.active_offer_count ?? rawAvailability?.active_offers ?? raw?.active_offers ?? 0);
  const totalOffers = Number(raw?.offer_count ?? rawAvailability?.total_offers ?? raw?.total_offers ?? activeOffers);

  const countries = (raw?.countries as string[]) ?? (rawAvailability?.countries as string[]) ?? [];
  const services = (raw?.services as string[]) ?? (rawAvailability?.services as string[]) ?? (raw?.provider ? [raw.provider as string] : []);

  const isMapped = Boolean(existingMapping && existingMapping.tmdb_id);
  const hasOffers = activeOffers > 0;

  let status: AvailabilityOfferStatus = "unknown";
  let statusLabel = "Unknown";
  let disclaimer = "";

  if (hasOffers) {
    status = "active_offer";
    statusLabel = "Active Offer";
    disclaimer = `Active stream available across ${services.length || 1} provider(s).`;
  } else if (isMapped) {
    status = "no_offer";
    statusLabel = "Mapped (No Offers)";
    disclaimer = "Content identity is mapped to TMDb, but has ZERO playable offers (Mapped ≠ Watchable).";
  } else {
    status = "no_offer";
    statusLabel = "No Offers";
    disclaimer = "No active streaming offers found for this provider catalogue item.";
  }

  return {
    status,
    status_label: statusLabel,
    total_offers: totalOffers,
    active_offers: activeOffers,
    countries,
    services,
    disclaimer,
  };
}

export function normalizeProviderContentItem(raw: Record<string, unknown>): ProviderContentItem {
  const existingMapping = normalizeExistingMapping(raw.existing_mapping as Record<string, unknown> | undefined);
  const availability = normalizeAvailabilitySummary(raw, existingMapping);

  const providerSlug = (raw.provider_slug as string) || (raw.provider as string) || "provider";
  const providerName = (raw.provider_name as string) || (raw.provider as string) || providerSlug;
  const providerContentId = (raw.provider_content_id as string) || (raw.raw_id as string) || String(raw.id);
  const nativeTitle = (raw.native_title as string) || (raw.title as string) || "Untitled";
  const contentType = raw.content_type === "series" ? "series" : "movie";
  const releaseYear = typeof raw.release_year === "number" ? raw.release_year : (typeof raw.year === "number" ? raw.year : null);

  return {
    id: raw.id as string | number,
    provider_slug: providerSlug,
    provider_name: providerName,
    provider_content_id: providerContentId,
    native_title: nativeTitle,
    content_type: contentType,
    release_year: releaseYear,
    provider_url: (raw.provider_url as string) || (raw.url as string) || undefined,
    metadata: (raw.metadata as Record<string, unknown>) || {},
    existing_mapping: existingMapping,
    availability_summary: availability,
    candidate_count: typeof raw.candidate_count === "number" ? raw.candidate_count : (Array.isArray(raw.candidates) ? raw.candidates.length : 0),
    top_confidence: typeof raw.top_confidence === "number" ? raw.top_confidence : undefined,
    resolution_category: ((raw.resolution_category as string) || (raw.status as string) || "unresolved") as ResolutionCategory,
    last_seen: (raw.last_seen as string) ?? null,
    revision: (raw.revision as number | string) ?? existingMapping?.revision ?? null,
  };
}

export function normalizeCandidate(raw: Record<string, unknown>): TMDbCandidate {
  const evidence: CandidateScoringEvidence = (raw.scoring_evidence as CandidateScoringEvidence) || (raw.evidence as CandidateScoringEvidence) || {};
  const score = typeof raw.confidence_score === "number" ? raw.confidence_score : (typeof raw.confidence === "number" ? raw.confidence : (evidence.final_score ?? 0));

  // Actual Wave 2A backend sends `is_legacy_unverified`
  const isLegacyUnverified = Boolean(raw.is_legacy_unverified ?? raw.legacy_unverified);
  const isHumanReviewed = raw.is_human_reviewed !== undefined ? Boolean(raw.is_human_reviewed) : !isLegacyUnverified;

  return {
    id: typeof raw.id === "number" ? raw.id : undefined,
    tmdb_id: Number(raw.tmdb_id),
    title: (raw.title as string) || (raw.tmdb_title as string) || "Unknown TMDb Title",
    original_title: (raw.original_title as string) || (raw.tmdb_original_title as string) || undefined,
    content_type: raw.content_type === "series" ? "series" : "movie",
    year: typeof raw.year === "number" ? raw.year : (typeof raw.tmdb_year === "number" ? raw.tmdb_year : null),
    poster_path: (raw.poster_path as string) ?? null,
    confidence_score: score,
    scoring_evidence: evidence,
    review_state: ((raw.review_state as string) || (raw.status as string) || "pending") as ReviewState,
    resolution_category: ((raw.resolution_category as string) || "unresolved") as ResolutionCategory,
    rejection_reason: raw.rejection_reason as string | undefined,
    reviewed_at: (raw.reviewed_at as string) ?? null,
    reviewed_by: (raw.reviewed_by as string) ?? null,
    is_human_reviewed: isHumanReviewed,
    legacy_unverified: isLegacyUnverified,
    competing_manual_mapping: (raw.competing_manual_mapping as CompetingManualMapping) ?? null,
  };
}

// ============================================================================
// API Client Functions
// ============================================================================

export interface QueueQueryParams {
  search?: string;
  status?: string;
  content_type?: string;
  provider?: string;
  page?: number;
  page_size?: number;
}

/**
 * Fetch the candidate review queue from Wave 2A backend:
 * GET /api/admin/provider-content/candidates/
 *
 * Query params: search, status, content_type, provider, page, page_size
 * Expected backend shape: { items: [...], page, pages, total_items, summary }
 */
export async function fetchCandidateQueue(
  params?: QueueQueryParams
): Promise<ProviderContentQueueResponse> {
  const query = new URLSearchParams();
  if (params?.search) query.set("search", params.search);
  if (params?.status) query.set("status", params.status);
  if (params?.content_type) query.set("content_type", params.content_type);
  if (params?.provider) query.set("provider", params.provider);
  if (params?.page) query.set("page", String(params.page));
  if (params?.page_size) query.set("page_size", String(params.page_size));

  const queryString = query.toString() ? `?${query.toString()}` : "";
  let res: Response;
  try {
    res = await fetch(`/api/admin/provider-content/candidates/${queryString}`);
  } catch (err: unknown) {
    throw new ApiError(
      err instanceof Error ? err.message : "Network failure connecting to candidate review backend.",
      0
    );
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(
      body.error ?? body.detail ?? `Candidate review backend endpoint is currently unavailable (${res.status}).`,
      res.status
    );
  }

  const raw = await res.json();
  const rawItems: Record<string, unknown>[] = raw.items || raw.results || [];
  const normalizedItems = rawItems.map(normalizeProviderContentItem);

  return {
    items: normalizedItems,
    page: raw.page ?? 1,
    pages: raw.pages ?? 1,
    total_items: raw.total_items ?? raw.count ?? normalizedItems.length,
    summary: raw.summary,
  };
}

/**
 * Fetch candidate details and evidence for a specific provider content:
 * GET /api/admin/provider-content/{id}/candidates/
 *
 * Wave 2A backend returns either flat fields:
 * { id, provider, title, year, existing_mapping, offer_count, active_offer_count, candidates, ... }
 * or nested { provider_content, candidates, summary }.
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

  const raw = await res.json();
  const rawContent = (raw.provider_content || raw) as Record<string, unknown>;
  const normalizedContent = normalizeProviderContentItem(rawContent);

  const rawCandidates: Record<string, unknown>[] = (raw.candidates || rawContent.candidates || []) as Record<string, unknown>[];
  const normalizedCandidates = rawCandidates.map(normalizeCandidate);

  let acceptedCount = 0;
  let rejectedCount = 0;
  let pendingCount = 0;
  let hasManualConflict = false;

  for (const c of normalizedCandidates) {
    if (c.review_state === "accepted") acceptedCount++;
    else if (c.review_state === "rejected") rejectedCount++;
    else pendingCount++;

    if (c.competing_manual_mapping) hasManualConflict = true;
  }

  return {
    provider_content: normalizedContent,
    candidates: normalizedCandidates,
    summary: raw.summary || {
      total_candidates: normalizedCandidates.length,
      accepted_count: acceptedCount,
      rejected_count: rejectedCount,
      pending_count: pendingCount,
      has_manual_conflict: hasManualConflict,
    },
  };
}

/**
 * Accept a candidate TMDb identity:
 * POST /api/admin/provider-content/{id}/candidates/{tmdb_id}/accept/
 *
 * Backend returns:
 * { status: "accepted" | "already_accepted", mapping: {...}, candidate: {...}, movie?: {...}, audit?: {...} }
 */
export async function acceptCandidate(
  id: string | number,
  tmdbId: number,
  payload: ReviewAcceptPayload
): Promise<NormalizedMutationResponse> {
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
        headers: getMutationHeaders(),
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

  const data = await res.json();
  const returnedTmdbId = data.candidate?.tmdb_id ?? data.mapping?.tmdb_id ?? data.tmdb_id ?? tmdbId;

  return {
    status: data.status || "accepted",
    tmdb_id: returnedTmdbId,
    provider_content_id: id,
    mapping_updated: data.status === "accepted",
    candidate_title: data.candidate?.title,
    mapping_id: data.mapping?.id,
    revision: data.mapping?.revision ?? null,
    reviewed_at: data.audit?.created_at || new Date().toISOString(),
  };
}

/**
 * Reject a candidate TMDb identity:
 * POST /api/admin/provider-content/{id}/candidates/{tmdb_id}/reject/
 *
 * Backend returns:
 * { status: "rejected", candidate: {...}, audit?: {...} }
 */
export async function rejectCandidate(
  id: string | number,
  tmdbId: number,
  payload: ReviewRejectPayload
): Promise<NormalizedMutationResponse> {
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
        headers: getMutationHeaders(),
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

  const data = await res.json();
  const returnedTmdbId = data.candidate?.tmdb_id ?? data.tmdb_id ?? tmdbId;

  return {
    status: data.status || "rejected",
    tmdb_id: returnedTmdbId,
    provider_content_id: id,
    mapping_updated: false,
    rejection_reason: trimmedReason,
    candidate_title: data.candidate?.title,
    reviewed_at: data.audit?.created_at || new Date().toISOString(),
  };
}
