import { useEffect, useMemo, useState } from "react";
import { Badge, Banner, Card, EmptyState, Spinner } from "../components/ui";
import { errorMessage, useAsync } from "../hooks/useAsync";
import { useLive } from "../hooks/useLive";
import { useSettings } from "../hooks/useSettings";
import { ApiClient, ApiError } from "../services/api";
import { defaultBaseUrl, type AppSettings } from "../services/storage";

export function SettingsPage(): JSX.Element {
  const { settings, update, api, signOut } = useSettings();
  const live = useLive();
  const [form, setForm] = useState<AppSettings>(settings);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ kind: "info" | "error" | "warn"; text: string } | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [group, setGroup] = useState("filters");
  const remote = useAsync(() => api.settings(), [api], { enabled: api.configured });

  useEffect(() => setForm(settings), [settings]);

  const save = async (): Promise<void> => {
    setResult(null);
    const base = form.apiBaseUrl.trim().replace(/\/+$/, "");
    if (base && !/^https?:\/\//.test(base)) {
      setResult({ kind: "error", text: "The API base URL must start with http:// or https://, or be left empty to use this page's origin." });
      return;
    }
    if (!form.apiKey.trim()) {
      setResult({ kind: "error", text: "The password is required." });
      return;
    }
    await update({ ...form, apiBaseUrl: base });
    setResult({ kind: "info", text: "Saved. Testing connection…" });
    await test({ ...form, apiBaseUrl: base });
  };

  const test = async (s: AppSettings = form): Promise<void> => {
    setTesting(true);
    try {
      const client = new ApiClient({ ...s, apiBaseUrl: s.apiBaseUrl.trim().replace(/\/+$/, "") });
      const health = await client.health();
      const status = await client.systemStatus();
      setResult({ kind: "info", text: `Connected: API v${health.version} (${health.environment}); database ${status.database.ok ? "ok" : "DOWN"}, redis ${status.redis.ok ? "ok" : "DOWN"}, scanner ${status.scanner.status}.` });
    } catch (err) {
      setResult({ kind: "error", text: err instanceof ApiError ? err.userMessage : String(err) });
    } finally {
      setTesting(false);
    }
  };

  const overridable = remote.data?.overridable ?? {};
  const groups = useMemo(() => Array.from(new Set(Object.keys(overridable).map((k) => k.split(".")[0]))), [overridable]);
  const keys = Object.keys(overridable).filter((k) => k.startsWith(`${group}.`));

  const applyOverrides = async (): Promise<void> => {
    const values: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(edits)) {
      if (v === "") continue;
      const current = overridable[k];
      values[k] = typeof current === "boolean" ? v === "true" : typeof current === "number" ? Number(v) : v;
    }
    if (!Object.keys(values).length) return;
    setSaving(true);
    try {
      await api.updateSettings(values);
      setEdits({});
      remote.reload();
      setResult({ kind: "info", text: `Applied ${Object.keys(values).length} override(s). The scanner picks them up within a minute.` });
    } catch (err) {
      setResult({ kind: "error", text: err instanceof ApiError ? (err.isForbidden ? "Not allowed to change runtime settings: use the PASSWORD or an API_ADMIN_KEYS key." : err.userMessage) : String(err) });
    } finally {
      setSaving(false);
    }
  };

  const reset = async (key: string): Promise<void> => {
    try {
      await api.resetSetting(key);
      remote.reload();
    } catch (err) {
      setResult({ kind: "error", text: err instanceof ApiError ? err.userMessage : String(err) });
    }
  };

  return (
    <>
      <Card title="Connection" right={<span className={`live ${live.status}`}><span className="dot" /> {live.status}</span>}>
        <div className="form">
          <div>
            <label>API base URL override (leave empty to use this page&apos;s origin: {defaultBaseUrl()})</label>
            <input placeholder={defaultBaseUrl()} value={form.apiBaseUrl} onChange={(e) => setForm({ ...form, apiBaseUrl: e.target.value })} />
          </div>
          <div>
            <label>Password (PASSWORD)</label>
            <input type="password" value={form.apiKey} onChange={(e) => setForm({ ...form, apiKey: e.target.value })} autoComplete="off" />
          </div>
          <div>
            <label>separate admin key (advanced, optional - the password already allows settings changes)</label>
            <input type="password" value={form.adminKey} onChange={(e) => setForm({ ...form, adminKey: e.target.value })} autoComplete="off" />
          </div>
          <div className="row">
            <button className="primary" onClick={() => void save()} disabled={testing}>
              save & test
            </button>
            <button onClick={() => void test()} disabled={testing || !form.apiKey}>
              {testing ? <Spinner /> : "test connection"}
            </button>
            <button className="danger" onClick={signOut} title="forget the keys stored in this browser">
              sign out
            </button>
          </div>
          {result ? <Banner kind={result.kind}>{result.text}</Banner> : null}
          <p className="faint tiny">Keys are stored only in this browser (localStorage). Never paste provider secrets (Helius/OpenAI/Telegram) here - those live on the server.</p>
        </div>
      </Card>
      <Card title="Runtime overrides" right={remote.data ? <Badge>{Object.keys(remote.data.overrides).length} active</Badge> : null}>
        {!api.configured ? <EmptyState>Connect first.</EmptyState> : remote.error ? <Banner kind="error">{errorMessage(remote.error)}</Banner> : !remote.data ? <Spinner /> : (
          <>
            <div className="tabs">
              {groups.map((g) => (
                <button key={g} className={g === group ? "active" : ""} onClick={() => setGroup(g)}>
                  {g}
                </button>
              ))}
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>key</th>
                    <th>effective</th>
                    <th>override</th>
                  </tr>
                </thead>
                <tbody>
                  {keys.map((k) => {
                    const value = overridable[k];
                    const overridden = k in (remote.data?.overrides ?? {});
                    return (
                      <tr key={k}>
                        <td>
                          <span className="mono">{k.split(".")[1]}</span>
                          {overridden ? (
                            <button className="small" style={{ marginLeft: 4 }} title="reset to environment default" onClick={() => void reset(k)}>
                              reset
                            </button>
                          ) : null}
                        </td>
                        <td className={`mono ${overridden ? "ok" : ""}`}>{String(value)}</td>
                        <td>
                          {typeof value === "boolean" ? (
                            <select value={edits[k] ?? ""} onChange={(e) => setEdits({ ...edits, [k]: e.target.value })}>
                              <option value="">–</option>
                              <option value="true">true</option>
                              <option value="false">false</option>
                            </select>
                          ) : (
                            <input value={edits[k] ?? ""} placeholder="new value" onChange={(e) => setEdits({ ...edits, [k]: e.target.value })} />
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="row" style={{ marginTop: 6 }}>
              <button className="primary" disabled={saving || !Object.values(edits).some((v) => v !== "")} onClick={() => void applyOverrides()}>
                apply overrides
              </button>
              <span className="faint tiny">thresholds, weights and alert settings only; secrets are environment-only</span>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
