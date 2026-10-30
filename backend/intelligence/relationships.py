"""Stage 4 - wallet relationship graph with confidence levels and cluster detection.

Relationship types and the confidence we assign per observation:
  timing_correlation  first buys within ~2s of each other in a token's first 30s        0.35 (+0.15 per repeat, max 0.8)
  repeated_cobuy      the same pair buys the same tokens within 60s on >= 3 tokens     0.7
  shared_funder       both wallets were funded by the same SOL source (Helius data)     0.35 (shared funding alone is weak)
  direct_transfer     token/SOL transfer between the two wallets (Helius data)           0.7
Clusters are connected components over edges with confidence >= WHALE_CLUSTER_MIN_CONFIDENCE.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import combinations
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.logging import get_logger
from backend.config.settings import get_settings
from backend.models import WalletRelationship
from backend.providers.base import BudgetExceededError, DataUnavailable, ProviderError
from backend.providers.helius import HeliusClient
from backend.scanner.state import TokenState
from backend.services import keys
from backend.services.redis_client import cache_get_json, cache_set_json

log = get_logger(__name__)

MAX_PAIRWISE_WALLETS = 25


@dataclass
class Edge:
    a: str
    b: str
    relationship_type: str
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str]:
        x, y = sorted((self.a, self.b))
        return x, y, self.relationship_type


@dataclass
class ClusterAnalysis:
    clusters: list[list[str]]
    wallet_to_cluster: dict[str, str]
    cluster_holding_pct: dict[str, float]
    largest_cluster_pct: float
    coordinated_wallets: int
    creator_cluster: list[str]
    edges: int
    flags: list[str] = field(default_factory=list)
    confidence: str = "low"

    def to_dict(self) -> dict[str, Any]:
        return {
            "clusters": [c for c in self.clusters if len(c) >= 2][:20],
            "cluster_holding_pct": self.cluster_holding_pct,
            "largest_cluster_pct": self.largest_cluster_pct,
            "coordinated_wallets": self.coordinated_wallets,
            "creator_cluster": self.creator_cluster,
            "edges": self.edges,
            "flags": self.flags,
            "confidence": self.confidence,
        }


def timing_edges(state: TokenState, window_seconds: float = 2.0) -> list[Edge]:
    """Pairs of wallets whose first buys landed within ``window_seconds`` during the first 30s."""
    # The creator's launch buy is in the create transaction itself, so it is excluded here: otherwise
    # every fast sniper would look creator-linked. Creator links need stronger evidence (funding/transfers).
    firsts = sorted(
        ((a.first_ts, w) for w, a in state.wallets.items() if w != state.creator and (a.first_ts - state.launch_time).total_seconds() <= 30),
        key=lambda x: x[0],
    )
    edges: list[Edge] = []
    i = 0
    while i < len(firsts):
        j = i
        while j + 1 < len(firsts) and (firsts[j + 1][0] - firsts[i][0]).total_seconds() <= window_seconds:
            j += 1
        group = [w for _, w in firsts[i : j + 1]]
        if len(group) >= 3:
            for x, y in combinations(group[:MAX_PAIRWISE_WALLETS], 2):
                edges.append(Edge(x, y, "timing_correlation", 0.35, {"mint": state.mint, "window_s": window_seconds, "group_size": len(group)}))
        i = j + 1
    return edges


async def persist_edges(session: AsyncSession, edges: list[Edge]) -> int:
    if not edges:
        return 0
    now = datetime.now(UTC)
    merged: dict[tuple[str, str, str], Edge] = {}
    for e in edges:
        merged[e.key] = e
    for e in merged.values():
        a, b, kind = e.key
        stmt = pg_insert(WalletRelationship).values(
            wallet_a=a, wallet_b=b, relationship_type=kind, confidence=e.confidence, evidence=e.evidence, observations=1, first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now
        )
        if kind == "timing_correlation":
            # repeated co-occurrence raises confidence: +0.15 per observation up to 0.8
            new_conf = WalletRelationship.confidence + 0.15
            stmt = stmt.on_conflict_do_update(
                index_elements=[WalletRelationship.wallet_a, WalletRelationship.wallet_b, WalletRelationship.relationship_type],
                set_={"confidence": _least(new_conf, 0.8), "observations": WalletRelationship.observations + 1, "last_seen_at": now, "updated_at": now, "evidence": stmt.excluded.evidence},
            )
        else:
            stmt = stmt.on_conflict_do_update(
                index_elements=[WalletRelationship.wallet_a, WalletRelationship.wallet_b, WalletRelationship.relationship_type],
                set_={"confidence": _greatest(WalletRelationship.confidence, e.confidence), "observations": WalletRelationship.observations + 1, "last_seen_at": now, "updated_at": now},
            )
        await session.execute(stmt)
    return len(merged)


def _least(a: Any, b: float) -> Any:
    from sqlalchemy import func

    return func.least(a, b)


def _greatest(a: Any, b: float) -> Any:
    from sqlalchemy import func

    return func.greatest(a, b)


async def load_edges(session: AsyncSession, wallets: list[str], min_confidence: float = 0.0) -> list[Edge]:
    if not wallets:
        return []
    rows = (
        await session.execute(
            select(WalletRelationship).where(
                or_(WalletRelationship.wallet_a.in_(wallets), WalletRelationship.wallet_b.in_(wallets)), WalletRelationship.confidence >= min_confidence
            )
        )
    ).scalars().all()
    return [Edge(r.wallet_a, r.wallet_b, r.relationship_type, r.confidence, r.evidence or {}) for r in rows]


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def groups(self) -> list[list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for x in list(self.parent):
            out[self.find(x)].append(x)
        return sorted(out.values(), key=len, reverse=True)


def analyze_clusters(state: TokenState, edges: list[Edge], balances: dict[str, float] | None = None, circulating: float | None = None) -> ClusterAnalysis:
    """Clusters among this token's wallets; combines timing + shared funder (+ others) with confidence weighting."""
    s = get_settings()
    min_conf = s.whale.cluster_min_confidence
    wallets = set(state.wallets)
    # combine evidence per pair: multiple weak relationships reinforce each other
    pair_conf: dict[tuple[str, str], float] = defaultdict(float)
    pair_types: dict[tuple[str, str], set[str]] = defaultdict(set)
    for e in edges:
        if e.a not in wallets and e.b not in wallets:
            continue
        key = tuple(sorted((e.a, e.b)))
        pair_types[key].add(e.relationship_type)
        pair_conf[key] = 1.0 - (1.0 - pair_conf[key]) * (1.0 - e.confidence)
    uf = UnionFind()
    strong = 0
    for key, conf in pair_conf.items():
        if conf >= min_conf:
            uf.union(*key)
            strong += 1
    clusters = [c for c in uf.groups() if len(c) >= 2]
    wallet_to_cluster: dict[str, str] = {}
    for c in clusters:
        cid = "cl_" + min(c)[:8]
        for w in c:
            wallet_to_cluster[w] = cid
    if balances is None:
        balances = {w: a.balance_estimate for w, a in state.wallets.items()}
        circulating = sum(b for b in balances.values() if b > 0) or None
    holding: dict[str, float] = {}
    for c in clusters:
        cid = wallet_to_cluster[c[0]]
        if circulating:
            holding[cid] = min(1.0, sum(balances.get(w, 0.0) for w in c if w in wallets) / circulating)
    largest = max(holding.values(), default=0.0)
    coordinated = sum(len([w for w in c if w in wallets]) for c in clusters)
    creator_cluster = [w for c in clusters if state.creator in c for w in c if w != state.creator] if state.creator else []
    flags: list[str] = []
    if largest > s.filters.max_cluster_holding_pct:
        flags.append(f"cluster_holds_{int(largest * 100)}pct")
    if coordinated > s.filters.max_coordinated_wallets:
        flags.append(f"coordinated_wallets_{coordinated}")
    if creator_cluster:
        flags.append(f"creator_linked_wallets_{len(creator_cluster)}")
    types = {t for ts in pair_types.values() for t in ts}
    confidence = "high" if ({"shared_funder", "direct_transfer"} & types and strong) else ("medium" if strong else "low")
    return ClusterAnalysis(clusters, wallet_to_cluster, holding, largest, coordinated, creator_cluster, strong, flags, confidence)


async def discover_funders(helius: HeliusClient, wallets: list[str], max_wallets: int = 10) -> dict[str, str | None]:
    """Largest SOL funder of each wallet from enhanced history (cached 24h, budgeted)."""
    out: dict[str, str | None] = {}
    for wallet in wallets[:max_wallets]:
        ck = keys.cache_key("funder", wallet)
        cached = await cache_get_json(ck)
        if cached is not None:
            out[wallet] = cached.get("funder")
            continue
        funder: str | None = None
        if helius.enhanced_available:
            try:
                txs = await helius.address_transactions(wallet, limit=50)
                inflows: dict[str, float] = defaultdict(float)
                for tx in txs:
                    for nt in tx.get("nativeTransfers") or []:
                        if nt.get("toUserAccount") == wallet and nt.get("fromUserAccount"):
                            inflows[str(nt["fromUserAccount"])] += float(nt.get("amount") or 0) / 1e9
                if inflows:
                    funder = max(inflows.items(), key=lambda kv: kv[1])[0]
            except (DataUnavailable, BudgetExceededError, ProviderError) as exc:
                log.debug("relationships.funder_unavailable", wallet=wallet, error=str(exc)[:100])
                continue
        await cache_set_json(ck, {"funder": funder}, 86400)
        out[wallet] = funder
    return out


def shared_funder_edges(funders: dict[str, str | None], mint: str) -> list[Edge]:
    by_funder: dict[str, list[str]] = defaultdict(list)
    for w, f in funders.items():
        if f:
            by_funder[f].append(w)
    edges: list[Edge] = []
    for funder, ws in by_funder.items():
        if len(ws) < 2:
            continue
        for a, b in combinations(ws[:MAX_PAIRWISE_WALLETS], 2):
            edges.append(Edge(a, b, "shared_funder", 0.35, {"funder": funder, "mint": mint}))
    return edges
