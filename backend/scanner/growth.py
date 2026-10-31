"""Growth tracker: follows survivors from 30 minutes to 7 days and re-tracks the ones that are growing.

Real-time tracking (PumpPortal trade subscriptions) is expensive and bounded, so after the launch window a
token moves to a schedule of cheap batch checks (DexScreener, 30 tokens per call). Every check stores a
``growth`` snapshot, computes growth metrics vs earlier observations (holders, liquidity, market cap,
drawdown from ATH, higher lows, turnover, buy pressure, migration) and reschedules the token:

  * liquid & active       -> every ``GROWTH_FAST_INTERVAL_MINUTES``
  * quiet but alive        -> at the next checkpoint (0.5h, 1h, 2h, 3h, 6h, 12h, 24h, ... 7d)
  * dead / older than 7d   -> stopped
  * strong growth score    -> put back into real-time tracking so the full pipeline (holders, whales, ML,
                              GPT, alerts) runs on it again with fresh data

Tokens enter the schedule when they leave real-time tracking with some activity, when they migrate, or
when DexScreener discovery (profiles/boosts) finds tokens the scanner never saw launch.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.database.engine import session_scope
from backend.intelligence.maturity import (
    PHASE_ARCHIVED,
    PHASE_DEAD,
    GrowthPoint,
    compute_growth,
    phase_for_age,
)
from backend.ml.features import growth_features
from backend.models import Token, TokenSnapshot
from backend.providers.base import BudgetExceededError, DataUnavailable, ProviderError
from backend.providers.dexscreener import DexScreenerClient, PairData
from backend.scanner.snapshots import persist_snapshot
from backend.scanner.state import TokenState
from backend.services import keys
from backend.services.redis_client import publish_event

if TYPE_CHECKING:
    from backend.scanner.main import ScannerWorker

log = get_logger(__name__)

WSOL = "So11111111111111111111111111111111111111112"


def next_checkpoint(age_hours: float, checkpoints: list[float]) -> float | None:
    for cp in checkpoints:
        if cp > age_hours + 1e-6:
            return cp
    return None


class GrowthTracker:
    def __init__(self, worker: ScannerWorker) -> None:
        self.worker = worker
        self.dex: DexScreenerClient = worker.dex
        self._stop = asyncio.Event()
        self.cycles = 0
        self.checked = 0
        self.retracked = 0
        self.discovered = 0
        self.last_cycle_at: datetime | None = None
        self.last_discovery_at: datetime | None = None
        self.errors = 0

    # ------------------------------------------------------------------ scheduling helpers
    def schedule_after_tracking(self, state: TokenState, reason: str) -> None:
        """Retire listener: decide whether a token that left real-time tracking deserves growth checks."""
        g = get_settings().growth
        if not g.enabled or state.phase == PHASE_DEAD or state.flags.get("hard_rejected"):
            state.next_growth_check_at = None
            return
        followed = state.flags.get("manual") or state.flags.get("source") in {"growth", "migration", "wallet_tracker"}
        if state.total_trades < g.min_tracking_trades_to_follow and not followed:
            state.next_growth_check_at = None
            return
        now = datetime.now(UTC)
        age_h = state.age_at(now) / 3600.0
        cp = next_checkpoint(age_h, g.checkpoints)
        state.next_growth_check_at = (state.launch_time + timedelta(hours=cp)) if cp is not None else None
        if state.next_growth_check_at is not None and state.next_growth_check_at < now + timedelta(minutes=5):
            state.next_growth_check_at = now + timedelta(minutes=5)
        state.phase = phase_for_age(state.age_at(now))

    def stats(self) -> dict[str, Any]:
        return {"cycles": self.cycles, "checked": self.checked, "retracked": self.retracked, "discovered": self.discovered, "errors": self.errors, "last_cycle_at": self.last_cycle_at.isoformat() if self.last_cycle_at else None, "last_discovery_at": self.last_discovery_at.isoformat() if self.last_discovery_at else None}

    async def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ loop
    async def run(self) -> None:
        g = get_settings().growth
        if not g.enabled:
            log.info("growth.disabled")
            return
        while not self._stop.is_set():
            try:
                await self.cycle()
                if g.discovery_enabled and (self.last_discovery_at is None or (datetime.now(UTC) - self.last_discovery_at).total_seconds() >= g.discovery_interval_minutes * 60):
                    await self.discover()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.errors += 1
                log.exception("growth.cycle_error")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=g.cycle_seconds)
            except TimeoutError:
                pass

    async def cycle(self) -> int:
        g = get_settings().growth
        now = datetime.now(UTC)
        async with session_scope() as session:
            due = (
                await session.execute(
                    select(Token)
                    .where(Token.next_growth_check_at.is_not(None), Token.next_growth_check_at <= now, Token.phase.not_in([PHASE_DEAD, PHASE_ARCHIVED]))
                    .order_by(Token.next_growth_check_at.asc())
                    .limit(g.max_tokens_per_cycle)
                )
            ).scalars().all()
            if not due:
                self.cycles += 1
                self.last_cycle_at = now
                return 0
            mints = [t.mint for t in due]
            try:
                pairs_by_mint = await self.dex.tokens(mints)
            except ProviderError as exc:
                log.warning("growth.dexscreener_failed", error=str(exc)[:160])
                await session.execute(update(Token).where(Token.mint.in_(mints)).values(next_growth_check_at=now + timedelta(minutes=5)))
                return 0
            sol_usd = await self.worker.sol_price.get()
            retracks = 0
            for token in due:
                try:
                    did_retrack = await self._check_token(session, token, pairs_by_mint.get(token.mint, []), sol_usd, now, allow_retrack=retracks < g.max_retracks_per_cycle)
                    retracks += 1 if did_retrack else 0
                    self.checked += 1
                except Exception:  # noqa: BLE001
                    self.errors += 1
                    log.exception("growth.check_failed", mint=token.mint)
                    token.next_growth_check_at = now + timedelta(minutes=15)
            self.retracked += retracks
        self.cycles += 1
        self.last_cycle_at = now
        return len(due)

    # ------------------------------------------------------------------ one token
    async def _history(self, session: AsyncSession, token: Token, limit: int = 48) -> list[GrowthPoint]:
        rows = (
            await session.execute(
                select(TokenSnapshot)
                .where(TokenSnapshot.mint == token.mint, TokenSnapshot.snapshot_kind.in_(["growth", "m15", "m10", "m5", "periodic"]))
                .order_by(TokenSnapshot.captured_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        points: list[GrowthPoint] = []
        for r in reversed(rows):
            q = r.data_quality or {}
            points.append(
                GrowthPoint(
                    at=r.captured_at,
                    price_usd=r.price_usd,
                    market_cap_usd=r.market_cap_usd,
                    liquidity_usd=r.liquidity_usd,
                    holder_count=r.holder_count if q.get("holder_count_source") not in {"stream_estimate", None} else None,
                    volume_h1_usd=q.get("volume_h1_usd"),
                    volume_h24_usd=q.get("volume_h24_usd"),
                    buys_h1=q.get("buys_h1"),
                    sells_h1=q.get("sells_h1"),
                    buys_h24=q.get("buys_h24"),
                    sells_h24=q.get("sells_h24"),
                    top10_pct=r.top10_pct,
                    dev_holding_pct=r.dev_holding_pct,
                    migrated=(r.pool_kind not in (None, "pump")),
                )
            )
        return points

    @staticmethod
    def _point_from_pair(pair: PairData, now: datetime, boosted: bool) -> GrowthPoint:
        return GrowthPoint(
            at=now,
            price_usd=pair.price_usd,
            market_cap_usd=pair.market_cap_usd or pair.fdv_usd,
            liquidity_usd=pair.liquidity_usd,
            volume_h1_usd=pair.volume_1h,
            volume_h24_usd=pair.volume_24h,
            buys_h1=pair.buys_1h,
            sells_h1=pair.sells_1h,
            buys_h24=(pair.raw.get("txns") or {}).get("h24", {}).get("buys"),
            sells_h24=(pair.raw.get("txns") or {}).get("h24", {}).get("sells"),
            migrated=pair.pool_kind != "pump",
            boosted=boosted or bool((pair.raw.get("boosts") or {}).get("active")),
        )

    async def _check_token(self, session: AsyncSession, token: Token, pairs: list[PairData], sol_usd: float | None, now: datetime, allow_retrack: bool) -> bool:
        g = get_settings().growth
        launch = token.launch_time or token.first_seen_at
        age_h = max(0.0, (now - launch).total_seconds() / 3600.0)
        token.last_growth_check_at = now
        token.growth_checks = (token.growth_checks or 0) + 1
        if age_h > g.max_age_days * 24:
            token.phase = PHASE_ARCHIVED
            token.next_growth_check_at = None
            return False
        primary = DexScreenerClient.primary_pair(pairs)
        if primary is None:
            # not indexed (still on the curve without a pool, or dead); try again later unless it has been long
            if age_h >= g.dead_after_hours_without_pair and (token.last_liquidity_usd or 0) < g.dead_liquidity_usd:
                token.phase = PHASE_DEAD
                token.status = "dead" if token.status not in {"rejected"} else token.status
                token.next_growth_check_at = None
            else:
                token.next_growth_check_at = now + timedelta(minutes=max(15, g.fast_interval_minutes))
            return False
        history = await self._history(session, token)
        boosted = (token.metadata_json or {}).get("dexscreener_boosted", False)
        current = self._point_from_pair(primary, now, boosted)
        holders_row: dict[str, Any] | None = None
        # DexScreener has no holder data: refresh holders from Helius for liquid survivors on a slow, budgeted cadence
        last_holder_at = next((p.at for p in reversed(history) if p.holder_count is not None), None)
        if (primary.liquidity_usd or 0.0) >= g.holder_refresh_min_liquidity_usd and (last_holder_at is None or (now - last_holder_at).total_seconds() >= g.holder_refresh_hours * 3600):
            try:
                dist = await self.worker.helius.holder_distribution(token.mint, infra_owners={primary.pair_address, token.bonding_curve_key or ""} - {""}, creator=token.creator_wallet, max_das_pages=2)
                current.holder_count = dist.holder_count
                current.top10_pct = dist.concentration(10)
                current.dev_holding_pct = dist.wallet_pct(token.creator_wallet)
                holders_row = {"holder_count": dist.holder_count, "top10_pct": current.top10_pct, "largest_holder_pct": dist.largest_pct, "dev_holding_pct": current.dev_holding_pct, "holder_count_source": "helius" if dist.holder_count_complete else "helius_partial"}
            except (DataUnavailable, BudgetExceededError, ProviderError) as exc:
                log.debug("growth.holders_unavailable", mint=token.mint, error=str(exc)[:120])
        metrics = compute_growth([*history, current], launch, token.ath_price_usd, now)
        token.ath_price_usd = max(token.ath_price_usd or 0.0, primary.price_usd or 0.0) or token.ath_price_usd
        token.ath_market_cap_usd = max(token.ath_market_cap_usd or 0.0, current.market_cap_usd or 0.0) or token.ath_market_cap_usd
        token.peak_liquidity_usd = max(token.peak_liquidity_usd or 0.0, current.liquidity_usd or 0.0) or token.peak_liquidity_usd
        token.last_liquidity_usd = current.liquidity_usd
        token.last_market_cap_usd = current.market_cap_usd
        token.growth_score = metrics.score
        token.symbol = token.symbol or ((primary.raw.get("baseToken") or {}).get("symbol"))
        token.name = token.name or ((primary.raw.get("baseToken") or {}).get("name"))
        if primary.pool_kind != "pump" and token.migrated_at is None:
            token.migrated_at = primary.pair_created_at or now
            token.pool_kind = primary.pool_kind
        # ---- snapshot (kind=growth) with features for the 24h models -------------------------------------
        feats = growth_features(metrics, current, age_h)
        row = {
            "mint": token.mint,
            "captured_at": now,
            "snapshot_kind": "growth",
            "age_seconds": int(age_h * 3600),
            "pool_kind": primary.pool_kind,
            "price_sol": (primary.price_usd / sol_usd) if (primary.price_usd and sol_usd) else primary.price_native,
            "price_usd": primary.price_usd,
            "sol_usd": sol_usd,
            "market_cap_usd": current.market_cap_usd,
            "fdv_usd": primary.fdv_usd,
            "liquidity_usd": primary.liquidity_usd,
            "liquidity_sol": primary.liquidity_quote if primary.quote_mint == WSOL else ((primary.liquidity_usd / sol_usd) if (primary.liquidity_usd and sol_usd) else None),
            "bonding_progress": 1.0 if primary.pool_kind != "pump" else None,
            "buys_5m": primary.buys_5m,
            "sells_5m": primary.sells_5m,
            "momentum_5m_pct": primary.price_change_5m,
            "holder_count": holders_row["holder_count"] if holders_row else None,
            "top10_pct": holders_row["top10_pct"] if holders_row else None,
            "largest_holder_pct": holders_row["largest_holder_pct"] if holders_row else None,
            "dev_holding_pct": holders_row["dev_holding_pct"] if holders_row else None,
            "data_sources": {"dexscreener": {"pair": primary.pair_address, "dex": primary.dex_id}, "growth_check": token.growth_checks, "holders": "helius" if holders_row else None},
            "data_quality": {"source": "growth_check", "holder_count_source": holders_row["holder_count_source"] if holders_row else None, "volume_h1_usd": primary.volume_1h, "volume_h24_usd": primary.volume_24h, "buys_h1": primary.buys_1h, "sells_h1": primary.sells_1h, "buys_h24": current.buys_h24, "sells_h24": current.sells_h24, "price_change_1h_pct": primary.price_change_1h, "growth": metrics.to_dict()},
            "features": feats,
        }
        await persist_snapshot(row)
        # ---- lifecycle decision --------------------------------------------------------------------------------
        if metrics.hard_fail or not metrics.alive:
            token.phase = PHASE_DEAD if not metrics.alive else token.phase
            if metrics.hard_fail:
                token.status = "rejected"
                token.status_reason = "; ".join(metrics.reasons)[:500]
                token.phase = PHASE_DEAD
            token.next_growth_check_at = None if token.phase == PHASE_DEAD else now + timedelta(hours=1)
            await publish_event(keys.EVENTS_CHANNEL, {"type": "token.growth", "mint": token.mint, "symbol": token.symbol, "phase": token.phase, "score": metrics.score, "reasons": metrics.reasons[:3], "at": now.isoformat()})
            return False
        token.phase = phase_for_age(age_h * 3600)
        liquid = (current.liquidity_usd or 0.0) >= g.fast_min_liquidity_usd and (current.volume_h1_usd or 0.0) > 0
        if liquid:
            token.next_growth_check_at = now + timedelta(minutes=g.fast_interval_minutes)
        else:
            cp = next_checkpoint(age_h, g.checkpoints)
            token.next_growth_check_at = (launch + timedelta(hours=cp)) if cp is not None else None
            if token.next_growth_check_at is not None and token.next_growth_check_at <= now:
                token.next_growth_check_at = now + timedelta(hours=1)
        await publish_event(keys.EVENTS_CHANNEL, {"type": "token.growth", "mint": token.mint, "symbol": token.symbol, "phase": token.phase, "score": metrics.score, "positives": metrics.positives[:3], "reasons": metrics.reasons[:2], "liquidity_usd": current.liquidity_usd, "market_cap_usd": current.market_cap_usd, "at": now.isoformat()})
        # ---- re-track strong survivors in real time ------------------------------------------------------------
        tracker = self.worker.tracker
        if not allow_retrack or tracker is None or token.mint in tracker.states or token.status == "rejected":
            return False
        cooldown_ok = token.last_retrack_at is None or (now - token.last_retrack_at).total_seconds() >= g.retrack_cooldown_minutes * 60
        if metrics.score >= g.retrack_min_score and (current.liquidity_usd or 0.0) >= g.retrack_min_liquidity_usd and cooldown_ok:
            state = await tracker.track_manually(token.mint, reason=f"growth re-track (score {metrics.score:.2f}: {', '.join(metrics.positives[:2])})")
            state.flags["manual"] = False
            state.flags["source"] = "growth"
            state.flags["launch_resolved"] = True
            state.launch_time = launch
            state.symbol, state.name, state.creator = token.symbol, token.name, token.creator_wallet
            state.phase = token.phase
            state.discovery_source = token.discovery_source
            state.growth = metrics.to_dict()
            state.growth_score = metrics.score
            state.ath_price_usd, state.ath_market_cap_usd, state.peak_liquidity_usd = token.ath_price_usd, token.ath_market_cap_usd, token.peak_liquidity_usd
            state.pair = primary
            state.pair_fetched_at = now
            state.pool_kind = primary.pool_kind
            state.migrated_at = token.migrated_at
            token.retracks = (token.retracks or 0) + 1
            token.last_retrack_at = now
            token.next_growth_check_at = now + timedelta(minutes=get_settings().scanner.track_duration_minutes + 5)
            log.info("growth.retracked", mint=token.mint, symbol=token.symbol, score=metrics.score, liquidity=current.liquidity_usd)
            return True
        return False

    # ------------------------------------------------------------------ discovery of tokens we never saw launch
    async def discover(self) -> int:
        self.last_discovery_at = datetime.now(UTC)
        try:
            found = await self.dex.discovered_tokens()
        except Exception as exc:  # noqa: BLE001
            log.warning("growth.discovery_failed", error=str(exc)[:160])
            return 0
        if not found:
            return 0
        now = datetime.now(UTC)
        added = 0
        async with session_scope() as session:
            mints = [f["mint"] for f in found]
            known = {m for (m,) in (await session.execute(select(Token.mint).where(Token.mint.in_(mints)))).all()}
            new = [f for f in found if f["mint"] not in known]
            if new:
                pairs_by_mint = await self.dex.tokens([f["mint"] for f in new])
                for f in new:
                    pairs = pairs_by_mint.get(f["mint"], [])
                    primary = DexScreenerClient.primary_pair(pairs)
                    if primary is None:
                        continue
                    created = min((p.pair_created_at for p in pairs if p.pair_created_at), default=None)
                    age_h = ((now - created).total_seconds() / 3600.0) if created else None
                    if age_h is not None and age_h > get_settings().growth.max_age_days * 24:
                        continue
                    base = primary.raw.get("baseToken") or {}
                    session.add(
                        Token(
                            mint=f["mint"],
                            name=base.get("name"),
                            symbol=base.get("symbol"),
                            source="dexscreener",
                            discovery_source="dexscreener_" + ("boost" if any(k.startswith("boost") for k in f["kinds"]) else "profile"),
                            launch_time=created or now,
                            pool_kind=primary.pool_kind,
                            status="screening",
                            status_reason="discovered via DexScreener " + "/".join(f["kinds"]),
                            first_seen_at=now,
                            phase=phase_for_age((age_h or 0.0) * 3600),
                            next_growth_check_at=now,
                            migrated_at=primary.pair_created_at if primary.pool_kind != "pump" else None,
                            metadata_json={"dexscreener_boosted": True, "kinds": f["kinds"], "description": f.get("description"), "links": f.get("links"), "url": f.get("url")},
                        )
                    )
                    added += 1
            # existing tokens that are boosted: make sure they are on the schedule and flagged
            for f in found:
                if f["mint"] in known:
                    await session.execute(
                        update(Token)
                        .where(Token.mint == f["mint"], Token.phase.not_in([PHASE_DEAD, PHASE_ARCHIVED]), or_(Token.next_growth_check_at.is_(None), Token.next_growth_check_at > now + timedelta(minutes=10)))
                        .values(next_growth_check_at=now)
                    )
        self.discovered += added
        if added:
            log.info("growth.discovered", added=added, seen=len(found))
        return added
