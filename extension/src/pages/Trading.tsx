import { useState } from "react";
import { Badge, Banner, Card, EmptyState, KV, Spinner, Stat, Tabs } from "../components/ui";
import { errorMessage, useAsync } from "../hooks/useAsync";
import { navigate } from "../hooks/useRoute";
import { useApi } from "../hooks/useSettings";
import { ApiError } from "../services/api";
import { fmtDateTime, fmtPct, fmtPctPoints, fmtPrice, fmtUsd, timeAgo } from "../services/format";
import type { PerformanceBucket, Trade, TradeCreate } from "../types/api";

type Tab = "open" | "closed" | "new" | "sizing" | "performance";

const emptyForm = (): TradeCreate => ({ mint: "", symbol: "", is_paper: true, entry_price_usd: 0, position_size_usd: 100, planned_stop_pct: 6, planned_targets_pct: [10, 15, 20], fees_usd: 0, slippage_usd: 0, entry_reason: "", candidate_type: "", signal_category: "" });

function PerfTable({ title, buckets }: { title: string; buckets: Record<string, PerformanceBucket> }): JSX.Element | null {
  const rows = Object.entries(buckets).filter(([, b]) => b.trades > 0);
  if (!rows.length) return null;
  return (
    <div style={{ marginTop: 8 }}>
      <h3>{title}</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>bucket</th>
              <th className="num">n</th>
              <th className="num">win</th>
              <th className="num">pnl</th>
              <th className="num">avg</th>
              <th className="num">PF</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([k, b]) => (
              <tr key={k}>
                <td>{k}</td>
                <td className="num">{b.trades}</td>
                <td className="num">{fmtPct(b.win_rate)}</td>
                <td className={`num ${(b.total_pnl_usd ?? 0) >= 0 ? "good" : "bad"}`}>{fmtUsd(b.total_pnl_usd, 2)}</td>
                <td className="num">{fmtPctPoints(b.avg_return_pct, 1, true)}</td>
                <td className="num">{b.profit_factor?.toFixed(2) ?? "n/a"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function TradingPage(): JSX.Element {
  const api = useApi();
  const [tab, setTab] = useState<Tab>("open");
  const [form, setForm] = useState<TradeCreate>(emptyForm());
  const [msg, setMsg] = useState<{ kind: "error" | "info"; text: string } | null>(null);
  const [closing, setClosing] = useState<Record<number, { exit_price_usd: string; exit_reason: string; fees_usd: string; slippage_usd: string }>>({});
  const [sizeInput, setSizeInput] = useState({ price: "0.00001", stop: "6" });
  const risk = useAsync(() => api.riskState(), [api, tab], { enabled: api.configured });
  const open = useAsync(() => api.trades({ status: "open", limit: 100 }), [api], { enabled: api.configured && tab === "open" });
  const closed = useAsync(() => api.trades({ status: "closed", limit: 100 }), [api], { enabled: api.configured && tab === "closed" });
  const perf = useAsync(() => api.performance(), [api], { enabled: api.configured && tab === "performance" });
  const sizing = useAsync(() => api.positionSize(Number(sizeInput.price) || 0.000001, Number(sizeInput.stop) || undefined), [api, sizeInput], { enabled: api.configured && tab === "sizing" });

  const submit = async (): Promise<void> => {
    setMsg(null);
    try {
      const body: TradeCreate = { ...form, symbol: form.symbol || null, entry_reason: form.entry_reason || null, candidate_type: form.candidate_type || null, signal_category: form.signal_category || null };
      const res = await api.createTrade(body);
      setMsg({ kind: "info", text: `Trade #${res.trade.id} recorded${res.risk_warning ? ` - ${res.risk_warning}` : ""}.` });
      setForm(emptyForm());
      setTab("open");
      open.reload();
      risk.reload();
    } catch (err) {
      setMsg({ kind: "error", text: err instanceof ApiError ? err.userMessage : String(err) });
    }
  };

  const closeTrade = async (t: Trade): Promise<void> => {
    const c = closing[t.id];
    if (!c || !Number(c.exit_price_usd)) {
      setMsg({ kind: "error", text: "Enter an exit price first." });
      return;
    }
    try {
      await api.updateTrade(t.id, { status: "closed", exit_price_usd: Number(c.exit_price_usd), exit_reason: c.exit_reason || undefined, fees_usd: Number(c.fees_usd) || 0, slippage_usd: Number(c.slippage_usd) || 0 });
      setMsg({ kind: "info", text: `Trade #${t.id} closed.` });
      open.reload();
      risk.reload();
    } catch (err) {
      setMsg({ kind: "error", text: err instanceof ApiError ? err.userMessage : String(err) });
    }
  };

  const remove = async (id: number): Promise<void> => {
    try {
      await api.deleteTrade(id);
      open.reload();
      closed.reload();
    } catch (err) {
      setMsg({ kind: "error", text: err instanceof ApiError ? err.userMessage : String(err) });
    }
  };

  if (!api.configured) return <Banner kind="warn">Configure the API in Settings first.</Banner>;
  const r = risk.data;
  return (
    <>
      <Card title="Paper trading & journal" right={<Badge tone="info">manual only</Badge>}>
        {r ? (
          <div className="grid-3">
            <Stat label="Open" value={`${r.open_positions}/${r.max_open_positions}`} tone={r.can_open_new ? undefined : "bad"} />
            <Stat label="PnL today" value={fmtUsd(r.realized_pnl_today_usd, 2)} tone={r.realized_pnl_today_usd >= 0 ? "good" : "bad"} />
            <Stat label="Daily loss limit" value={fmtUsd(r.daily_loss_limit_usd, 2)} sub={r.daily_loss_limit_hit ? "LIMIT HIT" : "ok"} tone={r.daily_loss_limit_hit ? "bad" : undefined} />
          </div>
        ) : null}
        <div style={{ marginTop: 8 }}>
          <Tabs value={tab} onChange={setTab} options={[{ id: "open", label: "Open" }, { id: "closed", label: "Closed" }, { id: "new", label: "+ New" }, { id: "sizing", label: "Sizing" }, { id: "performance", label: "Performance" }]} />
        </div>
        <p className="faint tiny">GemFinder never executes trades. Record what you did manually; nothing here touches a wallet.</p>
      </Card>
      {msg ? <Banner kind={msg.kind}>{msg.text}</Banner> : null}
      {tab === "open" && (
        <>
          {open.error ? <Banner kind="error">{errorMessage(open.error)}</Banner> : null}
          {open.data?.items.length === 0 ? <EmptyState>No open positions.</EmptyState> : null}
          <div className="list">
            {(open.data?.items ?? []).map((t) => (
              <div key={t.id} className="item static">
                <div className="title">
                  <span>
                    <strong>{t.symbol ?? t.mint.slice(0, 6)}</strong> <span className="faint small">#{t.id} {t.is_paper ? "paper" : "manual"}</span>
                  </span>
                  <a href={`#/candidates/${t.mint}`}>token →</a>
                </div>
                <div className="meta">
                  <span>entry {fmtPrice(t.entry_price_usd)}</span>
                  <span>size {fmtUsd(t.position_size_usd)}</span>
                  <span>stop {t.planned_stop_pct ?? "n/a"}%</span>
                  <span>targets {(t.planned_targets_pct ?? []).join("/")}%</span>
                  <span>{timeAgo(t.entry_at)}</span>
                </div>
                <div className="inline" style={{ marginTop: 6 }}>
                  <input placeholder="exit price USD" value={closing[t.id]?.exit_price_usd ?? ""} onChange={(e) => setClosing({ ...closing, [t.id]: { exit_price_usd: e.target.value, exit_reason: closing[t.id]?.exit_reason ?? "", fees_usd: closing[t.id]?.fees_usd ?? "", slippage_usd: closing[t.id]?.slippage_usd ?? "" } })} />
                  <input placeholder="reason" value={closing[t.id]?.exit_reason ?? ""} onChange={(e) => setClosing({ ...closing, [t.id]: { ...(closing[t.id] ?? { exit_price_usd: "", fees_usd: "", slippage_usd: "" }), exit_reason: e.target.value } })} />
                  <button className="small primary" onClick={() => void closeTrade(t)}>
                    close
                  </button>
                  <button className="small danger" onClick={() => void remove(t.id)}>
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
      {tab === "closed" && (
        <>
          {closed.error ? <Banner kind="error">{errorMessage(closed.error)}</Banner> : null}
          {closed.data?.items.length === 0 ? <EmptyState>No closed trades yet.</EmptyState> : null}
          <div className="list">
            {(closed.data?.items ?? []).map((t) => (
              <div key={t.id} className="item" onClick={() => navigate("candidates", t.mint)}>
                <div className="title">
                  <span>
                    <strong>{t.symbol ?? t.mint.slice(0, 6)}</strong> <span className="faint small">#{t.id}</span>
                  </span>
                  <span className={`score ${(t.realized_pnl_usd ?? 0) >= 0 ? "good" : "bad"}`}>
                    {fmtUsd(t.realized_pnl_usd, 2)} ({fmtPctPoints(t.realized_return_pct, 1, true)})
                  </span>
                </div>
                <div className="meta">
                  <span>
                    {fmtPrice(t.entry_price_usd)} → {fmtPrice(t.exit_price_usd)}
                  </span>
                  <span>{fmtDateTime(t.exit_at)}</span>
                  {t.signal_correct !== null ? <Badge tone={t.signal_correct ? "good" : "bad"}>{t.signal_correct ? "signal correct" : "signal wrong"}</Badge> : null}
                  {t.exit_reason ? <span className="faint">{t.exit_reason}</span> : null}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
      {tab === "new" && (
        <Card title="Record a trade">
          <div className="form">
            <div className="grid-2">
              <div>
                <label>mint *</label>
                <input value={form.mint} onChange={(e) => setForm({ ...form, mint: e.target.value.trim() })} />
              </div>
              <div>
                <label>symbol</label>
                <input value={form.symbol ?? ""} onChange={(e) => setForm({ ...form, symbol: e.target.value })} />
              </div>
              <div>
                <label>entry price USD *</label>
                <input type="number" step="any" value={form.entry_price_usd || ""} onChange={(e) => setForm({ ...form, entry_price_usd: Number(e.target.value) })} />
              </div>
              <div>
                <label>position size USD *</label>
                <input type="number" step="any" value={form.position_size_usd} onChange={(e) => setForm({ ...form, position_size_usd: Number(e.target.value) })} />
              </div>
              <div>
                <label>planned stop %</label>
                <input type="number" step="any" value={form.planned_stop_pct ?? ""} onChange={(e) => setForm({ ...form, planned_stop_pct: Number(e.target.value) })} />
              </div>
              <div>
                <label>targets % (comma)</label>
                <input value={(form.planned_targets_pct ?? []).join(",")} onChange={(e) => setForm({ ...form, planned_targets_pct: e.target.value.split(",").map((x) => Number(x.trim())).filter((x) => x > 0) })} />
              </div>
              <div>
                <label>candidate type</label>
                <input value={form.candidate_type ?? ""} placeholder="e.g. smart_money" onChange={(e) => setForm({ ...form, candidate_type: e.target.value })} />
              </div>
              <div>
                <label>signal category</label>
                <input value={form.signal_category ?? ""} placeholder="e.g. WHALE_ACCUMULATION" onChange={(e) => setForm({ ...form, signal_category: e.target.value })} />
              </div>
            </div>
            <div>
              <label>entry reason</label>
              <textarea rows={2} value={form.entry_reason ?? ""} onChange={(e) => setForm({ ...form, entry_reason: e.target.value })} />
            </div>
            <div className="row">
              <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input type="checkbox" style={{ width: "auto" }} checked={form.is_paper} onChange={(e) => setForm({ ...form, is_paper: e.target.checked })} /> paper trade (uncheck for a real manual trade you executed yourself)
              </label>
            </div>
            <button className="primary" disabled={!form.mint || !form.entry_price_usd || !form.position_size_usd} onClick={() => void submit()}>
              record trade
            </button>
            {r && !r.can_open_new ? <Banner kind="warn">Risk limits reached (max positions or daily loss). You can still record, but the reference rules say no new positions.</Banner> : null}
          </div>
        </Card>
      )}
      {tab === "sizing" && (
        <Card title="Position size calculator">
          <div className="inline">
            <div>
              <label>entry price USD</label>
              <input value={sizeInput.price} onChange={(e) => setSizeInput({ ...sizeInput, price: e.target.value })} />
            </div>
            <div>
              <label>stop %</label>
              <input value={sizeInput.stop} onChange={(e) => setSizeInput({ ...sizeInput, stop: e.target.value })} />
            </div>
          </div>
          {sizing.error ? <Banner kind="error">{errorMessage(sizing.error)}</Banner> : null}
          {sizing.data ? (
            <div style={{ marginTop: 8 }}>
              <div className="grid-3">
                <Stat label="Position" value={fmtUsd(sizing.data.position_size_usd, 2)} sub={`risk ${fmtUsd(sizing.data.risk_usd, 2)} (${sizing.data.risk_per_trade_pct}%)`} />
                <Stat label="Stop price" value={fmtPrice(sizing.data.stop_price_usd)} sub={`-${sizing.data.stop_loss_pct}%`} />
                <Stat label="Equity" value={fmtUsd(sizing.data.account_equity_usd)} sub={`max ${sizing.data.max_open_positions} open`} />
              </div>
              <KV rows={sizing.data.targets.map((t) => [`+${t.pct}% target`, `${fmtPrice(t.price_usd)} → ${fmtUsd(t.profit_usd, 2)} profit`])} />
              <p className="faint tiny" style={{ marginTop: 6 }}>{sizing.data.note}</p>
            </div>
          ) : (
            <Spinner />
          )}
        </Card>
      )}
      {tab === "performance" && (
        <Card title="Performance">
          {perf.error ? <Banner kind="error">{errorMessage(perf.error)}</Banner> : null}
          {perf.data ? (
            perf.data.overall.trades ? (
              <>
                <div className="grid-3">
                  <Stat label="Trades" value={perf.data.overall.trades} />
                  <Stat label="Win rate" value={fmtPct(perf.data.overall.win_rate)} />
                  <Stat label="Total PnL" value={fmtUsd(perf.data.overall.total_pnl_usd, 2)} tone={(perf.data.overall.total_pnl_usd ?? 0) >= 0 ? "good" : "bad"} />
                  <Stat label="Avg return" value={fmtPctPoints(perf.data.overall.avg_return_pct, 1, true)} />
                  <Stat label="Profit factor" value={perf.data.overall.profit_factor?.toFixed(2) ?? "n/a"} />
                  <Stat label="Max drawdown" value={fmtUsd(perf.data.overall.max_drawdown_usd, 2)} />
                </div>
                <PerfTable title="By candidate type" buckets={perf.data.by_candidate_type} />
                <PerfTable title="By model version" buckets={perf.data.by_model_version} />
                <PerfTable title="By signal category" buckets={perf.data.by_signal_category} />
                <PerfTable title="By GPT recommendation" buckets={perf.data.by_gpt_recommendation} />
                <PerfTable title="Paper vs manual" buckets={perf.data.paper_vs_manual} />
              </>
            ) : (
              <EmptyState>No closed trades yet - performance appears once trades are closed.</EmptyState>
            )
          ) : (
            <Spinner />
          )}
        </Card>
      )}
    </>
  );
}
