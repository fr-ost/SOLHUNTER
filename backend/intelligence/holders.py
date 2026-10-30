"""Stage 3 - holder distribution analysis (infrastructure accounts excluded)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.config.settings import get_settings
from backend.providers.helius import HolderDistribution
from backend.scanner.state import TokenState


@dataclass
class HolderAnalysis:
    holder_count: int | None
    holder_count_source: str
    holder_count_complete: bool
    largest_holder_pct: float | None
    top10_pct: float | None
    top20_pct: float | None
    dev_holding_pct: float | None
    infra_excluded_pct: float | None
    holder_growth_5m: int | None
    top10_change_pct: float | None
    bundled_wallets: int
    top_holders: list[dict[str, Any]] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    score: float = 0.5  # 0 = terrible, 1 = healthy
    confidence: str = "low"
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _stream_distribution(state: TokenState) -> tuple[list[tuple[str, float]], float]:
    """Approximate holdings from the trade stream (ignores transfers)."""
    balances = sorted(((w, a.balance_estimate) for w, a in state.wallets.items() if a.balance_estimate > 0), key=lambda kv: kv[1], reverse=True)
    circulating = sum(b for _, b in balances)
    return balances, circulating


def detect_bundles(state: TokenState, window_seconds: float = 2.0, min_wallets: int = 3) -> int:
    """Count wallets whose first buy landed within ``window_seconds`` of other first buys in the first 30s."""
    firsts = sorted(a.first_ts for w, a in state.wallets.items() if w != state.creator and (a.first_ts - state.launch_time).total_seconds() <= 30)
    if len(firsts) < min_wallets:
        return 0
    bundled = 0
    i = 0
    while i < len(firsts):
        j = i
        while j + 1 < len(firsts) and (firsts[j + 1] - firsts[i]).total_seconds() <= window_seconds:
            j += 1
        if j - i + 1 >= min_wallets:
            bundled += j - i + 1
        i = j + 1
    return bundled


def analyze_holders(state: TokenState, distribution: HolderDistribution | None, now: datetime | None = None) -> HolderAnalysis:
    settings = get_settings().filters
    now = now or datetime.now(UTC)
    flags: list[str] = []
    if distribution is not None:
        largest = distribution.largest_pct
        top10 = distribution.concentration(10)
        top20 = distribution.concentration(20)
        dev_pct = distribution.wallet_pct(state.creator)
        infra_pct = None
        if distribution.total_supply and distribution.circulating_excluding_infra is not None:
            infra_pct = 1.0 - distribution.circulating_excluding_infra / distribution.total_supply
        holder_count = distribution.holder_count
        source = distribution.source
        complete = distribution.holder_count_complete
        top_holders = [
            {"owner": h.owner, "token_account": h.token_account, "amount": h.amount, "pct": (h.amount / distribution.circulating_excluding_infra) if distribution.circulating_excluding_infra else None, "is_infrastructure": h.is_infrastructure, "label": h.label}
            for h in distribution.top_holders[:20]
        ]
        if holder_count is None:
            holder_count = state.stream_holder_estimate()
            source = "stream_estimate"
            complete = False
        confidence = "high" if (complete and distribution.holder_count is not None) else "medium"
    else:
        balances, circulating = _stream_distribution(state)
        if not balances or circulating <= 0:
            return HolderAnalysis(None, "unavailable", False, None, None, None, None, None, None, None, 0, flags=["holder_data_unavailable"], score=0.4, confidence="low", available=False)
        largest = balances[0][1] / circulating
        top10 = sum(b for _, b in balances[:10]) / circulating
        top20 = sum(b for _, b in balances[:20]) / circulating
        dev_agg = state.wallets.get(state.creator) if state.creator else None
        dev_pct = (dev_agg.balance_estimate / circulating) if dev_agg else None
        infra_pct = None
        holder_count = len(balances)
        source = "stream_estimate"
        complete = False
        top_holders = [{"owner": w, "amount": b, "pct": b / circulating, "is_infrastructure": False, "label": "creator" if w == state.creator else None} for w, b in balances[:20]]
        confidence = "low"
        flags.append("holders_from_stream_estimate")

    # growth: track holder count history on the state
    state.holder_history.append((now, int(holder_count or 0)))
    state.holder_history = state.holder_history[-60:]
    growth_5m: int | None = None
    for ts, count in state.holder_history:
        if (now - ts).total_seconds() >= 290:
            growth_5m = int(holder_count or 0) - count
    prev_top10 = state.flags.get("prev_top10_pct")
    top10_change = (top10 - prev_top10) if (top10 is not None and prev_top10 is not None) else None
    if top10 is not None:
        state.flags["prev_top10_pct"] = top10
    bundled = detect_bundles(state)

    score = 1.0
    if largest is not None and largest > settings.max_largest_holder_pct:
        flags.append(f"largest_holder_{int(largest * 100)}pct")
        score -= min(0.5, (largest - settings.max_largest_holder_pct) * 4)
    if top10 is not None and top10 > settings.max_top10_pct:
        flags.append(f"top10_{int(top10 * 100)}pct")
        score -= min(0.4, (top10 - settings.max_top10_pct) * 2)
    if top20 is not None and top20 > settings.max_top20_pct:
        flags.append(f"top20_{int(top20 * 100)}pct")
        score -= 0.15
    if holder_count is not None and holder_count < settings.min_holders:
        flags.append(f"few_holders_{holder_count}")
        score -= 0.25
    if growth_5m is not None and growth_5m < settings.min_holder_growth_5m:
        flags.append("holder_growth_stalled")
        score -= 0.1
    if top10_change is not None and top10_change > 0.05:
        flags.append("concentration_increasing")
        score -= 0.1
    if bundled >= 3:
        flags.append(f"bundled_buys_{bundled}")
        score -= 0.2
    if dev_pct is not None and dev_pct > settings.max_dev_holding_pct:
        flags.append(f"dev_holding_{int(dev_pct * 100)}pct")
        score -= 0.2
    return HolderAnalysis(
        holder_count=holder_count,
        holder_count_source=source,
        holder_count_complete=complete,
        largest_holder_pct=largest,
        top10_pct=top10,
        top20_pct=top20,
        dev_holding_pct=dev_pct,
        infra_excluded_pct=infra_pct,
        holder_growth_5m=growth_5m,
        top10_change_pct=top10_change,
        bundled_wallets=bundled,
        top_holders=top_holders,
        flags=flags,
        score=max(0.0, min(1.0, score)),
        confidence=confidence,
    )
