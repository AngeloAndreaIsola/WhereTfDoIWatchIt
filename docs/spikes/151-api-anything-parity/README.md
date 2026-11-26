# Spike #151 — API Anything parity (offline)

Offline parity baseline for the six unofficial collectors, prepared for issue
#151. Read `REPORT.md` for findings and the go/no-go; this file is the index and
reproduction guide.

## Result in one line

**NO-GO** on refactoring the six collectors onto API Anything; **KEEP** all six;
**CONDITIONAL HYBRID** only for a new eligible source after #124.

## Reproduce

From the repo root (network disabled, in-memory DB, no writes):

```bash
docker run --rm --network none \
  -v "$PWD/backend:/app" -w /app \
  -v "$PWD/docs/spikes/151-api-anything-parity:/probe" \
  -e PYTHONPATH=/probe:/app -e USE_SQLITE=1 -e DJANGO_DEBUG=1 \
  -e DJANGO_SECRET_KEY=dev-insecure-secret \
  -e DJANGO_SETTINGS_MODULE=probe_settings \
  --entrypoint python wheretfdoiwatchit-backend:latest /probe/probe.py
```

Writes `probe_results.json` next to the script and prints `probe.log` contents.

## Artifacts

| File | Purpose |
|---|---|
| `REPORT.md` | findings, parity matrices, go/no-go |
| `probe.py` | patches `fetch_url` per scraper, runs `_execute_run` offline |
| `probe_settings.py` | project settings with `:memory:` SQLite (never queried) |
| `probe.log` | exact console output |
| `probe_results.json` | structured metrics |
| `fixtures/api_anything_contract.json` | 17 dimensions + go/no-go criteria |
| `fixtures/*` | per-provider retained/synthetic payloads |

## Guarantees asserted by the probe

- `network_requests = 0` (container ran `--network none`)
- `database_rows_written = 0` (in-memory SQLite, never queried)
- `offers_created = 0` (discovery is metadata-only)
- all six unofficial providers fail closed with `source_permission_unverified`
- `raiplay` / `nhk_world` controls are `ALLOWED`

## Non-goals

No production code changes, no live fetches, no DB writes, no PR. The AnimeWorld
browse dedupe/typing bug and HydraHD native-ID gap are documented, not fixed.
