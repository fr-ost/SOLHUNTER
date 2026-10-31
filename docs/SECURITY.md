# Security

* **No custody**: the system never asks for seed phrases or private keys, never signs or submits
  transactions, and has no trading execution path. Paper trades are database rows.
* **Secrets** only come from environment variables (Railway service variables). `.env` is git-ignored;
  `.env.example` contains placeholders only. Logs scrub keys named like `api_key`, `token`, `secret`.
  The settings endpoint masks secrets. Provider errors are truncated and never include request URLs that
  contain keys.
* **Authentication**: every `/api/v1/*` route requires an API key (`X-API-Key` or Bearer). Admin keys are
  separate (`API_ADMIN_KEYS`) and required for settings changes. Keys are compared with constant-time
  comparison. WebSocket clients authenticate with the same key (`?token=`). In production the API refuses
  to serve protected routes if no keys are configured.
* **CORS**: the dashboard is served from the same origin as the API, so no cross-origin access is needed;
  only explicitly configured `CORS_ALLOWED_ORIGINS` are allowed in production (no wildcard).
* **Rate limiting**: Redis token bucket per key/IP (`RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_BURST`).
* **Input validation**: pydantic models on every request body/query; bounded `limit` values; mint and
  address lengths validated.
* **Error handling**: unhandled exceptions return a generic 500 with a request id; stack traces are only
  in logs. API docs are disabled in production.
* **Transport**: Railway terminates TLS; internal traffic to Postgres/Redis uses the private network
  (`railway.internal`).
* **Containers**: run as non-root `appuser`; pinned dependencies; no build tools in the final image.
* **Provider trust**: external data (PumpPortal, DexScreener, Helius, GPT output) is treated as data –
  parsed defensively, validated against schemas, and never executed or interpreted as instructions.
* **Admin surface**: runtime overrides are limited to a whitelist of non-secret operational keys;
  infrastructure URLs and credentials cannot be changed through the API.

## Recommended operational hygiene

* Rotate `API_KEYS` / `API_ADMIN_KEYS` periodically (comma-separated lists allow overlap during rotation).
* Keep Telegram bot tokens scoped to a private chat.
* Monitor `/api/v1/status/ai` and `/costs` to catch unexpected spending; limits stop calls automatically.
