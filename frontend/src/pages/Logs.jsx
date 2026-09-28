import React, { useEffect, useState, useCallback, useMemo } from "react";
import { elkApi } from "../api/api";
import Card from "../components/Card";
import Skeleton from "../components/Skeleton";
import {
  Filter,
  X,
  ChevronDown,
  ChevronUp,
  Database,
  AlertTriangle,
  ExternalLink,
  RefreshCw,
  Loader2,
  Copy,
  CheckCircle2,
} from "lucide-react";

const TIME_RANGES = [
  { label: "Last 15 min", value: 15 },
  { label: "Last 1 hour", value: 60 },
  { label: "Last 6 hours", value: 360 },
  { label: "Last 24 hours", value: 1440 },
];

function formatTimestamp(ts) {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

function truncate(str, len = 120) {
  if (!str) return "";
  return str.length > len ? str.slice(0, len) + "…" : str;
}

export default function Logs() {
  const [facets, setFacets] = useState({
    namespaces: [],
    services: [],
    pods: [],
  });
  const [facetsLoading, setFacetsLoading] = useState(true);
  const [facetsError, setFacetsError] = useState(null);

  const [pods, setPods] = useState([]);
  const [podsLoading, setPodsLoading] = useState(false);
  const [podsError, setPodsError] = useState(null);

  const [filters, setFilters] = useState({
    namespace: "",
    pod: "",
    service: "",
    pattern: "",
    since_minutes: 60,
  });

  const [results, setResults] = useState([]);
  const [resultsLoading, setResultsLoading] = useState(false);
  const [resultsError, setResultsError] = useState(null);
  const [totalHits, setTotalHits] = useState(0);
  const [expandedRows, setExpandedRows] = useState(new Set());
  const [selectedRow, setSelectedRow] = useState(null);
  const [offset, setOffset] = useState(0);
  const PAGE_SIZE = 50;

  const [esHealth, setEsHealth] = useState({ available: false });

  useEffect(() => {
    let cancelled = false;
    elkApi.health()
      .then((res) => {
        if (!cancelled) setEsHealth(res.data || { available: false });
      })
      .catch(() => { if (!cancelled) setEsHealth({ available: false }); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setFacetsLoading(true);
    setFacetsError(null);
    elkApi.facets({ since_minutes: filters.since_minutes })
      .then((res) => {
        if (!cancelled && res.data?.success) {
          setFacets({
            namespaces: res.data.namespaces || [],
            services: res.data.services || [],
            pods: res.data.pods || [],
          });
        } else if (!cancelled) {
          setFacetsError(res.data?.error || "Failed to load facets");
        }
      })
      .catch((err) => {
        if (!cancelled) setFacetsError(err.message || "Network error");
      })
      .finally(() => {
        if (!cancelled) setFacetsLoading(false);
      });
    return () => { cancelled = true; };
  }, [filters.since_minutes]);

  useEffect(() => {
    if (!filters.namespace) {
      setPods([]);
      return;
    }
    let cancelled = false;
    setPodsLoading(true);
    setPodsError(null);
    elkApi.podsByNamespace(filters.namespace, { since_minutes: filters.since_minutes })
      .then((res) => {
        if (!cancelled && res.data?.success) {
          setPods(res.data.pods || []);
        } else if (!cancelled) {
          setPodsError(res.data?.error || "Failed to load pods");
        }
      })
      .catch((err) => {
        if (!cancelled) setPodsError(err.message || "Network error");
      })
      .finally(() => {
        if (!cancelled) setPodsLoading(false);
      });
    return () => { cancelled = true; };
  }, [filters.namespace, filters.since_minutes]);

  useEffect(() => {
    if (filters.namespace && filters.pod && !pods.includes(filters.pod)) {
      setFilters((prev) => ({ ...prev, pod: "" }));
    }
  }, [filters.namespace, pods]);

  useEffect(() => {
    const timer = setTimeout(() => {
      let cancelled = false;
      setResultsLoading(true);
      setResultsError(null);
      const params = { ...filters, limit: PAGE_SIZE, offset };
      Object.keys(params).forEach((k) => {
        if (params[k] === "" || params[k] === undefined) delete params[k];
      });
      elkApi.search(params)
        .then((res) => {
          if (!cancelled) {
            const newHits = res.data?.hits || [];
            setResults((prev) => offset === 0 ? newHits : [...prev, ...newHits]);
            setTotalHits(res.data?.total || 0);
          }
        })
        .catch((err) => {
          if (!cancelled) setResultsError(err.message || "Network error");
        })
        .finally(() => {
          if (!cancelled) setResultsLoading(false);
        });
      return () => { cancelled = true; };
    }, 300);
    return () => clearTimeout(timer);
  }, [filters, offset]);

  const loadMore = useCallback(() => {
    setOffset((prev) => prev + PAGE_SIZE);
  }, []);

  const handleFilterChange = useCallback((key, value) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setOffset(0);
    setResults([]);
  }, []);

  const clearFilters = useCallback(() => {
    setFilters({
      namespace: "",
      pod: "",
      service: "",
      pattern: "",
      since_minutes: 60,
    });
    setOffset(0);
    setResults([]);
  }, []);

  const hasActiveFilters = useMemo(
    () => filters.namespace !== "" || filters.pod !== "" || filters.service !== "" || filters.pattern !== "" || filters.since_minutes !== 60,
    [filters]
  );

  const toggleRowExpand = useCallback((index) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }, []);

  const selectRow = useCallback((hit, index) => {
    setSelectedRow(selectedRow === index ? null : index);
  }, [selectedRow]);

  const copyToClipboard = useCallback((text) => {
    navigator.clipboard.writeText(text);
  }, []);

  const kibanaUrl = "http://localhost:5601";

  const buildKibanaUrl = useCallback(() => {
    const queryParts = [];
    if (filters.namespace) queryParts.push(`k8s_namespace_name:${filters.namespace}`);
    if (filters.pod) queryParts.push(`k8s_pod_name:${filters.pod}`);
    if (filters.service) queryParts.push(`k8s_labels.app.keyword:${filters.service}`);
    if (filters.pattern) queryParts.push(filters.pattern);

    const query = queryParts.length > 0 ? queryParts.join(" AND ") : "*";
    const now = new Date();
    const from = new Date(now.getTime() - filters.since_minutes * 60 * 1000);

    const gState = {
      time: { from: from.toISOString(), to: now.toISOString(), mode: "absolute" },
    };
    const aState = {
      index: "logs-opensre-*",
      query: { query_string: { query } },
      columns: ["@timestamp", "k8s_namespace_name", "k8s_pod_name", "k8s_labels.app.keyword", "log"],
      sort: [["@timestamp", "desc"]],
    };

    return `${kibanaUrl}/app/discover#/?_g=${encodeURIComponent(JSON.stringify(gState))}&_a=${encodeURIComponent(JSON.stringify(aState))}`;
  }, [filters, kibanaUrl]);

  const buildKibanaRowUrl = useCallback((hit) => {
    const src = hit._source || {};
    const queryParts = [];
    if (src.k8s_namespace_name) queryParts.push(`k8s_namespace_name:${src.k8s_namespace_name}`);
    if (src.k8s_pod_name) queryParts.push(`k8s_pod_name:${src.k8s_pod_name}`);

    const query = queryParts.length > 0 ? queryParts.join(" AND ") : "*";
    const now = new Date();
    const from = new Date(now.getTime() - filters.since_minutes * 60 * 1000);

    const gState = {
      time: { from: from.toISOString(), to: now.toISOString(), mode: "absolute" },
    };
    const aState = {
      index: "logs-opensre-*",
      query: { query_string: { query } },
      columns: ["@timestamp", "k8s_namespace_name", "k8s_pod_name", "k8s_labels.app.keyword", "log"],
      sort: [["@timestamp", "desc"]],
    };

    return `${kibanaUrl}/app/discover#/?_g=${encodeURIComponent(JSON.stringify(gState))}&_a=${encodeURIComponent(JSON.stringify(aState))}`;
  }, [filters.since_minutes, kibanaUrl]);

  const esAvailable = esHealth.available === true;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Logs Explorer</h1>
          <p className="page-head__sub">
            Search and filter Kubernetes logs
          </p>
        </div>
        <div className="page-head__actions" style={{ display: "flex", gap: "var(--space-2)", alignItems: "center", flexWrap: "wrap" }}>
          <span className={`badge badge--${esAvailable ? "success" : "danger"}`} style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
            {esAvailable ? (
              <>
                <CheckCircle2 size={12} /> Elasticsearch Connected
              </>
            ) : (
              <>
                <AlertTriangle size={12} /> Elasticsearch Unavailable
              </>
            )}
          </span>

          <a
            href={buildKibanaUrl()}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn--primary btn--sm"
            title="Open current filters in Kibana Discover"
            style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
          >
            <ExternalLink size={14} /> Open in Kibana
          </a>

          <button
            className="btn btn--ghost btn--sm"
            onClick={() => { setResultsLoading(true); setTimeout(() => setResultsLoading(false), 100); }}
            disabled={resultsLoading}
            title="Refresh results"
          >
            {resultsLoading ? <Loader2 size={14} className="btn__spinner" /> : <RefreshCw size={14} />}
          </button>
        </div>
      </div>

      {!esAvailable && (
        <div className="alert alert--warning" style={{ marginBottom: "var(--space-4)" }}>
          <AlertTriangle size={16} style={{ verticalAlign: "middle", marginRight: "var(--space-2)" }} />
          <strong>Elasticsearch is not available.</strong> Run <code>./observability/install.sh</code> to deploy the ELK stack.
        </div>
      )}

      <Card title="Filters" subtitle={esAvailable ? `${totalHits} matching logs` : "Elasticsearch unavailable"}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)", alignItems: "flex-end" }}>
          <div className="field" style={{ minWidth: 160 }}>
            <label htmlFor="logs-time-range">Time Range</label>
            <select
              id="logs-time-range"
              className="select"
              value={filters.since_minutes}
              onChange={(e) => handleFilterChange("since_minutes", Number(e.target.value))}
              disabled={!esAvailable}
            >
              {TIME_RANGES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </div>

          <div className="field" style={{ minWidth: 160 }}>
            <label htmlFor="logs-namespace">Namespace</label>
            <select
              id="logs-namespace"
              className="select"
              value={filters.namespace}
              onChange={(e) => handleFilterChange("namespace", e.target.value)}
              disabled={facetsLoading || !esAvailable}
            >
              <option value="">All namespaces</option>
              {facets.namespaces?.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </div>

          <div className="field" style={{ minWidth: 200 }}>
            <label htmlFor="logs-pod">Pod</label>
            <select
              id="logs-pod"
              className="select"
              value={filters.pod}
              onChange={(e) => handleFilterChange("pod", e.target.value)}
              disabled={podsLoading || !filters.namespace || !esAvailable}
            >
              <option value="">All pods</option>
              {pods?.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
            {!filters.namespace && !podsLoading && (
              <span className="text-muted" style={{ fontSize: 11 }}>Select namespace first</span>
            )}
            {podsLoading && <span className="text-muted" style={{ fontSize: 11 }}>Loading…</span>}
            {podsError && <span className="text-danger" style={{ fontSize: 11 }}>{podsError}</span>}
          </div>

          {facetsError && (
            <div className="text-danger" style={{ fontSize: 11 }}>
              Could not load log filters: {facetsError}
            </div>
          )}

          <div className="field" style={{ minWidth: 160 }}>
            <label htmlFor="logs-service">Service</label>
            <select
              id="logs-service"
              className="select"
              value={filters.service}
              onChange={(e) => handleFilterChange("service", e.target.value)}
              disabled={facetsLoading || !esAvailable}
            >
              <option value="">All services</option>
              {facets.services?.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          <div className="field" style={{ minWidth: 220, flex: 1 }}>
            <label htmlFor="logs-pattern">Search</label>
            <div style={{ display: "flex", gap: "var(--space-1)" }}>
              <input
                id="logs-pattern"
                type="text"
                className="input"
                placeholder="Filter by message content..."
                value={filters.pattern}
                onChange={(e) => handleFilterChange("pattern", e.target.value)}
                disabled={!esAvailable}
                style={{ flex: 1 }}
              />
              {filters.pattern && (
                <button
                  className="btn btn--ghost btn--sm"
                  onClick={() => handleFilterChange("pattern", "")}
                  aria-label="Clear search"
                >
                  <X size={14} />
                </button>
              )}
            </div>
          </div>

          {hasActiveFilters && (
            <button
              className="btn btn--ghost btn--sm"
              onClick={clearFilters}
              style={{ height: "fit-content", marginBottom: "var(--space-1)" }}
            >
              <Filter size={14} style={{ marginRight: 4 }} /> Clear All
            </button>
          )}
        </div>
      </Card>

      <Card title="Log Entries" subtitle={esAvailable ? `Showing ${results.length} of ${totalHits} total` : "—"}>
        {resultsError && (
          <div className="alert alert--danger" style={{ marginBottom: "var(--space-3)" }}>
            <AlertTriangle size={14} style={{ verticalAlign: "middle", marginRight: "var(--space-2)" }} />
            {resultsError}
          </div>
        )}

        {resultsLoading && results.length === 0 ? (
          <div className="stack stack--tight">
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} height={56} />
            ))}
          </div>
        ) : results.length === 0 ? (
          <div className="empty-state" style={{ padding: "var(--space-8)" }}>
            <Database size={48} />
            <div style={{ marginTop: "var(--space-3)" }}>
              <strong>No logs found</strong>
              <p style={{ marginTop: "var(--space-1)", color: "var(--muted)" }}>
                {esAvailable
                  ? "Try adjusting your filters or time range"
                  : "Elasticsearch is not available. Deploy the ELK stack first."}
              </p>
            </div>
          </div>
        ) : (
          <div style={{ overflow: "auto" }}>
            <table className="table" style={{ minWidth: 900 }}>
              <thead>
                <tr>
                  <th style={{ width: 36 }}></th>
                  <th style={{ width: 170 }}>Timestamp</th>
                  <th style={{ width: 130 }}>Namespace</th>
                  <th style={{ width: 220 }}>Pod</th>
                  <th style={{ width: 130 }}>Service</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {results.map((hit, index) => {
                  const src = hit._source || {};
                  const isExpanded = expandedRows.has(index);
                  const isSelected = selectedRow === index;
                  const namespace = src.k8s_namespace_name || "—";
                  const pod = src.k8s_pod_name || "—";
                  const service = src.k8s_labels?.app || "—";
                  return (
                    <React.Fragment key={hit._id || index}>
                      <tr
                        className={isSelected ? "row--selected" : ""}
                        style={{ cursor: "pointer" }}
                        onClick={() => selectRow(hit, index)}
                      >
                        <td style={{ textAlign: "center", width: 36 }}>
                          <button
                            className="btn btn--ghost btn--sm"
                            onClick={(e) => { e.stopPropagation(); toggleRowExpand(index); }}
                            style={{ padding: 0, border: "none", background: "none" }}
                          >
                            {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                          </button>
                        </td>
                        <td className="cell-mono cell-muted" style={{ width: 170, whiteSpace: "nowrap", fontSize: 12 }}>
                          {formatTimestamp(src["@timestamp"])}
                        </td>
                        <td className="cell-mono" style={{ width: 130 }}>
                          <span className="badge badge--info" style={{ fontSize: 11 }}>{namespace}</span>
                        </td>
                        <td className="cell-mono" style={{ width: 220, maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 12 }}>
                          {pod}
                        </td>
                        <td className="cell-mono" style={{ width: 130 }}>
                          {service}
                        </td>
                        <td className="cell-mono" style={{ maxWidth: 400, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 12 }}>
                          {truncate(src.log || src.message || "—", 150)}
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr>
                          <td colSpan={6} style={{ padding: 0 }}>
                            <div style={{ padding: "var(--space-3)", background: "var(--surface-1)", borderTop: "1px solid var(--border)" }}>
                                                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "var(--space-2)" }}>
                                <span className="text-muted" style={{ fontSize: 11 }}>Full log entry</span>
                                <div style={{ display: "flex", gap: "var(--space-1)" }}>
                                  <a
                                    href={buildKibanaRowUrl(hit)}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="btn btn--ghost btn--sm"
                                    onClick={(e) => e.stopPropagation()}
                                    title="View this pod in Kibana"
                                  >
                                    <ExternalLink size={12} /> Kibana
                                  </a>
                                  <button
                                    className="btn btn--ghost btn--sm"
                                    onClick={(e) => { e.stopPropagation(); copyToClipboard(JSON.stringify(src, null, 2)); }}
                                    title="Copy JSON"
                                  >
                                    <Copy size={12} />
                                  </button>
                                </div>
                              </div>
                              <pre className="code-block code-block--plain" style={{ maxHeight: 300, overflow: "auto", fontSize: 11 }}>
                                {JSON.stringify(src, null, 2)}
                              </pre>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {results.length > 0 && totalHits > results.length && (
          <div style={{ padding: "var(--space-3)", borderTop: "1px solid var(--border)", display: "flex", justifyContent: "center" }}>
            <button
              className="btn btn--primary"
              onClick={loadMore}
              disabled={resultsLoading}
            >
              {resultsLoading ? (
                <><Loader2 size={14} className="btn__spinner" /> Loading…</>
              ) : (
                <>Load more ({results.length} of {totalHits})</>
              )}
            </button>
          </div>
        )}
      </Card>
    </>
  );
}
