"""Wallet intelligence endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import require_api_key
from backend.api.serializers import orm_to_dict, rows_to_dicts
from backend.database.engine import get_db_session
from backend.models import (
    SmartMoneyEvent,
    Token,
    TokenTransaction,
    WalletProfile,
    WalletRelationship,
    WalletTokenPosition,
    WhaleEvent,
)

router = APIRouter(prefix="/wallets", tags=["wallets"], dependencies=[Depends(require_api_key)])


@router.get("/top")
async def top_wallets(
    category: str | None = Query(None),
    sort: str = Query("realized_pnl_sol", pattern="^(realized_pnl_sol|win_rate|profit_factor|tokens_traded|last_active_at)$"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    stmt = select(WalletProfile).where(WalletProfile.is_infrastructure.is_(False))
    if category:
        stmt = stmt.where(WalletProfile.category == category)
    column = getattr(WalletProfile, sort)
    rows = (await session.execute(stmt.order_by(column.desc().nulls_last()).limit(limit))).scalars().all()
    return {"items": rows_to_dicts(list(rows), exclude={"stats"})}


@router.get("/{address}")
async def wallet_detail(address: str, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    profile = await session.get(WalletProfile, address)
    positions = (
        await session.execute(
            select(WalletTokenPosition, Token.symbol)
            .join(Token, Token.mint == WalletTokenPosition.mint, isouter=True)
            .where(WalletTokenPosition.wallet == address)
            .order_by(WalletTokenPosition.last_trade_at.desc().nulls_last())
            .limit(100)
        )
    ).all()
    if profile is None and not positions:
        raise HTTPException(404, "Wallet not seen by the scanner")
    relationships = (
        await session.execute(
            select(WalletRelationship)
            .where(or_(WalletRelationship.wallet_a == address, WalletRelationship.wallet_b == address))
            .order_by(WalletRelationship.confidence.desc())
            .limit(100)
        )
    ).scalars().all()
    whale_events = (
        await session.execute(select(WhaleEvent).where(WhaleEvent.wallet == address).order_by(WhaleEvent.occurred_at.desc()).limit(50))
    ).scalars().all()
    smart_events = (
        await session.execute(select(SmartMoneyEvent).where(SmartMoneyEvent.wallet == address).order_by(SmartMoneyEvent.occurred_at.desc()).limit(50))
    ).scalars().all()
    return {
        "address": address,
        "profile": orm_to_dict(profile) if profile else None,
        "positions": [{**orm_to_dict(p), "symbol": sym} for p, sym in positions],
        "relationships": rows_to_dicts(list(relationships)),
        "whale_events": rows_to_dicts(list(whale_events)),
        "smart_money_events": rows_to_dicts(list(smart_events)),
    }


@router.get("/{address}/transactions")
async def wallet_transactions(
    address: str, limit: int = Query(200, ge=1, le=1000), session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(TokenTransaction, Token.symbol)
            .join(Token, Token.mint == TokenTransaction.mint, isouter=True)
            .where(TokenTransaction.wallet == address)
            .order_by(TokenTransaction.block_time.desc())
            .limit(limit)
        )
    ).all()
    return {"items": [{**orm_to_dict(tx, exclude={"raw"}), "symbol": sym} for tx, sym in rows]}
