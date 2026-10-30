"""Alert service: candidate alerts and urgent risk warnings with cooldowns and deduplication.

Runs inside the scanner (independent of the Chrome extension). Every alert is recorded in the
``alerts`` table (sent / failed / suppressed) so the dashboard can show history.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.database.engine import session_scope
from backend.models import Alert, CandidateScore, GptAnalysis
from backend.notifications.telegram import TelegramClient, TelegramError
from backend.scanner.state import TokenState
from backend.services import keys
from backend.services.redis_client import get_redis

log = get_logger(__name__)

URGENT_TYPES = {"whale_exit": "Whale exit warning", "insider_selling": "Insider / developer selling", "liquidity_withdrawal": "Liquidity withdrawal", "rug_pull": "Rug pull detected", "dev_dump": "Developer dump", "whale_distribution": "Whale distribution"}


def _fmt_usd(x: float | None) -> str:
    if x is None:
        return "n/a"
    if x >= 1_000_000:
        return f"${x / 1e6:.2f}M"
    if x >= 1_000:
        return f"${x / 1e3:.1f}k"
    return f"${x:.0f}"


def _pct(x: float | None, nd: int = 0) -> str:
    return "n/a" if x is None else f"{x * 100:.{nd}f}%"


def _age(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    m = int(seconds // 60)
    return f"{m}m" if m < 120 else f"{m // 60}h{m % 60:02d}m"


def links_for(mint: str) -> dict[str, str]:
    s = get_settings()
    out = {"dexscreener": f"https://dexscreener.com/solana/{mint}", "pumpfun": f"https://pump.fun/coin/{mint}"}
    base = s.dashboard_base_url or s.public_api_base_url
    if base:
        out["dashboard"] = f"{base.rstrip('/')}/api/v1/candidates/{mint}" if not s.dashboard_base_url else f"{base.rstrip('/')}/#/candidates/{mint}"
    return out


def build_candidate_message(state: TokenState, score: CandidateScore, snapshot: dict[str, Any], gpt: GptAnalysis | None = None, status_label: str = "CANDIDATE") -> str:
    sym = html.escape(state.symbol or "?")
    name = html.escape(state.name or "")
    whale = score.whale_summary or {}
    comps = score.components or {}
    exp = " (experimental)" if score.ml_is_experimental else ""
    lines = [
        f"<b>🔎 GemFinder {status_label}: {sym}</b> {name}",
        f"<code>{state.mint}</code>",
        f"Score <b>{score.overall_score:.0f}/100</b> · confidence {score.confidence} · age {_age(snapshot.get('age_seconds'))} · venue {state.pool_kind}",
        f"Mcap {_fmt_usd(snapshot.get('market_cap_usd'))} · Liquidity {_fmt_usd(snapshot.get('liquidity_usd'))} · Vol 5m {snapshot.get('volume_sol_5m') or 0:.1f} SOL · buyers 5m {snapshot.get('unique_buyers_5m') or 0}",
        f"Holders {snapshot.get('holder_count') or 'n/a'} · top10 {_pct(snapshot.get('top10_pct'))} · dev {_pct(snapshot.get('dev_holding_pct'))}",
        f"Whales: {score.whale_signal or 'n/a'} · smart-money buyers {whale.get('smart_money_buyers', 0)} · whale net 5m {whale.get('whale_net_flow_5m', 0):+.2f} SOL",
        f"Rug risk {_pct(score.rug_risk)}{exp} · P(+10/15/20) {_pct(score.breakout_p10)}/{_pct(score.breakout_p15)}/{_pct(score.breakout_p20)}{exp}",
    ]
    if gpt is not None and gpt.analysis:
        a = gpt.analysis
        p = a.get("probability_estimates") or {}
        lines.append(f"GPT: <b>{html.escape(str(gpt.recommendation))}</b> ({a.get('recommendation_confidence')} conf) · p10 {_pct(p.get('p_plus_10_pct'))} · rug {_pct(p.get('p_rug_or_collapse'))}")
        bull = a.get("bullish_evidence") or []
        bear = a.get("bearish_evidence") or []
        if bull:
            lines.append("✅ " + " | ".join(html.escape(str(b)) for b in bull[:3]))
        if bear:
            lines.append("⚠️ " + " | ".join(html.escape(str(b)) for b in bear[:3]))
    else:
        top = sorted(comps.items(), key=lambda kv: -kv[1])[:3]
        if top:
            lines.append("✅ " + ", ".join(f"{k} {v:.0f}" for k, v in top))
        warnings = list((score.rejection_reasons or [])[:3]) + [f"{k} -{v:.0f}" for k, v in sorted((score.penalties or {}).items(), key=lambda kv: -kv[1])[:2]]
        if warnings:
            lines.append("⚠️ " + " | ".join(html.escape(w) for w in warnings))
    ex = (score.execution or {}).get("estimates") or []
    ref = next((e for e in ex if e.get("size_usd") == 250.0), None)
    if ref and ref.get("available"):
        lines.append(f"Exec $250: sell impact {ref.get('sell_impact_pct')}% · round-trip cost {ref.get('round_trip_cost_pct')}%")
    links = links_for(state.mint)
    link_parts = [f'<a href="{links["dexscreener"]}">DexScreener</a>', f'<a href="{links["pumpfun"]}">pump.fun</a>']
    if "dashboard" in links:
        link_parts.insert(0, f'<a href="{links["dashboard"]}">Dashboard</a>')
    lines.append(" · ".join(link_parts))
    lines.append("<i>Research signal only - not financial advice. Verify sellability before any manual trade.</i>")
    return "\n".join(lines)


def build_risk_message(state: TokenState, risk_type: str, title: str, details: dict[str, Any] | None = None) -> str:
    sym = html.escape(state.symbol or "?")
    links = links_for(state.mint)
    lines = [
        f"<b>🚨 {URGENT_TYPES.get(risk_type, 'Risk escalation')}: {sym}</b>",
        f"<code>{state.mint}</code>",
        html.escape(title),
    ]
    if details:
        extra = {k: v for k, v in details.items() if k in {"score", "snapshot_kind", "from_status", "to_status"}}
        if extra:
            lines.append(" · ".join(f"{k} {v}" for k, v in extra.items()))
    lines.append(f'<a href="{links["dexscreener"]}">DexScreener</a>')
    return "\n".join(lines)


class AlertService:
    def __init__(self, telegram: TelegramClient | None = None) -> None:
        self.telegram = telegram or TelegramClient()
        self.sent = 0
        self.suppressed = 0
        self.failed = 0

    @property
    def enabled(self) -> bool:
        return self.telegram.enabled

    async def _cooldown_active(self, key: str, ttl: int) -> bool:
        try:
            return not bool(await get_redis().set(f"{keys.ALERT_COOLDOWN_PREFIX}{key}", "1", ex=ttl, nx=True))
        except Exception:  # noqa: BLE001
            return False

    async def _hourly_cap_reached(self) -> bool:
        cfg = get_settings().telegram
        try:
            r = get_redis()
            k = f"{keys.ALERT_COOLDOWN_PREFIX}hourly:{datetime.now(UTC):%Y%m%d%H}"
            n = await r.incr(k)
            await r.expire(k, 3700)
            return n > cfg.max_alerts_per_hour
        except Exception:  # noqa: BLE001
            return False

    async def _record(self, mint: str | None, alert_type: str, dedupe_key: str, title: str, message: str, status: str, error: str | None = None, urgent: bool = False, payload: dict[str, Any] | None = None) -> bool:
        async with session_scope() as session:
            stmt = pg_insert(Alert).values(mint=mint, alert_type=alert_type, channel="telegram", dedupe_key=dedupe_key[:256], title=title[:256], message=message, payload=payload, status=status, error=error, created_at=datetime.now(UTC), sent_at=datetime.now(UTC) if status == "sent" else None, urgent=urgent)
            stmt = stmt.on_conflict_do_nothing(index_elements=[Alert.dedupe_key])
            res = await session.execute(stmt)
            return bool(res.rowcount)

    async def _deliver(self, mint: str | None, alert_type: str, dedupe_key: str, title: str, message: str, urgent: bool, payload: dict[str, Any] | None = None) -> str:
        if not self.enabled:
            await self._record(mint, alert_type, dedupe_key, title, message, "suppressed", "telegram disabled", urgent, payload)
            self.suppressed += 1
            return "suppressed"
        try:
            await self.telegram.send_message(message)
            inserted = await self._record(mint, alert_type, dedupe_key, title, message, "sent", None, urgent, payload)
            self.sent += 1
            return "sent" if inserted else "sent_duplicate"
        except TelegramError as exc:
            await self._record(mint, alert_type, dedupe_key, title, message, "failed", str(exc)[:300], urgent, payload)
            self.failed += 1
            log.warning("alerts.send_failed", mint=mint, error=str(exc)[:200])
            return "failed"

    async def send_candidate_alert(self, state: TokenState, score: CandidateScore, snapshot: dict[str, Any], gpt: GptAnalysis | None = None) -> str:
        cfg = get_settings().telegram
        status = score.status
        if status == "watchlist" and not cfg.send_watchlist:
            return "skipped_watchlist"
        if status not in {"candidate", "watchlist"} or score.hard_fail:
            return "skipped_status"
        if score.overall_score < cfg.alert_min_score and status == "candidate":
            return "skipped_score"
        if gpt is not None and gpt.recommendation in {"REJECT", "HIGH_RISK"}:
            return "skipped_gpt_downgrade"
        hour_bucket = datetime.now(UTC).strftime("%Y%m%d%H")
        dedupe_key = f"{status}:{state.mint}:{hour_bucket}"
        try:
            exists = await get_redis().exists(f"{keys.ALERT_COOLDOWN_PREFIX}{status}:{state.mint}")
        except Exception:  # noqa: BLE001
            exists = 0
        if exists:
            self.suppressed += 1
            return "cooldown"
        if await self._hourly_cap_reached():
            await self._record(state.mint, status, dedupe_key, f"{status} {state.symbol}", "hourly cap", "suppressed", "hourly alert cap reached", False)
            self.suppressed += 1
            return "hourly_cap"
        message = build_candidate_message(state, score, snapshot, gpt, status_label=status.upper())
        outcome = await self._deliver(state.mint, status, dedupe_key, f"{status} {state.symbol or state.mint[:8]} score {score.overall_score:.0f}", message, urgent=False, payload={"score_id": score.id, "score": score.overall_score, "gpt": gpt.recommendation if gpt else None})
        if outcome.startswith("sent"):
            state.alerted = True
            await self._cooldown_active(f"{status}:{state.mint}", cfg.cooldown_seconds)
        return outcome

    async def send_risk_alert(self, state: TokenState, risk_type: str, title: str, details: dict[str, Any] | None = None) -> str:
        """Urgent warnings only for tokens we previously surfaced (alerted, candidate or watchlist)."""
        if not (state.alerted or state.status in {"candidate", "watchlist"}):
            return "skipped_not_surfaced"
        cfg = get_settings().telegram
        if await self._cooldown_active(f"risk:{risk_type}:{state.mint}", cfg.urgent_cooldown_seconds):
            self.suppressed += 1
            return "cooldown"
        dedupe_key = f"risk:{risk_type}:{state.mint}:{datetime.now(UTC):%Y%m%d%H%M}"
        message = build_risk_message(state, risk_type, title, details)
        return await self._deliver(state.mint, "risk_escalation" if risk_type not in URGENT_TYPES else risk_type, dedupe_key, title, message, urgent=True, payload=details)

    async def send_system_alert(self, title: str, message: str, dedupe_key: str) -> str:
        return await self._deliver(None, "system", dedupe_key, title, f"<b>GemFinder system</b>\n{html.escape(message)}", urgent=True)

    def stats(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "sent": self.sent, "suppressed": self.suppressed, "failed": self.failed}
