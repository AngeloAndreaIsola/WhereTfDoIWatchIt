# MANUAL ACTION REQUIRED — Antigravity Handoff (Phase 4)

## Status

This handoff is for **manual execution by the user**. Antigravity cannot be assumed
controllable from the Hermes environment. Do NOT treat this as launched.

## Baseline

- Repository: https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt
- Integration branch: `feature/scrapres-improvedment`
- Integration HEAD: `0044ce18dfb6b5be8f7e124effa7d9ce4e041db0`
- Audit command commit (NOT published to origin): `4210ee8` (in worktree
  `.worktrees/audit-mapping`, branch `feature/audit-mapping`)
- Follow-up audit commit: `7affa8b` ("refine audit: classify unmapped via local matcher")

## Recommended separate branch/worktree

Create a NEW worktree/branch, e.g. `phase4-antigravity-audit`, branched from
`0044ce1`. Do NOT edit Jules-owned files (TMDb discovery, RaiPlay evidence
fetching, mapping correction impact manifest).

## Scope (read-only, no live HTTP, no collector edits, no legacy retirement)

1. RaiPlay single/multi vs TMDb media-type classification audit.
   - For each ProviderContent row, compare `content_type` (movie/series/season/episode)
     against the linked ContentMapping `content_type` and target Movie `content_type`.
   - Flag mismatches; do not mutate.
2. Original-title, translated-title and release-year evidence.
   - Inspect ProviderContent `title`, `metadata`, `release_date`; compare against
     Movie `title`/`original_title`/`year`. Report mismatches and missing data.
3. Subtitle/audio metadata limitations.
   - Document what OfferEvidence/OfferPrice and ProviderContent metadata currently
     capture vs. what ranking requires (audio/subtitle language, quality).
4. Historical legacy RaiPlay reconciliation against canonical ProviderContent.
   - Cross-reference ScrapedContentMapping rows with ProviderContent by title/year;
     report overlaps/gaps. No deletion.
5. Investigation of the 25 mapped-but-no-offer records.
   - For each ContentMapping without a corresponding Offer, report provider_content_id,
     tmdb_id, match_method, confidence, and whether ProviderContent is currently available.
6. Evidence-backed recommendations and regression fixtures.
   - Produce a test-plan document; no code changes to production paths.

## Constraints

- No live HTTP requests.
- No collector edits.
- No legacy retirement.
- No migrations, no data mutation.
- Avoid overlapping implementation files with Jules A/B/C.
- Mark all output as MANUAL ACTION REQUIRED.

## Deliverables

- A markdown report under `docs/` on the new branch.
- Optional synthetic fixtures in `backend/.../tests/fixtures/` (new files only).
