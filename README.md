# SolHunter

AI-assisted research engine for newly launched Solana tokens (Pump.fun first). It runs 24/7 on Railway as a
single service: it discovers launches in real time, filters out scams, rugs and manipulation, analyses
holders, developers, whales and smart money, estimates breakout probabilities with validated machine-
learning models, asks GPT for a structured second opinion on the strongest candidates, and surfaces a
short ranked list in its **built-in web dashboard** and via **Telegram**.

**Decision support only, not a trading bot.** It never holds keys, signs transactions or trades. A day with
zero candidates is a normal outcome.

## Deploy in three services

SolHunter (one container: API + dashboard + scanner) + Railway PostgreSQL + Railway Redis.
Follow **[docs/RAILWAY_DEPLOYMENT.md](docs/RAILWAY_DEPLOYMENT.md)** — it covers where to get every key
(Helius, OpenAI, Telegram), the exact Railway clicks and variables, and verification. Then open your
Railway URL in a browser and log in with the `API_KEYS` value you chose.

## What is inside

```
backend/   FastAPI API + dashboard server + scanner worker (Python 3.12, async)
web/       Browser dashboard (React + TypeScript), built into the image by the Dockerfile
migrations/ Alembic migrations          scripts/  start.sh (SERVICE_ROLE=all|api|scanner), migrate, train
docs/      deployment, variables, architecture, providers, ML, API, security, operations, checklist
Dockerfile One image for every role     docker/docker-compose.yml  local stack
```

Pipeline: discovery → real-time tracking → snapshots at 1/3/5/10/15 min → cheap safety filters →
budgeted enrichment (holders, creator history, wallet profiles, clusters) → whale engine → ML
(experimental until validated) → full 10-stage pipeline → weighted ranking with penalties and confidence
→ GPT structured analysis (spend-limited) → Telegram alert → outcome labelling → periodic retraining.
Every rejection has a reason, every number has a source (verified / estimate / prediction), and hard
safety failures override momentum, whales and GPT.

## Local development

```bash
python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
cp .env.example .env                        # edit DATABASE_URL / REDIS_URL / keys
docker compose -f docker/docker-compose.yml up postgres redis -d
(cd web && npm install && npm run build)    # dashboard served by the API at /
SERVICE_ROLE=all python -m uvicorn backend.api.main:app --reload --port 8080
pytest -q && ruff check backend             # tests need PostgreSQL + Redis (see backend/tests/conftest.py)
```

## Documentation

| Doc | Content |
|---|---|
| `docs/RAILWAY_DEPLOYMENT.md` | Keys, Railway setup, variables, verification, troubleshooting |
| `docs/ENVIRONMENT_VARIABLES.md` | Every variable with defaults |
| `docs/ARCHITECTURE.md` · `docs/PROVIDERS.md` · `docs/ML.md` · `docs/API.md` | How it works |
| `docs/SECURITY.md` · `docs/OPERATIONS.md` · `docs/DEPLOYMENT_CHECKLIST.md` | Running it safely |
| `IMPLEMENTATION_STATUS.md` | What is done, known gaps, next tasks |

## Disclaimer

Research targets such as +10/+15/+20 % are scenarios, not promises. Memecoin launches are extremely
risky; most lose value. Nothing here is financial advice.
