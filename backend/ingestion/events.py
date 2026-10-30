"""Normalised ingestion events (provider-independent)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    source: str
    signature: str
    mint: str
    received_at: datetime
    block_time: datetime
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @property
    def dedupe_key(self) -> str:
        return f"{self.kind}:{self.signature}:{self.mint}"

    @property
    def kind(self) -> str:  # pragma: no cover - overridden
        return "event"


class NewTokenEvent(BaseEvent):
    kind_: Literal["create"] = "create"
    creator: str | None = None
    name: str | None = None
    symbol: str | None = None
    uri: str | None = None
    initial_buy_tokens: float | None = None
    initial_buy_sol: float | None = None
    bonding_curve_key: str | None = None
    v_sol_in_curve: float | None = None
    v_tokens_in_curve: float | None = None
    market_cap_sol: float | None = None
    pool: str = "pump"

    @property
    def kind(self) -> str:
        return "create"


class TradeEvent(BaseEvent):
    kind_: Literal["trade"] = "trade"
    trader: str
    side: Literal["buy", "sell"]
    sol_amount: float | None = None
    token_amount: float | None = None
    new_token_balance: float | None = None
    bonding_curve_key: str | None = None
    v_sol_in_curve: float | None = None
    v_tokens_in_curve: float | None = None
    market_cap_sol: float | None = None
    pool: str = "pump"

    @property
    def kind(self) -> str:
        return "trade"

    @property
    def dedupe_key(self) -> str:
        return f"trade:{self.signature}:{self.mint}:{self.trader}:{self.side}"

    @property
    def price_sol(self) -> float | None:
        if self.sol_amount and self.token_amount:
            return self.sol_amount / self.token_amount
        if self.v_sol_in_curve and self.v_tokens_in_curve:
            return self.v_sol_in_curve / self.v_tokens_in_curve
        return None


class MigrationEvent(BaseEvent):
    kind_: Literal["migrate"] = "migrate"
    pool: str = "pump-amm"
    pool_address: str | None = None

    @property
    def kind(self) -> str:
        return "migrate"


IngestionEvent = NewTokenEvent | TradeEvent | MigrationEvent
