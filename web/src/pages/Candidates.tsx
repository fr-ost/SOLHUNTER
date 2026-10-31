import { useMemo, useState } from "react";
import { CandidateRow } from "../components/CandidateRow";
import { Banner, Card, EmptyState, Spinner, Tabs } from "../components/ui";
import { errorMessage, useAsync } from "../hooks/useAsync";
import { useDebouncedLiveVersion } from "../hooks/useLive";
import { useApi } from "../hooks/useSettings";
import type { CandidateItem } from "../types/api";

type Status = "candidate" | "watchlist" | "screening" | "rejected" | "all";
type Sort = "score" | "newest" | "liquidity" | "p10" | "rug";

const sorters: Record<Sort, (a: CandidateItem, b: CandidateItem) => number> = {
  score: (a, b) => b.overall_score - a.overall_score,
  newest: (a, b) => Date.parse(b.scored_at) - Date.parse(a.scored_at),
  liquidity: (a, b) => (b.latest_snapshot?.liquidity_usd ?? -1) - (a.latest_snapshot?.liquidity_usd ?? -1),
  p10: (a, b) => (b.breakout_p10 ?? -1) - (a.breakout_p10 ?? -1),
  rug: (a, b) => (a.rug_risk ?? 2) - (b.rug_risk ?? 2),
};

export function CandidatesPage(): JSX.Element {
  const api = useApi();
  const version = useDebouncedLiveVersion(4000);
  const [status, setStatus] = useState<Status>("candidate");
  const [sort, setSort] = useState<Sort>("score");
  const [minScore, setMinScore] = useState<number>(0);
  const [query, setQuery] = useState("");
  const { data, error, loading, reload } = useAsync(() => api.candidates({ status, limit: 100, min_score: minScore || undefined }), [api, status, minScore, version], {
    refreshMs: 60_000,
    enabled: api.configured,
  });

  const items = useMemo(() => {
    const list = (data?.items ?? []).filter((i) => !query || `${i.symbol ?? ""} ${i.name ?? ""} ${i.mint}`.toLowerCase().includes(query.toLowerCase()));
    return [...list].sort(sorters[sort]);
  }, [data, sort, query]);

  if (!api.configured) return <Banner kind="warn">Configure the API in Settings first.</Banner>;
  return (
    <>
      <Card
        title="Candidate explorer"
        right={
          <button className="small" onClick={reload} disabled={loading}>
            {loading ? <Spinner /> : "refresh"}
          </button>
        }
      >
        <Tabs value={status} onChange={setStatus} options={[{ id: "candidate", label: "Candidates" }, { id: "watchlist", label: "Watchlist" }, { id: "screening", label: "Screening" }, { id: "rejected", label: "Rejected" }, { id: "all", label: "All" }]} />
        <div className="inline">
          <div>
            <label>sort</label>
            <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
              <option value="score">score</option>
              <option value="newest">newest</option>
              <option value="liquidity">liquidity</option>
              <option value="p10">P(+10%)</option>
              <option value="rug">lowest rug risk</option>
            </select>
          </div>
          <div>
            <label>min score</label>
            <input type="number" min={0} max={100} value={minScore} onChange={(e) => setMinScore(Number(e.target.value) || 0)} />
          </div>
          <div>
            <label>filter</label>
            <input placeholder="symbol / mint" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
        </div>
      </Card>
      {error ? <Banner kind="error">{errorMessage(error)}</Banner> : null}
      {data && items.length === 0 ? (
        <EmptyState>
          {status === "candidate" ? "No candidates right now. The scanner only promotes tokens that pass every safety stage - zero is a normal result." : "Nothing here yet."}
        </EmptyState>
      ) : null}
      <div className="list">{items.map((item) => <CandidateRow key={item.id} item={item} />)}</div>
      {data ? <div className="footer-note">{items.length} of {data.total} shown · scores are research rankings, never guarantees</div> : null}
    </>
  );
}
