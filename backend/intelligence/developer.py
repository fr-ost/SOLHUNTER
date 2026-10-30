"""Stage 2 - developer (creator) analysis.

Combines three evidence sources, each labelled so the UI/GPT can tell them apart:
  * database history: prior launches by the same creator that this scanner has seen (always available)
  * stream behaviour: the creator's own buys/sells on this token (always available)
  * Helius enhanced history: pump.fun-related activity of the creator wallet (optional, budgeted, cached)
Unknown history is reported as unknown - never as "clean".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.models import Token, TokenRiskEvent, TrainingSample
from backend.providers.base import BudgetExceededError, DataUnavailable, ProviderError
from backend.providers.helius import HeliusClient
from backend.scanner.state import TokenState
from backend.services import keys
from backend.services.redis_client import cache_get_json, cache_set_json

log = get_logger(__name__)


@dataclass
class CreatorAnalysis:
    creator: str | None
    known: bool
    prior_launches_db: int = 0
    prior_rugs_db: int = 0
    prior_migrations_db: int = 0
    prior_dead_db: int = 0
    prior_outcomes: list[dict[str, Any]] = field(default_factory=list)
    helius_pump_interactions: int | None = None
    helius_creates: int | None = None
    helius_history_complete: bool | None = None
    sol_balance: float | None = None
    dev_holding_pct: float | None = None
    dev_holding_source: str = "unknown"
    dev_sold_pct: float = 0.0
    dev_sells: int = 0
    dev_bought_sol: float = 0.0
    dev_sold_sol: float = 0.0
    initial_buy_sol: float | None = None
    risk_flags: list[str] = field(default_factory=list)
    risk_score: float = 0.5
    confidence: str = "low"
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


async def db_creator_history(session: AsyncSession, creator: str, exclude_mint: str) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(Token.mint, Token.symbol, Token.status, Token.status_reason, Token.launch_time, Token.migrated_at)
            .where(Token.creator_wallet == creator, Token.mint != exclude_mint)
            .order_by(Token.launch_time.desc())
            .limit(50)
        )
    ).all()
    mints = [r[0] for r in rows]
    rugged: set[str] = set()
    if mints:
        rug_rows = (
            await session.execute(
                select(TokenRiskEvent.mint).where(
                    TokenRiskEvent.mint.in_(mints), TokenRiskEvent.risk_type.in_(["rug_pull", "liquidity_withdrawal", "dev_dump"]), TokenRiskEvent.severity == "critical"
                )
            )
        ).all()
        rugged.update(r[0] for r in rug_rows)
        sample_rows = (await session.execute(select(TrainingSample.mint).where(TrainingSample.mint.in_(mints), TrainingSample.rugged.is_(True)))).all()
        rugged.update(r[0] for r in sample_rows)
    outcomes = []
    for mint, symbol, status, reason, launch_time, migrated_at in rows:
        outcomes.append(
            {
                "mint": mint,
                "symbol": symbol,
                "status": status,
                "reason": reason,
                "launch_time": launch_time.isoformat() if launch_time else None,
                "migrated": migrated_at is not None,
                "rugged": mint in rugged,
            }
        )
    return {
        "prior_launches": len(rows),
        "prior_rugs": len(rugged),
        "prior_migrations": sum(1 for r in rows if r[5] is not None),
        "prior_dead": sum(1 for r in rows if r[2] == "dead"),
        "outcomes": outcomes,
    }


async def helius_creator_history(helius: HeliusClient, creator: str) -> dict[str, Any] | None:
    """Count pump.fun related activity in the creator's recent enhanced transaction history (cached)."""
    ck = keys.cache_key("creator_history", creator)
    cached = await cache_get_json(ck)
    if cached is not None:
        return cached
    if not helius.enhanced_available:
        return None
    try:
        txs = await helius.address_transactions(creator, limit=100)
    except (DataUnavailable, BudgetExceededError) as exc:
        log.debug("developer.helius_unavailable", error=str(exc)[:120])
        return None
    except ProviderError as exc:
        log.debug("developer.helius_failed", error=str(exc)[:120])
        return None
    pump_interactions = 0
    creates = 0
    mints: set[str] = set()
    for tx in txs:
        source = str(tx.get("source") or "").upper()
        tx_type = str(tx.get("type") or "").upper()
        if "PUMP" in source:
            pump_interactions += 1
            for transfer in tx.get("tokenTransfers") or []:
                mint = transfer.get("mint")
                if mint:
                    mints.add(str(mint))
        if tx_type in {"CREATE", "TOKEN_MINT"} or ("PUMP" in source and tx_type == "CREATE"):
            creates += 1
    result = {
        "pump_interactions": pump_interactions,
        "creates": creates,
        "distinct_pump_mints": len(mints),
        "complete": len(txs) < 100,
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    await cache_set_json(ck, result, get_settings().scanner.creator_history_cache_seconds)
    return result


async def analyze_creator(
    state: TokenState,
    session: AsyncSession,
    helius: HeliusClient | None = None,
    deep: bool = False,
) -> CreatorAnalysis:
    settings = get_settings().filters
    creator = state.creator
    analysis = CreatorAnalysis(creator=creator, known=bool(creator))
    dev = state.dev_activity()
    analysis.sources.append("stream")
    analysis.dev_sold_pct = float(dev.get("sold_pct") or 0.0)
    analysis.dev_sells = int(dev.get("sells") or 0)
    analysis.dev_bought_sol = float(dev.get("bought_sol") or 0.0)
    analysis.dev_sold_sol = float(dev.get("sold_sol") or 0.0)
    analysis.initial_buy_sol = state.initial_buy_sol
    if not creator:
        analysis.risk_flags.append("creator_unknown")
        analysis.risk_score = 0.6
        analysis.confidence = "low"
        return analysis

    history = await db_creator_history(session, creator, state.mint)
    analysis.sources.append("db")
    analysis.prior_launches_db = history["prior_launches"]
    analysis.prior_rugs_db = history["prior_rugs"]
    analysis.prior_migrations_db = history["prior_migrations"]
    analysis.prior_dead_db = history["prior_dead"]
    analysis.prior_outcomes = history["outcomes"][:10]

    if state.holders is not None:
        analysis.dev_holding_pct = state.holders.wallet_pct(creator)
        analysis.dev_holding_source = "helius_holders"
    else:
        total = get_settings().scanner.pump_total_supply
        agg = state.wallets.get(creator)
        if agg is not None and total:
            analysis.dev_holding_pct = min(1.0, agg.balance_estimate / total)
            analysis.dev_holding_source = "stream_estimate"

    if deep and helius is not None:
        hist = await helius_creator_history(helius, creator)
        if hist is not None:
            analysis.sources.append("helius")
            analysis.helius_pump_interactions = hist["pump_interactions"]
            analysis.helius_creates = hist["creates"]
            analysis.helius_history_complete = hist["complete"]
        try:
            if helius.api_key:
                analysis.sol_balance = await helius.get_balance_sol(creator)
        except (ProviderError, BudgetExceededError):
            pass

    # ---- risk scoring (0 = clean, 1 = toxic) -------------------------------------------
    score = 0.25  # unknown-history baseline: not clean, not toxic
    flags = analysis.risk_flags
    if analysis.prior_rugs_db > settings.max_creator_prior_rugs:
        flags.append(f"creator_prior_rugs:{analysis.prior_rugs_db}")
        score += 0.6
    if analysis.prior_launches_db > settings.max_creator_prior_launches:
        flags.append(f"serial_launcher:{analysis.prior_launches_db}")
        score += 0.25
    elif analysis.prior_launches_db > 2 and analysis.prior_migrations_db == 0:
        flags.append("repeat_launcher_no_migrations")
        score += 0.15
    if analysis.helius_creates is not None and analysis.helius_creates > settings.max_creator_prior_launches:
        flags.append(f"helius_many_creates:{analysis.helius_creates}")
        score += 0.2
    if analysis.dev_sold_pct >= settings.reject_if_dev_sold_pct_over:
        flags.append(f"dev_sold_{int(analysis.dev_sold_pct * 100)}pct")
        score += 0.5
    elif analysis.dev_sold_pct >= settings.dev_sell_warning_pct:
        flags.append(f"dev_selling_{int(analysis.dev_sold_pct * 100)}pct")
        score += 0.2
    if analysis.dev_holding_pct is not None and analysis.dev_holding_pct > settings.max_dev_holding_pct:
        flags.append(f"dev_holding_{int(analysis.dev_holding_pct * 100)}pct")
        score += 0.25
    if analysis.initial_buy_sol is not None and analysis.initial_buy_sol > 5.0:
        flags.append("large_dev_initial_buy")
        score += 0.1
    if analysis.prior_migrations_db > 0 and analysis.prior_rugs_db == 0:
        score -= 0.15
    analysis.risk_score = max(0.0, min(1.0, score))
    evidence_points = (1 if analysis.prior_launches_db else 0) + (1 if analysis.helius_creates is not None else 0) + (1 if state.holders is not None else 0)
    analysis.confidence = "high" if evidence_points >= 2 else ("medium" if evidence_points == 1 else "low")
    return analysis
