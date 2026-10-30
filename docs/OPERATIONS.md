# Operations runbook

## Health & status

| Check | Where |
|---|---|
| API liveness/readiness | `GET /health`, `GET /ready` |
| Scanner heartbeat, leader, stale detection, memory, tracked tokens, evaluator counters | `GET /api/v1/status/system` → `scanner.stats`, `workers[]` |
| Provider connectivity, reconnects, last errors, credits | `GET /api/v1/status/providers` |
| GPT spend and limits, model verification | `GET /api/v1/status/ai` |
| Cost projection | `GET /api/v1/status/costs` |
| Model versions and metrics | `GET /api/v1/status/models` |
| Logs | Railway log explorer (JSON) or `GET /api/v1/logs?source=scanner` |
| Worker HTTP health | `GET http://gemfinder-scanner.railway.internal:8081/health` (private network) |

`incomplete_data_warning` in `/status/system` states when providers are degraded; candidates scored in
that condition carry lower confidence.

## Common situations

* **Scanner shows `stale`/`offline`** – check scanner logs. Typical causes: Redis unreachable (leader
  lock cannot be acquired → `scanner.standby`), schema not migrated (`migrations.schema_not_ready` – start
  the API with `RUN_MIGRATIONS_ON_START=true`), crash loop (see traceback in logs).
* **`pumpportal` down** – the client reconnects with exponential backoff (max 60 s) and re-subscribes;
  `reconnect_count` grows. If PumpPortal blacklists multiple connections, ensure only one scanner replica.
* **Helius budget exhausted** – `BudgetExceededError` is logged at debug level; holder data falls back to
  stream estimates (`holder_count_source = stream_estimate`). Raise `HELIUS_DAILY_CREDIT_BUDGET` or wait.
* **GPT disabled** – `/status/ai` → `disabled_reason`. Model unavailable? Set `AI_MODEL` to a model your
  key can access; the system never substitutes silently. Spend limit reached? Analyses are skipped with
  the reason recorded in `gpt_analyses.error`.
* **Dead letters** – persistence batches: Redis list `gemfinder:persistence:dead`; GPT jobs:
  `gemfinder:gpt:dead`. Inspect with `redis-cli LRANGE <key> 0 -1`.
* **No candidates for a long time** – this is expected on quiet days. Verify `live_counters.discovered`
  grows and inspect `candidate_scores` with `status=rejected` to see the dominant rejection reasons; tune
  `FILTER_*`/`RANK_*` via `PUT /api/v1/settings` (changes apply within ~60 s).
* **Retraining** – automatic daily when `training_samples ≥ ML_MIN_TRAINING_SAMPLES`; manual:
  `railway run --service gemfinder-scanner python -m backend.ml.train`.
* **Retention** – hourly job prunes raw events (3 d), transactions (30 d), wallet transactions (30 d),
  alerts (180 d), unlabelled & unscored snapshots (45 d). Training samples, labelled snapshots and
  candidate scores are kept.

## Redeploys

Both services are restart-safe: the API migrates under an advisory lock; the scanner restores tracked
mints from `scanner_checkpoints`, re-subscribes, and resumes labelling/training. Graceful shutdown on
SIGTERM flushes pending writes and releases the leader lock (give Railway ≥ 30 s grace).
