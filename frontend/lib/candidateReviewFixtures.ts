/**
 * Isolated mock fixtures for TMDb Candidate Review interface tests and local verification.
 * Adheres directly to PR #50 Wave 2A backend shapes:
 * - Queue response: { items, page, pages, total_items, summary }
 * - Detail response: flat ProviderContent fields { id, provider, title, year, existing_mapping, offer_count, active_offer_count, candidates }
 * - Accept response: { status: "accepted"|"already_accepted", mapping, candidate, movie, audit }
 * - Reject response: { status: "rejected", candidate, audit }
 */

export const mockRawQueueItemAmbiguous = {
  id: "pc-101",
  provider: "tubi",
  provider_slug: "tubi",
  provider_name: "Tubi",
  raw_id: "pinocchio-2022-stream",
  provider_content_id: "pinocchio-2022-stream",
  title: "Pinocchio",
  native_title: "Pinocchio",
  content_type: "movie",
  year: 2022,
  release_year: 2022,
  url: "https://tubitv.com/movies/12345/pinocchio",
  provider_url: "https://tubitv.com/movies/12345/pinocchio",
  existing_mapping: null,
  offer_count: 1,
  active_offer_count: 1,
  candidate_count: 3,
  top_confidence: 0.95,
  status: "ambiguous",
  resolution_category: "ambiguous",
  last_seen: "2025-05-10T12:00:00Z",
  revision: null,
};

export const mockRawQueueItemConflict = {
  id: "pc-102",
  provider: "raiplay",
  provider_slug: "raiplay",
  provider_name: "RaiPlay",
  raw_id: "the-thing-classic",
  provider_content_id: "the-thing-classic",
  title: "The Thing",
  native_title: "The Thing",
  content_type: "movie",
  year: 1982,
  release_year: 1982,
  url: "https://www.raiplay.it/programmi/thething",
  provider_url: "https://www.raiplay.it/programmi/thething",
  existing_mapping: {
    id: 12,
    tmdb_id: 60935,
    title: "The Thing",
    year: 2011,
    content_type: "movie",
    match_method: "automated_title_search",
    confidence: 0.55,
    is_manual: false,
    revision: 3,
  },
  offer_count: 1,
  active_offer_count: 1,
  candidate_count: 2,
  top_confidence: 0.98,
  status: "conflict",
  resolution_category: "conflict",
  last_seen: "2025-05-11T09:30:00Z",
  revision: 3,
};

export const mockRawQueueItemManualConflict = {
  id: "pc-103",
  provider: "prime_video",
  provider_slug: "prime_video",
  provider_name: "Prime Video",
  raw_id: "dune-part-1-amzn",
  provider_content_id: "dune-part-1-amzn",
  title: "Dune",
  native_title: "Dune",
  content_type: "movie",
  year: 2021,
  release_year: 2021,
  url: "https://www.primevideo.com/detail/0DUNE2021",
  provider_url: "https://www.primevideo.com/detail/0DUNE2021",
  existing_mapping: null,
  offer_count: 2,
  active_offer_count: 2,
  candidate_count: 2,
  top_confidence: 0.92,
  status: "conflict",
  resolution_category: "conflict",
  last_seen: "2025-05-11T14:15:00Z",
  revision: null,
};

export const mockRawQueueItemMappedNoOffer = {
  id: "pc-104",
  provider: "bbc_iplayer",
  provider_slug: "bbc_iplayer",
  provider_name: "BBC iPlayer",
  raw_id: "dr-who-special",
  provider_content_id: "dr-who-special",
  title: "Doctor Who: The Day of the Doctor",
  native_title: "Doctor Who: The Day of the Doctor",
  content_type: "movie",
  year: 2013,
  release_year: 2013,
  url: "https://www.bbc.co.uk/iplayer/episode/drwho",
  provider_url: "https://www.bbc.co.uk/iplayer/episode/drwho",
  existing_mapping: {
    id: 88,
    tmdb_id: 239770,
    title: "The Day of the Doctor",
    year: 2013,
    content_type: "movie",
    match_method: "manual_curation",
    confidence: 1.0,
    is_manual: true,
    revision: 1,
  },
  offer_count: 0,
  active_offer_count: 0,
  candidate_count: 1,
  top_confidence: 1.0,
  status: "unique_candidate",
  resolution_category: "unique_candidate",
  last_seen: "2025-05-12T08:00:00Z",
  revision: 1,
};

// PR #50 Queue responses: { items: [...], page, pages, total_items, summary }
export const mockBackendQueueResponse = {
  items: [
    mockRawQueueItemAmbiguous,
    mockRawQueueItemConflict,
    mockRawQueueItemManualConflict,
    mockRawQueueItemMappedNoOffer,
  ],
  page: 1,
  pages: 1,
  total_items: 4,
  summary: {
    total_unresolved: 4,
    ambiguous: 1,
    conflict: 2,
  },
};

export const mockBackendQueueEmpty = {
  items: [],
  page: 1,
  pages: 0,
  total_items: 0,
  summary: {
    total_unresolved: 0,
    ambiguous: 0,
    conflict: 0,
  },
};

// PR #50 Flat detail response for pc-101 (Pinocchio)
export const mockBackendDetailAmbiguous = {
  ...mockRawQueueItemAmbiguous,
  candidates: [
    {
      id: 1,
      tmdb_id: 532639,
      title: "Pinocchio",
      original_title: "Pinocchio",
      content_type: "movie",
      year: 2022,
      confidence_score: 0.95,
      scoring_evidence: {
        content_title: "Pinocchio",
        content_type: "movie",
        candidate_title: "Pinocchio",
        title_relation: "exact",
        year_match: "exact",
        supporting_evidence: [
          "Exact title match ('Pinocchio')",
          "Exact release year match (2022)",
        ],
        conflicting_evidence: [],
        warnings: ["Ambiguity Warning: Another candidate has an identical high confidence score (0.95)."],
      },
      status: "ambiguous",
      review_state: "ambiguous",
      resolution_category: "ambiguous",
    },
    {
      id: 2,
      tmdb_id: 789100,
      title: "Pinocchio: A True Story",
      original_title: "Pinocchio: A True Story",
      content_type: "movie",
      year: 2022,
      confidence_score: 0.75,
      scoring_evidence: {
        title_relation: "contains",
        year_match: "exact",
        supporting_evidence: [
          "Contains title 'Pinocchio'",
          "Exact release year match (2022)",
        ],
        conflicting_evidence: ["Extended subtitle in candidate title"],
      },
      status: "ambiguous",
      review_state: "ambiguous",
      resolution_category: "ambiguous",
    },
    {
      id: 3,
      tmdb_id: 511817,
      title: "Guillermo del Toro's Pinocchio",
      original_title: "Guillermo del Toro's Pinocchio",
      content_type: "movie",
      year: 2022,
      confidence_score: 0.8,
      scoring_evidence: {
        title_relation: "contains",
        year_match: "exact",
        supporting_evidence: [
          "Contains title 'Pinocchio'",
          "Exact release year match (2022)",
        ],
      },
      status: "ambiguous",
      review_state: "ambiguous",
      resolution_category: "ambiguous",
    },
  ],
};

// PR #50 Flat detail response for pc-102 (The Thing)
export const mockBackendDetailConflict = {
  ...mockRawQueueItemConflict,
  candidates: [
    {
      id: 11,
      tmdb_id: 1091,
      title: "The Thing",
      original_title: "The Thing",
      content_type: "movie",
      year: 1982,
      confidence_score: 0.98,
      scoring_evidence: {
        title_relation: "exact",
        year_match: "exact",
        supporting_evidence: [
          "Exact title match ('The Thing')",
          "Exact year match (1982)",
        ],
        warnings: [
          "CORRECTION_AUTHORIZATION_REQUIRED: Existing mapping points to TMDb 60935 (2011 remake). Remapping will override existing mapping.",
        ],
      },
      status: "pending",
      review_state: "pending",
      resolution_category: "conflict",
    },
    {
      id: 12,
      tmdb_id: 60935,
      title: "The Thing",
      original_title: "The Thing",
      content_type: "movie",
      year: 2011,
      confidence_score: 0.55,
      scoring_evidence: {
        title_relation: "exact",
        year_match: "conflict",
        supporting_evidence: ["Exact title match"],
        conflicting_evidence: ["Severe release year mismatch (1982 vs 2011)"],
        warnings: ["MOVIE_TYPE_CONFLICT: Year Conflict: candidate year 2011 diverges from provider year 1982."],
      },
      status: "rejected",
      review_state: "rejected",
      resolution_category: "conflict",
      rejection_reason: "Release year 2011 does not match provider broadcast of the 1982 classic.",
      legacy_unverified: true,
      is_human_reviewed: false,
    },
  ],
};

// PR #50 Flat detail response for pc-103 (Dune - Manual conflict)
export const mockBackendDetailManualConflict = {
  ...mockRawQueueItemManualConflict,
  candidates: [
    {
      id: 21,
      tmdb_id: 438631,
      title: "Dune",
      original_title: "Dune: Part One",
      content_type: "movie",
      year: 2021,
      confidence_score: 0.92,
      scoring_evidence: {
        title_relation: "exact",
        year_match: "exact",
        supporting_evidence: [
          "Exact title match",
          "Exact release year match (2021)",
        ],
        conflicting_evidence: [
          "Existing MANUAL mapping already claims this TMDb ID under a different provider content record",
        ],
      },
      status: "conflict",
      review_state: "conflict",
      resolution_category: "conflict",
      competing_manual_mapping: {
        mapping_id: 55,
        provider_slug: "prime_video",
        provider_content_id: "dune-4k-uhd",
        detail: "Another manual mapping 55 exists for provider prime_video:dune-4k-uhd claiming proposed TMDb 438631.",
      },
    },
    {
      id: 22,
      tmdb_id: 841,
      title: "Dune",
      original_title: "Dune",
      content_type: "movie",
      year: 1984,
      confidence_score: 0.5,
      scoring_evidence: {
        title_relation: "exact",
        year_match: "conflict",
        supporting_evidence: ["Exact title match"],
        conflicting_evidence: ["Year conflict (1984 vs 2021)"],
      },
      status: "rejected",
      review_state: "rejected",
      resolution_category: "conflict",
      rejection_reason: "David Lynch 1984 version, not the 2021 Denis Villeneuve film.",
    },
  ],
};

// PR #50 Flat detail response for pc-104 (Mapped with 0 offers)
export const mockBackendDetailMappedNoOffer = {
  ...mockRawQueueItemMappedNoOffer,
  candidates: [],
};
