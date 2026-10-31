import { useState } from "react";
import { Badge, Banner, Card, EmptyState, Spinner, statusTone } from "../components/ui";
import { errorMessage, useAsync } from "../hooks/useAsync";
import { navigate } from "../hooks/useRoute";
import { useApi } from "../hooks/useSettings";
import { ApiError } from "../services/api";
import { shortAddr, timeAgo } from "../services/format";

export function TokenSearchPage(): JSX.Element {
  const api = useApi();
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [trackMsg, setTrackMsg] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const search = useAsync(() => api.searchTokens(query, 30), [api, query], { enabled: api.configured && query.length > 0 });
  const recent = useAsync(() => api.recentTokens({ status: status || undefined, limit: 40 }), [api, status], { refreshMs: 30_000, enabled: api.configured && !query });

  const track = async (mint: string): Promise<void> => {
    setTrackMsg(null);
    try {
      await api.trackToken(mint, "requested from dashboard");
      setTrackMsg(`Tracking request queued for ${shortAddr(mint)} - the scanner picks it up within a few seconds.`);
    } catch (err) {
      setTrackMsg(err instanceof ApiError ? err.userMessage : String(err));
    }
  };

  if (!api.configured) return <Banner kind="warn">Configure the API in Settings first.</Banner>;
  const items = query ? search.data?.items ?? [] : recent.data?.items ?? [];
  const looksLikeMint = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(input.trim());
  return (
    <>
      <Card title="Token search">
        <div className="inline">
          <div>
            <label>symbol, name or mint</label>
            <input value={input} placeholder="e.g. GEM or 7xKX…" onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && setQuery(input.trim())} />
          </div>
          <button className="primary" onClick={() => setQuery(input.trim())}>
            search
          </button>
        </div>
        {looksLikeMint ? (
          <div className="row" style={{ marginTop: 6 }}>
            <button className="small" onClick={() => void track(input.trim())}>
              track this mint
            </button>
            <button className="small" onClick={() => navigate("candidates", input.trim())}>
              open
            </button>
            <span className="faint tiny">manual tracking subscribes the scanner to this token's trades</span>
          </div>
        ) : null}
        {trackMsg ? <Banner kind="info">{trackMsg}</Banner> : null}
        {!query ? (
          <div style={{ marginTop: 8 }}>
            <label>recently seen tokens</label>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">any status</option>
              {["tracking", "candidate", "watchlist", "rejected", "dead", "archived"].map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
        ) : null}
      </Card>
      {(query ? search.error : recent.error) ? <Banner kind="error">{errorMessage(query ? search.error : recent.error)}</Banner> : null}
      {(query ? search.loading : recent.loading) && !items.length ? <Spinner /> : null}
      {!items.length && !(query ? search.loading : recent.loading) ? <EmptyState>{query ? "No tokens match. The scanner only knows tokens it has seen since deployment." : "No tokens seen yet."}</EmptyState> : null}
      <div className="list">
        {items.map((t) => (
          <div key={t.mint} className="item" onClick={() => navigate("candidates", t.mint)}>
            <div className="title">
              <span>
                <strong>{t.symbol ?? "?"}</strong> <span className="muted small">{t.name ?? ""}</span>
              </span>
              <Badge tone={statusTone(t.status)}>{t.status}</Badge>
            </div>
            <div className="meta">
              <span>
                <code>{shortAddr(t.mint, 5)}</code>
              </span>
              <span>{t.pool_kind}</span>
              <span>launched {timeAgo(t.launch_time)}</span>
              {t.status_reason ? <span className="faint">{t.status_reason.slice(0, 60)}</span> : null}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
