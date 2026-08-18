# Operations Guide

This guide describes operational workflows, testing procedures, maintenance commands, and rate limit management for WhereTfDoIWatchIt.

---

## 1. Booting the Development Stack

The application uses Docker Compose to manage PostgreSQL database, Django backend, and Next.js frontend services.

### Start all services in the background:
```bash
docker compose up -d
```

### View container status:
```bash
docker compose ps
```

### View logs across all services:
```bash
docker compose logs -f
```

---

## 2. Applying Database Migrations

Ensure database schema changes are applied whenever you start or update the application.

### Execute pending migrations:
```bash
docker compose exec backend python manage.py migrate
```

---

## 3. Running Tests and Linting

### Running Backend Tests
Execute Django unit and integration tests inside the running backend container:
```bash
docker compose exec backend python manage.py test tracker
```

### Running Frontend Linting & Production Build
Navigate to the `frontend/` directory and run ESLint and Next.js production build checks:
```bash
cd frontend
npm run lint
npm run build
```

---

## 4. Background Availability Refresh Command

To keep cached availability fresh without exceeding daily API quotas, run the `refresh_availability` management command.

```bash
docker compose exec backend python manage.py refresh_availability
```

This command queries tracked movies with missing or stale availability data and updates their cached offers in PostgreSQL.

---

## 5. Operational Management of API Rate Limits & Caching Strategy

### The 100 Requests/Day Free-Tier Limit
The Streaming Availability API free tier enforces a strict quota of **100 requests per calendar day (UTC)**. Each API call returns availability across all supported countries for a single movie title.

### Cache-First Architecture
To operate safely within this limit:
- **On-Demand Caching**: Availability queries for a movie (`GET /api/movie/<tmdb_id>/availability/`) check the PostgreSQL cache first.
- **Cache Refreshing**: Fresh availability data is fetched only if no cache record exists or if the existing cache entry has expired.
- **Watchlist Import Isolation**: Importing Letterboxd watchlists only resolves movie metadata via TMDb and **does not** trigger live availability calls.
- **Batch Refresh Limit**: Background refresh jobs process ~90 stale entries per day, leaving headroom for on-demand user queries.
