import { useState } from "react";
import { Address, Badge, Banner, Card, EmptyState, KV, SourceBadge, Spinner, Stat, Tabs } from "../components/ui";
import { errorMessage, useAsync } from "../hooks/useAsync";
import { navigate } from "../hooks/useRoute";
import { useApi } from "../hooks/useSettings";
import { fmtAge, fmtPct, fmtSol, shortAddr, timeAgo } from "../services/format";

const CATEGORIES = ["", "smart_money", "profitable_early_buyer", "accumulating_whale", "distributing_whale", "sniper", "developer", "insider_associated", "coordinated_cluster", "market_maker", "unknown"];
const str = (v: unknown): string => (v === null || v === undefined ? "" : String(v));
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);

const categoryTone = (c: string): "good" | "bad" | "ok" | "" =>
  c === "smart_money" || c === "profitable_early_buyer" || c === "accumulating_whale" ? "good" : c === "insider_associated" || c === "developer" || c === "distributing_whale" || c === "coordinated_cluster" ? "bad" : c === "sniper" ? "ok" : "";

export function WalletsPage(): JSX.Element {
  const api = useApi();
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState("realized_pnl_sol");
  const [tab, setTab] = useState<"wallets" | "whales">("wallets");
  const [lookup, setLookup] = useState("");
  const wallets = useAsync(() => api.topWallets({ category: category || undefined, sort, limit: 60 }), [api, category, sort], { enabled: api.configured && tab === "wallets" });
  const whales = useAsync(() => api.whalesRecent({ limit: 60 }), [api], { refreshMs: 30_000, enabled: api.configured && tab === "whales" });
  if (!api.configured) return <Banner kind="warn">Configure the API in Settings first.</Banner>;
  return (
    <>
      <Card title="Wallet intelligence">
        <div className="inline">
          <div>
            <label>open wallet</label>
            <input placeholder="wallet address" value={lookup} onChange={(e) => setLookup(e.target.value)} onKeyDown={(e) => e.key === "Enter" && lookup.trim() && navigate("wallets", lookup.trim())} />
          </div>
          <button onClick={() => lookup.trim() && navigate("wallets", lookup.trim())}>open</button>
        </div>
        <div style={{ marginTop: 8 }}>
          <Tabs value={tab} onChange={setTab} options={[{ id: "wallets", label: "Top wallets" }, { id: "whales", label: "Recent whale trades" }]} />
        </div>
        {tab === "wallets" ? (
          <div className="inline">
            <div>
              <label>category</label>
              <select value={category} onChange={(e) => setCategory(e.target.value)}>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c ? c.replace(/_/g, " ") : "all categories"}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label>sort</label>
              <select value={sort} onChange={(e) => setSort(e.target.value)}>
                <option value="realized_pnl_sol">realized pnl</option>
                <option value="win_rate">win rate</option>
                <option value="profit_factor">profit factor</option>
                <option value="tokens_traded">tokens traded</option>
                <option value="last_active_at">last active</option>
              </select>
            </div>
          </div>
        ) : null}
      </Card>
      {tab === "wallets" ? (
        <>
          {wallets.error ? <Banner kind="error">{errorMessage(wallets.error)}</Banner> : null}
          {wallets.loading && !wallets.data ? <Spinner /> : null}
          {wallets.data && wallets.data.items.length === 0 ? <EmptyState>No wallet profiles yet. Profiles are built from observed trades; smart-money labels need a track record.</EmptyState> : null}
          <div className="list">
            {(wallets.data?.items ?? []).map((w) => (
              <div key={w.address} className="item" onClick={() => navigate("wallets", w.address)}>
                <div className="title">
                  <code>{shortAddr(w.address, 5)}</code>
                  <Badge tone={categoryTone(w.category)}>
                    {w.category.replace(/_/g, " ")} {w.category_confidence ? fmtPct(w.category_confidence) : ""}
                  </Badge>
                </div>
                <div className="meta">
                  <span>pnl {fmtSol(w.realized_pnl_sol)}</span>
                  <span>unreal. {fmtSol(w.unrealized_pnl_sol)}</span>
                  <span>WR {fmtPct(w.win_rate)}</span>
                  <span>PF {w.profit_factor?.toFixed(2) ?? "n/a"}</span>
                  <span>{w.tokens_traded} tokens</span>
                  <span>rugs {w.rug_exposure_count}</span>
                  <span>{timeAgo(w.last_active_at)}</span>
                </div>
              </div>
            ))}
          </div>
        </>
      ) : (
        <>
          {whales.error ? <Banner kind="error">{errorMessage(whales.error)}</Banner> : null}
          {whales.data ? (
            whales.data.whale_events.length ? (
              <div className="table-wrap card tight">
                <table>
                  <thead>
                    <tr>
                      <th>when</th>
                      <th>token</th>
                      <th>wallet</th>
                      <th>type</th>
                      <th className="num">SOL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {whales.data.whale_events.map((e, i) => (
                      <tr key={i} style={{ cursor: "pointer" }} onClick={() => navigate("candidates", str(e.mint))}>
                        <td>{timeAgo(str(e.occurred_at))}</td>
                        <td>{str(e.symbol) || shortAddr(str(e.mint))}</td>
                        <td>
                          <code>{shortAddr(str(e.wallet))}</code>
                          <div className="faint tiny">{str(e.wallet_category).replace(/_/g, " ")}</div>
                        </td>
                        <td className={str(e.direction) === "buy" ? "good" : "bad"}>{str(e.event_type)}</td>
                        <td className="num">{num(e.sol_amount)?.toFixed(2) ?? "n/a"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState>No whale trades recorded yet.</EmptyState>
            )
          ) : (
            <Spinner />
          )}
        </>
      )}
    </>
  );
}

export function WalletDetailPage({ address }: { address: string }): JSX.Element {
  const api = useApi();
  const { data, error, loading } = useAsync(() => api.wallet(address), [api, address], { enabled: api.configured });
  if (error) return <Banner kind="error">{errorMessage(error)}</Banner>;
  if (!data) return <div className="row"><Spinner /> {loading ? <span className="muted">Loading wallet…</span> : null}</div>;
  const p = data.profile;
  return (
    <>
      <Card title="Wallet" right={p ? <Badge tone={categoryTone(p.category)}>{p.category.replace(/_/g, " ")}</Badge> : <Badge>unprofiled</Badge>}>
        <div className="row small">
          <Address value={data.address} chars={8} />
          <a href={`https://solscan.io/account/${data.address}`} target="_blank" rel="noreferrer">
            Solscan ↗
          </a>
        </div>
        {p ? (
          <>
            <div className="grid-3" style={{ marginTop: 6 }}>
              <Stat label="Realized PnL" value={fmtSol(p.realized_pnl_sol)} tone={p.realized_pnl_sol > 0 ? "good" : p.realized_pnl_sol < 0 ? "bad" : undefined} sub={<SourceBadge kind="estimate" label="avg-cost basis" />} />
              <Stat label="Unrealized" value={fmtSol(p.unrealized_pnl_sol)} sub="open positions" />
              <Stat label="Win rate" value={fmtPct(p.win_rate)} sub={`${p.closed_positions} closed`} />
              <Stat label="Profit factor" value={p.profit_factor?.toFixed(2) ?? "n/a"} />
              <Stat label="Avg / median return" value={`${p.avg_return_pct?.toFixed(0) ?? "n/a"}% / ${p.median_return_pct?.toFixed(0) ?? "n/a"}%`} />
              <Stat label="Avg hold" value={fmtAge(p.avg_holding_seconds)} />
              <Stat label="Early-entry WR" value={fmtPct(p.early_entry_win_rate)} />
              <Stat label="Rug exposure" value={p.rug_exposure_count} tone={p.rug_exposure_count ? "bad" : undefined} />
              <Stat label="Tokens / trades" value={`${p.tokens_traded} / ${p.trades_count}`} />
            </div>
            <KV
              rows={[
                ["confidence", fmtPct(p.category_confidence)],
                ["reasons", (p.classification_reasons ?? []).join("; ") || "n/a"],
                ["first seen", timeAgo(p.first_seen_at)],
                ["last active", timeAgo(p.last_active_at)],
              ]}
            />
          </>
        ) : (
          <p className="muted small" style={{ marginTop: 6 }}>
            No performance profile yet - this wallet has positions but has not been profiled (profiles refresh a few minutes after trades).
          </p>
        )}
      </Card>
      <Card title={`Positions (${data.positions.length})`}>
        {data.positions.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>token</th>
                  <th>status</th>
                  <th className="num">spent</th>
                  <th className="num">received</th>
                  <th className="num">realized</th>
                  <th>basis</th>
                </tr>
              </thead>
              <tbody>
                {data.positions.map((pos, i) => (
                  <tr key={i} style={{ cursor: "pointer" }} onClick={() => navigate("candidates", str(pos.mint))}>
                    <td>
                      {str(pos.symbol) || shortAddr(str(pos.mint))}
                      {pos.is_early_buyer ? <span className="faint tiny"> early</span> : null}
                    </td>
                    <td>{str(pos.status)}</td>
                    <td className="num">{num(pos.sol_spent)?.toFixed(2)}</td>
                    <td className="num">{num(pos.sol_received)?.toFixed(2)}</td>
                    <td className={`num ${(num(pos.realized_pnl_sol) ?? 0) >= 0 ? "good" : "bad"}`}>{num(pos.realized_pnl_sol)?.toFixed(2)}</td>
                    <td className="tiny faint">{str(pos.cost_basis_confidence)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState>No positions observed.</EmptyState>
        )}
      </Card>
      <Card title={`Relationships (${data.relationships.length})`}>
        {data.relationships.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>other wallet</th>
                  <th>type</th>
                  <th className="num">conf.</th>
                  <th className="num">obs.</th>
                </tr>
              </thead>
              <tbody>
                {data.relationships.map((r, i) => {
                  const other = str(r.wallet_a) === data.address ? str(r.wallet_b) : str(r.wallet_a);
                  return (
                    <tr key={i} style={{ cursor: "pointer" }} onClick={() => navigate("wallets", other)}>
                      <td>
                        <code>{shortAddr(other)}</code>
                      </td>
                      <td>{str(r.relationship_type).replace(/_/g, " ")}</td>
                      <td className="num">{fmtPct(num(r.confidence))}</td>
                      <td className="num">{str(r.observations)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState>No relationships detected (shared funding alone never proves common ownership).</EmptyState>
        )}
      </Card>
      <Card title={`Whale / smart-money events (${data.whale_events.length + data.smart_money_events.length})`}>
        {data.whale_events.length ? (
          <div className="list">
            {data.whale_events.slice(0, 30).map((e, i) => (
              <div key={i} className="item" onClick={() => navigate("candidates", str(e.mint))}>
                <div className="title">
                  <span className={str(e.direction) === "buy" ? "good" : "bad"}>
                    {str(e.event_type)} {num(e.sol_amount)?.toFixed(2)} SOL
                  </span>
                  <span className="faint tiny">{timeAgo(str(e.occurred_at))}</span>
                </div>
                <div className="meta">
                  <span>{shortAddr(str(e.mint))}</span>
                  {e.corroborated ? <span>corroborated</span> : null}
                  {num(e.pct_of_liquidity) !== null ? <span>{fmtPct(num(e.pct_of_liquidity), 1)} of liquidity</span> : null}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState>No whale events for this wallet.</EmptyState>
        )}
      </Card>
    </>
  );
}
