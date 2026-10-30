# Chrome extension (side panel dashboard)

Source: `extension/` (Manifest V3, React 18 + TypeScript, Vite, recharts). Full instructions live in
`extension/README.md`; this page is the operator summary.

## Build and install

```bash
cd extension
npm install
npm run typecheck
npm run build
```

Load `extension/dist` as an unpacked extension (`chrome://extensions` → Developer mode → Load unpacked).
Clicking the toolbar icon opens the side panel (Chrome 116+).

## Connect it to Railway

1. Copy the extension ID from `chrome://extensions`.
2. On the `gemfinder-api` Railway service set `CHROME_EXTENSION_ID=<id>` (adds the
   `chrome-extension://<id>` origin to CORS) and redeploy.
3. In the side panel → Settings enter the API base URL (`https://<your-api>.up.railway.app`), an
   `API_KEYS` value and optionally an `API_ADMIN_KEYS` value (runtime overrides only). Save & test.

The extension stores these in `chrome.storage.local`; it never holds provider secrets. Domains outside
`*.railway.app` trigger an optional host-permission prompt.

## What it shows

Dashboard, candidate explorer and detail (charts, holders, developer history, whale timeline, wallet
profiles, ML predictions, GPT analysis, execution estimates, filter stages), wallet intelligence,
token search with manual tracking, historical candidates with realised outcomes, paper trading and
journal, runtime settings, provider/system status with cost estimates, and logs. Live updates arrive
over the authenticated WebSocket (`/ws?token=`), with exponential-backoff reconnect.

Provenance badges distinguish **verified** facts, **estimates**, **model** predictions (experimental
until validated) and **gpt** interpretation. There is no demo data: empty states are shown until the
scanner has produced results.
