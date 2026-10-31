"""DexScreener public API adapter.

Documented endpoints (https://docs.dexscreener.com/api/reference):
  GET /latest/dex/pairs/{chainId}/{pairId}        300 req/min
  GET /latest/dex/search?q=                        300 req/min
  GET /token-pairs/v1/{chainId}/{tokenAddress}     300 req/min
  GET /tokens/v1/{chainId}/{addresses}             300 req/min (up to 30 comma separated)
  GET /token-profiles/latest/v1                     60 req/min
  GET /token-boosts/latest/v1 | /top/v1             60 req/min
No API key required. Reported liquidity/market cap are treated as provider estimates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.providers.base import HttpProvider, ProviderError
from backend.services import keys
from backend.services.redis_client import cache_get_json, cache_set_json

log = get_logger(__name__)

CHAIN = "solana"


@dataclass
class PairData:
    pair_address: str
    dex_id: str
    base_mint: str
    quote_mint: str | None
    price_usd: float | None
    price_native: float | None
    liquidity_usd: float | None
    liquidity_base: float | None
    liquidity_quote: float | None
    fdv_usd: float | None
    market_cap_usd: float | None
    volume_5m: float | None
    volume_1h: float | None
    volume_24h: float | None
    buys_5m: int | None
    sells_5m: int | None
    buys_1h: int | None
    sells_1h: int | None
    price_change_5m: float | None
    price_change_1h: float | None
    pair_created_at: datetime | None
    labels: list[str]
    url: str | None
    fetched_at: datetime
    raw: dict[str, Any]

    @property
    def pool_kind(self) -> str:
        dex = (self.dex_id or "").lower()
        if dex in {"pumpswap", "pumpfun-amm", "pump-amm"}:
            return "pump-amm"
        if dex == "pumpfun":
            return "pump"
        if "raydium" in dex:
            return "raydium"
        return "other"


def _f(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def parse_pair(raw: dict[str, Any]) -> PairData | None:
    try:
        base = raw.get("baseToken") or {}
        quote = raw.get("quoteToken") or {}
        liquidity = raw.get("liquidity") or {}
        volume = raw.get("volume") or {}
        txns = raw.get("txns") or {}
        change = raw.get("priceChange") or {}
        created = raw.get("pairCreatedAt")
        return PairData(
            pair_address=str(raw.get("pairAddress")),
            dex_id=str(raw.get("dexId") or "unknown"),
            base_mint=str(base.get("address")),
            quote_mint=quote.get("address"),
            price_usd=_f(raw.get("priceUsd")),
            price_native=_f(raw.get("priceNative")),
            liquidity_usd=_f(liquidity.get("usd")),
            liquidity_base=_f(liquidity.get("base")),
            liquidity_quote=_f(liquidity.get("quote")),
            fdv_usd=_f(raw.get("fdv")),
            market_cap_usd=_f(raw.get("marketCap")),
            volume_5m=_f(volume.get("m5")),
            volume_1h=_f(volume.get("h1")),
            volume_24h=_f(volume.get("h24")),
            buys_5m=_i((txns.get("m5") or {}).get("buys")),
            sells_5m=_i((txns.get("m5") or {}).get("sells")),
            buys_1h=_i((txns.get("h1") or {}).get("buys")),
            sells_1h=_i((txns.get("h1") or {}).get("sells")),
            price_change_5m=_f(change.get("m5")),
            price_change_1h=_f(change.get("h1")),
            pair_created_at=datetime.fromtimestamp(created / 1000, tz=UTC) if isinstance(created, int | float) else None,
            labels=[str(x) for x in (raw.get("labels") or [])],
            url=raw.get("url"),
            fetched_at=datetime.now(UTC),
            raw=raw,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("dexscreener.parse_failed", error=str(exc)[:120])
        return None


class DexScreenerClient(HttpProvider):
    def __init__(self) -> None:
        settings = get_settings()
        # pairs-class endpoints: 300/min -> 5/s. Keep a margin: 4/s with burst 8.
        super().__init__("dexscreener", settings.dexscreener_base_url, rate_per_second=min(settings.dexscreener_rpm_pairs, 240) / 60.0, burst=8)
        self.enabled = settings.dexscreener_enabled
        self._cache_ttl = 20
        # profiles/boosts endpoints have a separate 60/min limit
        self._slow = HttpProvider("dexscreener", settings.dexscreener_base_url, rate_per_second=min(settings.dexscreener_rpm_other, 50) / 60.0, burst=3)

    async def aclose(self) -> None:  # type: ignore[override]
        await super().aclose()
        await self._slow.aclose()

    async def discovered_tokens(self) -> list[dict[str, Any]]:
        """Solana tokens with a paid DexScreener profile or boost (teams that market themselves).

        Uses ``/token-profiles/latest/v1``, ``/token-boosts/latest/v1`` and ``/token-boosts/top/v1`` (60 req/min class).
        """
        if not self.enabled:
            return []
        out: dict[str, dict[str, Any]] = {}
        for path, kind in (("/token-profiles/latest/v1", "profile"), ("/token-boosts/latest/v1", "boost"), ("/token-boosts/top/v1", "boost_top")):
            try:
                data = await self._slow.request_json("GET", path, retries=1)
            except ProviderError as exc:
                log.debug("dexscreener.discovery_failed", path=path, error=str(exc)[:120])
                continue
            for row in data if isinstance(data, list) else []:
                if str(row.get("chainId")) != CHAIN or not row.get("tokenAddress"):
                    continue
                mint = str(row["tokenAddress"])
                entry = out.setdefault(mint, {"mint": mint, "kinds": [], "url": row.get("url"), "description": (row.get("description") or "")[:300], "links": row.get("links") or []})
                entry["kinds"].append(kind)
                if row.get("totalAmount") is not None:
                    entry["boost_amount"] = row.get("totalAmount")
        return list(out.values())

    async def token_pairs(self, mint: str, use_cache: bool = True) -> list[PairData]:
        """All pools for a token (``/token-pairs/v1/solana/{mint}``)."""
        if not self.enabled:
            return []
        ck = keys.cache_key("dex:token_pairs", mint)
        if use_cache:
            cached = await cache_get_json(ck)
            if cached is not None:
                return [p for p in (parse_pair(r) for r in cached) if p]
        data = await self.request_json("GET", f"/token-pairs/v1/{CHAIN}/{mint}")
        rows = data if isinstance(data, list) else (data or {}).get("pairs") or []
        await cache_set_json(ck, rows, self._cache_ttl)
        return [p for p in (parse_pair(r) for r in rows) if p]

    async def tokens(self, mints: list[str]) -> dict[str, list[PairData]]:
        """Pairs for up to 30 tokens in one call (``/tokens/v1/solana/{a,b,c}``)."""
        if not self.enabled or not mints:
            return {}
        out: dict[str, list[PairData]] = {m: [] for m in mints}
        for i in range(0, len(mints), 30):
            chunk = mints[i : i + 30]
            data = await self.request_json("GET", f"/tokens/v1/{CHAIN}/{','.join(chunk)}")
            rows = data if isinstance(data, list) else (data or {}).get("pairs") or []
            for raw in rows:
                pair = parse_pair(raw)
                if pair and pair.base_mint in out:
                    out[pair.base_mint].append(pair)
        return out

    async def pair(self, pair_address: str) -> PairData | None:
        if not self.enabled:
            return None
        data = await self.request_json("GET", f"/latest/dex/pairs/{CHAIN}/{pair_address}")
        rows = (data or {}).get("pairs") or []
        return parse_pair(rows[0]) if rows else None

    async def search(self, query: str) -> list[PairData]:
        if not self.enabled:
            return []
        data = await self.request_json("GET", "/latest/dex/search", params={"q": query})
        rows = (data or {}).get("pairs") or []
        return [p for p in (parse_pair(r) for r in rows) if p and p.raw.get("chainId") == CHAIN]

    @staticmethod
    def primary_pair(pairs: list[PairData]) -> PairData | None:
        """Pick the deepest pool as the token's primary venue."""
        if not pairs:
            return None
        return max(pairs, key=lambda p: (p.liquidity_usd or 0.0, p.volume_1h or 0.0))


async def fetch_sol_usd(client: DexScreenerClient) -> float | None:
    """SOL/USD from DexScreener (wrapped SOL pools quoted in USDC/USDT)."""
    try:
        pairs = await client.token_pairs("So11111111111111111111111111111111111111112", use_cache=False)
    except ProviderError:
        return None
    stable_quotes = {
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    }
    candidates = [p for p in pairs if p.quote_mint in stable_quotes and p.price_usd]
    if not candidates:
        candidates = [p for p in pairs if p.price_usd]
    if not candidates:
        return None
    best = max(candidates, key=lambda p: p.liquidity_usd or 0.0)
    return best.price_usd
