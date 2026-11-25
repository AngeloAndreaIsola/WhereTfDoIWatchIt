# Issue #97 Stage D bounded runner

**Ready for implementation and authorization review. Stage D execution awaits a separate approval.** The proposed run spends up to 12 HTTP reservations on one NL title search per reviewed identity. It has zero retry slots, a burst of one, one request in flight, and a gap of at least five seconds after each response. The runner also enforces stricter reviewed receipt and upstream pacing. [Stage D planning and cohort](../audits/issue97/stage-d-readiness.md)

## Review scope

| Batch | Exact typed scope | Review point |
| --- | --- | --- |
| 1 | Movie 238, series 1396 | Review The Godfather and Breaking Bad before signing batch 2 |
| 2 | Movies 496243, 603, 619264, 840430, 565310 | Review all five checkpoints before signing batch 3 |
| 3 | Movie 116149 and series 70523, 66732, 2316, 1438 | Reconcile all 12 slots and classify observations |

The policy admits only POST to `https://apis.justwatch.com/graphql` using the pinned one-page `GetSearchTitles` document for market `NL`. The canonical policy SHA-256 is `d7308681ae99376352eeedf9d271e63fa2a6e02e63a6e801d7e9b6d749fb8727`. Coherence (movie TMDb 220289) awaits a year decision. The five optional `GetTitleNode` calls for series 1396, 70523, 66732, 2316 and 1438 belong to a future authorization phase after title review. That phase would require a separately reviewed implementation and immutable policy linked to the existing run, with its own operation slots and a cumulative spend reconciliation. The fixed two-title experiment in #86 retains its existing policy, digest and six-attempt ceiling. [Pinned manifest](../../backend/availability/services/issue97_stage_d_titles.json) · [Fixed experiment runbook](stage2g_bounded_availability_experiment.md)

## Preflight on the intended disposable target

1. Verify the reviewed commit and the database connection out of band. Use a disposable PostgreSQL database on a dedicated application target with no refresh processes, queue producers, overlapping tasks or other consumers of the upstream grant. Keep the database for all three batches and retain its fingerprint across resumes. Database identity is derived from the PostgreSQL cluster system identifier and the database OID. Process and grant exclusivity require operator verification outside PostgreSQL.
2. Verify the applied migration and graph on that target. The following commands use the backend's configured database. Run them from `backend/` with the target's reviewed connection settings.

   ```sh
   python manage.py makemigrations --check --dry-run
   python manage.py migrate --check
   python manage.py showmigrations availability
   AVAILABILITY_STAGE_D_DISPOSABLE=1 python manage.py issue97_stage_d --batch 1
   ```

   Each preflight performs database reads. Read the JSON `readiness` and `blockers` fields. Review the database fingerprint, endpoint, document and manifest digests, typed scopes, empty or reconciled ledgers, inactive locks, and empty queue. A ready report includes the exact `authorization_scope` and the latest allowable expiry. Repeat for the requested batch after inspecting the prior checkpoints.
3. Confirm the exact enabled JustWatch and NL rows and the 12 matching typed `Movie` rows. Inventory all prior JustWatch traffic on the dedicated target and the upstream allowance. Reconcile any existing spend on the same grant with the receipt ledger. For prior spend, include `prior_receipt_idle_since` in the evidence JSON with a reviewed timezone-aware time at or after the last response and reservation. This is the initial pacing reference. Another receipt's spend, an existing legacy experiment's spend, an in-flight reservation or an unfinished refresh blocks the runner. Once the first Stage D reservation claims the database, the ordinary JustWatch transport and fixed experiment are excluded on that target across batches and restarts.
4. Review the following evidence and provide it through the approved runtime configuration only after approval. Keep these records and the signing key in approved custody.

   | Required evidence | Exact inputs |
   | --- | --- |
   | Current runtime permission | Enabled JustWatch source receipt, exact endpoint, `countries: ["NL"]`, `operations: ["search"]`, reviewed reference and reviewer, validity window, finite request ceiling and minimum interval |
   | Independent access permission | `AVAILABILITY_STAGE_D_EVIDENCE` with the actual `database_identity`, matching `grant_reference` and `exclusive_grant_use: true`. Its `access_permission` record must carry `status: "authorized"`, matching grant `evidence_reference`, `reviewed_by`, `checked_at`, `valid_from`, `valid_until`, exact `endpoint`, `country: "NL"`, `operations: ["GetSearchTitles"]`, `document_sha256`, all 12 ordered `scope_ids`, a `maximum_requests` of at least 12 and `maximum_retries: 0`. |
   | Current quota | `quota` record with reviewer, evidence reference, check time and expiry. Use `status: "available"` with `window_id`, actual `remaining` and shared receipt `spent_at_review` for a metered grant. Supply explicit evidence for `status: "unmetered_public"` if applicable. The receipt and the 12-slot local cap still apply. |
   | Current rate policy | `rate_policy` record with `reviewed_by`, `evidence_reference`, `checked_at`, `valid_until`, `maximum_in_flight`, `max_burst` and `minimum_interval_seconds`. The upstream policy must allow a burst and one in-flight request. The effective interval is the highest of the manifest, receipt and reviewed upstream minima. |
   | Approved evidence custody | Decide who can inspect restricted captures, how raw bytes will be retained if required, and where they can be reviewed. The runner stores a digest of the received body and a sanitized observation on the same disposable database. |
   | Signed approval for each batch | Require a separate reviewer to sign the exact preflight `authorization_scope` plus timezone-aware `expires_at`. The token is the unpadded Base64url JSON payload, a dot, and the hex HMAC-SHA256 of the encoded payload. `AVAILABILITY_STAGE_D_AUTH_KEY` must provide at least 32 bytes of secret material. All three evidence records must have been checked within the preceding hour. Expiry is bounded by every applicable receipt, evidence and freshness deadline. |

5. Review the first batch and each later batch before supplying a subsequent token. Compare the attempt ordinals, raw response digests and sanitized observation digests. Confirm the typed match, NL scope, offer classification and shared spend. The later token binds the ordered prior checkpoint evidence. For a separately approved run, the command requires the batch, a protected token file via `--execute-token-file`, the exact fingerprint via `--confirm-database-identity`, and the disposable attestation.

The actual runtime receipt, the independent exact permission, quota/rate evidence, exclusive grant confirmation, approved capture custody, signed batch approvals, and the real disposable target fingerprint/preflight remain outstanding. This work used synthetic receipts and mock responses on disposable PostgreSQL. Historical Notte invocation counts establish neither HTTP transmissions nor unused allowance. [Evidence accounting](../audits/issue97/stage-d-readiness.md)

## Reconciliation and recovery

- A committed reservation debits both the 12-slot experiment and the shared receipt in one transaction. A second event records handoff immediately before calling the HTTP client. The response event is written with the outcome and receipt state in a single transaction. A handoff is local evidence of reaching the send boundary. A correlated HTTP response is the basis for confirmed transmission. A spent reservation without a response is reported as unknown, including an interruption before handoff. The report also counts handoffs without confirmation.
- Stop before the next send on expired or changed evidence, lock contention, queue activity, mismatched state or exhausted allowance. A denial, redirect, 429, challenge, non-success response, malformed response, or typed identity or market mismatch records the known outcome and blocks continuation. The 429 cooldown is at least ten minutes and honors a longer `Retry-After`. Redirects, adapter retries, implicit season lookups, and source fallback are disabled.
- A validated title observation requires matching TMDb ID, content type, year and reviewed labels, with any supplied market or localized path consistent with NL. The fixed NL request supplies the market when the response omits it. Exact titles with no mapped offers record availability as unknown. Source offer variants from the single returned search page and comparable service/class presence are separate. Unknown mappings and malformed offer rows are reported for review. The projection excludes raw provider URLs and is same-source evidence. [Offer-variant draft #198](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/198)
- If an intact validated response exists and the process stopped before checkpointing, review it and use `issue97_stage_d --recover-checkpoint --confirm-database-identity <fingerprint>` with the disposable attestation. Recovery performs only the local checkpoint write and preserves the original observation timestamp. Resume with the same database and a current matching token. A spent unknown or known rejection remains blocked for review. A fresh token, new receipt reference, different database, or later batch cannot erase an unresolved reservation.

## Migration integration

`0019_stage_d_run_ledger` depends on `0017_justwatch_request_budget` so this PR can be tested on the current master. #198 independently adds `0018_offerprice_variants` with the same parent. Distinct migration names preserve both changes. When the branches are combined, add an empty merge migration (for example, `0020`) that explicitly depends on both leaves and verify the resulting graph before applying it to any shared target. The Stage D migration depends transitively on the append-only trigger function supplied by `0015`. [#198](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/198)
