import { useState } from "react";
import { Banner, Card, EmptyState, Spinner } from "../components/ui";
import { errorMessage, useAsync } from "../hooks/useAsync";
import { useApi } from "../hooks/useSettings";
import { ApiError } from "../services/api";
import { fmtDateTime, shortAddr } from "../services/format";

export function JournalPage(): JSX.Element {
  const api = useApi();
  const [content, setContent] = useState("");
  const [title, setTitle] = useState("");
  const [mint, setMint] = useState("");
  const [tradeId, setTradeId] = useState("");
  const [tags, setTags] = useState("");
  const [entryType, setEntryType] = useState("note");
  const [error, setError] = useState<string | null>(null);
  const journal = useAsync(() => api.journal({ limit: 200 }), [api], { enabled: api.configured });

  const submit = async (): Promise<void> => {
    setError(null);
    try {
      await api.createJournal({
        content,
        title: title || null,
        mint: mint || null,
        trade_id: tradeId ? Number(tradeId) : null,
        entry_type: entryType,
        tags: tags ? tags.split(",").map((t) => t.trim()).filter(Boolean) : null,
      });
      setContent("");
      setTitle("");
      setTags("");
      journal.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.userMessage : String(err));
    }
  };

  const remove = async (id: number): Promise<void> => {
    try {
      await api.deleteJournal(id);
      journal.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.userMessage : String(err));
    }
  };

  if (!api.configured) return <Banner kind="warn">Configure the API in Settings first.</Banner>;
  return (
    <>
      <Card title="New journal entry">
        <div className="form">
          <div className="grid-2">
            <div>
              <label>title</label>
              <input value={title} onChange={(e) => setTitle(e.target.value)} />
            </div>
            <div>
              <label>type</label>
              <select value={entryType} onChange={(e) => setEntryType(e.target.value)}>
                {["note", "lesson", "review", "idea", "mistake"].map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label>mint (optional)</label>
              <input value={mint} onChange={(e) => setMint(e.target.value.trim())} />
            </div>
            <div>
              <label>trade id (optional)</label>
              <input value={tradeId} onChange={(e) => setTradeId(e.target.value.replace(/\D/g, ""))} />
            </div>
          </div>
          <div>
            <label>content *</label>
            <textarea rows={3} value={content} onChange={(e) => setContent(e.target.value)} />
          </div>
          <div>
            <label>tags (comma separated)</label>
            <input value={tags} onChange={(e) => setTags(e.target.value)} />
          </div>
          <button className="primary" disabled={!content.trim()} onClick={() => void submit()}>
            save entry
          </button>
          {error ? <Banner kind="error">{error}</Banner> : null}
        </div>
      </Card>
      {journal.error ? <Banner kind="error">{errorMessage(journal.error)}</Banner> : null}
      {journal.loading && !journal.data ? <Spinner /> : null}
      {journal.data?.items.length === 0 ? <EmptyState>No journal entries yet.</EmptyState> : null}
      <div className="list">
        {(journal.data?.items ?? []).map((e) => (
          <div key={e.id} className="item static">
            <div className="title">
              <span>
                <strong>{e.title ?? e.entry_type}</strong> <span className="faint small">{fmtDateTime(e.created_at)}</span>
              </span>
              <button className="small danger" onClick={() => void remove(e.id)}>
                ✕
              </button>
            </div>
            <p className="small" style={{ whiteSpace: "pre-wrap", margin: "4px 0" }}>
              {e.content}
            </p>
            <div className="meta">
              <span>{e.entry_type}</span>
              {e.mint ? <a href={`#/candidates/${e.mint}`}>{shortAddr(e.mint)}</a> : null}
              {e.trade_id ? <span>trade #{e.trade_id}</span> : null}
              {(e.tags ?? []).map((t) => (
                <span key={t} className="badge">
                  {t}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
