# SolHunter web dashboard

React + TypeScript + Vite single-page app served by the SolHunter API at `/` (the Dockerfile builds it).

```bash
npm install
npm run typecheck
npm run build          # -> dist/ (copied into the API image)
VITE_API_BASE=http://localhost:8080 npm run dev   # local dev with API proxy
```

Sign in with a value from `API_KEYS` (and optionally `API_ADMIN_KEYS` for runtime settings). Keys live in
the browser's localStorage only. The API base URL defaults to the page origin; an override is available
under "advanced" on the login screen and in Settings.
