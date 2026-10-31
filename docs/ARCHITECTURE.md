# Architecture

SolHunter is a modular, event-driven research platform. By default it runs as ONE process (API + web
dashboard + scanner, `SERVICE_ROLE=all`) with PostgreSQL and Redis; the roles can be split into two
services without code changes:

```
 PumpPortal WS ──┐                                   ┌──> Telegram alerts
 DexScreener  ───┼─> scanner (same process by default) ─────┤
 Helius RPC   ───┘      │  tracker / snapshots       └──> Redis pub/sub ──> API ──> browser dashboard (web/)
                        │  filters / whale engine / ML / GPT                    (REST + WebSocket + web dashboard)
                        ▼
                   PostgreSQL  <──────────────────────────────── solhunter-api (reads, paper trades, settings)
```

## Backend packages (`backend/`)

| Package | Responsibility |
|---|---|
| `config` | `Settings` (pydantic-settings, nested groups), structured logging with secret scrubbing. |
| `database` | Async SQLAlchemy engine, advisory-locked migrations, schema wait. |
| `models` | ORM tables (31 tables, see `migrations/versions`). |
| `providers` | Adapters: PumpPortal (WS), DexScreener, Helius (+ public RPC fallback), SOL price. Rate limits, retries, usage/credit accounting, status tracking. |
| `ingestion` | Normalised events, deduplication (memory + Redis), idempotent batch persistence, raw event storage. |
| `scanner` | Worker entrypoint, leader election/heartbeat, token tracker lifecycle, in-memory `TokenState`, snapshot scheduler, evaluator orchestration, Pump.fun maths, health server. |
| `intelligence` | Developer analysis, holder analysis, manipulation detection, wallet positions/profiles/classification, relationship graph + clusters. |
| `whale_engine` | Whale accumulation/distribution metrics and the eight whale signals. |
| `filters` | Ten-stage pipeline with explainable reasons; hard safety failures override everything. |
| `execution` | Liquidity/execution analyzer ($100/$250/$500 estimates from reserves). |
| `ranking` | Weighted score with components, penalties, confidence and status thresholds. |
| `ml` | Point-in-time features, outcome labeller, XGBoost training (walk-forward), registry, scheduler, predictor. |
| `ai` | GPT analyst: strict schema, context builder, spend controls, queue with dead-letter. |
| `notifications` | Telegram client and alert service (cooldowns, dedupe, urgent warnings). |
| `risk` / `services` | Paper trading & position sizing, runtime settings overrides, status aggregation, retention, Redis helpers. |
| `api` | FastAPI app: auth, CORS, rate limiting, routers, WebSocket live hub, serves the built dashboard (`web/dist`) and embeds the scanner in `all` mode. |

## Token lifecycle inside the scanner

1. **Discovery** – `subscribeNewToken` message → `NewTokenEvent` → `TokenState` created, token + launch
   persisted, trade subscription added (bounded by `SCANNER_MAX_TRACKED_TOKENS`, LRU eviction of idle
   tokens older than 10 minutes).
2. **Tracking** – every trade updates the in-memory state (windows, momentum, per-wallet aggregates,
   developer activity) and is persisted idempotently; wallet positions are updated in batches.
3. **Snapshots** – at 1/3/5/10/15 minutes after launch plus every 60 s afterwards a `token_snapshots`
   row is written (market state + data-quality flags). DexScreener is only queried for migrated or
   manually tracked tokens.
4. **Evaluation** – the evaluator runs the cheap pass (stream-only). If the token is promising it spends
   budgeted provider credits (Helius holders, creator history, funders), loads wallet profiles, runs the
   whale engine, builds the feature vector, predicts, runs the full pipeline and ranks. Results are
   stored in `candidate_scores`, `model_predictions`, `whale_events`, `smart_money_events`,
   `token_risk_events`; the snapshot gets its feature vector for later labelling.
5. **GPT + alerts** – candidates above `RANK_GPT_MIN_SCORE` are queued for GPT analysis (spend-limited);
   the Telegram alert waits for the verdict (REJECT/HIGH_RISK downgrades to watchlist). Urgent warnings
   are sent for tokens previously surfaced.
6. **Expiry** – tokens are retired after the tracking window (90 min) or when dead; a checkpoint in
   `scanner_checkpoints` lets a restarted worker re-track recent mints.
7. **Learning** – after the outcome horizon the labeller turns snapshots into `training_samples`; the ML
   scheduler retrains daily when enough samples exist and activates better models.

## Reliability

* Single active scanner: Redis leader lock (`SET NX PX` + refresh); a redeploy waits for the previous
  instance to release the lock on SIGTERM.
* Every ingest path is idempotent (unique constraints + `ON CONFLICT DO NOTHING`), so replays after
  restarts never duplicate rows. Failed batches go to a Redis dead-letter list after three attempts.
* Provider outages degrade gracefully: the token keeps stream-derived data, affected fields are marked
  unavailable, and provider status is visible in the dashboard.
* Heartbeats (Redis + `worker_heartbeats`) make stale workers visible in `/api/v1/status/system`.

## Data quality philosophy

* Unavailable ≠ zero: features use NaN, snapshots carry `data_quality`, GPT is told what is missing.
* Verified facts (stream/chain) vs estimates (DexScreener, reserve maths, stream-derived holder counts)
  vs model predictions are labelled throughout the API and UI.
* Nothing in the system signs transactions, stores keys or trades.
