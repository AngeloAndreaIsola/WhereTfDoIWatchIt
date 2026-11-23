# Availability verification on November 23, 2026

**Draft ready for review. Production accuracy remains unproven.** All eight priority NL title/provider/class pairs have independent first-party evidence of an **advertised** offer. Across the expanded panel, the frozen application clone returned zero current offers. Its two free-source controls exposed a trailer-only listing and a link to the wrong edition of The Office. Keep #97 open and #103 on hold. [Acceptance checklist](issue97-acceptance-checklist.md) · [Issue #97](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/97) · [Issue #103](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/103)

## Sample, clocks and baseline

The original Stage 2B sample contains 30 title/market cases. Italy had nine historical advertised observations and six unverified cases. The Netherlands had five and ten, respectively. The logged observations date to November 12–13. Some are search results, some describe several providers/classes without pairing them, and the historic Dune search encountered the 1984 film. Each original row has an individually linked disposition in the matrix. The replay uses corrected Coherence movie 220289 in both markets and retains 300602 in the historical-reference column. Italy’s The Longest Yard has neither a typed local Movie nor a reviewed direct-ID response. [Original sample](live_sample.csv) · [Case and offer matrix](issue97-provider-reconciliation.csv)

The reference hour is **15:00–16:00 UTC on November 23**. The 34-case panel includes all 30 original cases plus four controls chosen from existing real clone rows. UK Office probes a free listing with an ambiguous edition, Canadian Farewell probes a free film, Australian Dune probes a recorded expiry and US Office probes another subscription storefront for the same edition. These additions reuse existing titles. There are 16 distinct typed titles, 23 movie cases and 11 series cases. Selection favors identifiable stale overlaps and failure modes. The results apply only to these cases.

| Market | Title cases | Raw clone rows | Reconciled last-known offers returned | Current offers returned | First-party result |
|---|---:|---:|---:|---:|---|
| NL | 15 | 25 | 14 | 0 | 13 advertised provider/class pairs across seven titles and three provider websites |
| IT | 15 | 5 | 4 | 0 | Apple’s Italian Inception page identifies the film without a rental offer. Rakuten Dune returned a client shell. Netflix redirected The Platform to NL. |
| GB | 1 | 3 | 3 | 0 | Free BBC listing links the UK edition to the US title in last-known history. |
| CA | 1 | 1 | 1 | 0 | CBC advertises a 2m14s Farewell trailer. Free/basic metadata cannot establish access to the 100-minute feature. |
| AU | 1 | 2 | 1 | 0 | HBO Max identifies Dune and an Australian storefront. An unavailable-in-your-country template and plan marketing cannot adjudicate AU rights from NL. |
| US | 1 | 1 | 1 | 0 | Peacock returned a regional unavailable redirect. |

The app baseline is the owner-authorized November 22 disposable PostgreSQL 18 clone, already migrated to tracker 0061. It came from a dump with reported digest `ccfbd9291e570a5479cb77e2d60a8afe271953aaf5ef7f90d4905c806eeb2794`. The run read only the clone. It did not refresh the live catalogue or obtain a new serving snapshot. The 37 stored rows have October 2–24 last-seen times. All 37 have stale evidence and a positive lifecycle flag. The 34-case audit found all seven represented source slugs enabled in this snapshot. The registered public-domain source had no persisted row. Configuration and availability in the serving system remain unknown. [Source counts and panel results](issue97-reconciliation-summary.json)

The clean starting checkout and remote master were `752dc23fd5beb2699612c2a2ee29a1fee0f4824e`. PR #186 corrected the NL cohort and guarded local identity, #187 supplied the per-offer comparator, and #188 supplied direct-ID manifest fallback. This review adds two typed aliases, an opt-in NL class/ID/host map, a repeatable display measurement and accurate provenance in the audit command’s generated reports. The original report generator printed a fixed historical SHA, a Stage 2C reproduction command and conclusions about missing Dune rows even on populated clones. Now its summary records the supplied revision, input digests and options used, and its discrepancy count comes from the current run. Historical reports are preserved. [PR #186](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/186) · [PR #187](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/187) · [PR #188](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/188)

## Public evidence and display outcomes

Full SHA-256 checks matched **16/16** original HTML and PNG files and the prior intake and decision files. The eight UTC intake stamps match the previous manifest. On the original disk, each recorded observation precedes the HTML save and the subsequent PNG save by less than 15 seconds. These are distinct clocks. The manifest retains capture stamps and actual file creation/modification times. All eight screenshots were inspected with local OCR as a secondary check. Consent overlays obscure some details, so class, identity and territory decisions use the title-specific HTML. The request ledger records new document reads, redirects, response digests and UTC start/end times. [Manifest](issue97-evidence-manifest.json)

| Priority NL pair | New review | Canonical independent candidate in the November 22 clone |
|---|---|---|
| Breaking Bad · Netflix subscription | Title ID 70143836, Sony link, verified series identity and title-specific plan flag | 5486 / 778, JustWatch / Streaming Availability |
| Dark · Netflix subscription | 80100172, TMDb-linked show, creators and title plan flag | 5483, JustWatch. No typed Movie for display. |
| Stranger Things · Netflix subscription | 80057281, TMDb-linked show, creators and title plan flag | 5473, JustWatch. No typed Movie for display. |
| The Platform · Netflix subscription | 81128579, original title El hoyo and exact TMDb homepage | 5951, JustWatch. No typed Movie for display. |
| Inception · Netflix subscription | New anonymous direct page 70131314. The earlier search login form remains rejected. | 5481, JustWatch. Exclude Watchmode 10166 from independent Watchmode reconciliation. |
| Inception · Prime Video subscription | Exact movie GTI, NL territory, movie `isPrime=true`, and its own base-Prime action distinct from the channel | 10149, JustWatch |
| Dune · Rakuten rent | Original €2.99 title rental reconfirmed from preserved NL capture | 10572, JustWatch |
| The Holdovers · Rakuten rent | Original €3.99 title rental reconfirmed from preserved NL capture | 10380, JustWatch |

Seven of eight original pages now support their proposed class. The new direct Netflix Inception page supplies the eighth priority pair. Five additional pairs have title-specific first-party evidence on those same pages: Dune and Holdovers purchases (€9.99 and €7.99), Inception Prime rental and purchase (€2.99 and €4.99), and its separately sold HBO Max channel. None of the five has an exact matching stored clone row for the title, market, provider and class. No signup, account entitlement, checkout or playback was tested. [Accepted observations](issue97-accepted-observations.csv) · [Rejected observations](issue97-rejected-observations.csv) · [Identity decisions](issue97-identity-decisions.md)

**Displayed catalogue coverage.** The real legacy availability view executed in-process on the disposable clone. Preferences were set in memory for the case market and personal history queries returned empty querysets. Canonical Movie and Offer queries, reconciliation, ranking and response serialization used real clone data. The catalogue status uses the same watchability functions as the application and an explicitly empty subscription selection. Of 34 cases, 18 returned HTTP 200 and 16 returned 404. Twelve cases showed needs-recheck and six showed unknown. The view exposed 24 last-known offers across the sample and zero current offers. Its missing-title path can return 404 even when an old canonical offer is stored for the typed TMDb ID. No browser or serving UI was measured. [Read-path replay](../../../scripts/audit_issue97_sample.py) · [Tracker read adapter](../../../backend/tracker/services/availability_reads.py)

**Sample false negatives and false positives depend on the claim.** For retrieval of a publicly advertised listing in the clone’s current-offer array, zero of the **13 known-positive pairs** appeared during the reference hour. Ten omissions came from HTTP 200 responses and three from missing typed Movie rows. Counting a 404 as a retrieval miss gives **13 misses / 13 advertised positives**, including 8/8 priority pairs. The clone communicated uncertainty or missing data. It made **zero explicit negative assertions** about those titles. There were no eligible independently confirmed negative pairs and no current positive assertions to score as false positives. For source-fresh canonical accuracy there are **0 eligible same-window independent pairs, 0 classified agreements/conflicts, 0 classified FP/FN and 13/13 unresolved positives**. Rates are undefined. No entitlement or playback denominator exists. The response-read clock and the stored observation clock must stay separate. [Machine-readable counts](issue97-reconciliation-summary.json)

**Concrete data defect.** In a read-only replay, select series 2316, GB, source bbc_iplayer, offer 3504. Its public URL identifies programme b00jd68z. The response for US Office includes bbc_iplayer/free in last-known history while the current array is empty. The BBC describes Wernham Hogg in Slough, and NBC identifies Dunder Mifflin in Scranton. This is one confirmed wrong provider identity in one sampled BBC history link. Remediation should quarantine/review that exact mapping and its season/episode descendants, record the replacement identity from primary evidence, and test that neither edition inherits the other’s offers. No stored row was edited. The CBC trailer is a needs-recheck case. The current public page cannot establish what the feature offered on October 24. [BBC defect #190](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/190) · [Identity integrity #112](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/112) · [BBC listing](https://www.bbc.co.uk/iplayer/episodes/b00jd68z/the-office) · [CBC listing](https://gem.cbc.ca/the-farewell) · [CBC territory](https://cbchelp.cbc.ca/hc/en-ca/articles/360045250014-New-to-CBC-Gem-Technical-Support)

**Expiry and fresh collection.** The AU Dune JustWatch row 6923 has a stored end date of March 31, 2027 UTC and October last-seen evidence. The read path withholds an expiry sourced only from JustWatch because its end-date semantics are unverified. A future end date never refreshes the row. The existing stale/expired/ambiguous fixture suite exercised the display distinction. For Inception the native Prime adapter parsed the actual preserved page using its territory check, detail parser and offer projection. It emitted subscription, rent and buy for movie 0FFJSDLK6HUWC5T8XJUQODMFJ3 in NL. Those three outputs use the same first-party bytes as the external evidence. No independent verifier or persisted refresh resulted. Prime and CBC use catalogue-driven collectors. Netflix/Rakuten have no equivalent in-scope targeted first-party refresh. The metered aggregators and JustWatch preflight were unavailable for refresh under this authorization. [Prime adapter](../../../backend/availability/sources/prime_video.py) · [Freshness tests](../../../backend/tracker/tests/test_stale_offer_semantics.py) · [Expiry gate #175](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/175)

The Django audit was also run separately on all 13 accepted pairs. Seven provider/class batches respect its one-external-row-per-typed-title/market interface. It returned **5 APP_DATA_STALE, 3 APP_DATA_MISSING and 5 INSUFFICIENT_APP_EVIDENCE**. The three missing typed Movie rows still have raw stale offers. Each first-party matrix row records its actual Django result. [Matrix](issue97-provider-reconciliation.csv)

## Historical Watchmode reconciliation

The older November 14 Stage 2D NL inventory and its 300602 Coherence row remain historical evidence. A similarly sized later inventory cannot repair or refresh the earlier one. [Stage 2D](../stage2d/database_coverage.md)

The pinned November 22 pilot contained **166** NL rows (26 sub, 66 rent, 74 buy). The corrected Coherence lookup has zero NL rows. The immutable 15-case application export contains 25 stale raw offers, zero verified offers and four Watchmode-origin rows. Replaying the historical identity and provider decisions produced 13 accepted identities and two blocked, with 127 unmapped rows, 23 missing exact offers, ten stale overlaps, and six identity-unverified rows.

Using the reviewed code and fresh audit export **of that same clone**, 14/15 identities pass. Coherence remains blocked. There are **23 stale overlap rows / 17 distinct canonical pairs**, **125 missing exact app offer rows**, and **18 unmapped meJane rows**. Three overlapping external rows have mixed app source provenance. Explicitly remove Watchmode app IDs 10165, 10166, 6170 and 10858 from independent corroboration. No external row relies solely on a Watchmode app row. All historical offers are outside the reference hour. [Provider decisions](issue97-provider-mapping-decisions.md) · [Row-level reconciliation](issue97-provider-reconciliation.csv)

## Reproduction and verification

The authorization covered up to 40 anonymous provider website document reads. **26 GETs** were issued, including redirect hops: 19 HTTP 200, five 302, one 301 and one 404. Requests used a plain audit user agent, no cookies or credentials, no automated redirect handling or asset loading, and at least three seconds between requests within each batch. A separate, preselected Peacock help request ran after its title redirected to “unavailable”. Neither redirect was followed. NLZIET’s authorization destination was also left unopened. Apple support article 118402 concerned ringtones and was discarded. Article 108816 documented movie rental. CBC’s /help 404 and the subsequent customer help documents are all counted. New raw material is local in a private directory. There were zero metered API calls, playback sessions or charges. [Full request ledger](issue97-evidence-manifest.json)

## Evidence custody and offline reproduction

The owner sealed the 109-file allowlist on November 23 at 20:34 UTC. The single-host archive is `~/Library/Application Support/WhereTfDoIWatchIt/issue97/2026-11-23-v1/issue97-evidence.tar`. It is 18,728,960 bytes. Its SHA-256 is `6aaa66279d95c54d4b2bddbfe8ff677ec960182a79f71ddcfb7b95b9264147ad`. The Git manifest lists each member path and full SHA-256. The sealed archive includes the baseline manifest from draft `077bcc78c539cda1294610ad1f4a1e1e955affe2`, the 16 original HTML/screenshots, 26 later public response bodies, 15 direct-ID responses and their index, two Watchmode input files, and the pinned intake, scope, clone exports and comparator outputs. The seven pair-audit groups each retain their exact input, application snapshot and comparison. The archived allowlist excludes the database dump, environment configuration, credentials, personal-account records and unrelated folders. [Archive inventory](issue97-evidence-manifest.json)

On the owner’s Mac, the archive resolves directly under local Application Support. The version directory and archive belong to the operator and have modes `0500` and `0400`, respectively. Both have the native macOS user immutable flag (`uchg`). The operator checked the mode, owner and flag with `stat` and `ls -ldeO`. Opening the file for writing and attempting to change its mode each returned a permission error. The enclosing issue directory is owner-only (`0700`). The owner can clear the flag. This is one local copy on one host. There is no verified independent custodian, offsite copy or disaster-recovery guarantee. The archive format uses normalized member headers. Original acquisition timestamps come from the baseline manifest and request ledger.

With a checkout alone, reviewers can inspect the code, published rows, full digests, counts and decision rationales, and run the public regression tests below. Raw-byte verification and semantic re-extraction require authorized local access to the archive. A file digest supports integrity since capture. The original capture times, request location, absence of a login and storage controls are operator records. The archive has no independent signed timestamp. The clone is separately required only to rerun the application. The archive and Git repository suffice for both frozen comparators, inspecting the 34 recorded display results and the first-party pair reconciliation. No cache paths or network requests are used by the following replay.

Run from the repository root with Python 3.11+ and the baseline Git revision available locally. The restore verifies the container before creating its destination, checks membership and independently hashes the bytes read back from every restored file. The final four `cmp` checks require byte-identical CSV and JSON outputs for both historical and reviewed decisions.

```sh
set -eu
umask 077
OUT=$(mktemp -d)
R="$OUT/restored"
python3 scripts/restore_issue97_archive.py --restore-to "$R"
python3 scripts/verify_issue97_evidence.py --restored "$R"
mkdir "$OUT/original-code"
git archive 752dc23fd5beb2699612c2a2ee29a1fee0f4824e scripts/reconcile_watchmode_offline.py backend/availability/audit_identity.py > "$OUT/code.tar"
tar -xf "$OUT/code.tar" -C "$OUT/original-code"
compare() {
  name=$1 script=$2 app=$3 revision=$4
  shift 4
  python3 "$script" "$@" --cohort "$R/stage2b/nl_pilot_cohort_20261122_v2.csv" \
    --identity-manifest "$R/stage2b/identity_manifest_20261122.csv" --manifest-evidence-dir "$R/tmdb" \
    --watchmode-v1 "$R/watchmode/v1.json" --watchmode-correction "$R/watchmode/v2.json" \
    --expected-cohort-sha abcbb06161a5afc5896b507d4f1b1bf1c4fb9831d72973ed2be47487eb17d40b \
    --expected-identity-manifest-sha ae78a757272aecbb9824d9ef3cea4d8f125bd5e150cc6abafcc96e50e2737925 \
    --expected-v1-sha 2b30ab7d78a20a308cafcbda1c495e37d30b4ead4d3bd012affc175e96cea204 \
    --expected-correction-sha e815d65784795b944bbca7abe3d7cfd6b06ae6fc395a45750e372edfddd31f42 \
    --app-snapshot "$R/$app/application_snapshot.csv" --app-comparison "$R/$app/comparison.csv" \
    --snapshot-label tracker_97_audit_20261122 --audit-code-sha "$revision" \
    --output "$OUT/$name.csv" --summary "$OUT/$name.json"
  cmp "$OUT/$name.csv" "$R/local_replays/$name.csv"
  cmp "$OUT/$name.json" "$R/local_replays/$name.json"
}
compare historical "$OUT/original-code/scripts/reconcile_watchmode_offline.py" previous 8e10b76277fd74928d17a8ef52bfc7aab8d6a4e7
compare reviewed scripts/reconcile_watchmode_offline.py final-nl 3e23fb830910f9a6f2aae3770c9894c62f54b6a3 --reviewed-nl-providers
```

## What the verifier establishes

| Decision | Mechanical check against frozen evidence | Judgment and limit |
|---|---|---|
| Five Netflix subscriptions | For each accepted ID, parse the typed JSON-LD name and URL. Match the same top-level video ID, the title model’s availability and ads-plan flags, and anonymous request country NL. The parser reads data without running captured JavaScript. | Treating those title flags as an advertised subscription is a reviewed interpretation. The exact title aliases, series-year meaning, credits and cross-source ID decisions are documented separately. No current viewer access or playback follows from these flags. |
| Prime base subscription and HBO Max add-on | Bind the page GTI to the film detail, title, year and movie type, plus both NL territory fields. Check the detail’s Prime flag and its own primary PRIME/Prime action. Check that the channel option under that GTI carries PV_CHANNEL/wbdmaxnl. | The reviewed channel registry maps that benefit to HBO Max via Prime. The evidence distinguishes the two subscriptions. Trial qualifications, an account’s plans and playback are unknown. |
| Prime rental and purchase | Extract RENTAL and PURCHASE from the film’s own primary actions. Match the final EUR amounts €2.99 and €4.99 after the struck-through purchase price. | These are store offers on the captured page. A completed transaction and later pricing are unknown. |
| Four Rakuten transactions | Match the movie JSON-LD title and copyright year, canonical NL URL and slug, Netherlands chart text and each specific rent/buy button’s EUR amount. | The canonical route and named territorial chart identify the storefront. A storefront listing does not establish the viewer’s billing territory or rights after checkout. |
| Eight rejections | Hash, URL, time, typed matrix joins, individual decision counts and absence of positive or access claims are checked. Redirect status and Location are recorded in the original request ledger, including empty response bodies. | Human review classified the Netflix login form, BBC’s UK-series identifiers, CBC’s trailer runtime, the Rakuten client shell, Netflix IT redirect to NL, Apple collection/trailer, HBO’s conditional region template and Peacock’s regional denial. The verifier does not derive these eight semantic judgments from the HTML. None establishes a title-level negative. |
| Identity, source independence and historical provider maps | The verifier compares all 15 typed TMDb IDs, names and years to the pinned responses. The comparator mechanically applies reviewed match rules to 166 pilot rows. The published first-party rows bind each priority ID to its frozen typed title, market, provider and class. | Cross-service identity, exact alternate names, interpreting source independence, title specificity and provider nomenclature remain reviewed decisions. OCR was a secondary manual aid. The numerical replay does not turn stale overlaps into independent current observations. |

Across accepted rows the verifier checks unique observation IDs and unique typed title/market/provider/class keys, all eight priority bindings, source file digests and UTC, 13 NL advertised positives, six subscriptions, three rentals, three purchases and one add-on. Across all 21 observations it checks zero observed entitlements or playbacks and zero confirmed negative pairs. For each positive it checks the archived HTTP response, current and last-known offer membership and the corresponding provider/class Django comparison. The resulting 10 omissions on HTTP 200 and three on 404, five stale/three missing/five insufficient audit results, and zero eligible independent accuracy pairs agree with the published summary. The eight rejection rows share no observation IDs with the positives. [Identity decisions](issue97-identity-decisions.md) · [Mapping decisions](issue97-provider-mapping-decisions.md) · [Verifier](../../../scripts/verify_issue97_evidence.py)

## Separate disposable PostgreSQL check

The recorded panel used Python 3.11.15, Django 5.2.17 and PostgreSQL 18.7. Original panel code was `3ed0212aefcf7155e39190c46f898236fa534e22`. The NL audit was `3e23fb830910f9a6f2aae3770c9894c62f54b6a3`. Reproduction of the live read path requires the authorized November 22 disposable clone and a backend Python environment. Supply its authorized name independently of connection configuration. The audit compares PostgreSQL’s actual `current_database()` to this required argument, requires PostgreSQL and default read-only transactions, then reads the cohort and creates the output. A new run records a new read time. The archive preserves the earlier hour’s measurement.

```sh
umask 077
OUT=$(mktemp -d)
export PGOPTIONS='-c default_transaction_read_only=on'
export POSTGRES_DB=tracker_97_audit_20261122 POSTGRES_HOST=/tmp
export POSTGRES_USER="$(id -un)" POSTGRES_PASSWORD='' DJANGO_DEBUG=1
python scripts/audit_issue97_sample.py --expected-database tracker_97_audit_20261122 \
  --audit-code-sha "$(git rev-parse HEAD)" --output "$OUT/panel"
```

Revision `b08d133f378a8249aba698a1e304cf5d4be1c521` ran that command on the authorized clone with read-only default `on`. It read 34 cases and exited 0. All 34 display rows matched the archive after excluding the new read timestamps. Keeping the same connection and supplying `--expected-database tracker_97_other_disposable` with `--output "$OUT/refusal/panel"` exited 2 before creating even the `refusal` directory. The regression also checks that cohort selection, the audit management command and display measurement receive zero calls. A deliberately damaged archive failed its digest with exit 1 before restore. A changed HTML file in a separate throwaway restoration failed the input digest check with exit 1. Both are expected refusals.

```sh
python3 -m unittest discover -s scripts -p test_issue97_verification.py -v
python3 -m unittest discover -s scripts -p test_reconcile_watchmode_offline.py -v
./scripts/test-backend-isolated.sh availability.tests.test_audit_availability_accuracy -v 2
./scripts/test-backend-isolated.sh tracker availability -v 1
```

| Review verification | Result |
|---|---|
| First-party and database boundary regressions | 8 tests, exit 0 |
| Standalone comparator regressions | 18 tests, exit 0 |
| Audit Django module | 29 tests, exit 0 |
| Full tracker and availability suites | 3,731 tests, exit 0 |
| Private archive and restored byte checks | 109/109, exit 0 |
| First-party verifier and historical/reviewed comparators | 13 positives, eight rejected observations and two 166-row replays, exit 0. Four CSV/JSON byte comparisons exited 0. |
| Hosted CI | NOT PROVEN |

The isolated backend runs used the wrapper’s separate Compose projects and tmpfs PostgreSQL 16 databases with the matching cached dependency image `sha256:7f2b532c2e9db5f0930f35afc0b466d4dce0d85701901cca3e9ed73f162f8de7`, individually mounted read-only backend paths and an internal network. The earlier nested bind-mount failure and initial run’s test evidence remain in Git at the baseline draft revision. This revision made zero new provider requests and incurred zero charges.

The next evidence gate is a contemporaneous, authorized independent canonical source for a measured provider/class, with verified positive and negative title evidence in the same market and observation window. Entitlement and playback require separate legitimate evidence. Coverage expansion requires independent territorial observations for the blocked markets and free feature films. Notifications, watchable-home and catalogue-completeness claims depend on these distinctions. [Notifications #147](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/147) · [Homepage #119](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/119) · [Catalogue #161](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/161)
