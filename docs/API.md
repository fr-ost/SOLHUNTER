# API reference (summary)

Base URL: the Railway domain of `solhunter-api`. All `/api/v1/*` endpoints require `X-API-Key: <key>`
(or `Authorization: Bearer <key>`). Admin-only endpoints need a key from `API_ADMIN_KEYS`.
Responses are JSON; timestamps are ISO-8601 UTC. Rate limiting returns `429` with `Retry-After`.
Interactive docs (`/docs`) are available only outside production.

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness (public). |
| `GET /ready` | Readiness: DB, Redis, schema revision (public; 503 when degraded). |
| `GET /api/v1/dashboard` | Scanner status, provider status, 24h counts, top candidates, recent whale/smart-money activity, alerts, cost estimate, queue backlog. |
| `GET /api/v1/candidates?status&limit&offset&min_score` | Ranked latest scores (`candidate`, `watchlist`, `rejected`, `all`) with latest snapshot. |
| `GET /api/v1/candidates/{mint}` | Full research view: token, latest score, score history, snapshots, holders, launches, pools, whale & smart-money events, risk events, predictions, GPT analysis, history, wallet profiles, creator history, execution estimates, whale summary, filter stage results. |
| `GET /api/v1/tokens/search?q=` / `GET /api/v1/tokens/recent` | Search by mint/symbol/name; recently discovered tokens. |
| `GET /api/v1/tokens/{mint}` + `/snapshots`, `/transactions`, `/holders`, `/risk-events` | Token research data. |
| `POST /api/v1/tokens/track` `{mint, reason}` | Ask the scanner to track a mint (queued via Redis, 202). |
| `GET /api/v1/wallets/top?category&sort&limit` | Wallet leaderboard (excludes infrastructure accounts). |
| `GET /api/v1/wallets/{address}` + `/transactions` | Profile, positions, relationships, events. |
| `GET /api/v1/whales/recent?limit&mint` / `GET /api/v1/whales/signals` | Whale & smart-money feeds, latest whale signals per token. |
| `GET /api/v1/history/candidates?status&since&until` | Historical candidates with realised outcomes (when labelled). |
| `GET /api/v1/history/outcomes/summary` | Hit/stop/rug rates for past candidates and watchlist tokens. |
| `GET /api/v1/history/alerts` | Alert log. |
| `GET/POST /api/v1/trades`, `GET/PATCH/DELETE /api/v1/trades/{id}` | Paper / manual trades (no execution). |
| `GET /api/v1/trades/performance` | Performance by candidate type, model version, signal category, GPT verdict. |
| `GET/POST /api/v1/journal`, `DELETE /api/v1/journal/{id}` | Trade journal. |
| `GET /api/v1/risk/state`, `GET /api/v1/risk/position-size?entry_price_usd&stop_pct` | Risk limits and reference position sizing. |
| `GET /api/v1/settings` / `PUT /api/v1/settings` (admin) / `DELETE /api/v1/settings/{key}` (admin) | Effective config (secrets masked), runtime overrides. |
| `GET /api/v1/status/system`, `/providers`, `/costs`, `/ai`, `/models` | Observability. |
| `GET /api/v1/logs?source&level&limit` | Recent structured logs (API in-memory + scanner via Redis). |
| `WS /ws?token=<key>` | Live events: `token.discovered`, `token.evaluated`, `token.migrated`, `gpt.analysis`, heartbeats. |

Every object carries provenance where relevant: `data_sources` / `data_quality` on snapshots,
`holder_count_source`, `ml_is_experimental`, `execution.source`, GPT `status`.
