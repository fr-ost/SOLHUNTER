"""Outcome labelling: turns snapshots (with point-in-time features) into training samples.

For every snapshot older than the horizon we look at what the price and liquidity did AFTER the
snapshot (trades and later snapshots) and record the realised outcome. Dead tokens (no trades
after the snapshot) are kept as negative samples: for a would-be buyer a token nobody trades is
an unsellable position, which we record as -100% with ``dead=True``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.intelligence.wallets import mark_token_rugged
from backend.models import Token, TokenRiskEvent, TokenSnapshot, TokenTransaction, TrainingSample

log = get_logger(__name__)


@dataclass
class Outcome:
    max_return_pct: float
    min_return_pct: float
    close_return_pct: float
    hit_10: bool
    hit_15: bool
    hit_20: bool
    hit_stop: bool
    time_to_10_s: float | None
    time_to_15_s: float | None
    time_to_20_s: float | None
    rugged: bool
    liquidity_removed: bool
    dead: bool
    observations: int


def compute_outcome(
    entry_price: float,
    entry_liquidity: float | None,
    observations: list[tuple[datetime, float, float | None]],
    entry_time: datetime,
    cost_pct: float,
    stop_pct: float,
    rug_signalled: bool = False,
) -> Outcome:
    """``observations``: (time, price_sol, liquidity_sol|None) strictly after entry, within the horizon."""
    if entry_price <= 0 or not observations:
        return Outcome(0.0, -100.0, -100.0, False, False, False, True, None, None, None, rug_signalled, rug_signalled, True, 0)
    targets = {10: cost_pct + 10.0, 15: cost_pct + 15.0, 20: cost_pct + 20.0}
    time_to: dict[int, float | None] = {10: None, 15: None, 20: None}
    max_ret = -100.0
    min_ret = 100.0
    stop_hit = False
    first_target_time: float | None = None
    liquidity_removed = False
    for ts, price, liq in observations:
        ret = (price / entry_price - 1.0) * 100.0
        elapsed = (ts - entry_time).total_seconds()
        max_ret = max(max_ret, ret)
        min_ret = min(min_ret, ret)
        if ret <= -stop_pct and first_target_time is None and not stop_hit:
            stop_hit = True
        for t, needed in targets.items():
            if time_to[t] is None and ret >= needed:
                time_to[t] = elapsed
                if first_target_time is None:
                    first_target_time = elapsed
        if entry_liquidity and liq is not None and liq < entry_liquidity * 0.2:
            liquidity_removed = True
    close_ret = (observations[-1][1] / entry_price - 1.0) * 100.0
    rugged = rug_signalled or liquidity_removed or close_ret < -85.0
    return Outcome(
        max_return_pct=round(max_ret, 4),
        min_return_pct=round(min_ret, 4),
        close_return_pct=round(close_ret, 4),
        hit_10=time_to[10] is not None,
        hit_15=time_to[15] is not None,
        hit_20=time_to[20] is not None,
        hit_stop=stop_hit,
        time_to_10_s=time_to[10],
        time_to_15_s=time_to[15],
        time_to_20_s=time_to[20],
        rugged=rugged,
        liquidity_removed=liquidity_removed,
        dead=len(observations) < 3 and close_ret < -50.0,
        observations=len(observations),
    )


async def label_pending_snapshots(session: AsyncSession, limit: int = 200, now: datetime | None = None) -> int:
    settings = get_settings()
    now = now or datetime.now(UTC)
    horizon = timedelta(minutes=settings.scanner.outcome_horizon_minutes)
    ready_before = now - timedelta(minutes=settings.scanner.outcome_label_delay_minutes)
    # Only label snapshots whose token was still tracked for the whole horizon (otherwise the outcome
    # would be truncated), or whose token is already final (dead / rejected with a hard failure).
    pending = (
        await session.execute(
            select(TokenSnapshot)
            .join(Token, Token.mint == TokenSnapshot.mint)
            .outerjoin(TrainingSample, TrainingSample.snapshot_id == TokenSnapshot.id)
            .where(
                TrainingSample.id.is_(None),
                TokenSnapshot.features.is_not(None),
                func.jsonb_typeof(TokenSnapshot.features) == "object",
                TokenSnapshot.captured_at < ready_before,
                (Token.tracking_until.is_(None)) | (Token.tracking_until >= TokenSnapshot.captured_at + horizon) | (Token.status.in_(["dead", "rejected"])),
            )
            .order_by(TokenSnapshot.captured_at.asc())
            .limit(limit)
        )
    ).scalars().all()
    if not pending:
        return 0
    rows: list[dict[str, Any]] = []
    rugged_mints: set[str] = set()
    for snap in pending:
        if snap.price_sol is None:
            continue
        end = snap.captured_at + horizon
        trades = (
            await session.execute(
                select(TokenTransaction.block_time, TokenTransaction.price_sol, TokenTransaction.v_sol_in_curve)
                .where(TokenTransaction.mint == snap.mint, TokenTransaction.block_time > snap.captured_at, TokenTransaction.block_time <= end, TokenTransaction.price_sol.is_not(None))
                .order_by(TokenTransaction.block_time.asc())
            )
        ).all()
        later_snaps = (
            await session.execute(
                select(TokenSnapshot.captured_at, TokenSnapshot.price_sol, TokenSnapshot.liquidity_sol)
                .where(TokenSnapshot.mint == snap.mint, TokenSnapshot.captured_at > snap.captured_at, TokenSnapshot.captured_at <= end, TokenSnapshot.price_sol.is_not(None))
                .order_by(TokenSnapshot.captured_at.asc())
            )
        ).all()
        obs: list[tuple[datetime, float, float | None]] = [(t, float(p), (float(v) - settings.scanner.pump_initial_virtual_sol) if v is not None else None) for t, p, v in trades]
        obs.extend((t, float(p), float(liq) if liq is not None else None) for t, p, liq in later_snaps)
        obs.sort(key=lambda x: x[0])
        rug_rows = (
            await session.execute(
                select(TokenRiskEvent.id).where(
                    TokenRiskEvent.mint == snap.mint,
                    TokenRiskEvent.risk_type.in_(["rug_pull", "liquidity_withdrawal"]),
                    TokenRiskEvent.severity == "critical",
                    and_(TokenRiskEvent.occurred_at > snap.captured_at, TokenRiskEvent.occurred_at <= end),
                ).limit(1)
            )
        ).first()
        outcome = compute_outcome(
            float(snap.price_sol), snap.liquidity_sol, obs, snap.captured_at, settings.ml.fee_and_slippage_pct, settings.risk.stop_loss_pct, rug_signalled=rug_rows is not None
        )
        if outcome.rugged:
            rugged_mints.add(snap.mint)
        rows.append(
            {
                "mint": snap.mint,
                "snapshot_id": snap.id,
                "snapshot_at": snap.captured_at,
                "snapshot_kind": snap.snapshot_kind,
                "features": snap.features,
                "entry_price_sol": snap.price_sol,
                "horizon_minutes": settings.scanner.outcome_horizon_minutes,
                "max_return_pct": outcome.max_return_pct,
                "min_return_pct": outcome.min_return_pct,
                "close_return_pct": outcome.close_return_pct,
                "hit_10": outcome.hit_10,
                "hit_15": outcome.hit_15,
                "hit_20": outcome.hit_20,
                "hit_stop": outcome.hit_stop,
                "time_to_10_s": outcome.time_to_10_s,
                "time_to_15_s": outcome.time_to_15_s,
                "time_to_20_s": outcome.time_to_20_s,
                "rugged": outcome.rugged,
                "liquidity_removed": outcome.liquidity_removed,
                "dead": outcome.dead,
                "observations": outcome.observations,
                "label_complete": True,
                "labelled_at": now,
            }
        )
    if rows:
        stmt = pg_insert(TrainingSample).values(rows).on_conflict_do_nothing(index_elements=[TrainingSample.snapshot_id])
        await session.execute(stmt)
    for mint in rugged_mints:
        await mark_token_rugged(session, mint)
    log.info("labeler.labelled", samples=len(rows), rugged_tokens=len(rugged_mints))
    return len(rows)
