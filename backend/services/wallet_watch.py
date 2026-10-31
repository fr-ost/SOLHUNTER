"""Manual wallet tracker: reacts to trades by wallets the user follows (counters, live event, Telegram)."""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import update

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.database.engine import session_scope
from backend.ingestion.events import TradeEvent
from backend.models import TrackedWallet
from backend.notifications.alerts import AlertService
from backend.scanner.state import TokenState
from backend.services import keys
from backend.services.redis_client import get_redis, publish_event

if TYPE_CHECKING:
    from backend.scanner.main import ScannerWorker

log = get_logger(__name__)


def build_wallet_trade_message(cfg: dict[str, Any], ev: TradeEvent, state: TokenState | None, sol_usd: float | None) -> str:
    label = html.escape(cfg.get("label") or f"{ev.trader[:4]}…{ev.trader[-4:]}")
    side = "BOUGHT" if ev.side == "buy" else "SOLD"
    icon = "🟢" if ev.side == "buy" else "🔴"
    sol = ev.sol_amount or 0.0
    usd = f" (${sol * sol_usd:,.0f})" if sol_usd else ""
    symbol = html.escape((state.symbol if state else None) or ev.mint[:6])
    lines = [
        f"<b>👁 {icon} {label} {side} {sol:.2f} SOL{usd} of {symbol}</b>",
        f"wallet <code>{ev.trader}</code>",
        f"token <code>{ev.mint}</code>",
    ]
    if state is not None and state.last_evaluation:
        ev_info = state.last_evaluation
        lines.append(f"SolHunter: {ev_info.get('status')} · score {float(ev_info.get('score') or 0):.0f} · {ev_info.get('confidence')}")
    elif state is not None and state.flags.get("source") == "wallet_tracker":
        lines.append("SolHunter: now tracking this token - analysis follows in ~1-5 minutes")
    if state is not None and state.market_cap_sol_safe is not None and sol_usd:
        lines.append(f"Mcap ≈ ${state.market_cap_sol_safe * sol_usd:,.0f}")
    lines.append(
        f'<a href="https://dexscreener.com/solana/{ev.mint}">DexScreener</a> · '
        f'<a href="https://solscan.io/account/{ev.trader}">Wallet</a> · '
        f'<a href="https://solscan.io/tx/{ev.signature}">Tx</a>'
    )
    lines.append("<i>Research signal only - not financial advice.</i>")
    return "\n".join(lines)


class WalletWatch:
    def __init__(self, worker: ScannerWorker) -> None:
        self.worker = worker
        evaluator = worker.evaluator
        self.alerts: AlertService = evaluator.alerts if evaluator is not None else AlertService()
        self.alerts_sent = 0

    async def on_tracked_trade(self, state: TokenState, ev: TradeEvent, cfg: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        async with session_scope() as session:
            await session.execute(
                update(TrackedWallet)
                .where(TrackedWallet.address == ev.trader)
                .values(trades_seen=TrackedWallet.trades_seen + 1, last_trade_at=ev.block_time, updated_at=now)
            )
        await publish_event(
            keys.EVENTS_CHANNEL,
            {"type": "wallet.trade", "wallet": ev.trader, "label": cfg.get("label"), "side": ev.side, "sol": ev.sol_amount, "mint": ev.mint, "symbol": state.symbol, "signature": ev.signature, "at": ev.block_time.isoformat()},
        )
        await self._maybe_alert(state, ev, cfg)

    async def _maybe_alert(self, state: TokenState, ev: TradeEvent, cfg: dict[str, Any]) -> str:
        if ev.side == "buy" and not cfg.get("alert_on_buy", True):
            return "disabled"
        if ev.side == "sell" and not cfg.get("alert_on_sell", True):
            return "disabled"
        if (ev.sol_amount or 0.0) < float(cfg.get("min_alert_sol") or 0.0):
            return "below_min"
        cooldown = get_settings().wallet_alert_cooldown_seconds
        try:
            fresh = await get_redis().set(f"{keys.ALERT_COOLDOWN_PREFIX}wallet:{ev.trader}:{ev.mint}:{ev.side}", "1", ex=cooldown, nx=True)
        except Exception:  # noqa: BLE001
            fresh = True
        if not fresh:
            return "cooldown"
        sol_usd = await self.worker.sol_price.get()
        message = build_wallet_trade_message(cfg, ev, state, sol_usd)
        title = f"{cfg.get('label') or ev.trader[:6]} {ev.side} {ev.sol_amount or 0:.2f} SOL {state.symbol or ev.mint[:6]}"
        outcome = await self.alerts._deliver(  # noqa: SLF001 - shared delivery/recording path
            ev.mint, "wallet_trade", f"wallet:{ev.trader}:{ev.signature}:{ev.side}", title, message, urgent=False,
            payload={"wallet": ev.trader, "side": ev.side, "sol": ev.sol_amount, "signature": ev.signature},
        )
        if outcome.startswith("sent"):
            self.alerts_sent += 1
        return outcome
