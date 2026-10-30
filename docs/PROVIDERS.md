# External providers

| Provider | Used for | Endpoint(s) | Auth | Limits / cost | Notes |
|---|---|---|---|---|---|
| **PumpPortal** (data API) | New token creations, migrations, token trades | `wss://pumpportal.fun/api/data` – `subscribeNewToken`, `subscribeMigration`, `subscribeTokenTrade{keys}`, `subscribeAccountTrade{keys}`, `unsubscribe*` | none | Free for the data stream (verify current terms on pumpportal.fun; trade streams were reported as metered in some sources). PumpPortal asks for **one** connection per client – we use exactly one. | Message fields are not formally documented; parsing is defensive and raw payloads are stored. |
| **DexScreener** | Pool liquidity/price/volume for migrated tokens, SOL/USD | `GET /token-pairs/v1/solana/{mint}`, `GET /tokens/v1/solana/{a,b,…}`, `GET /latest/dex/pairs/solana/{pair}`, `GET /latest/dex/search` | none | 300 req/min on these endpoints (60/min on profiles/boosts/orders which we do not use). | Reported liquidity/market cap are treated as estimates and cross-checked against stream data. |
| **Helius** | Holder distribution (`getTokenSupply`, `getTokenLargestAccounts`, `getMultipleAccounts`, DAS `getTokenAccounts`), balances, creator history & funders (Enhanced `GET /v0/addresses/{address}/transactions`, `POST /v0/transactions`) | `https://mainnet.helius-rpc.com/?api-key=…`, `https://api.helius.xyz/v0/…` | `HELIUS_API_KEY` | Free: 1M credits/month, 10 RPC rps, 2 DAS/Enhanced rps. Our budgeting assumes ~1 credit/RPC, 10/DAS, 100/Enhanced call (conservative). | Without a key the scanner falls back to the public Solana RPC for standard methods and reports DAS/enhanced data as unavailable. Enhanced WebSockets (Business plan) are not required. |
| **OpenAI** | GPT research analyst | Responses API via the official SDK (`responses.parse` with a strict JSON schema), `models.retrieve` for verification | `OPENAI_API_KEY` | Priced per token; set `AI_INPUT_COST_PER_1M_USD` / `AI_OUTPUT_COST_PER_1M_USD` to the current price of `AI_MODEL`. | Default model `gpt-5.6`; if unavailable the analyst is disabled (status visible in `/api/v1/status/ai`), never substituted. |
| **Telegram** | Alerts | `POST https://api.telegram.org/bot<token>/sendMessage`, `getMe` | bot token + chat id | Bot API rate limits (429 with `retry_after` honoured). | HTML parse mode, link previews disabled. |
| **Public Solana RPC** | Fallback only | `https://api.mainnet-beta.solana.com` | none | Heavily rate limited; we use ≤2 rps. | |

## What requires credentials

* Helius key – strongly recommended (holder counts, concentration excluding LP/curve accounts, creator
  history, funding relationships). Everything else keeps working with stream-derived estimates.
* OpenAI key – GPT analysis. Optional.
* Telegram – alerts. Optional.

## Unverified assumptions to confirm on first live run

* PumpPortal message field names (`txType`, `vSolInBondingCurve`, `vTokensInBondingCurve`,
  `marketCapSol`, `initialBuy`, `solAmount`, `newTokenBalance`, `pool`) – the normaliser tolerates
  missing fields; check `raw_events` and `scanner` logs after the first minutes.
* Pump.fun bonding-curve constants (`SCANNER_PUMP_*`) used for progress and impact estimates.
* Helius credit costs per method and the enhanced-transaction `type`/`source` enums used to count
  pump.fun creations (`CREATE`, source containing `PUMP`).
* OpenAI pricing placeholders.
