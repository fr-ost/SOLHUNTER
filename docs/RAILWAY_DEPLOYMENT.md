# Deploying GemFinder AI to Railway (GitHub → Railway)

This guide deploys four Railway services from this repository: **gemfinder-api**, **gemfinder-scanner**,
**PostgreSQL** and **Redis**. Both application services build from the same repo with different Dockerfiles
and start commands. The scanner is a persistent worker – never configure it as a cron job.

> Railway's UI and feature names change. Verify against https://docs.railway.com before relying on
> any step marked *(verify)*. Note that Railway announced that config-as-code files (`railway.json` /
> `railway.toml`) are deprecated for new services in favour of Infrastructure-as-Code, so this project
> relies on **Dockerfiles + service settings/variables**, which work regardless.

## 0. Prerequisites

* A GitHub repository containing this code (push your branch/main).
* A Railway account and project.
* Credentials you want to use (all optional at first boot, but the product needs them to be useful):
  * Helius API key (free tier) – holder data, creator history, credit-budgeted.
  * OpenAI API key with access to the configured model (`AI_MODEL`, default `gpt-5.6`).
  * Telegram bot token (from @BotFather) and your chat id.

## 1. Create the databases

1. In the Railway project: **+ New → Database → PostgreSQL**. Name it `Postgres`.
2. **+ New → Database → Redis**. Name it `Redis`.
3. Railway exposes connection variables on these services (`DATABASE_URL`, `REDIS_URL`, plus public
   variants). Use the **private** URLs via reference variables (next steps) so traffic stays inside the
   project network and is not billed as egress.

## 2. Create the API service (`gemfinder-api`)

1. **+ New → GitHub Repo** → select this repository (install the Railway GitHub app if asked).
2. Rename the service to `gemfinder-api`.
3. **Settings → Build**: builder *Dockerfile*; Dockerfile path `Dockerfile.api`
   *(verify: if the UI has no Dockerfile-path field, set the service variable `RAILWAY_DOCKERFILE_PATH=Dockerfile.api`)*.
4. **Settings → Deploy**:
   * Start command: leave empty (the image's `CMD` runs `scripts/start_api.sh`), or set `./scripts/start_api.sh`.
   * Health check path: `/health` (deploy-time check). Health check timeout: 120s.
   * Restart policy: *On failure* (or *Always*).
   * Replicas: 1 (the API is stateless, more replicas are safe; migrations are advisory-locked).
5. **Settings → Networking**: *Generate domain* → you get `https://gemfinder-api-….up.railway.app`.
   Railway injects `PORT`; the app binds `0.0.0.0:$PORT`.
6. **Variables** (see `docs/ENVIRONMENT_VARIABLES.md` for the full list):

   | Variable | Value |
   |---|---|
   | `APP_ENV` | `production` |
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
   | `REDIS_URL` | `${{Redis.REDIS_URL}}` |
   | `RUN_MIGRATIONS_ON_START` | `true` (**only** on the API) |
   | `API_KEYS` | a long random string (used by the Chrome extension) |
   | `API_ADMIN_KEYS` | another long random string (settings changes) |
   | `CHROME_EXTENSION_ID` | the id shown in `chrome://extensions` after loading the extension |
   | `PUBLIC_API_BASE_URL` | the generated domain, e.g. `https://gemfinder-api-production.up.railway.app` |
   | `LOG_FORMAT` | `json` |
   | `AI_ENABLED` / `TELEGRAM_ENABLED` | `false` on the API (the scanner owns these integrations) |

   Generate keys with `openssl rand -hex 32`.

7. Deploy. Watch the logs for `migrations.completed` and `api.starting`. Open `https://<domain>/ready` –
   it must return `{"status":"ready","database":true,"redis":true,...}`.

## 3. Create the scanner service (`gemfinder-scanner`)

1. **+ New → GitHub Repo** → the same repository again. Rename to `gemfinder-scanner`.
2. **Settings → Build**: Dockerfile path `Dockerfile.worker` (or `RAILWAY_DOCKERFILE_PATH=Dockerfile.worker`).
3. **Settings → Deploy**: start command empty (image runs `scripts/start_scanner.sh`), restart policy *Always*,
   replicas **1**. Do **not** add a cron schedule – the worker runs continuously and holds a Redis
   leader lock so an overlapping redeploy never scans twice.
   Health check: optional. The worker serves `GET /health` on `SCANNER_HTTP_HEALTH_PORT` (8081). If you
   want Railway to check it, set `PORT=8081` on this service and health check path `/health` *(verify)*.
   No public domain is needed.
4. **Variables**:

   | Variable | Value |
   |---|---|
   | `APP_ENV` | `production` |
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
   | `REDIS_URL` | `${{Redis.REDIS_URL}}` |
   | `RUN_MIGRATIONS_ON_START` | `false` (the scanner waits for the API's migration) |
   | `HELIUS_API_KEY` | your key (optional; without it holder data is limited) |
   | `OPENAI_API_KEY`, `AI_ENABLED=true`, `AI_MODEL=gpt-5.6` | GPT analyst (optional) |
   | `AI_INPUT_COST_PER_1M_USD`, `AI_OUTPUT_COST_PER_1M_USD` | current OpenAI prices for your model (used for spend limits) |
   | `AI_DAILY_SPEND_LIMIT_USD`, `AI_MONTHLY_SPEND_LIMIT_USD` | e.g. `1.5` / `25` |
   | `TELEGRAM_ENABLED=true`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | alerts (optional) |
   | `DASHBOARD_BASE_URL` / `PUBLIC_API_BASE_URL` | used for links inside Telegram alerts |
   | `LOG_FORMAT` | `json` |
   | `API_KEYS` / `API_ADMIN_KEYS` | not needed on the scanner |

5. Deploy. Expected log sequence: `migrations.schema_ready` → `heartbeat.leader_acquired` →
   `pumpportal.connected` → `scanner.started`. The API's `/api/v1/status/system` now shows the scanner
   heartbeat as `running`.

## 4. Wire the Chrome extension

Build and load the extension (`docs/EXTENSION.md`). In its Settings page enter the API domain and the
`API_KEYS` value. Copy the extension id into the API service variable `CHROME_EXTENSION_ID` and redeploy
the API so CORS allows the `chrome-extension://<id>` origin.

## 5. Migrations strategy

* Only the API runs `alembic upgrade head` at start, inside a PostgreSQL advisory lock
  (`backend/database/migrate.py`). Multiple API replicas or overlapping deploys cannot race.
* The scanner polls the `alembic_version` table until it matches the code's head revision
  (`MIGRATION_WAIT_TIMEOUT_SECONDS`), then starts. It never runs migrations itself.
* Manual migrations: `railway run --service gemfinder-api ./scripts/migrate.sh` *(verify CLI syntax)*.
* New migrations: `alembic revision --autogenerate -m "..."` locally, commit, deploy the API first.

## 6. Backups

Railway Postgres supports backups from the database service page *(verify your plan)*. Additionally you
can dump with `pg_dump "$DATABASE_PUBLIC_URL" > backup.sql` from your machine. The tables
`token_snapshots`, `training_samples`, `candidate_scores`, `model_versions` are the ML history – never
truncate them in production.

## 7. Operations checklist after the first deploy

- [ ] `GET /ready` returns 200 on the API.
- [ ] `/api/v1/status/system` (with `X-API-Key`) shows `scanner.status = running`, providers
      `pumpportal: connected`.
- [ ] `/api/v1/status/providers` shows `helius` connected (or `solana_rpc` fallback if no key).
- [ ] `/api/v1/status/ai` shows `enabled: true` and the model verified (scanner logs `analyst.model_verified`);
      if the model is unavailable the scanner logs `analyst.model_unavailable` and analysis stays off – fix
      `AI_MODEL` rather than expecting a fallback.
- [ ] Send yourself a test Telegram message: the first candidate alert, or check `/api/v1/history/alerts`.
- [ ] `/api/v1/status/costs` projection is within budget; adjust `AI_*` limits and `HELIUS_*` budgets.
- [ ] Redeploy the scanner once and confirm `heartbeat.leader_acquired` appears only after the old
      instance logged `scanner.stopped` (no duplicate scanning).

## 8. Scaling and cost notes

* Hobby-sized Railway instances are enough initially: API (~150 MB RAM), scanner (~300–500 MB RAM
  depending on `SCANNER_MAX_TRACKED_TOKENS`), Postgres, Redis.
* Postgres growth is dominated by `token_transactions` and `token_snapshots`. Retention defaults prune
  transactions after 120 days and raw events after 7 days; snapshots and training samples are kept.
* Helius free tier (1M credits/month) is budgeted via `HELIUS_DAILY_CREDIT_BUDGET` /
  `HELIUS_MONTHLY_CREDIT_BUDGET`; when exhausted the scanner continues with stream-derived estimates and
  marks data as unavailable.
