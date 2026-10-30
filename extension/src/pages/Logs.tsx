import { useState } from "react";
import { Banner, Card, EmptyState, Spinner, Tabs } from "../components/ui";
import { errorMessage, useAsync } from "../hooks/useAsync";
import { useApi } from "../hooks/useSettings";
import { fmtTime } from "../services/format";

export function LogsPage(): JSX.Element {
  const api = useApi();
  const [source, setSource] = useState<"all" | "api" | "scanner">("scanner");
  const [level, setLevel] = useState("");
  const logs = useAsync(() => api.logs({ source, level: level || undefined, limit: 300 }), [api, source, level], { refreshMs: 15_000, enabled: api.configured });
  if (!api.configured) return <Banner kind="warn">Configure the API in Settings first.</Banner>;
  return (
    <>
      <Card title="System logs" right={logs.loading ? <Spinner /> : <button className="small" onClick={logs.reload}>refresh</button>}>
        <Tabs value={source} onChange={setSource} options={[{ id: "scanner", label: "Scanner" }, { id: "api", label: "API" }, { id: "all", label: "All" }]} />
        <select value={level} onChange={(e) => setLevel(e.target.value)}>
          <option value="">all levels</option>
          <option value="info">info</option>
          <option value="warning">warning</option>
          <option value="error">error</option>
        </select>
      </Card>
      {logs.error ? <Banner kind="error">{errorMessage(logs.error)}</Banner> : null}
      <Card tight>
        {logs.data ? (
          logs.data.items.length ? (
            [...logs.data.items].reverse().map((r, i) => {
              const { event, level: lvl, timestamp, logger, source: src, ...rest } = r;
              const extras = Object.entries(rest)
                .filter(([k]) => !["service", "request_id"].includes(k))
                .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
                .join(" ");
              return (
                <div key={i} className="log">
                  <span className="faint">{fmtTime(typeof timestamp === "string" ? timestamp : null)}</span> <span className={`lvl ${String(lvl ?? "")}`}>{String(lvl ?? "")}</span> <span className="faint">[{String(src ?? "")}{logger ? `:${String(logger).split(".").slice(-1)[0]}` : ""}]</span> {String(event ?? "")} <span className="muted">{extras}</span>
                </div>
              );
            })
          ) : (
            <EmptyState>No log records available.</EmptyState>
          )
        ) : (
          <Spinner />
        )}
      </Card>
    </>
  );
}
