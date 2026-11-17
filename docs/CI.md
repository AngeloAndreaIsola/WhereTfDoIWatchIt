# Continuous Integration (CI) Documentation

This project uses **GitHub Actions** for Continuous Integration (CI) to ensure code quality, build success, and test pass rates across both backend and frontend applications.

The workflow configuration file is located at `.github/workflows/ci.yml`.

---

## Workflow Triggers

The CI pipeline runs automatically on:
- Every `push` to the `master` branch.
- Every `pull_request` against any branch.

---

## Jobs Overview

The workflow consists of two independent jobs running on `ubuntu-latest`:

### 1. Backend (`backend`)

* **Working Directory:** `./backend`
* **Runtime:** Python 3.12 (matching `backend/Dockerfile`).
* **Database Service:** PostgreSQL 16 container (`postgres:16`) with `pg_isready` health check.
* **Dependencies Caching:** Caches pip packages using `backend/requirements.txt`.
* **Environment Variables:**
  * `POSTGRES_DB=tracker_test`
  * `POSTGRES_USER=tracker`
  * `POSTGRES_PASSWORD=tracker_dev`
  * `POSTGRES_HOST=localhost`
  * `POSTGRES_PORT=5432`
  * `TMDB_API_KEY=""`
  * `RAPIDAPI_KEY=""`
* **Steps:**
  1. Checks out repository (`actions/checkout@v4`).
  2. Sets up Python 3.12 with pip cache (`actions/setup-python@v5`).
  3. Installs requirements via `pip install -r requirements.txt`.
  4. Applies migrations explicitly to the disposable CI PostgreSQL service.
  5. Executes `python manage.py test tracker availability -v 2 --noinput`; Django creates its separate test database.

> **Mocking External APIs:** All backend unit tests mock calls to TMDb and RapidAPI (Streaming Availability API). `TMDB_API_KEY` and `RAPIDAPI_KEY` are explicitly set to empty strings in CI so that no external network calls or rate limits are consumed during testing.

### 2. Frontend (`frontend`)

* **Working Directory:** `./frontend`
* **Runtime:** Node.js 22 (required for modern jsdom / undici test dependencies).
* **Dependencies Caching:** Caches npm packages using `frontend/package-lock.json`.
* **Steps:**
  1. Checks out repository (`actions/checkout@v4`).
  2. Sets up Node.js 22 with npm cache (`actions/setup-node@v4`).
  3. Installs dependencies via `npm ci`.
  4. Runs ESLint via `npm run lint`.
  5. Builds Next.js application via `npm run build` with `NEXT_TELEMETRY_DISABLED=1`.
  6. Runs frontend unit tests via `npm test` (using Vitest).

---

## Reproducing & Diagnosing CI Failures Locally

If a CI run fails, follow these instructions to reproduce and fix the failure locally.

### Backend Failures

To run backend tests locally using Docker Compose (recommended), use the
isolated test profile. It starts a separate PostgreSQL service on tmpfs and
cleans up its unique Compose project on exit:

```bash
./scripts/test-backend-isolated.sh tracker availability -v 2
```

To run backend tests locally on bare metal / host environment with PostgreSQL running:

```bash
cd backend
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export POSTGRES_DB=tracker
export POSTGRES_USER=tracker
export POSTGRES_PASSWORD=tracker_dev
export TMDB_API_KEY=""
export RAPIDAPI_KEY=""

pip install -r requirements.txt
python manage.py test tracker availability -v 2 --noinput
```

The Django test runner creates and migrates its own `test_tracker` database;
do not run `migrate` against the configured application database as a test
setup step.

### Frontend Failures

To run frontend lint, build, and tests locally using Docker Compose:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run lint
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run build
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm test
```

To run frontend commands locally on host:

```bash
cd frontend
npm ci
npm run lint
NEXT_TELEMETRY_DISABLED=1 npm run build
npm test
```

### Reading GitHub Actions Logs

1. Go to the **Actions** tab on GitHub for the repository.
2. Select the failed workflow run corresponding to your commit/PR.
3. Click on the failing job (**Backend Tests** or **Frontend Lint, Build & Test**).
4. Expand the step marked with a red `X` to view detailed output and failure stack traces.
