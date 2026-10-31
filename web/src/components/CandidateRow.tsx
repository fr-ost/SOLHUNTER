import { ageSecondsFromLaunch, fmtAge, fmtPct, fmtPrice, fmtUsd, timeAgo } from "../services/format";
import type { CandidateItem } from "../types/api";
import { navigate } from "../hooks/useRoute";
import { Badge, Score, ScoreBar, SourceBadge, gptTone, statusTone, whaleTone } from "./ui";

export function CandidateRow({ item }: { item: CandidateItem }): JSX.Element {
  const snap = item.latest_snapshot ?? null;
  const age = snap?.age_seconds ?? ageSecondsFromLaunch(item.launch_time);
  const buys = snap?.buys_5m ?? null;
  const sells = snap?.sells_5m ?? null;
  const ratio = buys !== null && sells !== null ? (sells > 0 ? (buys / sells).toFixed(2) : buys > 0 ? "∞" : "n/a") : "n/a";
  return (
    <div className="item" onClick={() => navigate("candidates", item.mint)}>
      <div className="title">
        <span>
          <strong>{item.symbol ?? "?"}</strong> <span className="muted small">{item.name ?? ""}</span>
        </span>
        <span className="row">
          <Badge tone={statusTone(item.status)}>{item.status}</Badge>
          <Score value={item.overall_score} />
        </span>
      </div>
      <ScoreBar score={item.overall_score} />
      <div className="meta">
        <span>age {fmtAge(age)}</span>
        <span>{fmtPrice(snap?.price_usd)}</span>
        <span>mcap {fmtUsd(snap?.market_cap_usd)}</span>
        <span>liq {fmtUsd(snap?.liquidity_usd)}</span>
        <span>vol5m {snap?.volume_sol_5m?.toFixed(1) ?? "n/a"} SOL</span>
        <span>b/s {ratio}</span>
        <span>top10 {fmtPct(snap?.top10_pct)}</span>
      </div>
      <div className="meta">
        <span>
          <SourceBadge kind="model" /> rug {fmtPct(item.rug_risk)} · p10 {fmtPct(item.breakout_p10)} · p20 {fmtPct(item.breakout_p20)}
          {item.p_2x_24h != null ? <> · 2x/24h {fmtPct(item.p_2x_24h)}</> : null}
          {item.ml_is_experimental ? <span className="faint"> (experimental)</span> : null}
        </span>
      </div>
      <div className="meta">
        {item.phase ? <Badge tone={item.phase === "established" ? "good" : item.phase === "survival" ? "ok" : ""} title="lifecycle phase">{item.phase}</Badge> : null}
        {item.growth_score != null ? <Badge tone={item.growth_score >= 0.6 ? "good" : ""} title="growth score (survivor re-checks)">growth {Math.round(item.growth_score * 100)}</Badge> : null}
        {item.whale_signal ? <Badge tone={whaleTone(item.whale_signal)}>{item.whale_signal.replace(/_/g, " ").toLowerCase()}</Badge> : null}
        {snap?.smart_money_buyers ? <Badge tone="good">{snap.smart_money_buyers} smart-money buyers</Badge> : null}
        {item.gpt_recommendation ? (
          <Badge tone={gptTone(item.gpt_recommendation)}>
            <SourceBadge kind="gpt" /> {item.gpt_recommendation.replace(/_/g, " ").toLowerCase()}
          </Badge>
        ) : null}
        <span className="faint">{item.confidence} conf · {timeAgo(item.scored_at)}</span>
      </div>
    </div>
  );
}
