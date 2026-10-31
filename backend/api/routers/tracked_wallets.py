"""Manual wallet tracker: add/remove wallets to follow and read their trades."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import require_api_key
from backend.api.serializers import orm_to_dict
from backend.config.settings import get_settings
from backend.database.engine import get_db_session
from backend.models import CandidateScore, Token, TokenTransaction, TrackedWallet, WalletProfile, utcnow

router = APIRouter(prefix="/tracked-wallets", tags=["wallet tracker"], dependencies=[Depends(require_api_key)])

BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _validate_address(v: str) -> str:
    v = v.strip()
    if not BASE58.match(v):
        raise ValueError("not a valid Solana wallet address")
    return v


class TrackedWalletCreate(BaseModel):
    address: str
    label: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=512)
    alert_on_buy: bool = True
    alert_on_sell: bool = True
    min_alert_sol: float = Field(default=0.0, ge=0, le=10_000)
    auto_track_tokens: bool = True

    _addr = field_validator("address")(lambda cls, v: _validate_address(v))  # noqa: E731


class TrackedWalletUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=512)
    active: bool | None = None
    alert_on_buy: bool | None = None
    alert_on_sell: bool | None = None
    min_alert_sol: float | None = Field(default=None, ge=0, le=10_000)
    auto_track_tokens: bool | None = None


async def _stats(session: AsyncSession, addresses: list[str]) -> dict[str, dict[str, Any]]:
    if not addresses:
        return {}
    since = datetime.now(UTC) - timedelta(hours=24)
    rows = (
        await session.execute(
            select(
                TokenTransaction.wallet,
                func.count(),
                func.coalesce(func.sum(case((TokenTransaction.tx_type == "buy", TokenTransaction.sol_amount), else_=0.0)), 0.0),
                func.coalesce(func.sum(case((TokenTransaction.tx_type == "sell", TokenTransaction.sol_amount), else_=0.0)), 0.0),
                func.count(func.distinct(TokenTransaction.mint)),
            )
            .where(TokenTransaction.wallet.in_(addresses), TokenTransaction.block_time >= since, TokenTransaction.tx_type.in_(["buy", "sell"]))
            .group_by(TokenTransaction.wallet)
        )
    ).all()
    out = {w: {"trades_24h": int(n), "buy_sol_24h": round(float(b), 4), "sell_sol_24h": round(float(s), 4), "net_sol_24h": round(float(b) - float(s), 4), "tokens_24h": int(t)} for w, n, b, s, t in rows}
    profiles = (await session.execute(select(WalletProfile).where(WalletProfile.address.in_(addresses)))).scalars().all()
    for p in profiles:
        out.setdefault(p.address, {})["profile"] = {
            "category": p.category,
            "category_confidence": p.category_confidence,
            "realized_pnl_sol": p.realized_pnl_sol,
            "unrealized_pnl_sol": p.unrealized_pnl_sol,
            "win_rate": p.win_rate,
            "tokens_traded": p.tokens_traded,
            "closed_positions": p.closed_positions,
        }
    return out


@router.get("")
async def list_tracked(session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    rows = (await session.execute(select(TrackedWallet).order_by(TrackedWallet.created_at.desc()))).scalars().all()
    stats = await _stats(session, [r.address for r in rows])
    return {
        "items": [{**orm_to_dict(r), **stats.get(r.address, {})} for r in rows],
        "limit": get_settings().max_tracked_wallets,
    }


@router.post("", status_code=201)
async def add_tracked(body: TrackedWalletCreate, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    existing = await session.get(TrackedWallet, body.address)
    if existing is None:
        count = await session.scalar(select(func.count()).select_from(TrackedWallet).where(TrackedWallet.active.is_(True))) or 0
        if count >= get_settings().max_tracked_wallets:
            raise HTTPException(400, f"Tracking limit reached ({get_settings().max_tracked_wallets}); remove a wallet or raise MAX_TRACKED_WALLETS")
        existing = TrackedWallet(address=body.address)
        session.add(existing)
    for field, value in body.model_dump(exclude={"address"}).items():
        setattr(existing, field, value)
    existing.active = True
    existing.updated_at = utcnow()
    await session.flush()
    return {"wallet": orm_to_dict(existing), "note": "The scanner subscribes within ~10 seconds."}


@router.patch("/{address}")
async def update_tracked(address: str, body: TrackedWalletUpdate, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    row = await session.get(TrackedWallet, address)
    if row is None:
        raise HTTPException(404, "Wallet is not tracked")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.updated_at = utcnow()
    await session.flush()
    return {"wallet": orm_to_dict(row)}


@router.delete("/{address}", status_code=204)
async def remove_tracked(address: str, session: AsyncSession = Depends(get_db_session)) -> None:
    row = await session.get(TrackedWallet, address)
    if row is None:
        raise HTTPException(404, "Wallet is not tracked")
    await session.delete(row)


async def _trades(session: AsyncSession, wallets: list[str], limit: int) -> list[dict[str, Any]]:
    if not wallets:
        return []
    labels = {w: lbl for w, lbl in (await session.execute(select(TrackedWallet.address, TrackedWallet.label).where(TrackedWallet.address.in_(wallets)))).all()}
    rows = (
        await session.execute(
            select(TokenTransaction, Token.symbol, Token.name, Token.status)
            .join(Token, Token.mint == TokenTransaction.mint, isouter=True)
            .where(TokenTransaction.wallet.in_(wallets), TokenTransaction.tx_type.in_(["buy", "sell"]))
            .order_by(TokenTransaction.block_time.desc())
            .limit(limit)
        )
    ).all()
    mints = list({tx.mint for tx, *_ in rows})
    scores: dict[str, CandidateScore] = {}
    if mints:
        for sc in (await session.execute(select(CandidateScore).where(CandidateScore.mint.in_(mints), CandidateScore.is_latest.is_(True)))).scalars().all():
            scores[sc.mint] = sc
    out = []
    for tx, symbol, name, token_status in rows:
        sc = scores.get(tx.mint)
        out.append(
            {
                "signature": tx.signature,
                "wallet": tx.wallet,
                "label": labels.get(tx.wallet),
                "side": tx.tx_type,
                "sol_amount": tx.sol_amount,
                "token_amount": tx.token_amount,
                "price_sol": tx.price_sol,
                "market_cap_sol": tx.market_cap_sol,
                "block_time": tx.block_time.isoformat(),
                "mint": tx.mint,
                "symbol": symbol,
                "name": name,
                "token_status": token_status,
                "score": sc.overall_score if sc else None,
                "score_status": sc.status if sc else None,
                "rug_risk": sc.rug_risk if sc else None,
            }
        )
    return out


@router.get("/trades/recent")
async def recent_trades(limit: int = Query(100, ge=1, le=500), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    wallets = [w for (w,) in (await session.execute(select(TrackedWallet.address))).all()]
    return {"items": await _trades(session, wallets, limit)}


@router.get("/{address}/trades")
async def wallet_trades(address: str, limit: int = Query(200, ge=1, le=1000), session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    return {"items": await _trades(session, [address], limit)}
