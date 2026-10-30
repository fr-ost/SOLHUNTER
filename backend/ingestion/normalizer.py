"""Turn raw provider payloads into normalised events. Parsing is defensive by design."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.config.logging import get_logger
from backend.ingestion.events import IngestionEvent, MigrationEvent, NewTokenEvent, TradeEvent

log = get_logger(__name__)


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _s(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _block_time(raw: dict[str, Any], received_at: datetime) -> datetime:
    for key in ("timestamp", "blockTime", "block_time", "time"):
        value = raw.get(key)
        if isinstance(value, int | float) and value > 0:
            ts = float(value)
            if ts > 1e12:  # milliseconds
                ts /= 1000.0
            try:
                return datetime.fromtimestamp(ts, tz=UTC)
            except (OverflowError, OSError, ValueError):
                continue
    return received_at


def normalize_pumpportal(raw: dict[str, Any], received_at: datetime | None = None) -> IngestionEvent | None:
    """Map a PumpPortal websocket message to an event; returns None for unknown shapes."""
    received_at = received_at or datetime.now(UTC)
    tx_type = str(raw.get("txType") or "").lower()
    mint = _s(raw.get("mint"))
    signature = _s(raw.get("signature"))
    if not mint or not signature:
        return None
    block_time = _block_time(raw, received_at)
    pool = _s(raw.get("pool")) or "pump"
    common = {"source": "pumpportal", "signature": signature, "mint": mint, "received_at": received_at, "block_time": block_time, "raw": raw}

    if tx_type == "create":
        initial_buy_tokens = _f(raw.get("initialBuy"))
        initial_buy_sol = _f(raw.get("solAmount"))
        return NewTokenEvent(
            creator=_s(raw.get("traderPublicKey")) or _s(raw.get("creator")),
            name=_s(raw.get("name")),
            symbol=_s(raw.get("symbol")),
            uri=_s(raw.get("uri")),
            initial_buy_tokens=initial_buy_tokens,
            initial_buy_sol=initial_buy_sol,
            bonding_curve_key=_s(raw.get("bondingCurveKey")),
            v_sol_in_curve=_f(raw.get("vSolInBondingCurve")),
            v_tokens_in_curve=_f(raw.get("vTokensInBondingCurve")),
            market_cap_sol=_f(raw.get("marketCapSol")),
            pool=pool,
            **common,
        )
    if tx_type in {"buy", "sell"}:
        trader = _s(raw.get("traderPublicKey"))
        if not trader:
            return None
        return TradeEvent(
            trader=trader,
            side=tx_type,  # type: ignore[arg-type]
            sol_amount=_f(raw.get("solAmount")),
            token_amount=_f(raw.get("tokenAmount")),
            new_token_balance=_f(raw.get("newTokenBalance")),
            bonding_curve_key=_s(raw.get("bondingCurveKey")),
            v_sol_in_curve=_f(raw.get("vSolInBondingCurve")),
            v_tokens_in_curve=_f(raw.get("vTokensInBondingCurve")),
            market_cap_sol=_f(raw.get("marketCapSol")),
            pool=pool,
            **common,
        )
    if tx_type in {"migrate", "migration"} or (not tx_type and pool in {"pump-amm", "raydium"} and raw.get("pool")):
        return MigrationEvent(pool=pool if pool != "pump" else "pump-amm", pool_address=_s(raw.get("poolAddress") or raw.get("pool_address")), **common)
    log.debug("normalizer.unknown_tx_type", tx_type=tx_type)
    return None
