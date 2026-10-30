# GemFinder AI - Chrome extension (Manifest V3 side panel)

The side-panel dashboard for the GemFinder AI scanner. It talks only to your deployed
GemFinder API (Railway) over HTTPS/WSS with an API key you configure. It contains no
provider secrets, never touches a wallet and never executes trades.

## Build

```bash
cd extension
npm install
npm run typecheck
npm run build
```

The build writes `extension/dist/` with `manifest.json`, `index.html` (side panel), `background.js`
(service worker), `icons/` and hashed `assets/`. Icons can be regenerated with `npm run icons`.

## Load unpacked

1. Open `chrome://extensions`, enable **Developer mode**.
2. **Load unpacked** and select the `extension/dist` folder.
3. Pin the extension; clicking the toolbar icon opens the side panel (Chrome 116+).
4. Copy the extension **ID** shown on the card (a 32-character string).

## Allow the extension on the API (CORS)

Set `CHROME_EXTENSION_ID=<that id>` on the Railway `gemfinder-api` service (or add
`chrome-extension://<id>` to `CORS_ALLOWED_ORIGINS`) and redeploy. Requests from the
extension are then accepted; everything else stays blocked.

## Configure

Open the side panel → **Settings**:

- **API base URL**: the public HTTPS domain of the `gemfinder-api` service, e.g.
  `https://gemfinder-api-production.up.railway.app` (never localhost).
- **API key**: one of the values you set in `API_KEYS`.
- **Admin key** (optional): one of `API_ADMIN_KEYS`, required only to change runtime overrides.
- Save & test. A non-`railway.app` domain triggers a host-permission prompt (optional host permissions).

Settings are kept in `chrome.storage.local` of your browser profile only.

## Pages

Dashboard · Candidates (explorer + detail with charts, holders, developer, whales, ML, GPT, execution
estimates, filter stages) · Wallets (top wallets, wallet detail) · Search (+ manual tracking) ·
History (past candidates with realised outcomes, alerts) · Trading (paper/manual trades, sizing,
performance) · Journal · Status (services, providers, AI spend, costs, models) · Logs · Settings.

Badges mark data provenance everywhere: **verified** (on-chain/stream facts), **estimate**
(DexScreener, reserve maths, stream estimates), **model** (ML predictions, flagged experimental
until validated) and **gpt** (LLM interpretation). Empty states are honest - there is no demo data.

## Development

`npm run dev` serves the panel as a normal web page (settings fall back to localStorage) for UI work;
the extension APIs (`chrome.*`) are only available when loaded unpacked.
