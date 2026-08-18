# WhereTfDoIWatchIt Frontend

Next.js + Tailwind frontend for the single-user streaming tracker.

## Development

```bash
npm install
npm run dev
```

The development server runs on port 3000 inside the container. In the dev Docker Compose stack it is exposed on <http://localhost:3001>.

## Verification

```bash
npm run lint
npm run build
npm test
```

`npm test` runs the Vitest/React Testing Library smoke suite for the main search, preferences, watchlist report, and ambiguous-match resolver pages. Tests mock `fetch` and do not call the backend or the Streaming Availability API.
