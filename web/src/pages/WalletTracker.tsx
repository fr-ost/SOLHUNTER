import { useState } from "react";
import { Address, Badge, Banner, Card, EmptyState, Spinner, Stat, statusTone } from "../components/ui";
import { errorMessage, useAsync } from "../hooks/useAsync";
import { useDebouncedLiveVersion } from "../hooks/useLive";
import { navigate } from "../hooks/useRoute";
import { useApi } from "../hooks/useSettings";
import { ApiError } from "../services/api";
import { fmtNum, fmtPct, fmtSol, timeAgo } from "../services/format";
import type { TrackedTrade, TrackedWallet } from "../types/api";

const ADDRESS_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

function TradeRow({ t, showWallet }: { t: TrackedTrade; showWallet: boolean }): JSX.Element {
  return (
    <tr>
      <td className="small muted">{timeAgo(t.block_time)}</td>
      {showWallet ? <td>{t.label ? <b>{t.label}</b> : <Address value={t.wallet} />}</td> : null}
      <td>
        <Badge tone={t.side === "buy" ? "good" : "bad"}>{t.side}</Badge>
      </td>
      <td className="mono">{fmtSol(t.sol_amount)}</td>
      <td>
        <a href={`#/candidates/${t.mint}`} onClick={(e) => { e.preventDefault(); navigate("candidates", t.mint); }}>
          {t.symbol ?? <Address value={t.mint} />}
        </a>
      </td>
      <td>{t.score_status ? <Badge tone={statusTone(t.score_status)}>{t.score_status} {t.score != null ? Math.round(t.score) : ""}</Badge> : <span className="small muted">{t.token_status ?? "not analysed"}</span>}</td>
      <td className="small">{t.rug_risk != null ? fmtPct(t.rug_risk) : "n/a"}</td>
      <td className="small">
        <a href={`https://solscan.io/tx/${t.signature}`} target="_blank" rel="noreferrer">tx</a> ·{" "}
        <a href={`https://dexscreener.com/solana/${t.mint}`} target="_blank" rel="noreferrer">chart</a>
      </td>
    </tr>
  );
}

function TradesTable({ items, showWallet }: { items: TrackedTrade[]; showWallet: boolean }): JSX.Element {
  if (!items.length) return <EmptyState>No trades recorded yet. Trades appear here the moment a tracked wallet buys or sells on Pump.fun / PumpSwap.</EmptyState>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>when</th>
            {showWallet ? <th>wallet</th> : null}
            <th>side</th>
            <th>SOL</th>
            <th>token</th>
            <th>SolHunter</th>
            <th>rug risk</th>
            <th>links</th>
          </tr>
        </thead>
        <tbody>
          {items.map((t) => (
            <TradeRow key={`${t.signature}-${t.wallet}-${t.side}`} t={t} showWallet={showWallet} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WalletCard({ w, onChange, onSelect, selected }: { w: TrackedWallet; onChange: () => void; onSelect: () => void; selected: boolean }): JSX.Element {
  const api = useApi();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const patch = async (body: Parameters<typeof api.updateTrackedWallet>[1]): Promise<void> => {
    setBusy(true);
    setErr(null);
    try {
      await api.updateTrackedWallet(w.address, body);
      onChange();
    } catch (e) {
      setErr(e instanceof ApiError ? e.userMessage : String(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async (): Promise<void> => {
    if (!window.confirm(`Stop tracking ${w.label ?? w.address}?`)) return;
    setBusy(true);
    try {
      await api.removeTrackedWallet(w.address);
      onChange();
    } catch (e) {
      setErr(e instanceof ApiError ? e.userMessage : String(e));
      setBusy(false);
    }
  };
  const p = w.profile;
  return (
    <Card
      title={
        <span>
          {w.label ? <b>{w.label}</b> : null} <Address value={w.address} chars={6} /> {w.active ? null : <Badge tone="bad">paused</Badge>}
        </span>
      }
      right={busy ? <Spinner /> : <button className="small" onClick={onSelect}>{selected ? "hide trades" : "show trades"}</button>}
    >
      {err ? <Banner kind="error">{err}</Banner> : null}
      <div className="grid-3">
        <Stat label="24h trades" value={fmtNum(w.trades_24h ?? 0)} sub={`${fmtNum(w.tokens_24h ?? 0)} tokens · last ${w.last_trade_at ? timeAgo(w.last_trade_at) : "never"}`} />
        <Stat label="24h flow" value={`${(w.net_sol_24h ?? 0) >= 0 ? "+" : ""}${fmtSol(w.net_sol_24h ?? 0)}`} sub={`bought ${fmtSol(w.buy_sol_24h ?? 0)} · sold ${fmtSol(w.sell_sol_24h ?? 0)}`} />
        <Stat
          label="Track record (seen by SolHunter)"
          value={p ? `${p.realized_pnl_sol >= 0 ? "+" : ""}${fmtSol(p.realized_pnl_sol)}` : "n/a"}
          sub={p ? `${p.category} · win ${p.win_rate != null ? fmtPct(p.win_rate) : "n/a"} · ${p.closed_positions} closed` : "builds up as trades are observed"}
        />
      </div>
      <div className="row" style={{ gap: 14, flexWrap: "wrap", marginTop: 8 }}>
        <label className="small">
          <input type="checkbox" checked={w.alert_on_buy} onChange={(e) => patch({ alert_on_buy: e.target.checked })} /> alert on buys
        </label>
        <label className="small">
          <input type="checkbox" checked={w.alert_on_sell} onChange={(e) => patch({ alert_on_sell: e.target.checked })} /> alert on sells
        </label>
        <label className="small">
          <input type="checkbox" checked={w.auto_track_tokens} onChange={(e) => patch({ auto_track_tokens: e.target.checked })} /> analyse tokens it buys
        </label>
        <label className="small">
          min SOL for alert{" "}
          <input style={{ width: 70 }} type="number" min={0} step={0.1} defaultValue={w.min_alert_sol} onBlur={(e) => Number(e.target.value) !== w.min_alert_sol && patch({ min_alert_sol: Number(e.target.value) || 0 })} />
        </label>
        <button className="small" onClick={() => patch({ active: !w.active })}>{w.active ? "pause" : "resume"}</button>
        <button className="small danger" onClick={remove}>remove</button>
      </div>
      {w.notes ? <p className="small muted">{w.notes}</p> : null}
    </Card>
  );
}

export function WalletTrackerPage(): JSX.Element {
  const api = useApi();
  const live = useDebouncedLiveVersion(2500);
  const wallets = useAsync(() => api.trackedWallets(), [api, live], { enabled: api.configured });
  const [selected, setSelected] = useState<string | null>(null);
  const trades = useAsync(() => (selected ? api.trackedWalletTrades(selected, 200) : api.trackedTrades(150)), [api, live, selected], { enabled: api.configured });
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const [notes, setNotes] = useState("");
  const [minSol, setMinSol] = useState("0");
  const [alertBuy, setAlertBuy] = useState(true);
  const [alertSell, setAlertSell] = useState(true);
  const [autoTrack, setAutoTrack] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const add = async (): Promise<void> => {
    setError(null);
    setOk(null);
    const a = address.trim();
    if (!ADDRESS_RE.test(a)) {
      setError("That does not look like a Solana wallet address (32-44 base58 characters).");
      return;
    }
    try {
      await api.addTrackedWallet({ address: a, label: label.trim() || null, notes: notes.trim() || null, min_alert_sol: Number(minSol) || 0, alert_on_buy: alertBuy, alert_on_sell: alertSell, auto_track_tokens: autoTrack });
      setOk(`Now tracking ${label.trim() || a}. The scanner subscribes within ~10 seconds.`);
      setAddress("");
      setLabel("");
      setNotes("");
      wallets.reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.userMessage : String(e));
    }
  };

  const items = wallets.data?.items ?? [];
  return (
    <>
      <Card title="Track a wallet">
        <p className="small muted">
          Paste any Solana wallet. SolHunter follows its Pump.fun / PumpSwap trades in real time, sends Telegram alerts, and (optionally) runs the full safety and
          ranking analysis on every token it buys. Research only - nothing is copied or traded automatically.
        </p>
        {error ? <Banner kind="error">{error}</Banner> : null}
        {ok ? <Banner kind="info">{ok}</Banner> : null}
        <div className="form-grid">
          <label>
            Wallet address
            <input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="e.g. 5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1" />
          </label>
          <label>
            Label (optional)
            <input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={64} placeholder="e.g. Whale-1" />
          </label>
          <label>
            Notes (optional)
            <input value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={512} placeholder="where you found it, why it matters" />
          </label>
          <label>
            Min SOL for alert
            <input type="number" min={0} step={0.1} value={minSol} onChange={(e) => setMinSol(e.target.value)} />
          </label>
        </div>
        <div className="row" style={{ gap: 14, flexWrap: "wrap", marginTop: 8 }}>
          <label className="small"><input type="checkbox" checked={alertBuy} onChange={(e) => setAlertBuy(e.target.checked)} /> alert on buys</label>
          <label className="small"><input type="checkbox" checked={alertSell} onChange={(e) => setAlertSell(e.target.checked)} /> alert on sells</label>
          <label className="small"><input type="checkbox" checked={autoTrack} onChange={(e) => setAutoTrack(e.target.checked)} /> analyse tokens it buys</label>
          <button className="primary" onClick={add} disabled={!address.trim()}>Track wallet</button>
        </div>
      </Card>

      {wallets.error ? <Banner kind="error">{errorMessage(wallets.error)}</Banner> : null}
      <Card title={`Tracked wallets (${items.length}${wallets.data ? ` / ${wallets.data.limit}` : ""})`} right={wallets.loading ? <Spinner /> : null}>
        {!items.length && !wallets.loading ? <EmptyState>No wallets tracked yet. Add one above.</EmptyState> : null}
      </Card>
      {items.map((w) => (
        <WalletCard key={w.address} w={w} onChange={wallets.reload} selected={selected === w.address} onSelect={() => setSelected(selected === w.address ? null : w.address)} />
      ))}

      <Card title={selected ? `Trades by ${items.find((w) => w.address === selected)?.label ?? selected.slice(0, 8)}` : "Latest trades by tracked wallets"} right={trades.loading ? <Spinner /> : selected ? <button className="small" onClick={() => setSelected(null)}>all wallets</button> : null}>
        {trades.error ? <Banner kind="error">{errorMessage(trades.error)}</Banner> : null}
        <TradesTable items={trades.data?.items ?? []} showWallet={!selected} />
      </Card>
    </>
  );
}
