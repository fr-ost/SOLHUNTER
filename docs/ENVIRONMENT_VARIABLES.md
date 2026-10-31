# Environment variables

Default setup: ONE `solhunter` service (`SERVICE_ROLE=all`) — set everything there. The `api`/`scanner`
column only matters if you split roles into two services.
All runtime thresholds in the `FILTER_*`, `RANK_*`, `WHALE_*`, `SCANNER_*`, `TELEGRAM_*`, `EXEC_*`,
`RISK_*`, `ML_*`, `AI_*` groups can also be changed at runtime through `PUT /api/v1/settings`
(admin key); environment values are the defaults, database overrides win.

| Variable | Service | Default | Purpose |
|---|---|---|---|
| `APP_ENV` | both | `development` | `production` enables strict checks (auth required, docs disabled). |
| `SERVICE_ROLE` | both | `all` | `all` = API + dashboard + scanner in one process; `api` / `scanner` for split deployments. |
| `LOG_LEVEL` / `LOG_FORMAT` | both | `INFO` / `json` | Structured logs; `console` for local dev. |
| `PORT` | api | injected by Railway | API bind port (`0.0.0.0`). |
| `DATABASE_URL` | both | localhost | PostgreSQL URL; `postgresql://` is converted to asyncpg automatically. Use `${{Postgres.DATABASE_URL}}`. |
| `REDIS_URL` | both | localhost | Redis URL. Use `${{Redis.REDIS_URL}}`. |
| `RUN_MIGRATIONS_ON_START` | all/api=`true`, split scanner=`false` | `true` | Advisory-locked `alembic upgrade head` at start. |
| `MIGRATION_WAIT_TIMEOUT_SECONDS` | scanner | `180` | How long the scanner waits for the schema. |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | both | `5` / `5` | Connection pool. |
| `API_KEYS` | api | empty | Comma-separated keys; dashboard login. Required in production. |
| `API_ADMIN_KEYS` | api | empty | Comma-separated admin keys (settings changes from the dashboard). |
| `CORS_ALLOWED_ORIGINS` | api | empty | Extra origins; the built-in dashboard is same-origin and needs none. |
| `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_BURST` | api | `120` / `40` | Per-key/IP token bucket (Redis). |
| `WS_MAX_CONNECTIONS` | api | `50` | WebSocket client cap. |
| `PUBLIC_API_BASE_URL` | both | empty | Public API URL (used in alert links). |
| `DASHBOARD_BASE_URL` | scanner | empty | Optional dashboard link base for alerts. |
| `PUMPPORTAL_ENABLED` / `PUMPPORTAL_WS_URL` | scanner | `true` / `wss://pumpportal.fun/api/data` | Real-time discovery stream. |
| `PUMPPORTAL_MAX_TRADE_SUBSCRIPTIONS` | scanner | `250` | Upper bound of token trade subscriptions. |
| `DEXSCREENER_ENABLED` / `DEXSCREENER_BASE_URL` | scanner | `true` / `https://api.dexscreener.com` | Pool data for migrated tokens, SOL price. |
| `DEXSCREENER_RPM_PAIRS` / `DEXSCREENER_RPM_OTHER` | scanner | `300` / `60` | Documented rate limits. |
| `HELIUS_ENABLED` / `HELIUS_API_KEY` | scanner | `true` / empty | Holder distribution, creator history, funding analysis. Without a key: public RPC fallback, DAS/enhanced data unavailable. |
| `HELIUS_RPC_URL` / `HELIUS_API_BASE_URL` | scanner | helius defaults | Endpoints. |
| `HELIUS_RPC_RPS` / `HELIUS_ENHANCED_RPS` | scanner | `10` / `2` | Free-tier rate limits. |
| `HELIUS_DAILY_CREDIT_BUDGET` / `HELIUS_MONTHLY_CREDIT_BUDGET` | scanner | `30000` / `1000000` | Credit budgets enforced in Redis. |
| `SOLANA_FALLBACK_RPC_URL` | scanner | public mainnet RPC | Used only without a Helius key. |
| `SOL_USD_FALLBACK_PRICE` | scanner | empty | Last-resort SOL price if DexScreener is unreachable. |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | scanner | empty | GPT analyst credentials. |
| `AI_ENABLED` / `AI_MODEL` | scanner | `true` / `gpt-5.6` | Model is verified at start; no silent substitution. |
| `AI_VERIFY_MODEL_ON_START` | scanner | `true` | Calls `models.retrieve`. |
| `AI_REASONING_EFFORT` | scanner | empty | Optional `low/medium/high` if the model supports it. |
| `AI_MAX_OUTPUT_TOKENS` / `AI_TIMEOUT_SECONDS` / `AI_MAX_RETRIES` | scanner | `3000` / `90` / `2` | Call limits. |
| `AI_INPUT_COST_PER_1M_USD` / `AI_OUTPUT_COST_PER_1M_USD` | scanner | placeholders | **Set to current OpenAI pricing** – drives spend limits and cost dashboard. |
| `AI_DAILY_SPEND_LIMIT_USD` / `AI_MONTHLY_SPEND_LIMIT_USD` | scanner | `1.5` / `25` | Hard stops. |
| `AI_MAX_ANALYSES_PER_DAY` / `AI_MAX_ANALYSES_PER_TOKEN` | scanner | `60` / `2` | Caps. |
| `AI_CACHE_TTL_SECONDS` | scanner | `900` | Context-hash cache. |
| `TELEGRAM_ENABLED` / `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | scanner | `false` | Alerts. |
| `TELEGRAM_ALERT_MIN_SCORE` / `TELEGRAM_SEND_WATCHLIST` | scanner | `72` / `false` | Alert thresholds. |
| `TELEGRAM_COOLDOWN_SECONDS` / `TELEGRAM_URGENT_COOLDOWN_SECONDS` / `TELEGRAM_MAX_ALERTS_PER_HOUR` | scanner | `1800` / `600` / `10` | Spam control. |
| `SCANNER_MAX_TRACKED_TOKENS` | scanner | `250` | Memory/subscription bound. |
| `SCANNER_TRACK_DURATION_MINUTES` | scanner | `90` | Tracking window per token. |
| `SCANNER_SNAPSHOT_SCHEDULE_MINUTES` | scanner | `1,3,5,10,15` | ML snapshot schedule. |
| `SCANNER_PERIODIC_SNAPSHOT_INTERVAL_SECONDS` | scanner | `60` | Re-evaluation cadence. |
| `SCANNER_HEARTBEAT_INTERVAL_SECONDS` / `SCANNER_STALE_AFTER_SECONDS` | scanner | `15` / `90` | Heartbeat + stale detection. |
| `SCANNER_LEADER_LOCK_TTL_SECONDS` | scanner | `30` | Single-active-scanner lock. |
| `SCANNER_HTTP_HEALTH_ENABLED` / `SCANNER_HTTP_HEALTH_PORT` | scanner | `true` / `8081` | Worker health endpoint. |
| `SCANNER_OUTCOME_HORIZON_MINUTES` / `SCANNER_OUTCOME_LABEL_DELAY_MINUTES` | scanner | `60` / `65` | Outcome labelling horizon. |
| `SCANNER_PUMP_*` | scanner | pump.fun constants | Bonding-curve constants (verify against pump.fun). |
| `FILTER_*` | scanner | see `backend/config/settings.py` | All stage thresholds. |
| `RANK_*` | scanner | see settings | Component weights, candidate/watchlist/alert/GPT score thresholds, hourly candidate cap. |
| `WHALE_*` | scanner | see settings | Whale thresholds, smart-money criteria, cluster confidence. |
| `EXEC_*` | both | `100,250,500`, fees | Execution analyzer sizes and fee assumptions. |
| `RISK_*` | both | 1% / 2.5% / 2 / 6% / 10,15,20 | Paper-trading reference risk settings. |
| `ML_*` | scanner | see settings | Minimum samples, retrain interval, experimental threshold, execution cost for labels. |
| `RETENTION_*` | scanner | 3 / 30 / 30 / 180 / 45 | Retention days: raw events, transactions, wallet transactions, alerts, unlabelled snapshots (labelled/scored snapshots and training samples are never pruned). |
| `COST_RAILWAY_ESTIMATE_USD_MONTH` / `COST_HELIUS_PLAN_USD_MONTH` | api | `20` / `0` | Display-only cost assumptions. |
