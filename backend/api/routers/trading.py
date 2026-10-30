"""Paper trading, trade journal and risk endpoints (manual only - nothing is executed)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import require_api_key
from backend.api.serializers import orm_to_dict, rows_to_dicts
from backend.database.engine import get_db_session
from backend.models import PaperTrade, TradeJournalEntry
from backend.services import trading_service

router = APIRouter(tags=["trading"], dependencies=[Depends(require_api_key)])


class TradeCreate(BaseModel):
    mint: str = Field(min_length=32, max_length=64)
    symbol: str | None = None
    candidate_score_id: int | None = None
    is_paper: bool = True
    entry_price_usd: float = Field(gt=0)
    entry_at: datetime | None = None
    entry_liquidity_usd: float | None = None
    entry_market_cap_usd: float | None = None
    position_size_usd: float = Field(gt=0, le=1_000_000)
    planned_stop_pct: float | None = Field(default=None, ge=0, le=100)
    planned_targets_pct: list[float] | None = None
    fees_usd: float = Field(default=0.0, ge=0)
    slippage_usd: float = Field(default=0.0, ge=0)
    entry_reason: str | None = Field(default=None, max_length=2000)
    signal_category: str | None = Field(default=None, max_length=48)
    candidate_type: str | None = Field(default=None, max_length=48)
    model_version: str | None = Field(default=None, max_length=64)
    gpt_recommendation: str | None = Field(default=None, max_length=32)
    extra: dict[str, Any] | None = None


class TradeUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(open|closed)$")
    symbol: str | None = None
    entry_price_usd: float | None = Field(default=None, gt=0)
    position_size_usd: float | None = Field(default=None, gt=0)
    planned_stop_pct: float | None = None
    planned_targets_pct: list[float] | None = None
    exit_price_usd: float | None = Field(default=None, gt=0)
    exit_at: datetime | None = None
    fees_usd: float | None = Field(default=None, ge=0)
    slippage_usd: float | None = Field(default=None, ge=0)
    entry_reason: str | None = Field(default=None, max_length=2000)
    exit_reason: str | None = Field(default=None, max_length=2000)
    signal_correct: bool | None = None
    signal_category: str | None = None
    candidate_type: str | None = None
    extra: dict[str, Any] | None = None


class JournalCreate(BaseModel):
    trade_id: int | None = None
    mint: str | None = Field(default=None, max_length=64)
    entry_type: str = Field(default="note", max_length=24)
    title: str | None = Field(default=None, max_length=256)
    content: str = Field(min_length=1, max_length=10000)
    tags: list[str] | None = None


@router.get("/trades")
async def list_trades(
    status: str | None = Query(None, pattern="^(open|closed)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    stmt = select(PaperTrade)
    if status:
        stmt = stmt.where(PaperTrade.status == status)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await session.execute(stmt.order_by(PaperTrade.entry_at.desc()).limit(limit).offset(offset))).scalars().all()
    return {"items": rows_to_dicts(list(rows)), "total": int(total), "limit": limit, "offset": offset}


@router.post("/trades", status_code=201)
async def create_trade(body: TradeCreate, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    state = await trading_service.risk_state(session)
    trade = await trading_service.create_trade(session, body.model_dump())
    return {"trade": orm_to_dict(trade), "risk_state": state, "risk_warning": None if state["can_open_new"] else "Risk limits exceeded (max positions or daily loss limit)"}


@router.get("/trades/performance")
async def trade_performance(session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    return await trading_service.performance(session)


@router.get("/trades/{trade_id}")
async def get_trade(trade_id: int, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    trade = await session.get(PaperTrade, trade_id)
    if trade is None:
        raise HTTPException(404, "Trade not found")
    journal = (await session.execute(select(TradeJournalEntry).where(TradeJournalEntry.trade_id == trade_id).order_by(TradeJournalEntry.created_at.desc()))).scalars().all()
    return {"trade": orm_to_dict(trade), "journal": rows_to_dicts(list(journal))}


@router.patch("/trades/{trade_id}")
async def update_trade(trade_id: int, body: TradeUpdate, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    trade = await session.get(PaperTrade, trade_id)
    if trade is None:
        raise HTTPException(404, "Trade not found")
    await trading_service.update_trade(session, trade, body.model_dump(exclude_unset=True))
    return {"trade": orm_to_dict(trade)}


@router.delete("/trades/{trade_id}", status_code=204)
async def delete_trade(trade_id: int, session: AsyncSession = Depends(get_db_session)) -> None:
    trade = await session.get(PaperTrade, trade_id)
    if trade is None:
        raise HTTPException(404, "Trade not found")
    await session.delete(trade)


@router.get("/journal")
async def list_journal(
    mint: str | None = Query(None), limit: int = Query(100, ge=1, le=500), session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    stmt = select(TradeJournalEntry)
    if mint:
        stmt = stmt.where(TradeJournalEntry.mint == mint)
    rows = (await session.execute(stmt.order_by(TradeJournalEntry.created_at.desc()).limit(limit))).scalars().all()
    return {"items": rows_to_dicts(list(rows))}


@router.post("/journal", status_code=201)
async def create_journal(body: JournalCreate, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    entry = await trading_service.add_journal_entry(session, body.model_dump())
    return orm_to_dict(entry)


@router.delete("/journal/{entry_id}", status_code=204)
async def delete_journal(entry_id: int, session: AsyncSession = Depends(get_db_session)) -> None:
    entry = await session.get(TradeJournalEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "Journal entry not found")
    await session.delete(entry)


@router.get("/risk/state")
async def risk_state(session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    return await trading_service.risk_state(session)


@router.get("/risk/position-size")
async def position_size(entry_price_usd: float = Query(gt=0), stop_pct: float | None = Query(None, ge=0.1, le=90)) -> dict[str, Any]:
    return trading_service.position_size(entry_price_usd, stop_pct)
