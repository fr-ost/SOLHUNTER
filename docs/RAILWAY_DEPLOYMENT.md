# SolHunter on Railway — complete setup guide

SolHunter runs 24/7 as **one Railway service** (API + web dashboard + scanner in a single container) plus
Railway's **PostgreSQL** and **Redis**. You control everything from the dashboard at your Railway URL.
Total: 3 services, about 30 minutes of setup. No local machine is needed after the first push.

> Railway's UI changes over time; names below match the current dashboard. Where a step may differ it is
> marked *(check)*.

---

## Part A — Collect your keys (10 minutes)

You need three kinds of keys. Only the first is mandatory to boot; the others unlock features.

### A1. Your dashboard password (mandatory)
Pick any password you like (plain text, e.g. `moonshot2026`). You will set it as `PASSWORD` and type it
once on the dashboard login screen. It also unlocks the Settings page.

### A2. Helius API key (strongly recommended, free)
Helius provides on-chain holder data, creator history and wallet funding links.
1. Go to https://dashboard.helius.dev and sign up (email or GitHub).
2. Create a project → **API Keys** → copy the key (a UUID like `1a2b3c4d-...`).
3. Free plan is enough to start (1M credits/month). SolHunter budgets credits automatically
   (`HELIUS_DAILY_CREDIT_BUDGET`, default 30 000/day).
Without it SolHunter still runs using stream-derived estimates, with lower confidence.

### A3. OpenAI API key (optional — GPT research analyst)
1. https://platform.openai.com → sign in → **API keys** → **Create new secret key** → copy (`sk-...`).
2. Add a payment method and a **monthly budget** under Billing → Limits (SolHunter also enforces its own
   limits: `AI_DAILY_SPEND_LIMIT_USD`, `AI_MONTHLY_SPEND_LIMIT_USD`).
3. Confirm your account can use the model you set in `AI_MODEL` (default `gpt-5.6`; check the Models page).
   If the model is unavailable SolHunter disables GPT analysis and tells you in the dashboard — it never
   silently swaps models.
4. Look up the model's price per 1M input/output tokens and set `AI_INPUT_COST_PER_1M_USD` and
   `AI_OUTPUT_COST_PER_1M_USD` so spend limits and the cost page are accurate.

### A4. Telegram bot (optional — alerts on your phone)
1. In Telegram open **@BotFather** → `/newbot` → follow the prompts → copy the **bot token**
   (`123456789:AAH...`).
2. Open your new bot in Telegram and press **Start**, send any message.
3. Find your **chat id**: open in a browser
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` and read `"chat":{"id": 123456789, ...}`.
   (For a group/channel add the bot there; group ids are negative numbers.)

---

## Part B — Put the code on GitHub (2 minutes)

The repository is already on GitHub (`fr-ost/solhunter`). Merge/push the branch you want to deploy
to `main` (Railway deploys a branch of your choice). Nothing else is needed: the `Dockerfile` at the
repo root builds the dashboard and the backend.

---

## Part C — Create the Railway project (15 minutes)

### C1. Databases
1. https://railway.app → **New Project** → **Empty project**. Name it `solhunter`.
2. **+ Create** → **Database** → **PostgreSQL**.
3. **+ Create** → **Database** → **Redis**.
Wait until both show as deployed.

### C2. The SolHunter service
1. **+ Create** → **GitHub Repo** → authorise Railway for GitHub if asked → pick `fr-ost/solhunter`.
2. Click the new service → **Settings**:
   * **Service name**: `solhunter`.
   * **Source → Branch**: `main` (or your branch).
   * **Build**: Railway detects the root `Dockerfile` automatically *(check: Builder shows "Dockerfile")*.
   * **Deploy → Healthcheck path**: `/health`, timeout 300 s (the first boot runs migrations).
   * **Deploy → Restart policy**: *Always*.
   * **Deploy → Replicas**: 1 (keep it at 1 — the scanner must run exactly once).
   * **Networking → Public Networking → Generate Domain** → you get
     `https://solhunter-production-xxxx.up.railway.app`. (Port: 8080 if asked.)
3. **Variables** → **Raw Editor** → paste, then edit the values:

```
APP_ENV=production
SERVICE_ROLE=all
LOG_FORMAT=json
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
PASSWORD=<your password>
PUBLIC_API_BASE_URL=https://<your generated domain>
HELIUS_API_KEY=<helius key or leave empty>
OPENAI_API_KEY=<sk-... or leave empty>
AI_ENABLED=true
AI_MODEL=gpt-5.6
AI_INPUT_COST_PER_1M_USD=<price of AI_MODEL>
AI_OUTPUT_COST_PER_1M_USD=<price of AI_MODEL>
AI_DAILY_SPEND_LIMIT_USD=1.5
AI_MONTHLY_SPEND_LIMIT_USD=25
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=<bot token or leave empty and set TELEGRAM_ENABLED=false>
TELEGRAM_CHAT_ID=<chat id>
```
   `${{Postgres.DATABASE_URL}}` and `${{Redis.REDIS_URL}}` are Railway *reference variables*: they point
   at the private network addresses and stay correct if Railway rotates passwords. If your database
   services have different names, use those names inside `${{...}}`.
   Everything else has sensible defaults (see `docs/ENVIRONMENT_VARIABLES.md`); you can change the
   operational ones later from the dashboard **Settings** page without redeploying.

4. **Deploy** (Railway deploys automatically after saving variables). The first build takes 3–6 minutes
   (it compiles the dashboard and installs Python packages).

### C3. Verify
Open the **Deploy logs** and look for, in order:
`migrations.completed` → `api.starting` → `api.embedded_scanner_started` → `heartbeat.leader_acquired`
→ `pumpportal.connected` → `scanner.started`. If `OPENAI_API_KEY` is set you also see
`analyst.model_verified` (or `analyst.model_unavailable` — then fix `AI_MODEL`).

Then open `https://<your domain>/` in a browser and enter your `PASSWORD` on the login screen. The dashboard shows the scanner as **running**, providers as **connected**, and
tokens being discovered within a minute.

---

## Part D — Day-to-day use

* **Dashboard**: candidates, watchlist, whale activity, cost projection, scanner health.
* **Settings**: tune filter thresholds, ranking weights, alert thresholds live.
* **Telegram**: candidate alerts and urgent warnings; a quiet day with zero alerts is normal.
* **Survivors**: tokens 30 minutes to 7 days old that are alive, re-checked for growth (liquidity, holders,
  drawdown, turnover). Strong growers are pulled back into real-time analysis automatically. Candidates are
  never promoted in the first 15 minutes.
* **Wallet tracker**: paste any wallet address to follow it. SolHunter records its Pump.fun/PumpSwap trades
  in real time, sends a Telegram alert on buys/sells (per-wallet minimum size, 5-minute cooldown per token),
  and runs the full safety + ranking analysis on every token it buys. Up to `MAX_TRACKED_WALLETS` (100).
* **Paper trading / journal**: record manual trades; performance by signal type and model version.
* **Logs & Status**: provider status, GPT spend, model versions, recent logs — all in the dashboard.

Railway redeploys automatically on every push to the chosen branch. Deploys are safe: migrations run
under a lock, the scanner hands over through a Redis leader lock, and pending writes are flushed on
shutdown.

---

## Part E — Optional: split into two services (only if needed)

If you ever want to scale the API separately, create a second service from the same repo with
`SERVICE_ROLE=scanner` and `RUN_MIGRATIONS_ON_START=false`, and set `SERVICE_ROLE=api` on the first.
The same image serves both roles; nothing else changes. Keep exactly one scanner.

---

## Part F — Costs (rough)

| Item | Typical monthly |
|---|---|
| Railway (1 service ~0.5–1 GB RAM, Postgres, Redis) | $10–25 depending on plan/usage |
| Helius free tier | $0 |
| OpenAI (capped by `AI_MONTHLY_SPEND_LIMIT_USD`) | ≤ $25 by default |
| Telegram | $0 |

The dashboard **Status → Costs** page shows month-to-date and projected spend.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Build fails at `npm run build` | Check the deploy logs; the web build needs `web/package-lock.json` (committed). Retry the deploy. |
| `/health` OK but dashboard says scanner offline | Logs: `scanner.standby` means another instance holds the lock (replicas > 1 or a previous deploy still stopping) — wait 60 s or set replicas to 1. |
| `migrations.schema_not_ready` | Only in split mode when the scanner starts before the API; it retries for 3 minutes. |
| Login says invalid password | Use the exact `PASSWORD` value (no leading/trailing spaces). |
| No tokens discovered | Check `pumpportal` in Status → Providers; the client reconnects automatically. |
| GPT disabled | Status → AI shows the reason (model unavailable, spend limit, missing key). |
