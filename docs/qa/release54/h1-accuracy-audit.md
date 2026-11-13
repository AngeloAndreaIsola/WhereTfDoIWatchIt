# H1 — NL/IT/JP Streaming Availability Accuracy Audit

**Read-only audit. No code or DB mutations performed.**

## 1. Inspected baseline

- Git SHA: `45bfe85bb85cd375bdd290afe43c724c75e2f0e1` (branch `feature/scrapres-improvedment`)
- DB: live local Postgres (docker-compose dev). Counts: movies=828, providers=92, subscriptions=15, availability=1541, languageinfo=3176, preference=1.
- Preference: user_id=1, current_country=NL, vpn_enabled=True, candidate_countries=[].
- Availability by country (NL/IT/JP): NL=32 rows, IT=27, JP=27 (86 total rows across the three targets).
- No migrations run, no DB writes, no paid collectors invoked, no quotas activated.

## 2. Data sources and limitations

- Local Postgres aggregate counts and provider distribution only. Per-title ground-truth verification against provider pages was NOT performed (would require paid/external calls and network verification outside scope).
- Entitlement findings derived from code review of `access.py` `_match` fuzzy matching and `build_user_access`.
- Freshness findings derived from `Availability.last_checked_at` + model fields; expiry semantics reviewed in code.
- Language findings derived from `score.py` / `preferences.py` and access classifier.
- Quota findings derived from `source_policy.py` (gate OFF by default).

## 3. Sample matrix (aggregate, not per-title fabricated)

Representative distribution observed: NL/IT/JP availability rows are dominated by major aggregators (netflix, prime, disney, apple, hbo, skyshowtime, mubi). Provider slugs in DB include viaplay-like and prime-channel style names; exact alias collisions could not be ground-truthed without external pages.

Observation buckets covered by code paths:
- Included subscription offers: access.py SUBSCRIPTION access type + entitles()
- Free/ads: FREE/FREE_WITH_ADS
- Add-on channels: addon_parent_slug handling
- Rental/purchase: RENT/BUY excluded from qualifying by design
- Stale-positive: Availability.last_checked_at + refresh state
- Missing language metadata: is_language_match defaults True in ClassifiedOffer? Actually score_offer handles missing → UNKNOWN; access classifier reason "Language information unknown"

## 4. Entitlement findings (J1)

P0: `UserAccess._match` uses substring containment with len>=3, causing false positives:
- "viaplay" matches "viaplay_sports" (and vice versa) → Viaplay Sports entitlement incorrectly granted if user holds Viaplay.
- "prime" matches "prime_video" and any "prime_*" channel slug → base subscription conflated with channels.
- Name-only rows bucketed into `included` regardless of access_mode (line 243) → loose matching.
- Auto-counted BVOD/AVOD/FAST/SCRAPER providers all added to free/included without user declaration → may over-grant but intended for free catalogues.

Recommendation: exact slug equality for entitlement; alias map explicit. J1 owns fix.

## 5. Freshness findings (D1)

- `_empty_result` treats CHECKED refresh state as UNAVAILABLE — conflates "checked, no offers" with confirmed negative. Missing results treated as definitive negatives (P1).
- Stale-positive offers: no expiry field on Availability; only last_checked_at. Last-known offers can be displayed as current without staleness threshold enforcement in read path (availability_reads to verify). (P1)
- Conflicting evidence across sources: resolver merges; no conflict marker surfaced. (P2)
- Unknown vs negative not distinguished in UI labels beyond refresh_state.

## 6. Quota findings (J2)

- `AVAILABILITY_CONSULT_METERED_ONLY_WHEN_UNRESOLVED` defaults OFF (env "0"). Gate inactive. Metered aggregator (streaming_availability) runs for all titles when applicable.
- Counterfactual function available; no measured savings because gate off. Estimated savings not computed (no run of counterfactual over title states) — could be computed read-only via availability_report.title_source_report but not executed here. (P2 to compute)
- Watchmode finite budget: role BROAD_AGGREGATOR (unmetered by policy) — verify Watchmode rate limits configured separately; has_configured_rate_limit distinction noted.

## 7. JustWatch mapping findings (J3)

- Country-aware ExternalProviderMapping (migration 0035): aggregator ids per market. Risk: unmapped provider skipped? Need regression tests for unmapped skip, no auto-subscribe, ambiguity fail-closed, partial pagination. J3 owns.
- Title/year ambiguity: TMDb search matching; manual confirm step exists. Risk of wrong tmdb_id for similar titles. (P1)
- Discovered titles entering watchlists: verify importer does not auto-add. (P2, tests)

## 8. Language-overlap correctness

- V2 viewers rule IS implemented in score.py (evaluate_language_rule "viewers" type: every viewer must have at least one language in offer audio∪subs). Good.
- MISSING METADATA BUG (P1): when offer has no audio/subs, offer_all_langs is empty, so v_langs.intersection(empty) is empty → is_match=False. Thus a title with unknown language metadata is treated as a definite mismatch and excluded, even though SPEC says missing metadata must remain UNKNOWN, not a definite mismatch. Access classifier reason text says "Language information unknown" but verdict still excludes. Fix: if no language info, is_match should be None/unknown, not False; and access classifier should not exclude on unknown.
- score_tuple priority order in code: spec says audio → subs → quality, but code uses (match_score, quality_rank, audio_rank, sub_rank) for viewers rule vs (match_score, audio_rank, sub_rank, quality_rank) for flat. Inconsistent ordering; minor. (P2)
- Two-viewer profiles supported via language_rule JSON; single Preference row is the container. OK.

## 9. P0 release blockers

1. Entitlement false positives via fuzzy substring matching (Viaplay/Viaplay Sports, Prime base/channels) — access.py `_match`. Blocks release correctness.
2. Missing language metadata treated as definite mismatch instead of UNKNOWN — score.py evaluate_language_rule. Blocks spec compliance.

## 10. Recommended regression tests

- access: exact-slug entitlement, viaplay vs viaplay_sports, prime base vs channels, addon requires base.
- score: missing metadata → not definite mismatch (unknown).
- freshness: checked-with-no-offers → unavailable vs unknown distinction.
- mapping: unmapped skip, ambiguity fail-closed, partial pagination.

## 11. Unsupported conclusions / not performed

- No per-title provider-page ground truth verified (no paid/external calls).
- No counterfactual quota savings computed (gate off; function available but not run over title states).
- No migration or DB mutation performed.

