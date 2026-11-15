# Stage 2E — Source status

Code SHA: `00d9dc843c558dc2000f0bb4fba055671c5d012f`. Database facts below are
from the read-only Stage 2D snapshot dated 2026-11-14. The report retained
aggregate `SyncRun` counts, not per-source last-attempt timestamps, queue
counts, or all source configurations. Those unknowns are marked explicitly;
no current database connection was used in this task.

**Terms:** “registered” means an implementation is added to
`source_registry`; “persisted” means a `Source` database row exists; neither
proves a live execution. “Last success / attempt” is `not retained` unless
stated. “Latest observation” is the latest observed offer timestamp in the
Stage 2D snapshot where available; the global range was 2026-09-30–2026-11-01.
Pending work counts were not retained. Freshness thresholds are code defaults;
database overrides are unknown except for JustWatch and Watchmode, which were
seeded to 12h and 24h respectively.

| Source | Code registration / persisted / enabled | Declared country scope | Last success / attempt; latest observation | Freshness | Pending | Capability / metering | Why no fresh evidence |
|---|---|---|---|---|---|---|---|
| `justwatch` | yes / yes / yes | unrestricted per call; explicit country required | 669 SyncRuns, last status succeeded; exact times not retained; cohort observations Oct 5–24 | 12h (DB seeded) | unknown | title + catalogue; aggregator, no billed API quota; operational rate-limit/site risk | last observations aged out; main app stack stopped |
| `watchmode` | yes / yes / yes | NL, CA, JP (plan-scoped) | 870 SyncRuns, last status succeeded; exact times not retained; cohort observations Oct 16–24 | 24h (DB seeded) | unknown | title targeted; paid API, documented plan 2,500 requests/month; current balance unknown | last observations aged out; monthly plan state unknown |
| `streaming_availability` | yes / yes / yes | unrestricted response scope | 5,575 SyncRuns, last status succeeded; latest cohort observation Oct 2–6 | 24h code default | unknown | title targeted; metered API, documented 100/day free tier; Compose gate defaults off; current balance unknown | observations aged out; gate and budget may further suppress this source |
| `rai_play` | yes / yes / yes | IT | 7 SyncRuns, last status succeeded; exact source timestamp not retained | 24h | unknown | catalogue only; public-site requests, no billed API quota; high request volume | no recent scheduled catalogue run evidenced; last offer date globally Nov 1 |
| `prime_video` | yes / yes / yes | NL | 7 SyncRuns, last status succeeded; cohort observation Oct 16 | 24h | unknown | catalogue only; public pages, no billed API quota; large pages and territory-sensitive | observations aged out; catalogue worker/cron stopped or not running |
| `mediaset_infinity` | yes / yes / yes | IT | 39 SyncRuns, last status succeeded; cohort observation Oct 24 | 24h | unknown | title targeted; public GraphQL site, no billed API quota; exactly 2 calls for a matching movie | observations aged out; offers include a provider-specific IT subscription example |
| `pluto_tv` | yes / yes / yes | AU, AT, BR, CA, CH, CL, DE, DK, EC, FI, FR, GB, GT, HN, IE, IT, MX, NI, NO, PA, PE, PY, SE, SV, US, UY, VE | 5 SyncRuns, last status succeeded; latest timestamp not retained | 24h | unknown | catalogue only; public endpoint, no billed API quota; geo/egress and bot controls | no recent observation evidenced; supported market/egress must be checked |
| `public_domain_archive` (Internet Archive) | yes / **no** / unknown | US | no Source row; no persisted Source run evidence retained | 24h | unknown | catalogue only; adapter has explicit request cap; public metadata endpoint | A5 provider/source provisioning gap; do not activate |
| `bbc_iplayer` | yes / yes / yes | GB | 4 SyncRuns, last status succeeded; timestamp not retained | 24h | unknown | catalogue only; public pages; high-volume walk | no recent run evidenced |
| `cbc_gem` | yes / yes / yes | CA | 18 SyncRuns, last status succeeded; timestamp not retained | 6h | unknown | catalogue only; public endpoint | no recent run evidenced |
| `tubi` | yes / yes / yes | US | 1 SyncRun, last status succeeded; timestamp not retained | 24h | unknown | catalogue only; public pages, high-volume, US egress | no recent run evidenced |
| `youtube` | yes / yes / yes | US, AU, JP, DE, GB free-with-ads program | 3 SyncRuns, last status succeeded; timestamp not retained | 24h | unknown | title targeted; public endpoint, no billed API quota; up to 2 requests, country constrained | no matching recent offer; IT/NL are outside declared free program scope |
| `pbs` | yes / yes / yes | US | 0 SyncRuns | 24h | unknown | catalogue only; public API | configured but never run in retained snapshot |
| `abc_iview` | yes / yes / yes | AU | 0 SyncRuns | 24h | unknown | catalogue only; public pages; ~1.6k detail requests per full walk | configured but never run; expensive walk |
| `legacy_scraper` | no current registry entry / yes / yes | historical/unrestricted | 0 SyncRuns | 12h code policy | unknown | historical scraper path, not a current registered source | intentionally not reactivated; legacy rows do not refresh canonical offers |

## Requested source distinctions

- **JustWatch:** real executions are demonstrated by 669 successful historical
  SyncRuns, but it is a secondary aggregator, non-authoritative, and
  operationally fragile despite no billed quota.
- **Watchmode:** registered and historically successful, but paid and plan
  constrained; no quota calls or balance checks were made.
- **Streaming Availability:** registered and historically dominant; it is
  metered, its Compose gate defaults off, and it is not needed for a bounded
  no-metered experiment.
- **RaiPlay / Prime Video / Pluto TV:** catalogue collectors; cannot be
  title-targeted through `RefreshRequest`. Do not run their broad catalogue
  walks as part of this experiment.
- **Mediaset Infinity:** the most useful direct IT title-targeted collector in
  this sample. It is unmetered but still makes two HTTP requests per matching
  movie and does not prove the title's year because the provider omits it.
- **Internet Archive:** source code registration alone is insufficient. The
  absent database source row and missing canonical provider seed block safe
  persistence/checkpointing (A5).

No provider endpoint was called and no request or paid quota was consumed.
