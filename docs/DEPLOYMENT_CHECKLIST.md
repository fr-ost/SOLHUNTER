# Definition-of-done checklist for the first Railway deployment

| # | Requirement | Status | How verified |
|---|---|---|---|
| 1 | API starts successfully | ✅ | `backend/tests/test_production_startup.py` boots `scripts/start.sh` (`SERVICE_ROLE=all`) with `APP_ENV=production` and migrations enabled; `/health` 200, `/ready` 200, embedded scanner reaches `running`. |
| 2 | Scanner runs independently | ✅ | `test_worker_starts_heartbeats_and_stops_cleanly`: worker acquires the leader lock, heartbeats, serves `/health`, stops on request. Runs without the API. |
| 3 | PostgreSQL migrations work | ✅ | Alembic initial migration applied in every test session; `alembic check` reports no drift. |
| 4 | Redis connections work | ✅ | Rate limiting, dedupe, leader lock, counters exercised in tests. |
| 5 | Real token discovery with provider credentials | ⚠️ untested live | PumpPortal protocol implemented and tested against a local WebSocket server; the live endpoint could not be reached from the build sandbox. Verify on first deploy (`pumpportal.connected`, `live_counters.discovered`). |
| 6 | Events are persisted | ✅ | Tracker + persistence tests (idempotent replay). |
| 7 | Scanner reconnects after disconnection | ✅ | `test_pumpportal_client_subscribes_and_reconnects` (forced close → reconnect → re-subscribe). |
| 8 | Token filters work | ✅ | Stage and pipeline tests incl. hard-fail precedence. |
| 9 | Whale intelligence works with available data | ✅ | Whale engine, wallet classification, relationship tests; live wallet history accumulates after deployment. |
| 10 | Candidate ranking works | ✅ | Ranker tests, end-to-end evaluator test. |
| 11 | GPT analysis works with a valid API key | ⚠️ untested live | Implemented against the official SDK (`responses.parse`) with strict schema; tested with a fake client. Model availability is verified at start-up; confirm `analyst.model_verified` in logs. |
| 12 | Spending controls work | ✅ | Daily/monthly/per-token limits tested. |
| 13 | Telegram alerts work | ⚠️ untested live | Client tested with mocked Bot API (incl. 429); needs real token/chat id. |
| 14 | Web dashboard connects to the deployed API | ✅ built into the image | The Dockerfile builds `web/` and the API serves it at `/`; log in with `PASSWORD`. Browser rendering not exercised in the build sandbox. |
| 15 | API authentication works | ✅ | Auth tests + production boot test (401 without key). |
| 16 | No localhost dependency in production config | ✅ | Production validator warns on localhost URLs; Dockerfile/start script bind `0.0.0.0:$PORT`; dashboard uses same origin. |
| 17 | Railway deployment instructions complete | ✅ | `docs/RAILWAY_DEPLOYMENT.md`, `docs/ENVIRONMENT_VARIABLES.md`. |
| 18 | Automated tests pass | ✅ | `pytest -q` (see IMPLEMENTATION_STATUS.md for the count). |
| 19 | No secrets committed | ✅ | `.env` ignored, `.env.example` placeholders, test asserts no `sk-` in the example. |
| 20 | Recovers safely after restarts | ✅ | Idempotent ingestion, checkpoint restore, leader lock release on SIGTERM, migration wait. |

Items marked ⚠️ require real credentials / a live environment and must be confirmed during the first
deployment using the operations checklist in `docs/RAILWAY_DEPLOYMENT.md` §7.
