<div align="center">

<img width="100%" alt="SolHunter — AI-assisted Solana token research, Pump.fun first" src="https://capsule-render.vercel.app/api?type=waving&amp;color=0:6C2BD9,50:FF2E93,100:FFB703&amp;height=220&amp;section=header&amp;text=SolHunter&amp;fontSize=68&amp;fontColor=ffffff&amp;fontAlignY=32&amp;animation=fadeIn&amp;desc=AI-assisted%20Solana%20token%20research%20%C2%B7%20Pump.fun%20first&amp;descAlignY=62&amp;descAlign=50&amp;descSize=18"/>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://readme-typing-svg.demolab.com?font=Fira+Code&amp;weight=600&amp;size=22&amp;duration=3600&amp;pause=700&amp;color=FFB703&amp;center=true&amp;vCenter=true&amp;width=820&amp;height=48&amp;lines=AI-assisted+research+for+new+Solana+tokens;Pump.fun+launches%2C+tracked+in+real+time;Filters+rugs%2C+whales%2C+and+manipulation;ML+probabilities+plus+a+GPT+second+opinion;Dashboard+and+Telegram.+Never+a+trading+bot." />
  <source media="(prefers-color-scheme: light)" srcset="https://readme-typing-svg.demolab.com?font=Fira+Code&amp;weight=600&amp;size=22&amp;duration=3600&amp;pause=700&amp;color=6C2BD9&amp;center=true&amp;vCenter=true&amp;width=820&amp;height=48&amp;lines=AI-assisted+research+for+new+Solana+tokens;Pump.fun+launches%2C+tracked+in+real+time;Filters+rugs%2C+whales%2C+and+manipulation;ML+probabilities+plus+a+GPT+second+opinion;Dashboard+and+Telegram.+Never+a+trading+bot." />
  <img width="820" alt="AI-assisted research for new Solana tokens. Pump.fun launches, tracked in real time. Filters rugs, whales, and manipulation. ML probabilities plus a GPT second opinion. Dashboard and Telegram. Never a trading bot." src="https://readme-typing-svg.demolab.com?font=Fira+Code&amp;weight=600&amp;size=22&amp;duration=3600&amp;pause=700&amp;color=6C2BD9&amp;center=true&amp;vCenter=true&amp;width=820&amp;height=48&amp;lines=AI-assisted+research+for+new+Solana+tokens;Pump.fun+launches%2C+tracked+in+real+time;Filters+rugs%2C+whales%2C+and+manipulation;ML+probabilities+plus+a+GPT+second+opinion;Dashboard+and+Telegram.+Never+a+trading+bot." />
</picture>

*A 24/7 research engine for newly launched Solana tokens. It ranks a short list. It does not trade.*

<br/>

<img alt="Decision support only" src="https://img.shields.io/badge/decision_support_only-FFB703?style=for-the-badge&amp;labelColor=6C2BD9"/>
<img alt="Pump.fun first" src="https://img.shields.io/badge/Pump.fun_first-FF2E93?style=for-the-badge"/>
<img alt="Does not trade" src="https://img.shields.io/badge/does_not_trade-6C2BD9?style=for-the-badge"/>
<img alt="Solana" src="https://img.shields.io/badge/Solana-9945FF?style=for-the-badge&amp;logo=solana&amp;logoColor=white"/>

<br/>

<img alt="Python 3.12" src="https://img.shields.io/badge/Python_3.12-3776AB?style=for-the-badge&amp;logo=python&amp;logoColor=white"/>
<img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&amp;logo=fastapi&amp;logoColor=white"/>
<img alt="React 18" src="https://img.shields.io/badge/React_18-61DAFB?style=for-the-badge&amp;logo=react&amp;logoColor=1A1024"/>
<img alt="TypeScript" src="https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&amp;logo=typescript&amp;logoColor=white"/>
<img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&amp;logo=postgresql&amp;logoColor=white"/>
<img alt="Redis" src="https://img.shields.io/badge/Redis-FF4438?style=for-the-badge&amp;logo=redis&amp;logoColor=white"/>

<br/>

<img alt="Docker" src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&amp;logo=docker&amp;logoColor=white"/>
<img alt="Railway" src="https://img.shields.io/badge/Railway-6C2BD9?style=for-the-badge&amp;logo=railway&amp;logoColor=white"/>
<img alt="Telegram alerts" src="https://img.shields.io/badge/Telegram_alerts-FF2E93?style=for-the-badge&amp;logo=telegram&amp;logoColor=white"/>
<img alt="XGBoost" src="https://img.shields.io/badge/XGBoost-FFB703?style=for-the-badge&amp;labelColor=1A1024"/>
<img alt="OpenAI" src="https://img.shields.io/badge/OpenAI-412991?style=for-the-badge&amp;logo=openai&amp;logoColor=white"/>

<br/>

<a href="#overview">Overview</a> ·
<a href="#key-features">Features</a> ·
<a href="#tech-stack">Stack</a> ·
<a href="#getting-started">Getting started</a> ·
<a href="#configuration">Configuration</a> ·
<a href="#usage">Usage</a> ·
<a href="#project-structure">Structure</a> ·
<a href="#documentation">Docs</a>

</div>

> **Decision support only. Not a trading bot.** SolHunter never holds keys, signs transactions, or trades. A day with zero candidates is a normal outcome.

<a id="overview"></a>

## 🔭 Overview

SolHunter is an AI-assisted research engine for newly launched Solana tokens, **Pump.fun first**. It is meant to run 24/7 on [Railway](https://railway.app) as a **single service**: one container serves the API, the built-in web dashboard, and the scanner.

It discovers launches in real time, filters out scams, rugs, and manipulation, analyses holders, developers, whales, and smart money, estimates breakout probabilities with validated machine-learning models, asks GPT for a structured second opinion on the strongest candidates, and surfaces a **short ranked list** in the dashboard and, optionally, on Telegram.

Paper trading and a manual journal live in the dashboard as research notes. Position sizes shown there are reference maths. The system does not send them.

<a id="key-features"></a>

## ⚡ Key features

| | |
| --- | --- |
| **Live discovery** | PumpPortal WebSocket for new Pump.fun tokens, with bounded trade subscriptions and reconnect. |
| **Market data** | DexScreener for migrated pools, token profiles and boosts, and the SOL price. Public Solana RPC is the fallback when Helius is unset. |
| **On-chain enrichment** | Budgeted Helius calls for holder distribution, creator history, and funders. Without a key, the scanner keeps stream-derived estimates and lowers confidence. |
| **Lifecycle-aware filters** | A two-minute-old launch is not judged like a day-old token. Age-scaled limits, a **screening** status, and hard failures that override momentum, whales, and GPT. |
| **Growth tracking** | After the live window, survivors are re-checked (about 30 minutes to 7 days). Strong, liquid growers go back into real-time tracking. |
| **Whale engine** | Eight signals, including accumulation, distribution, exit warning, insider selling, coordinated buying, and smart-money consensus. |
| **Machine learning** | XGBoost models for rug risk, breakout, and expected return. Walk-forward validation. **Experimental until enough labelled samples exist.** |
| **GPT analyst** | Structured verdict on candidates above the score bar. Spend limits, cache, and no silent model substitution. |
| **Alerts** | Optional Telegram alerts with cooldowns, an hourly cap, and urgent warnings. |
| **Dashboard** | React app served by the API at `/`, with live WebSocket updates. Every figure is tagged **verified**, **estimate**, or **prediction**. |
| **Wallet tracker** | Follow wallets and see their Pump.fun / PumpSwap trades. Telegram alerts are optional. A buy can start live tracking so that token runs through the same pipeline. |
| **Explainability** | Every rejection has a reason. Every number has a source. |

<details>
<summary><b>The ten filter stages</b></summary>

<br/>

A cheap pass runs on stream data before any paid enrichment: data validation, developer, holders, liquidity, buyer quality, trading pressure, momentum, and manipulation. Relationships and the ML stage run with the full pipeline. When Helius holders, creator history, or wallet profiles are present, the earlier stages use them and raise confidence.

| # | Stage | What it looks at |
| --- | --- | --- |
| 1 | Data validation | Mint, price, age, trade count, stale or contradictory inputs |
| 2 | Developer | Creator sells, prior rugs, dev holding |
| 3 | Holders | Concentration, holder count, top-holder share (age-scaled) |
| 4 | Relationships | Wallet clusters and linked funding |
| 5 | Liquidity | Depth in USD and SOL, sell impact at the reference size |
| 6 | Buyer quality | Unique buyers and organic flow |
| 7 | Trading pressure | Buy versus sell pressure |
| 8 | Momentum | Short-window price and trade momentum |
| 9 | Manipulation | Bundles, wash patterns, confirmed manipulation |
| 10 | ML | Rug, breakout, and return predictions |

Hard safety failures are final: liquidity pull, developer dump, a launch bundle still holding a large share after a few minutes, confirmed manipulation, whale exit. Soft misses stay in **screening** and keep being evaluated. Nothing is promoted or alerted before `RANK_MIN_CANDIDATE_AGE_MINUTES` (default 15), except tokens opened from the wallet tracker.

</details>

<a id="tech-stack"></a>

## 🧰 Tech stack

| Layer | What this repo uses |
| --- | --- |
| Language | Python 3.12 (`requires-python >= 3.12`) |
| API | FastAPI, Uvicorn, Pydantic, SQLAlchemy 2 (async), asyncpg, Alembic |
| Dashboard | React 18, TypeScript, Vite, Recharts. Node 20.19 or newer (the image builds with Node 22) |
| Data | PostgreSQL 16, Redis 7 |
| ML | XGBoost (CPU wheel), scikit-learn, NumPy, joblib |
| Providers | PumpPortal, DexScreener, Helius, OpenAI, Telegram |
| Runtime | Docker (one image, `SERVICE_ROLE=all\|api\|scanner`), Railway |

<a id="getting-started"></a>

## 🚀 Getting started

SolHunter is three Railway services: **this app** (one container: API + dashboard + scanner) + **Railway PostgreSQL** + **Railway Redis**.

Follow **[docs/RAILWAY_DEPLOYMENT.md](docs/RAILWAY_DEPLOYMENT.md)**. It covers where to get every key (Helius, OpenAI, Telegram), the Railway clicks and variables, and how to verify the deploy. Then open your Railway URL and log in with the `PASSWORD` you chose.

Keep the SolHunter service at **one replica**. The scanner takes a Redis leader lock and must run once. Health check path: `/health` (the first boot runs migrations; the deployment doc uses a 300 s timeout). `scripts/start.sh` is the container entrypoint.

| `SERVICE_ROLE` | What that process runs |
| --- | --- |
| `all` (default) | API + dashboard + scanner. This is the Railway setup. |
| `api` | API + dashboard only |
| `scanner` | Scanner worker only. Leave migrations to the API (`RUN_MIGRATIONS_ON_START=false` on a split scanner). |

<details>
<summary><b>Local development</b></summary>

<br/>

Python 3.12, Node 20.19+, Docker (for Postgres and Redis).

```bash
python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
cp .env.example .env                        # edit DATABASE_URL / REDIS_URL / keys
docker compose -f docker/docker-compose.yml up postgres redis -d
(cd web && npm install && npm run build)    # dashboard served by the API at /
SERVICE_ROLE=all python -m uvicorn backend.api.main:app --reload --port 8080
pytest -q && ruff check backend             # tests need PostgreSQL + Redis (see backend/tests/conftest.py)
```

`docker/docker-compose.yml` is the **local** stack, not the Railway config. It publishes PostgreSQL as user `solhunter`, password `solhunter`, database `solhunter`, on `localhost:5432`, and Redis on `localhost:6379`. `.env.example` ships with `postgresql://postgres:postgres@localhost:5432/solhunter`, so point `DATABASE_URL` at the compose user before you start the API. `REDIS_URL=redis://localhost:6379/0` matches compose.

The full local stack, image included, is `docker compose -f docker/docker-compose.yml up --build` from the repo root.

Integration tests read `TEST_DATABASE_URL` (default `postgresql://postgres@localhost:5433/solhunter_test`) and `TEST_REDIS_URL` (default `redis://localhost:6380/1`). They skip when those services are unreachable. See `backend/tests/conftest.py`.

Manual helpers, if you are not letting the process migrate or train itself:

```bash
./scripts/migrate.sh          # python -m alembic upgrade head
./scripts/train_models.sh     # python -m backend.ml.train
```

</details>

<details>
<summary><b>Dashboard package only</b></summary>

<br/>

From `web/` (also written up in [`web/README.md`](web/README.md)):

```bash
npm install
npm run typecheck
npm run build                                         # -> dist/ (copied into the API image)
VITE_API_BASE=http://localhost:8080 npm run dev       # Vite dev server, API elsewhere
```

</details>

<a id="configuration"></a>

## ⚙️ Configuration

Copy [`.env.example`](.env.example) to `.env` for local work. **Never commit a real `.env`.** On Railway, set the same names as service variables. Defaults, which service needs each name, and what it does are in **[docs/ENVIRONMENT_VARIABLES.md](docs/ENVIRONMENT_VARIABLES.md)**.

Names only below. No values.

Runtime thresholds in the `FILTER_*`, `RANK_*`, `WHALE_*`, `SCANNER_*`, `TELEGRAM_*`, `EXEC_*`, `RISK_*`, `ML_*`, `AI_*`, and `GROWTH_*` groups can also be changed from the dashboard Settings page (`PUT /api/v1/settings`, admin key). Environment values are the defaults. Database overrides win.

| Name | Needed for | Without it |
| --- | --- | --- |
| `PASSWORD` | Dashboard login, and settings access | In production the API refuses requests |
| `HELIUS_API_KEY` | Holder distribution, creator history, funders | Stream-based estimates only, lower confidence |
| `OPENAI_API_KEY` | GPT analysis | Ranking continues without the GPT component; alerts go out immediately |
| `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` | Phone or channel alerts | Alerts are stored as suppressed. `TELEGRAM_ENABLED` defaults off |

`AI_MODEL` is verified at start (`AI_VERIFY_MODEL_ON_START`). If that model is unavailable, GPT analysis is disabled and the dashboard says so. SolHunter does not swap in a different model. Set `AI_INPUT_COST_PER_1M_USD` and `AI_OUTPUT_COST_PER_1M_USD` to the current price of the model you configured so spend limits and the cost page stay honest.

<details>
<summary><b>Environment variable names</b></summary>

<br/>

**Application.** `APP_ENV` · `SERVICE_ROLE` · `LOG_LEVEL` · `LOG_FORMAT` · `PORT` · `PUBLIC_API_BASE_URL` · `DASHBOARD_BASE_URL`

**Database and Redis.** `DATABASE_URL` · `REDIS_URL` · `RUN_MIGRATIONS_ON_START` · `MIGRATION_WAIT_TIMEOUT_SECONDS` · `DB_POOL_SIZE` · `DB_MAX_OVERFLOW`

**API security.** `PASSWORD` · `API_KEYS` · `API_ADMIN_KEYS` · `CORS_ALLOWED_ORIGINS` · `RATE_LIMIT_PER_MINUTE` · `RATE_LIMIT_BURST` · `WS_MAX_CONNECTIONS`

**Providers.** `PUMPPORTAL_ENABLED` · `PUMPPORTAL_WS_URL` · `PUMPPORTAL_MAX_TRADE_SUBSCRIPTIONS` · `DEXSCREENER_ENABLED` · `DEXSCREENER_BASE_URL` · `DEXSCREENER_RPM_PAIRS` · `DEXSCREENER_RPM_OTHER` · `HELIUS_ENABLED` · `HELIUS_API_KEY` · `HELIUS_RPC_URL` · `HELIUS_API_BASE_URL` · `HELIUS_RPC_RPS` · `HELIUS_ENHANCED_RPS` · `HELIUS_DAILY_CREDIT_BUDGET` · `HELIUS_MONTHLY_CREDIT_BUDGET` · `SOLANA_FALLBACK_RPC_URL` · `SOL_USD_FALLBACK_PRICE`

**OpenAI.** `OPENAI_API_KEY` · `OPENAI_BASE_URL` · `AI_ENABLED` · `AI_MODEL` · `AI_VERIFY_MODEL_ON_START` · `AI_REASONING_EFFORT` · `AI_MAX_OUTPUT_TOKENS` · `AI_TIMEOUT_SECONDS` · `AI_MAX_RETRIES` · `AI_INPUT_COST_PER_1M_USD` · `AI_OUTPUT_COST_PER_1M_USD` · `AI_DAILY_SPEND_LIMIT_USD` · `AI_MONTHLY_SPEND_LIMIT_USD` · `AI_MAX_ANALYSES_PER_DAY` · `AI_MAX_ANALYSES_PER_TOKEN` · `AI_CACHE_TTL_SECONDS`

**Telegram.** `TELEGRAM_ENABLED` · `TELEGRAM_BOT_TOKEN` · `TELEGRAM_CHAT_ID` · `TELEGRAM_ALERT_MIN_SCORE` · `TELEGRAM_SEND_WATCHLIST` · `TELEGRAM_COOLDOWN_SECONDS` · `TELEGRAM_URGENT_COOLDOWN_SECONDS` · `TELEGRAM_MAX_ALERTS_PER_HOUR`

**Scanner and wallets.** `SCANNER_MAX_TRACKED_TOKENS` · `SCANNER_TRACK_DURATION_MINUTES` · `SCANNER_SNAPSHOT_SCHEDULE_MINUTES` · `SCANNER_PERIODIC_SNAPSHOT_INTERVAL_SECONDS` · `SCANNER_HEARTBEAT_INTERVAL_SECONDS` · `SCANNER_STALE_AFTER_SECONDS` · `SCANNER_LEADER_LOCK_TTL_SECONDS` · `SCANNER_HTTP_HEALTH_ENABLED` · `SCANNER_HTTP_HEALTH_PORT` · `SCANNER_MIN_FIRST_MINUTE_TRADES` · `SCANNER_OUTCOME_HORIZON_MINUTES` · `SCANNER_OUTCOME_LABEL_DELAY_MINUTES` · `SCANNER_PUMP_*` · `MAX_TRACKED_WALLETS` · `WALLET_ALERT_COOLDOWN_SECONDS`

**Threshold groups.** `FILTER_*` (including the age curves) · `RANK_*` · `RANK_MIN_CANDIDATE_AGE_MINUTES` · `GROWTH_*` · `WHALE_*` · `EXEC_*` · `RISK_*` · `ML_*` · `RETENTION_*`

**Cost display (not billing).** `COST_RAILWAY_ESTIMATE_USD_MONTH` · `COST_HELIUS_PLAN_USD_MONTH`

**Tests.** `TEST_DATABASE_URL` · `TEST_REDIS_URL`

</details>

<a id="usage"></a>

## 📡 Usage

Open the Railway URL (or `http://localhost:8080` locally). Sign in with `PASSWORD`. Optional `API_KEYS` are extra read-only keys; optional `API_ADMIN_KEYS` can change settings. The password already covers both. The key stays in this browser's `localStorage` only. The API base defaults to the page origin. An override sits under **Advanced** on the login screen and in Settings.

The scanner, with `SERVICE_ROLE=all`, starts in the same process. The sidebar shows a live badge when the WebSocket is receiving scanner events.

```
discovery → real-time tracking → snapshots at 1/3/5/10/15 min
→ cheap safety filters → budgeted enrichment (holders, creator history, wallet profiles, clusters)
→ whale engine → ML (experimental until validated) → full 10-stage pipeline
→ weighted ranking with penalties and confidence → GPT structured analysis (spend-limited)
→ Telegram alert → outcome labelling → periodic retraining
```

| Phase | Age | What happens |
| --- | --- | --- |
| **Launch** | 0–30 min | Real-time tracking. Limits scale with age. Soft misses stay in screening. Hard failures stop the token. |
| **Survival** | 30 min–24 h | Live tracking ends after `SCANNER_TRACK_DURATION_MINUTES` (default 90). The growth tracker re-checks with DexScreener. A strong score plus enough liquidity puts the token back on the live pipeline. |
| **Established** | 1–7 days | Same growth checks, tighter concentration limits. The 24-hour labels matter most. After 7 days the token is archived. |
| **Dead / archived** | — | No liquidity and no volume, a hard failure, or too old. |

Discovery is not only forward-looking. Migrations of tokens the scanner never saw are tracked, and DexScreener profiles and boosts are polled so a token launched before the process started can still be found.

| Page | What it is for |
| --- | --- |
| Dashboard | Scanner heartbeat, provider health, and the current short list |
| Candidates | Ranked candidates, plus a detail view with charts |
| Survivors | Tokens from 30 minutes to 7 days old, re-checked for growth |
| Wallet tracker | Wallets you follow, their trades, optional Telegram alerts |
| Wallets | Profiles built from observed flow |
| Token search | Look up a mint and track it |
| History | Past candidates and realised outcomes |
| Paper trading | Reference positions and sizing. Nothing is executed |
| Journal | Manual notes beside the research |
| Status | System, leader lock, models |
| Logs | Recent structured logs |
| Settings | Runtime overrides of the threshold groups above |

Outcome labels used for training include the launch-window **+10 / +15 / +20 %** scenarios and, once enough growth snapshots exist, 24-hour **+50 % / +100 %** labels. Those are training targets, not promises. See the disclaimer.

GPT recommendations are `STRONG_CANDIDATE`, `WATCHLIST`, `HIGH_RISK`, `REJECT`, and `INSUFFICIENT_DATA`. A `REJECT` or `HIGH_RISK` verdict downgrades a candidate to the watchlist. The alert waits for the verdict when GPT is enabled and within budget.

<a id="project-structure"></a>

## 🗂️ Project structure

```
backend/    FastAPI API + dashboard server + scanner worker (Python 3.12, async)
web/        Browser dashboard (React + TypeScript), built into the image by the Dockerfile
migrations/ Alembic migrations
scripts/    start.sh (SERVICE_ROLE=all|api|scanner), migrate, train
docs/       deployment, variables, architecture, providers, ML, API, security, operations, checklist
Dockerfile  One image for every role
docker/     docker-compose.yml — local stack
```

| Package | Responsibility |
| --- | --- |
| `config` | Settings and structured logs with secret scrubbing |
| `providers` | PumpPortal, DexScreener, Helius, SOL price. Rate limits and credit budgets |
| `ingestion` | Normalised events, dedupe, idempotent writes |
| `scanner` | Tracker, snapshots, growth, heartbeat, leader lock |
| `intelligence` | Developer, holders, manipulation, wallet graph |
| `whale_engine` | Whale and smart-money signals |
| `filters` / `ranking` / `execution` | Ten stages, weighted score, liquidity estimates at $100 / $250 / $500 |
| `ml` | Features, labels, training, registry, predictor |
| `ai` | GPT analyst, spend limits, queue |
| `notifications` | Telegram |
| `api` | REST, WebSocket, and the built `web/dist` dashboard |

<a id="documentation"></a>

## 📚 Documentation

| Doc | Content |
| --- | --- |
| [`docs/RAILWAY_DEPLOYMENT.md`](docs/RAILWAY_DEPLOYMENT.md) | Keys, Railway setup, variables, verification, troubleshooting |
| [`docs/ENVIRONMENT_VARIABLES.md`](docs/ENVIRONMENT_VARIABLES.md) | Every variable with defaults |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How a token moves through the system |
| [`docs/PROVIDERS.md`](docs/PROVIDERS.md) | PumpPortal, DexScreener, Helius |
| [`docs/ML.md`](docs/ML.md) | Features, labels, training, experimental policy |
| [`docs/API.md`](docs/API.md) | HTTP and WebSocket surface |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Auth, secrets, what is never stored |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Running it day to day |
| [`docs/DEPLOYMENT_CHECKLIST.md`](docs/DEPLOYMENT_CHECKLIST.md) | First-deploy checks |
| [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) | What is done, known gaps, next tasks |

<a id="disclaimer"></a>

## ⚠️ Disclaimer

Research targets such as +10 / +15 / +20 %, and the 24-hour +50 / +100 % labels, are scenarios, not promises. Memecoin launches are extremely risky; most lose value. Nothing here is financial advice.

<a id="credits"></a>

## 💜 Credits

Built by **[Shahriar Ahmed](https://github.com/fr-ost)**, Co-Founder & COO of **[Unique Labs](https://www.uniquelabs.org)**.

[shahriarahmed.net](https://shahriarahmed.net) · [X / @0x_nation](https://x.com/0x_nation) · [Unique Labs](https://www.uniquelabs.org)

<div align="center">

<img width="100%" alt="Footer wave" src="https://capsule-render.vercel.app/api?type=waving&amp;color=0:FFB703,50:FF2E93,100:6C2BD9&amp;height=120&amp;section=footer"/>

</div>
