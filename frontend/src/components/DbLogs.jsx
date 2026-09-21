import { useState } from "react";
import Card from "./Card";
import Badge from "./Badge";
import { FileText, History, Loader2, RefreshCw, Search } from "lucide-react";

const TAIL_OPTIONS = [50, 200, 500];
const SINCE_OPTIONS = [15, 60, 360, 1440];

function logTone(line = "") {
  const lower = line.toLowerCase();
  if (
    lower.includes("error") ||
    lower.includes("exception") ||
    lower.includes("fail") ||
    lower.includes("fatal") ||
    lower.includes("panic") ||
    lower.includes("refused")
  )
    return "danger";
  if (lower.includes("warn") || lower.includes("timeout") || lower.includes("slow"))
    return "warning";
  return null;
}

function renderLines(lines) {
  if (!lines || lines.length === 0) return "No log lines returned.";
  return lines.map((line, i) => {
    const tone = logTone(line);
    return (
      <div
        key={i}
        style={
          tone === "danger"
            ? { color: "var(--danger)" }
            : tone === "warning"
              ? { color: "var(--warning, #b7791f)" }
              : undefined
        }
      >
        {line}
      </div>
    );
  });
}

/**
 * Shared database log viewer: live `kubectl logs` tail + Elasticsearch
 * history for a K8s database pod. `target` is "yugabyte" | "aerospike";
 * `fetchLive` / `fetchHistory` are the matching dbInvestigationApi fns.
 */
export default function DbLogs({ target, podHint, fetchLive, fetchHistory }) {
  const [tab, setTab] = useState("live");
  const [tail, setTail] = useState(200);
  const [previous, setPrevious] = useState(false);
  const [live, setLive] = useState(null);
  const [liveLoading, setLiveLoading] = useState(false);
  const [liveError, setLiveError] = useState(null);

  const [sinceMinutes, setSinceMinutes] = useState(60);
  const [pattern, setPattern] = useState("");
  const [history, setHistory] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState(null);

  const loadLive = async () => {
    setLiveLoading(true);
    setLiveError(null);
    try {
      const res = await fetchLive({ tail, previous });
      if (res.data?.success) {
        setLive(res.data);
      } else {
        setLive(null);
        setLiveError(res.data?.error || "Failed to fetch logs.");
      }
    } catch (e) {
      setLive(null);
      setLiveError(e.response?.data?.error || e.message);
    } finally {
      setLiveLoading(false);
    }
  };

  const loadHistory = async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const res = await fetchHistory({
        since_minutes: sinceMinutes,
        limit: 50,
        ...(pattern.trim() ? { pattern: pattern.trim() } : {}),
      });
      if (res.data?.success) {
        setHistory(res.data);
      } else {
        setHistory(null);
        setHistoryError(
          [res.data?.error, res.data?.hint].filter(Boolean).join(" ") ||
            "Failed to fetch log history."
        );
      }
    } catch (e) {
      setHistory(null);
      const data = e.response?.data;
      setHistoryError(
        [data?.error, data?.hint].filter(Boolean).join(" ") ||
          e.message
      );
    } finally {
      setHistoryLoading(false);
    }
  };

  return (
    <Card
      title="Database logs"
      subtitle={`Live tail + history for pod ${podHint}`}
      actions={
        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          <button
            type="button"
            className={`btn btn--sm ${tab === "live" ? "btn--primary" : "btn--ghost"}`}
            onClick={() => setTab("live")}
          >
            <FileText size={14} /> Live tail
          </button>
          <button
            type="button"
            className={`btn btn--sm ${tab === "history" ? "btn--primary" : "btn--ghost"}`}
            onClick={() => setTab("history")}
          >
            <History size={14} /> History
          </button>
        </div>
      }
    >
      {tab === "live" ? (
        <>
          <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", alignItems: "center", marginBottom: "var(--space-3)" }}>
            <select
              className="select"
              value={tail}
              onChange={(e) => setTail(Number(e.target.value))}
              style={{ width: "auto" }}
              aria-label="Tail lines"
            >
              {TAIL_OPTIONS.map((n) => (
                <option key={n} value={n}>Last {n} lines</option>
              ))}
            </select>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13 }}>
              <input
                type="checkbox"
                checked={previous}
                onChange={(e) => setPrevious(e.target.checked)}
              />
              Previous container (restarts)
            </label>
            <button
              type="button"
              className="btn btn--primary btn--sm"
              onClick={loadLive}
              disabled={liveLoading}
            >
              {liveLoading ? <Loader2 size={14} className="btn__spinner" /> : <RefreshCw size={14} />}
              {" "}Refresh
            </button>
            {live && (
              <Badge tone="neutral">{live.count} lines{live.previous ? " · previous" : ""}</Badge>
            )}
          </div>
          {liveError && (
            <div className="alert alert--danger" style={{ marginBottom: "var(--space-3)" }}>
              {liveError}
            </div>
          )}
          <pre
            className="code-block code-block--plain"
            style={{ maxHeight: 380, overflow: "auto", fontSize: 12, whiteSpace: "pre-wrap" }}
          >
            {live ? renderLines(live.lines) : "Press Refresh to load the live log tail."}
          </pre>
        </>
      ) : (
        <>
          <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", alignItems: "center", marginBottom: "var(--space-3)" }}>
            <select
              className="select"
              value={sinceMinutes}
              onChange={(e) => setSinceMinutes(Number(e.target.value))}
              style={{ width: "auto" }}
              aria-label="Time range"
            >
              {SINCE_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  Last {n >= 60 ? `${n / 60}h` : `${n}m`}
                </option>
              ))}
            </select>
            <input
              type="text"
              placeholder="Keyword filter (e.g. ERROR, timeout)"
              value={pattern}
              onChange={(e) => setPattern(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") loadHistory();
              }}
              style={{ flex: 1, minWidth: 180 }}
            />
            <button
              type="button"
              className="btn btn--primary btn--sm"
              onClick={loadHistory}
              disabled={historyLoading}
            >
              {historyLoading ? <Loader2 size={14} className="btn__spinner" /> : <Search size={14} />}
              {" "}Search
            </button>
            {history && (
              <Badge tone="neutral">{history.total} matches</Badge>
            )}
          </div>
          {historyError && (
            <div className="alert alert--danger" style={{ marginBottom: "var(--space-3)" }}>
              {historyError}
            </div>
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", maxHeight: 380, overflow: "auto" }}>
            {!history && !historyError && (
              <div className="text-muted" style={{ fontSize: 13 }}>
                Search Elasticsearch history for this pod (needs the ES port-forward).
              </div>
            )}
            {(history?.hits || []).map((hit) => {
              const src = hit._source || {};
              const line = src.log || src.message || JSON.stringify(src);
              const tone = logTone(line);
              return (
                <div
                  key={hit._id}
                  style={{
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    padding: "var(--space-2) var(--space-3)",
                    fontSize: 12,
                    fontFamily: "monospace",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    ...(tone === "danger"
                      ? { borderColor: "var(--danger)" }
                      : tone === "warning"
                        ? { borderColor: "var(--warning, #b7791f)" }
                        : {}),
                  }}
                >
                  <div className="text-muted" style={{ fontSize: 11, marginBottom: 4 }}>
                    {src["@timestamp"] || hit["@timestamp"] || "—"}
                  </div>
                  {line}
                </div>
              );
            })}
          </div>
        </>
      )}
    </Card>
  );
}
