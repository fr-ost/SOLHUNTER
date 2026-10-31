import { useState } from "react";
import { Banner, Spinner } from "../components/ui";
import { useSettings } from "../hooks/useSettings";
import { ApiClient, ApiError } from "../services/api";
import { defaultBaseUrl } from "../services/storage";

/** Shown until the password is saved. Validates it against the live API before storing it. */
export function LoginPage(): JSX.Element {
  const { settings, update } = useSettings();
  const [apiKey, setApiKey] = useState("");
  const [adminKey, setAdminKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(settings.apiBaseUrl);
  const [advanced, setAdvanced] = useState(Boolean(settings.apiBaseUrl));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    setError(null);
    const key = apiKey.trim();
    if (!key) {
      setError("Enter the password (the PASSWORD variable on your SolHunter service).");
      return;
    }
    setBusy(true);
    try {
      const candidate = { apiBaseUrl: advanced ? baseUrl.trim().replace(/\/+$/, "") : "", apiKey: key, adminKey: adminKey.trim() };
      const client = new ApiClient(candidate);
      await client.systemStatus();
      await update(candidate);
    } catch (err) {
      if (err instanceof ApiError && err.isAuth) setError("Invalid password.");
      else setError(err instanceof ApiError ? err.userMessage : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="card login-card" onSubmit={(e) => void submit(e)}>
        <div className="brand" style={{ marginBottom: 10 }}>
          <img src="/favicon.png" alt="" />
          <h1>SolHunter</h1>
        </div>
        <p className="small muted">Solana token research scanner. Sign in with the password configured on your SolHunter service (<span className="mono">PASSWORD</span>).</p>
        <div className="form">
          <div>
            <label>Password</label>
            <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="current-password" autoFocus />
          </div>
          <div>
            <label>Separate admin key (advanced, optional - leave empty; your password already allows settings changes)</label>
            <input type="password" value={adminKey} onChange={(e) => setAdminKey(e.target.value)} autoComplete="off" />
          </div>
          <button type="button" className="small" onClick={() => setAdvanced((a) => !a)} style={{ alignSelf: "flex-start" }}>
            {advanced ? "hide" : "advanced"}: API base URL
          </button>
          {advanced ? (
            <div>
              <label>API base URL (leave empty to use this page&apos;s origin: {defaultBaseUrl()})</label>
              <input placeholder={defaultBaseUrl()} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
            </div>
          ) : null}
          <button className="primary" type="submit" disabled={busy}>
            {busy ? <Spinner /> : "sign in"}
          </button>
          {error ? <Banner kind="error">{error}</Banner> : null}
          <p className="faint tiny">Keys are stored only in this browser (localStorage). Provider secrets (Helius, OpenAI, Telegram) stay on the server.</p>
        </div>
      </form>
    </div>
  );
}
