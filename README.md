# GemFinder AI

AI-assisted research platform for newly launched Solana tokens (Pump.fun first). It continuously
discovers launches, tracks trading in real time, filters out scams/rugs/manipulation, analyses holders,
developers, whales and smart money, estimates breakout probabilities with validated machine-learning
models, asks GPT for a structured second opinion on the strongest candidates, and surfaces a short
ranked list through Telegram and a Chrome side-panel dashboard.

**It is a decision-support tool, not a trading bot.** It never holds keys, signs transactions or trades.
A day with zero candidates is a normal outcome.

## Repository layout

```
backend/        FastAPI API + scanner worker (Python 3.12, async)
  api/          REST + WebSocket, auth, rate limiting
  scanner/      persistent worker, token lifecycle, evaluator
  providers/    PumpPortal, DexScreener, Helius adapters
  ingestion/    normalisation, dedupe, idempotent persistence
  intelligence/ developer, holder, manipulation, wallet & relationship analysis
  whale_engine/ whale accumulation/distribution signals
  filters/      10-stage explainable filter pipeline
  execution/    liquidity & execution estimates ($100/$250/$500)
  ranking/      weighted candidate ranking
  ml/           features, labelling, XGBoost training, registry
  ai/           GPT analyst with strict schema and spend controls
  notifications/ Telegram alerts
  tests/        pytest suite (unit + integration + end-to-end)
extension/      Chrome extension (Manifest V3 side panel, React + TypeScript)
migrations/     Alembic migrations
scripts/        start/migrate/train scripts used by the containers
docs/           architecture, deployment, providers, ML, API, security, operations
Dockerfile.api / Dockerfile.worker / docker/docker-compose.yml
```

## Quick start (local)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                      # edit DATABASE_URL / REDIS_URL / keys
docker compose -f docker/docker-compose.yml up postgres redis -d
alembic upgrade head
RUN_MIGRATIONS_ON_START=false python -m uvicorn backend.api.main:app --reload --port 8080
python -m backend.scanner.main            # second terminal: the worker
```

Run the tests (they need PostgreSQL and Redis; defaults: `postgresql://postgres@localhost:5433/gemfinder_test`,
`redis://localhost:6380/1`, override with `TEST_DATABASE_URL` / `TEST_REDIS_URL`):

```bash
pytest -q
ruff check backend
```

## Deploy

Follow **docs/RAILWAY_DEPLOYMENT.md** (GitHub → Railway, four services, variables per service,
migration strategy, checklist) and **docs/EXTENSION.md** for the Chrome extension.

## How a candidate is produced

discovery → tracking (real-time trades) → snapshots at 1/3/5/10/15 min → cheap safety filters →
budgeted enrichment (holders, creator history, wallet profiles, clusters) → whale engine → ML
(experimental until validated) → full 10-stage pipeline → weighted ranking with penalties and confidence
→ GPT structured analysis (spend-limited) → Telegram alert → outcome labelling → periodic retraining.

Every rejection has a reason, every number has a source (verified / estimate / prediction), and hard
safety failures override momentum, whales and GPT.

## Documentation

| Doc | Content |
|---|---|
| `docs/ARCHITECTURE.md` | Components, token lifecycle, reliability model |
| `docs/RAILWAY_DEPLOYMENT.md` | Step-by-step deployment and checklist |
| `docs/ENVIRONMENT_VARIABLES.md` | All variables and which service needs them |
| `docs/PROVIDERS.md` | Endpoints, limits, credentials, unverified assumptions |
| `docs/ML.md` | Dataset, labels, validation, versioning |
| `docs/API.md` | Endpoint summary |
| `docs/SECURITY.md` / `docs/OPERATIONS.md` | Security model and runbook |
| `docs/EXTENSION.md` | Building and loading the Chrome extension |
| `IMPLEMENTATION_STATUS.md` | What is done, what is not, known gaps, next tasks |

## Disclaimer

Research targets such as +10/+15/+20 % are scenarios, not promises. Memecoin launches are extremely
risky; most lose value. Nothing here is financial advice.
