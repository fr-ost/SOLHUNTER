"""Realistic PumpPortal-shaped payloads (field names observed in the public stream)."""

from __future__ import annotations

from typing import Any

MINT = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
CREATOR = "DevWa11et111111111111111111111111111111111"
BUYER_A = "BuyerA1111111111111111111111111111111111111"
BUYER_B = "BuyerB1111111111111111111111111111111111111"
CURVE = "CurveKey111111111111111111111111111111111111"


def create_event(mint: str = MINT, signature: str = "sig-create-1", creator: str = CREATOR) -> dict[str, Any]:
    return {
        "signature": signature,
        "mint": mint,
        "traderPublicKey": creator,
        "txType": "create",
        "initialBuy": 59_000_000.0,
        "solAmount": 1.8,
        "bondingCurveKey": CURVE,
        "vTokensInBondingCurve": 1_014_000_000.0,
        "vSolInBondingCurve": 31.8,
        "marketCapSol": 31.36,
        "name": "Test Gem",
        "symbol": "GEM",
        "uri": "https://ipfs.io/ipfs/example",
        "pool": "pump",
    }


def trade_event(
    signature: str,
    trader: str,
    side: str = "buy",
    sol: float = 0.5,
    tokens: float = 15_000_000.0,
    v_sol: float = 32.3,
    v_tokens: float = 999_000_000.0,
    mint: str = MINT,
    new_balance: float | None = None,
) -> dict[str, Any]:
    return {
        "signature": signature,
        "mint": mint,
        "traderPublicKey": trader,
        "txType": side,
        "tokenAmount": tokens,
        "solAmount": sol,
        "newTokenBalance": new_balance if new_balance is not None else tokens,
        "bondingCurveKey": CURVE,
        "vTokensInBondingCurve": v_tokens,
        "vSolInBondingCurve": v_sol,
        "marketCapSol": v_sol / v_tokens * 1_000_000_000,
        "pool": "pump",
    }


def migration_event(mint: str = MINT, signature: str = "sig-migrate-1") -> dict[str, Any]:
    return {"signature": signature, "mint": mint, "txType": "migrate", "pool": "pump-amm"}


def dexscreener_pair(mint: str = MINT, liquidity_usd: float = 25_000.0, price_usd: float = 0.00004) -> dict[str, Any]:
    return {
        "chainId": "solana",
        "dexId": "pumpswap",
        "url": f"https://dexscreener.com/solana/pool{mint[:6]}",
        "pairAddress": f"Pool{mint[:40]}",
        "baseToken": {"address": mint, "name": "Test Gem", "symbol": "GEM"},
        "quoteToken": {"address": "So11111111111111111111111111111111111111112", "name": "Wrapped SOL", "symbol": "SOL"},
        "priceNative": "0.00000025",
        "priceUsd": str(price_usd),
        "txns": {"m5": {"buys": 40, "sells": 12}, "h1": {"buys": 300, "sells": 120}, "h6": {"buys": 300, "sells": 120}, "h24": {"buys": 300, "sells": 120}},
        "volume": {"m5": 4000.0, "h1": 30000.0, "h6": 30000.0, "h24": 30000.0},
        "priceChange": {"m5": 12.5, "h1": 80.0, "h6": 80.0, "h24": 80.0},
        "liquidity": {"usd": liquidity_usd, "base": 300_000_000.0, "quote": liquidity_usd / 2 / 160.0},
        "fdv": 40_000.0,
        "marketCap": 40_000.0,
        "pairCreatedAt": 1730000000000,
        "labels": [],
    }


def helius_largest_accounts(accounts: list[tuple[str, float]]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"context": {"slot": 1}, "value": [{"address": a, "amount": str(int(amt * 1e6)), "decimals": 6, "uiAmount": amt, "uiAmountString": str(amt)} for a, amt in accounts]},
    }


def helius_supply(total: float = 1_000_000_000.0) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 1}, "value": {"amount": str(int(total * 1e6)), "decimals": 6, "uiAmount": total, "uiAmountString": str(total)}}}


def helius_multiple_accounts(owners: list[str | None]) -> dict[str, Any]:
    values = []
    for owner in owners:
        if owner is None:
            values.append(None)
        else:
            values.append({"data": {"parsed": {"info": {"owner": owner, "mint": MINT}, "type": "account"}, "program": "spl-token"}, "owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"})
    return {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 1}, "value": values}}


def helius_token_accounts(owners: list[str], cursor: str | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "result": {"total": len(owners), "limit": 1000, "cursor": cursor, "token_accounts": [{"address": f"acc{i}", "mint": MINT, "owner": o, "amount": 1000, "delegated_amount": 0, "frozen": False} for i, o in enumerate(owners)]}}
