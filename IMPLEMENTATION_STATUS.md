# Implementation status

_Last updated: 2026-10-30 — backend test run: **105 passed** (`pytest -q`, PostgreSQL + Redis required)._

## Completed phases

| Phase | Scope | Status |
|---|---|---|
| 1 Foundation | Settings, logging, 31-table schema + Alembic, FastAPI (auth, CORS, rate limit, health), Redis, Dockerfiles, start scripts, compose | ✅ |
| 2 Ingestion | PumpPortal WS (reconnect, bounded subscriptions), DexScreener, Helius (+budget, public fallback), normalisation, dedupe, idempotent batching, tracker lifecycle, snapshots, checkpoints, retention | ✅ |
| 3 Intelligence | Developer/holder/manipulation analysis, wallet positions & profiles & classification, relationship graph & clusters, whale engine (8 signals) | ✅ |
| 4 Filtering & ranking | 10 configurable stages with explainable reasons, execution analyzer, weighted ranking with penalties/confidence, evaluator orchestration, risk events, rug detection | ✅ |
| 5 ML | Point-in-time features, outcome labelling, XGBoost (rug / breakout / expected return) with grouped walk-forward validation, calibration, metrics, versioned registry, scheduler, CLI, experimental policy | ✅ |
| 6 GPT analyst | Responses API + strict schema, model verification without substitution, spend limits, cache, retries, queue + dead letter, verdict integration | ✅ (live call untested) |
| 7 Web dashboard | Built-in browser dashboard (React 18 + TypeScript + Vite + recharts) served by the API at `/`: login with API key, dashboard, candidates, candidate detail with charts, wallets, token search/track, history with outcomes, paper trading, journal, settings with runtime overrides, status, logs; live WebSocket updates; verified/estimate/model badges | ✅ builds & type-checks; browser rendering not exercised in this environment |
| 8 Production readiness | Telegram alerts, status/monitoring, security review, production start-up test, docs, checklist | ✅ (live integrations untested) |

## Remaining work / known gaps

* **Live validation with real credentials** – PumpPortal stream, Helius, OpenAI (`gpt-5.6`), Telegram and
  Railway itself were not reachable from the build environment. The code paths are tested with local
  servers and mocks; the first deployment must confirm them (see `docs/DEPLOYMENT_CHECKLIST.md`).
* **Dashboard in a browser** – built and type-checked but not opened in a browser here: verify login, charts
  and live updates after the first deploy.
* **Docker image build** – the Docker daemon was unavailable in the build sandbox; Dockerfiles were
  reviewed and the start scripts were executed directly (production start-up test). Build once locally
  or let Railway build.
* **PumpPortal message schema** – field names follow the widely used public format but are not
  formally documented; the normaliser is defensive and stores raw payloads for verification.
* **Pricing placeholders** – `AI_INPUT_COST_PER_1M_USD` / `AI_OUTPUT_COST_PER_1M_USD` must be set to the
  real price of the configured model; Helius credit costs per method are conservative estimates.
* **Wallet history cold start** – smart-money classification needs track records that accumulate only
  after the scanner has run for days; until then whale signals are size-based and labelled as such.
* **ML cold start** – predictions are from the heuristic baseline (flagged experimental) until
  `ML_MIN_TRAINING_SAMPLES` labelled samples exist (typically 1–3 days of operation) and validation
  metrics clear the bar.
* **Optional enhancements** – Pump.fun migration pool address resolution via DexScreener is done lazily;
  Helius enhanced websockets (paid plan) are not used; multi-user accounts are out of scope (single-tenant
  API keys).

## Required credentials

| Credential | Needed for | Without it |
|---|---|---|
| `HELIUS_API_KEY` | holder distribution, creator history, funders | stream-based estimates only, lower confidence |
| `OPENAI_API_KEY` | GPT analysis | ranking without the GPT component, alerts sent immediately |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | alerts | alerts recorded as suppressed in the DB |
| `PASSWORD` | dashboard login (also settings access) | API refuses requests in production |

## Test coverage (backend)

Settings · API auth/health/rate limiting · trading & journal endpoints · normalisation/dedupe/state maths ·
provider adapters (mocked HTTP, real local WebSocket reconnect) · tracker/persistence idempotency ·
snapshot schedule · worker start/stop/leader lock · intelligence modules · whale engine · filters/ranking/
execution/features/predictor · evaluator end-to-end (discovery → candidate score, rug → risk events) ·
labeller/training/registry · GPT analyst/queue/spend · Telegram/alerts · production start-up.

## Exact next tasks

1. Push to GitHub, create the Railway services per `docs/RAILWAY_DEPLOYMENT.md`, set variables.
2. Watch the scanner logs for the first `token.discovered` events and inspect `raw_events` to confirm
   the PumpPortal field mapping; adjust `backend/ingestion/normalizer.py` if a field differs.
3. Confirm `analyst.model_verified` (or set `AI_MODEL`), send a Telegram test alert, set pricing variables.
4. Open the Railway URL, log in with your `PASSWORD`, verify the dashboard shows the live scanner.
5. After 24–72 h: check `/api/v1/status/models` for the first trained versions and
   `/api/v1/history/outcomes/summary` for realised candidate outcomes; tune `FILTER_*`/`RANK_*`.
