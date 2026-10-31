"""Helius adapter: JSON-RPC, DAS (getTokenAccounts) and Enhanced Transactions API.

Endpoints:
  RPC      POST {HELIUS_RPC_URL}/?api-key=KEY            (JSON-RPC 2.0; DAS methods share this endpoint)
  Enhanced POST {HELIUS_API_BASE_URL}/v0/transactions?api-key=KEY         body {"transactions": [sig...]}
           GET  {HELIUS_API_BASE_URL}/v0/addresses/{address}/transactions?api-key=KEY&limit=&before=&type=
Free tier (verify on helius.dev/docs/billing): 1M credits/month, 10 RPC req/s, 2 DAS/Enhanced req/s.
Credit costs below are configurable estimates used for budgeting, not billing truth.

Without an API key, standard RPC methods fall back to the public Solana RPC (very low rate),
and DAS / Enhanced data is reported as unavailable (never faked).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.providers.base import (
    AsyncRateLimiter,
    BudgetExceededError,
    DataUnavailable,
    HttpProvider,
    ProviderError,
)
from backend.services import keys
from backend.services.redis_client import cache_get_json, cache_set_json, get_redis

log = get_logger(__name__)

# Approximate credit costs (Helius publishes the exact table; keep these conservative).
CREDITS_RPC = 1.0
CREDITS_DAS = 10.0
CREDITS_ENHANCED = 100.0

BURN_ADDRESSES = {"1nc1nerator11111111111111111111111111111111", "11111111111111111111111111111111"}
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
RAYDIUM_AMM_PROGRAM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
KNOWN_INFRA_OWNERS = {PUMP_FUN_PROGRAM, PUMP_AMM_PROGRAM, RAYDIUM_AMM_PROGRAM} | BURN_ADDRESSES


@dataclass
class TokenHolder:
    token_account: str
    owner: str | None
    amount: float
    is_infrastructure: bool = False
    label: str | None = None


@dataclass
class HolderDistribution:
    mint: str
    total_supply: float | None
    holder_count: int | None
    holder_count_complete: bool
    top_holders: list[TokenHolder]
    circulating_excluding_infra: float | None
    captured_at: datetime
    source: str
    notes: list[str] = field(default_factory=list)

    def concentration(self, n: int) -> float | None:
        base = self.circulating_excluding_infra or self.total_supply
        if not base:
            return None
        real = [h for h in self.top_holders if not h.is_infrastructure]
        return min(1.0, sum(h.amount for h in real[:n]) / base)

    @property
    def largest_pct(self) -> float | None:
        return self.concentration(1)

    def wallet_pct(self, owner: str | None) -> float | None:
        if not owner:
            return None
        base = self.circulating_excluding_infra or self.total_supply
        if not base:
            return None
        amount = sum(h.amount for h in self.top_holders if h.owner == owner)
        return min(1.0, amount / base)


class CreditBudget:
    """Daily / monthly credit budget enforced through Redis counters (shared across restarts)."""

    def __init__(self, daily: int, monthly: int) -> None:
        self.daily = daily
        self.monthly = monthly

    @staticmethod
    def _keys() -> tuple[str, str]:
        now = datetime.now(UTC)
        return f"{keys.PREFIX}:helius:credits:{now:%Y-%m-%d}", f"{keys.PREFIX}:helius:credits:{now:%Y-%m}"

    async def reserve(self, credits: float) -> None:
        day_key, month_key = self._keys()
        try:
            r = get_redis()
            day = float(await r.get(day_key) or 0)
            month = float(await r.get(month_key) or 0)
        except Exception:  # noqa: BLE001 - if Redis is down, allow but log
            return
        if day + credits > self.daily:
            raise BudgetExceededError(f"Helius daily credit budget ({self.daily}) exhausted")
        if month + credits > self.monthly:
            raise BudgetExceededError(f"Helius monthly credit budget ({self.monthly}) exhausted")
        pipe = r.pipeline()
        pipe.incrbyfloat(day_key, credits)
        pipe.expire(day_key, 3 * 86400)
        pipe.incrbyfloat(month_key, credits)
        pipe.expire(month_key, 40 * 86400)
        await pipe.execute()

    async def usage(self) -> dict[str, float]:
        day_key, month_key = self._keys()
        try:
            r = get_redis()
            return {"day": float(await r.get(day_key) or 0), "month": float(await r.get(month_key) or 0)}
        except Exception:  # noqa: BLE001
            return {"day": 0.0, "month": 0.0}


class HeliusClient:
    def __init__(self) -> None:
        s = get_settings()
        self.settings = s
        self.api_key = s.helius_api_key if s.helius_enabled else None
        self.budget = CreditBudget(s.helius_daily_credit_budget, s.helius_monthly_credit_budget)
        if self.api_key:
            self.rpc = HttpProvider("helius", s.helius_rpc_url, rate_per_second=s.helius_rpc_rps, burst=int(s.helius_rpc_rps))
            self.enhanced = HttpProvider("helius", s.helius_api_base_url, rate_per_second=s.helius_enhanced_rps, burst=2)
            self.das_limiter = AsyncRateLimiter(s.helius_enhanced_rps, burst=2)
            self._rpc_url = f"/?api-key={self.api_key}"
            self.mode = "helius"
        else:
            self.rpc = HttpProvider("solana_rpc", s.solana_fallback_rpc_url, rate_per_second=2.0, burst=2)
            self.enhanced = None
            self.das_limiter = None
            self._rpc_url = "/"
            self.mode = "public_rpc"
            self.rpc.tracker.set_status("connected", note="Public RPC fallback - DAS/Enhanced data unavailable (no HELIUS_API_KEY)")

    @property
    def enhanced_available(self) -> bool:
        return self.enhanced is not None

    async def aclose(self) -> None:
        await self.rpc.aclose()
        if self.enhanced:
            await self.enhanced.aclose()

    # ------------------------------------------------------------------ core RPC
    async def _rpc_call(self, method: str, params: list[Any], credits: float = CREDITS_RPC) -> Any:
        if self.api_key:
            await self.budget.reserve(credits)
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        data = await self.rpc.request_json("POST", self._rpc_url, json_body=body, credits=credits)
        if not isinstance(data, dict):
            raise ProviderError(f"{method}: malformed RPC response")
        if "error" in data:
            err = data["error"]
            raise ProviderError(f"{method}: {err.get('message') if isinstance(err, dict) else err}")
        return data.get("result")

    async def get_token_supply(self, mint: str) -> float | None:
        result = await self._rpc_call("getTokenSupply", [mint])
        value = (result or {}).get("value") or {}
        ui = value.get("uiAmount")
        if ui is None and value.get("amount") is not None:
            ui = int(value["amount"]) / (10 ** int(value.get("decimals", 6)))
        return float(ui) if ui is not None else None

    async def get_token_largest_accounts(self, mint: str) -> list[dict[str, Any]]:
        result = await self._rpc_call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
        return list((result or {}).get("value") or [])

    async def get_multiple_accounts(self, pubkeys: list[str]) -> list[dict[str, Any] | None]:
        out: list[dict[str, Any] | None] = []
        for i in range(0, len(pubkeys), 100):
            chunk = pubkeys[i : i + 100]
            result = await self._rpc_call("getMultipleAccounts", [chunk, {"encoding": "jsonParsed", "commitment": "confirmed"}])
            out.extend(list((result or {}).get("value") or []))
        return out

    async def get_token_balance_for_owner(self, owner: str, mint: str) -> float | None:
        result = await self._rpc_call(
            "getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}]
        )
        total = 0.0
        found = False
        for acc in (result or {}).get("value") or []:
            try:
                total += float(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"] or 0.0)
                found = True
            except (KeyError, TypeError, ValueError):
                continue
        return total if found else 0.0

    async def get_signatures_for_address(self, address: str, limit: int = 100, before: str | None = None) -> list[dict[str, Any]]:
        opts: dict[str, Any] = {"limit": min(limit, 1000), "commitment": "confirmed"}
        if before:
            opts["before"] = before
        result = await self._rpc_call("getSignaturesForAddress", [address, opts])
        return list(result or [])

    async def health_probe(self) -> dict[str, Any]:
        """Cheap connectivity/key check (getSlot, ~1 credit). Raises on failure."""
        slot = await self._rpc_call("getSlot", [{"commitment": "confirmed"}])
        usage = await self.budget.usage() if self.api_key else {"day": 0.0, "month": 0.0}
        return {"mode": self.mode, "slot": slot, "credits_today": usage["day"], "credits_month": usage["month"]}

    async def get_balance_sol(self, address: str) -> float | None:
        result = await self._rpc_call("getBalance", [address, {"commitment": "confirmed"}])
        lamports = (result or {}).get("value")
        return lamports / 1e9 if lamports is not None else None

    async def get_transaction(self, signature: str) -> dict[str, Any] | None:
        result = await self._rpc_call(
            "getTransaction", [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}]
        )
        return result

    # ------------------------------------------------------------------ DAS
    async def get_token_accounts(self, mint: str, limit: int = 1000, cursor: str | None = None) -> dict[str, Any]:
        if not self.api_key or self.das_limiter is None:
            raise DataUnavailable("DAS getTokenAccounts requires HELIUS_API_KEY")
        await self.das_limiter.acquire()
        params: dict[str, Any] = {"mint": mint, "limit": limit, "options": {"showZeroBalance": False}}
        if cursor:
            params["cursor"] = cursor
        result = await self._rpc_call("getTokenAccounts", [params], credits=CREDITS_DAS)
        return result or {}

    # ------------------------------------------------------------------ Enhanced
    async def parse_transactions(self, signatures: list[str]) -> list[dict[str, Any]]:
        if not self.enhanced:
            raise DataUnavailable("Enhanced Transactions API requires HELIUS_API_KEY")
        out: list[dict[str, Any]] = []
        for i in range(0, len(signatures), 100):
            await self.budget.reserve(CREDITS_ENHANCED)
            data = await self.enhanced.request_json(
                "POST", f"/v0/transactions?api-key={self.api_key}", json_body={"transactions": signatures[i : i + 100]}, credits=CREDITS_ENHANCED
            )
            out.extend(data or [])
        return out

    async def address_transactions(self, address: str, limit: int = 100, before: str | None = None, tx_type: str | None = None) -> list[dict[str, Any]]:
        if not self.enhanced:
            raise DataUnavailable("Enhanced Transactions API requires HELIUS_API_KEY")
        await self.budget.reserve(CREDITS_ENHANCED)
        params: dict[str, Any] = {"api-key": self.api_key, "limit": min(limit, 100)}
        if before:
            params["before"] = before
        if tx_type:
            params["type"] = tx_type
        data = await self.enhanced.request_json("GET", f"/v0/addresses/{address}/transactions", params=params, credits=CREDITS_ENHANCED)
        return list(data or [])

    # ------------------------------------------------------------------ composite helpers
    async def holder_distribution(
        self, mint: str, infra_owners: set[str] | None = None, creator: str | None = None, max_das_pages: int = 3
    ) -> HolderDistribution:
        """Top-20 token accounts (RPC) mapped to owners + optional DAS holder count."""
        ck = keys.cache_key("helius:holders", mint)
        cached = await cache_get_json(ck)
        if cached:
            return _distribution_from_cache(cached)
        notes: list[str] = []
        infra_owners = set(infra_owners or set()) | KNOWN_INFRA_OWNERS
        try:
            supply, largest = await asyncio.gather(self.get_token_supply(mint), self.get_token_largest_accounts(mint))
        except BudgetExceededError as exc:
            raise DataUnavailable(str(exc)) from exc
        accounts = [str(a.get("address")) for a in largest]
        owners: dict[str, str | None] = {}
        if accounts:
            try:
                infos = await self.get_multiple_accounts(accounts)
                for acc, info in zip(accounts, infos, strict=False):
                    try:
                        owners[acc] = info["data"]["parsed"]["info"]["owner"] if info else None
                    except (KeyError, TypeError):
                        owners[acc] = None
            except (ProviderError, BudgetExceededError) as exc:
                notes.append(f"owner mapping unavailable: {str(exc)[:80]}")
        holders: list[TokenHolder] = []
        for a in largest:
            try:
                amount = float(a.get("uiAmount") or 0.0)
            except (TypeError, ValueError):
                amount = 0.0
            acc = str(a.get("address"))
            owner = owners.get(acc)
            is_infra = bool(owner and owner in infra_owners) or acc in infra_owners
            label = None
            if is_infra:
                label = "burn" if (owner in BURN_ADDRESSES or acc in BURN_ADDRESSES) else "pool/curve"
            elif creator and owner == creator:
                label = "creator"
            holders.append(TokenHolder(token_account=acc, owner=owner, amount=amount, is_infrastructure=is_infra, label=label))
        holders.sort(key=lambda h: h.amount, reverse=True)
        infra_amount = sum(h.amount for h in holders if h.is_infrastructure)
        circulating = (supply - infra_amount) if supply else None
        holder_count: int | None = None
        complete = False
        if self.api_key:
            try:
                holder_count, complete = await self._count_holders(mint, max_das_pages)
            except (DataUnavailable, BudgetExceededError, ProviderError) as exc:
                notes.append(f"holder count unavailable: {str(exc)[:80]}")
        else:
            notes.append("holder count unavailable without HELIUS_API_KEY")
        dist = HolderDistribution(
            mint=mint,
            total_supply=supply,
            holder_count=holder_count,
            holder_count_complete=complete,
            top_holders=holders,
            circulating_excluding_infra=circulating,
            captured_at=datetime.now(UTC),
            source=self.mode,
            notes=notes,
        )
        await cache_set_json(ck, _distribution_to_cache(dist), 60)
        return dist

    async def _count_holders(self, mint: str, max_pages: int) -> tuple[int, bool]:
        owners: set[str] = set()
        cursor: str | None = None
        for _ in range(max_pages):
            page = await self.get_token_accounts(mint, limit=1000, cursor=cursor)
            for acc in page.get("token_accounts") or []:
                try:
                    if float(acc.get("amount") or 0) > 0:
                        owners.add(str(acc.get("owner")))
                except (TypeError, ValueError):
                    continue
            cursor = page.get("cursor")
            if not cursor or len(page.get("token_accounts") or []) < 1000:
                return len(owners), True
        return len(owners), False


def _distribution_to_cache(d: HolderDistribution) -> dict[str, Any]:
    return {
        "mint": d.mint,
        "total_supply": d.total_supply,
        "holder_count": d.holder_count,
        "holder_count_complete": d.holder_count_complete,
        "circulating_excluding_infra": d.circulating_excluding_infra,
        "captured_at": d.captured_at.isoformat(),
        "source": d.source,
        "notes": d.notes,
        "top_holders": [h.__dict__ for h in d.top_holders],
    }


def _distribution_from_cache(c: dict[str, Any]) -> HolderDistribution:
    return HolderDistribution(
        mint=c["mint"],
        total_supply=c.get("total_supply"),
        holder_count=c.get("holder_count"),
        holder_count_complete=bool(c.get("holder_count_complete")),
        top_holders=[TokenHolder(**h) for h in c.get("top_holders", [])],
        circulating_excluding_infra=c.get("circulating_excluding_infra"),
        captured_at=datetime.fromisoformat(c["captured_at"]),
        source=c.get("source", "cache"),
        notes=list(c.get("notes", [])),
    )
