# Phase 0 — Project Scaffold Design

Date: 2026-08-08
Status: Approved

## Purpose

Scaffold the monorepo foundation for the Streaming Tracker (see `docs/SPEC.md`, `docs/PLAN.md`). Deliver a locally runnable three-service stack — Postgres, Django backend, Next.js frontend — wired together, with a health check proving connectivity end to end.

## Repo layout

Monorepo, single compose stack:

```
WhereTfDoIWatchIt/
├── docker-compose.yml        # base: prod-style (gunicorn, next start)
├── docker-compose.dev.yml    # dev override: bind-mounts, hot reload
├── .env.example              # committed; real values go in .env (gitignored)
├── .gitignore
├── frontend/                 # Next.js 14 + TypeScript + App Router + Tailwind
└── backend/                  # Django 5 + DRF
```

Compose is split into a base file plus a dev override (rather than one file with profiles): compose-native, no profile logic, dev adds bind-mounts and dev servers without touching the base.

## Services

| service | image / runtime | notes |
|---|---|---|
| `db` | postgres:16-alpine | named volume, healthcheck |
| `backend` | python:3.12-slim | Django 5, DRF; gunicorn (base) / `runserver --reload` (dev); entrypoint runs migrations on start |
| `frontend` | node:20-alpine | Next.js 14, TypeScript, App Router, Tailwind; `next dev` (dev) / `next start` (base) |

## Django scaffold

- Project name `config`, app name `tracker`.
- Postgres connection via environment variables (no hardcoded credentials).
- Django admin enabled — it is the manual `LanguageInfo` data-entry screen per the SPEC.
- Endpoint `GET /api/health/` → `{"status": "ok", "db": "ok"}` proving all three layers talk.
- Django REST Framework added now; it is the API layer for Phases 2–4.
- Container entrypoint runs `migrate` before starting the server.

## Next.js scaffold

- Created via `create-next-app` with App Router + TypeScript + Tailwind.
- Backend URL configured via environment variable.
- Home page fetches the backend health endpoint and renders the result — visual proof of connectivity.

## Explicitly out of scope (manual steps)

- VPS provisioning — compose file ships deployment-ready; provisioning happens later by hand.
- API key wiring — TMDb and Streaming Availability (RapidAPI) keys go into `.env` when obtained.
- Data models (Phase 1) and search / availability / watchlist endpoints (Phases 2–4).

## Definition of done

- `docker-compose up` boots `db`, `backend`, `frontend`.
- Django admin reachable on :8000; `/api/health/` returns `{"status":"ok","db":"ok"}`.
- Next.js on :3000 renders the home page showing the backend is connected.
