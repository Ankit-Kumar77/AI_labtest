import { useEffect, useState, useRef } from "react";
import { Link } from "react-router-dom";
import api, { dbInvestigationApi } from "../api/api";
import Card from "../components/Card";
import Badge from "../components/Badge";
import ProblemFraming from "../components/ProblemFraming";
import ReportFindings from "../components/ReportFindings";
import { extractReport, extractRecommendedActions, stripAnsi } from "../utils/opensre";
import useSessionState from "../hooks/useSessionState";
import {
  BrainCircuit,
  Loader2,
  Send,
  ChevronDown,
  AlertTriangle,
  CheckCircle2,
  Sparkles,
  Search,
  Target,
  Gauge,
   ListChecks,
   FileText,
   Database,
  HardDrive,
  Layers,
  GitCommit,
  ExternalLink,
  Table,
  AlertCircle,
  CheckCircle,
  XCircle,
  Activity,
} from "lucide-react";

function DatabaseEvidenceDisplay({ evidence, targetType }) {
  const inv = evidence?.investigations || {};
  const dbName = targetType === "yugabyte" ? "YugabyteDB" : "Aerospike";
  const dbIcon = targetType === "yugabyte" ? <HardDrive size={16} /> : <Database size={16} />;

  function renderInvestigationSection(title, data, renderItem) {
    if (!data || !data.success) return null;
    const items = data.data || [];
    if (!items.length && typeof items !== "object") return null;
    
    const displayItems = Array.isArray(items) ? items : (items.data || items);
    if (!displayItems || !displayItems.length) return null;

    return (
      <details className="raw-output" style={{ marginBottom: "var(--space-3)" }}>
        <summary style={{ cursor: "pointer", fontWeight: 500, color: "var(--primary)" }}>
          {dbIcon} {title} ({displayItems.length} items)
        </summary>
        <div style={{ marginTop: "var(--space-2)", fontSize: 12, fontFamily: "monospace" }}>
          {Array.isArray(displayItems) ? (
            displayItems.slice(0, 20).map((item, idx) => (
              <div key={idx} style={{ padding: "var(--space-1) 0", borderBottom: "1px solid var(--border)" }}>
                {renderItem(item)}
              </div>
            ))
          ) : (
            <pre className="code-block code-block--plain" style={{ maxHeight: 300, overflow: "auto" }}>
              {JSON.stringify(displayItems, null, 2)}
            </pre>
          )}
        </div>
      </details>
    );
  }

  function renderYugabyteItem(item) {
    if (item.node_name) return `${item.node_name}: ${item.node_status} (leader: ${item.leader_count}, follower: ${item.follower_count})`;
    if (item.total_connections !== undefined) return `Connections: total=${item.total_connections}, active=${item.active_connections}, idle=${item.idle_connections}, waiting=${item.waiting_connections}`;
    if (item.query_preview) return `Query (${item.calls} calls, ${item.mean_exec_time?.toFixed(2)}ms avg): ${item.query_preview}`;
    if (item.query) return `PID ${item.pid}: ${item.state} - ${item.query?.substring(0, 100)}`;
    if (item.table_name) return `Table: ${item.table_name} (${item.total_size_bytes ? `${Math.round(item.total_size_bytes/1024)}KB` : 'no size'})`;
    if (item.primary_key_duplicates) return `PK Duplicates: ${JSON.stringify(item.primary_key_duplicates)}`;
    if (item.unexpected_nulls) return `Unexpected NULLs: ${JSON.stringify(item.unexpected_nulls)}`;
    if (item.foreign_key_violations) return `FK Violations: ${JSON.stringify(item.foreign_key_violations)}`;
    return JSON.stringify(item).substring(0, 200);
  }

  function renderAerospikeItem(item) {
    // Cluster Health nodes
    if (item.node_name) return `${item.node_name}: ${item.status} (client_conns: ${item.client_connections || 'N/A'}, objects: ${item.objects || 'N/A'}, uptime: ${item.uptime || 'N/A'}s)`;
    // Cluster stats
    if (item.stats) return `Cluster: ${Object.keys(item.stats).length} nodes reporting`;
    // Namespaces - item is namespace object with stats and sets
    if (item.namespace) return `Namespace: ${item.namespace} - Sets: ${item.sets?.join(', ') || 'none'}`;
    // Record inspection
    if (item.key) return `Record ${item.key}: ${JSON.stringify(item.bins)}`;
    // Data integrity checks
    if (item.missing_required_fields?.length) return `Missing fields: ${item.missing_required_fields.map(f => `${f.key}.${f.field}`).join(', ')}`;
    if (item.duplicate_logical_records?.length) return `Duplicates: ${item.duplicate_logical_records.map(d => `${d.field}=${d.value}`).join(', ')}`;
    if (item.invalid_field_values?.length) return `Invalid values: ${item.invalid_field_values.map(v => `${v.key}.${v.field}=${v.value}`).join(', ')}`;
    // Latency info
    if (item.node) return `${item.node}: reads=${item.total_read_ops}, writes=${item.total_write_ops}, read>1ms=${item.read_latency_gt_1ms}, write>1ms=${item.write_latency_gt_1ms}`;
    // Demo set stats
    if (item.objects !== undefined) return `Objects: ${item.objects}, Tombstones: ${item.tombstones}, Data: ${Math.round((item.data_used_bytes || 0)/1024)}KB`;
    return JSON.stringify(item).substring(0, 200);
  }

  const renderItem = targetType === "yugabyte" ? renderYugabyteItem : renderAerospikeItem;

  return (
    <div className="stack" style={{ gap: "var(--space-3)" }}>
      <div className="report-grid">
        <div className="report-metric">
          <div className="report-metric__label">Database</div>
          <div className="report-metric__value cell-mono">{dbName}</div>
        </div>
        <div className="report-metric">
          <div className="report-metric__label">Endpoint</div>
          <div className="report-metric__value cell-mono">{evidence.endpoint}</div>
        </div>
        <div className="report-metric">
          <div className="report-metric__label">Health</div>
          <div className="report-metric__value">
            <Badge tone={inv.health?.success ? "success" : "danger"}>
              {inv.health?.success ? "Connected" : "Failed"}
            </Badge>
          </div>
        </div>
        <div className="report-metric">
          <div className="report-metric__label">Investigations</div>
          <div className="report-metric__value">{Object.keys(inv).filter(k => inv[k]?.success).length} / {Object.keys(inv).length}</div>
        </div>
      </div>

      {targetType === "yugabyte" && (
        <>
          {renderInvestigationSection("Cluster Health", inv.cluster_health, renderItem)}
          {renderInvestigationSection("Connections", inv.connections, renderItem)}
          {renderInvestigationSection("Slow Queries", inv.slow_queries, renderItem)}
          {renderInvestigationSection("Recent Errors", inv.recent_errors, renderItem)}
          {renderInvestigationSection("Schema", inv.schema, (item) => `Table: ${item.table_name} (${item.columns?.length || 0} cols, ${item.constraints?.length || 0} constraints)`)}
          {renderInvestigationSection("Table Statistics", inv.table_stats, (item) => `${item.table_name}: ${item.live_tuples} rows, ${item.dead_tuples} dead, ${Math.round((item.total_size_bytes || 0)/1024)}KB`)}
          {renderInvestigationSection("Data Integrity", inv.data_integrity, (item) => {
            const parts = [];
            if (item.primary_key_duplicates?.length) parts.push(`PK Duplicates: ${item.primary_key_duplicates.reduce((sum, d) => sum + (d.duplicates?.length || 0), 0)}`);
            if (item.unexpected_nulls?.length) parts.push(`NULLs: ${item.unexpected_nulls.length}`);
            if (item.foreign_key_violations?.length) parts.push(`FK Violations: ${item.foreign_key_violations.length}`);
            return parts.length ? parts.join(', ') : 'No issues detected';
          })}
          {renderInvestigationSection("Replication", inv.replication, renderItem)}
        </>
      )}

      {targetType === "aerospike" && (
        <>
          {renderInvestigationSection("Cluster Health", inv.cluster_health, renderItem)}
          {renderInvestigationSection("Namespaces", inv.namespaces, (item) => {
            // item is the namespace data object with stats and sets
            const sets = item.sets?.join(', ') || 'none';
            const nodeCount = item.stats ? Object.keys(item.stats).length : 0;
            return `Namespace: ${Object.keys(inv.namespaces?.data || {})[0] || 'test'} - ${nodeCount} nodes, Sets: ${sets}`;
          })}
          {renderInvestigationSection("Operation Errors", inv.operation_errors, (item) => {
            if (item.connection_errors?.length) return `Connection Errors: ${item.connection_errors.map(e => `${e.node}: ${e.issue}`).join('; ')}`;
            if (item.timeouts?.length) return `Timeouts: ${item.timeouts.map(e => `${e.node}: ${e.count}`).join('; ')}`;
            if (item.server_errors?.length) return `Server Errors: ${item.server_errors.map(e => `${e.node}: ${e.issue}`).join('; ')}`;
            return 'No errors detected';
          })}
          {renderInvestigationSection("Latency", inv.latency, renderItem)}
          {renderInvestigationSection("Data Integrity", inv.data_integrity, (item) => {
            const parts = [];
            if (item.missing_required_fields?.length) parts.push(`Missing: ${item.missing_required_fields.length}`);
            if (item.duplicate_logical_records?.length) parts.push(`Duplicates: ${item.duplicate_logical_records.length}`);
            if (item.invalid_field_values?.length) parts.push(`Invalid: ${item.invalid_field_values.length}`);
            return parts.length ? parts.join(', ') : `Scanned ${item.records_scanned} records - OK`;
          })}
          {renderInvestigationSection("Demo Set Stats", inv.demo_set_stats, renderItem)}
        </>
      )}
    </div>
  );
}

export default function AIAnalysis() {
  const [version, setVersion] = useState("");

  const [clusters, setClusters] = useSessionState("opensre:clusters", []);
  const [cluster, setCluster] = useSessionState("opensre:cluster", "");

  const [pods, setPods] = useSessionState("opensre:pods", []);
  const [namespace, setNamespace] = useSessionState("opensre:namespace", "");
  const [podName, setPodName] = useSessionState("opensre:pod", "");

  const [targetType, setTargetType] = useSessionState("opensre:targetType", "pod");
  const [dbHealth, setDbHealth] = useState(null);
  const [dbEvidence, setDbEvidence] = useState(null);
  const [dbEvidenceLoading, setDbEvidenceLoading] = useState(false);
  const [dbEvidenceError, setDbEvidenceError] = useState(null);

  const [investigation, setInvestigation] = useSessionState(
    "opensre:investigation",
    null
  );
  const [investigationTarget, setInvestigationTarget] = useSessionState(
    "opensre:investigationTarget",
    ""
  );
  const [savedIncidentId, setSavedIncidentId] = useState(null);
  const [gitCorrelation, setGitCorrelation] = useState(null);
  const [loading, setLoading] = useState(false);

  const [message, setMessage] = useState("");
  const [chat, setChat] = useSessionState("opensre:chat", []);
  const [chatLoading, setChatLoading] = useState(false);

  const [podsLoading, setPodsLoading] = useState(false);

  const [vmMetrics, setVmMetrics] = useState(null);
  const [esSignals, setEsSignals] = useState(null);

  const chatEndRef = useRef(null);
  const initialDataRef = useRef({ clusters: clusters.length, pods: pods.length });
  const skipPodLoadRef = useRef(pods.length > 0);

  useEffect(() => {
    async function loadInitialData() {
      try {
        const versionRes = await api.get("/opensre/version");
        setVersion(stripAnsi(versionRes.data.stdout || ""));

        if (initialDataRef.current.clusters > 0) return;

        const clusterRes = await api.get("/kubernetes/clusters");
        const clusterLines = (clusterRes.data.stdout || "")
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean);

        setClusters(clusterLines);
        if (clusterLines.length > 0) {
          setCluster(clusterLines[0]);
        }
      } catch {
        setVersion("");
      }
    }

    loadInitialData();
  }, [setCluster, setClusters]);

  useEffect(() => {
    if (!cluster) {
      setPods([]);
      setNamespace("");
      setPodName("");
      return;
    }

    if (skipPodLoadRef.current) {
      skipPodLoadRef.current = false;
      return;
    }

    async function loadPods() {
      setPodsLoading(true);

      try {
        const response = await api.get("/kubernetes/pods", {
          params: { context: cluster },
        });

        const podLines = (response.data.stdout || "")
          .split("\n")
          .slice(1)
          .filter(Boolean);

        const podData = podLines.map((line) => {
          const cols = line.trim().split(/\s+/);
          return {
            namespace: cols[0],
            name: cols[1],
            ready: cols[2],
            status: cols[3],
          };
        });

        setPods(podData);
        setNamespace(podData[0]?.namespace || "");
        setPodName(podData[0]?.name || "");
        setInvestigation(null);
      } catch (err) {
        console.error("Failed to load pods:", err);
        setPods([]);
        setNamespace("");
        setPodName("");
      } finally {
        setPodsLoading(false);
      }
    }

    loadPods();
  }, [cluster, setInvestigation, setNamespace, setPodName, setPods]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat, chatLoading]);

  useEffect(() => {
    if (targetType === "pod") {
      setDbHealth(null);
      setDbEvidence(null);
      return;
    }

    let cancelled = false;
    setDbHealth(null);
    setDbEvidence(null);
    setDbEvidenceError(null);

    // Load database evidence for yugabyte/aerospike
    if (targetType === "yugabyte" || targetType === "aerospike") {
      setDbEvidenceLoading(true);
      const evidenceApi = targetType === "yugabyte" 
        ? dbInvestigationApi.yugabyteEvidence 
        : dbInvestigationApi.aerospikeEvidence;
      
      evidenceApi()
        .then((res) => {
          if (!cancelled && res.data?.success) {
            setDbEvidence(res.data.evidence);
          } else if (!cancelled) {
            setDbEvidenceError(res.data?.error || "Failed to load database evidence");
          }
        })
        .catch((err) => {
          if (!cancelled) setDbEvidenceError(err.message || "Failed to load database evidence");
        })
        .finally(() => {
          if (!cancelled) setDbEvidenceLoading(false);
        });
    }

    if (targetType === "stack") {
      Promise.all([
        api.get("/metrics/health"),
        api.get("/metrics/grafana/health"),
      ])
        .then(([vmRes, grafanaRes]) => {
          if (cancelled) return;

          const vmOk = !!vmRes.data?.success;
          const grafanaOk = !!grafanaRes.data?.success;

          setDbHealth({
            success: vmOk && grafanaOk,
            label: `${vmOk ? "VM up" : "VM down"} · ${grafanaOk ? "Grafana up" : "Grafana down"}`,
          });
        })
        .catch(() => {
          if (!cancelled) setDbHealth(null);
        });

      return () => {
        cancelled = true;
      };
    }

    api
      .get(`/${targetType}/health`)
      .then((res) => {
        if (!cancelled) setDbHealth(res.data);
      })
      .catch(() => {
        if (!cancelled) setDbHealth(null);
      });

    return () => {
      cancelled = true;
    };
  }, [targetType]);

  useEffect(() => {
    if (targetType !== "pod") {
      setVmMetrics(null);
      setEsSignals(null);
    }
  }, [targetType]);

  async function investigatePod() {
    if (targetType === "pod" && (!namespace || !podName)) return;

    setLoading(true);
    setInvestigation(null);
    setSavedIncidentId(null);
    setVmMetrics(null);
    setEsSignals(null);

    try {
      let response;

      if (targetType === "pod") {
        response = await api.get(
          `/opensre/investigate/pod/${namespace}/${podName}`,
          { params: { context: cluster } }
        );
      } else if (targetType === "stack") {
        response = await api.get("/opensre/investigate/stack", {
          params: { context: cluster },
        });
      } else if (targetType === "yugabyte") {
        response = await dbInvestigationApi.opensreInvestigateYugabyte();
      } else if (targetType === "aerospike") {
        response = await dbInvestigationApi.opensreInvestigateAerospike();
      } else {
        response = await api.get(
          `/opensre/investigate/target/${targetType}`
        );
      }

      const data = response.data;

      if (!data.success) {
        setInvestigation({
          error: data.stderr || "OpenSRE investigation failed.",
        });
        setGitCorrelation(null);
      } else {
        const stdout = stripAnsi(data.stdout || "");
        setInvestigation({ stdout, report: extractReport(stdout) });
        // The backend auto-saved this run to the persisted incident
        // history — keep the id so the user can jump straight to it.
        if (data.incident_id) setSavedIncidentId(data.incident_id);

        // Capture VictoriaMetrics pod metrics and ES log signals
        if (data.vm_metrics) setVmMetrics(data.vm_metrics);
        if (data.es_signals) setEsSignals(data.es_signals);

        try {
          const corrRes = await api.get("/investigation/git-correlation", {
            params: { limit: 10 },
          });
          setGitCorrelation(corrRes.data.success ? corrRes.data : null);
        } catch {
          setGitCorrelation(null);
        }
      }

      setInvestigationTarget(
        targetType === "pod"
          ? `${namespace}/${podName}`
          : targetType === "stack"
            ? "Full stack (all components)"
            : targetType
      );
    } catch (err) {
      console.error(err);
      setInvestigation({ error: err.message });
    } finally {
      setLoading(false);
    }
  }

  async function sendMessage() {
    const text = message.trim();
    if (!text || chatLoading) return;

    setChat((previous) => [...previous, { role: "user", content: text }]);
    setMessage("");
    setChatLoading(true);

    try {
      const payload = { message: text };

      if (targetType === "pod") {
        payload.cluster = cluster;
        payload.namespace = namespace;
        payload.pod = podName;
      } else {
        if (targetType === "stack") {
          payload.cluster = cluster;
        }
        payload.target_type = targetType;
      }

      const response = await api.post("/opensre/chat", payload);

      const data = response.data;
      const output = [
        data.stdout || "",
        data.stderr ? `\n\n--- STDERR ---\n${data.stderr}` : "",
      ].join("");

      setChat((previous) => [
        ...previous,
        {
          role: "opensre",
          content: output || "OpenSRE returned no output.",
        },
      ]);
    } catch (err) {
      console.error(err);
      setChat((previous) => [
        ...previous,
        { role: "opensre", content: `OpenSRE request failed.\n\n${err.message}` },
      ]);
    } finally {
      setChatLoading(false);
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  }

  const namespaces = [...new Set(pods.map((pod) => pod.namespace))];
  const selectedNamespacePods = pods.filter((pod) => pod.namespace === namespace);
  const report = investigation?.report;

  const validityScore =
    report?.validity_score != null ? Math.round(report.validity_score * 100) : null;

  const scoreTone =
    validityScore == null
      ? "neutral"
      : validityScore >= 75
        ? "success"
        : validityScore >= 40
          ? "warning"
          : "danger";

  const recommendedActions = extractRecommendedActions(report);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>AI Analysis</h1>
          <p className="page-head__sub">
            Run live root-cause investigations and chat with OpenSRE.
          </p>
        </div>

        <div className="page-head__actions">
          {version && (
            <Badge tone="primary">
              <Sparkles size={12} /> OpenSRE · {version}
            </Badge>
          )}
        </div>
      </div>

      <Card
        title="Run investigation"
        subtitle="Select a workload to launch a live root-cause analysis"
        actions={
          <button
            type="button"
            className="btn btn--primary"
            onClick={investigatePod}
            disabled={
              loading ||
              podsLoading ||
              (targetType === "pod" && (!namespace || !podName))
            }
          >
            {loading ? (
              <>
                <Loader2 size={15} className="btn__spinner" /> Investigating…
              </>
            ) : (
              <>
                <Search size={15} /> Run investigation
              </>
            )}
          </button>
        }
      >
        <div className="form-grid">
          <div className="field">
            <label htmlFor="ai-target">Target</label>
            <select
              id="ai-target"
              className="select"
              value={targetType}
              onChange={(e) => setTargetType(e.target.value)}
            >
              <option value="pod">Kubernetes pod</option>
              <option value="aerospike">Aerospike</option>
              <option value="yugabyte">YugabyteDB</option>
              <option value="stack">Full stack</option>
            </select>
          </div>

          {targetType === "pod" ? (
            <>
              <div className="field">
                <label htmlFor="ai-cluster">Cluster</label>
                <select
                  id="ai-cluster"
                  className="select"
                  value={cluster}
                  onChange={(e) => setCluster(e.target.value)}
                  disabled={clusters.length === 0}
                >
                  {clusters.length === 0 && <option value="">No contexts</option>}
                  {clusters.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </div>

              <div className="field">
                <label htmlFor="ai-namespace">Namespace</label>
                <select
                  id="ai-namespace"
                  className="select"
                  value={namespace}
                  onChange={(e) => {
                    const newNamespace = e.target.value;
                    setNamespace(newNamespace);
                    setPodName(
                      pods.find((pod) => pod.namespace === newNamespace)?.name || ""
                    );
                  }}
                  disabled={podsLoading || namespaces.length === 0}
                >
                  {namespaces.length === 0 && <option value="">Loading…</option>}
                  {namespaces.map((ns) => (
                    <option key={ns} value={ns}>
                      {ns}
                    </option>
                  ))}
                </select>
              </div>

              <div className="field">
                <label htmlFor="ai-pod">Pod</label>
                <select
                  id="ai-pod"
                  className="select"
                  value={podName}
                  onChange={(e) => setPodName(e.target.value)}
                  disabled={podsLoading || selectedNamespacePods.length === 0}
                >
                  {selectedNamespacePods.length === 0 && (
                    <option value="">Loading…</option>
                  )}
                  {selectedNamespacePods.map((pod) => (
                    <option key={pod.name} value={pod.name}>
                      {pod.name}
                    </option>
                  ))}
                </select>
              </div>
            </>
          ) : (
            <div className="field">
              <label>Service</label>
              <div className="target-readout">
                <span className="target-readout__icon">
                  {targetType === "stack" ? (
                    <Layers size={16} />
                  ) : targetType === "aerospike" ? (
                    <Database size={16} />
                  ) : (
                    <HardDrive size={16} />
                  )}
                </span>
                <div className="target-readout__body">
                  <div className="target-readout__name">
                    {targetType === "stack"
                      ? "Observability stack"
                      : targetType === "aerospike"
                        ? "Aerospike"
                        : "YugabyteDB"}
                  </div>
                  <div className="target-readout__meta">
                    {targetType === "stack"
                      ? "K8s · VictoriaMetrics · OTel · Grafana"
                      : targetType === "aerospike"
                        ? "Host container · 127.0.0.1:3001"
                        : "Host container · 127.0.0.1:5433"}
                  </div>
                </div>
                {dbHealth && (
                  <Badge tone={dbHealth.success ? "success" : "danger"}>
                    {dbHealth.label ?? (dbHealth.success ? "Up" : "Down")}
                  </Badge>
                )}
              </div>
            </div>
          )}
        </div>

        {podsLoading && (
          <p className="text-muted" style={{ marginTop: "var(--space-3)", fontSize: 13 }}>
            <Loader2 size={13} className="btn__spinner" /> Loading pods from
            selected cluster…
          </p>
        )}
      </Card>

      {(targetType === "yugabyte" || targetType === "aerospike") && (
        <Card
          title="Database evidence"
          subtitle="Live investigation data collected from the database"
          actions={
            dbEvidenceLoading ? (
              <Badge tone="neutral">
                <Loader2 size={12} className="btn__spinner" /> Collecting…
              </Badge>
            ) : dbEvidenceError ? (
              <Badge tone="danger">
                <AlertCircle size={12} /> Failed
              </Badge>
            ) : dbEvidence ? (
              <Badge tone="success">
                <CheckCircle size={12} /> Collected
              </Badge>
            ) : (
              <Badge tone="neutral">Not loaded</Badge>
            )
          }
        >
          {dbEvidenceLoading ? (
            <div className="stack stack--tight">
              {[0, 1, 2].map((i) => (
                <div key={i} className="skeleton" style={{ height: 44, borderRadius: 4 }} />
              ))}
            </div>
          ) : dbEvidenceError ? (
            <div className="empty-state">
              <AlertCircle size={26} />
              <div>
                <strong>Evidence collection failed</strong>
                <p style={{ marginTop: "var(--space-1)" }}>{dbEvidenceError}</p>
              </div>
            </div>
          ) : dbEvidence ? (
            <DatabaseEvidenceDisplay evidence={dbEvidence} targetType={targetType} />
          ) : (
            <div className="empty-state">
              <Table size={26} /> Waiting for evidence…
            </div>
          )}
        </Card>
      )}

      {investigation && (
        <Card
          title="Investigation report"
          subtitle={`Target · ${investigationTarget}`}
          actions={
            <>
              <Link
                to={savedIncidentId ? `/incident?report=${savedIncidentId}` : "/incident"}
                className="btn btn--ghost btn--sm"
              >
                <FileText size={13} /> Open incident report
              </Link>
              {savedIncidentId && (
                <Badge tone="success">
                  <CheckCircle2 size={12} /> Saved to history
                </Badge>
              )}
              <Badge tone={report ? "success" : "warning"}>
                {report ? (
                  <>
                    <CheckCircle2 size={12} /> Analyzed
                  </>
                ) : (
                  <>
                    <AlertTriangle size={12} /> Inconclusive
                  </>
                )}
              </Badge>
            </>
          }
        >
          {investigation.error ? (
            <div className="empty-state">
              <AlertTriangle size={26} />
              <div>
                <strong>Investigation failed</strong>
                <p style={{ marginTop: "var(--space-1)" }}>
                  {investigation.error}
                </p>
              </div>
            </div>
          ) : (
            <>
              {report && (
                <>
                  <div className="report-cards">
                    <div className="report-card report-card--root">
                      <div className="report-card__icon">
                        <Target size={18} />
                      </div>
                      <div>
                        <div className="report-card__label">Root cause</div>
                        <div className="report-card__value">
                          {report.root_cause || "Not determined"}
                        </div>
                        <div className="report-card__meta">
                          <Badge
                            tone={report.is_noise ? "warning" : "info"}
                          >
                            {report.is_noise
                              ? "Noise / low signal"
                              : "Incident"}
                          </Badge>
                        </div>
                      </div>
                    </div>

                    <div className="report-card report-card--score">
                      <div className="report-card__icon">
                        <Gauge size={18} />
                      </div>
                      <div>
                        <div className="report-card__label">
                          Validity score
                        </div>
                        <div className="report-card__value">
                          {validityScore != null ? `${validityScore}%` : "—"}
                        </div>
                        <div className="score-bar">
                          <div
                            className={`score-bar__track score-bar__track--${scoreTone}`}
                          >
                            <div
                              className="score-bar__fill"
                              style={{
                                width: `${validityScore ?? 0}%`,
                              }}
                            />
                          </div>
                        </div>
                      </div>
                    </div>

                    <div className="report-card report-card--actions">
                      <div className="report-card__icon">
                        <ListChecks size={18} />
                      </div>
                      <div>
                        <div className="report-card__label">
                          Recommended actions
                        </div>
                        {recommendedActions.length > 0 ? (
                          <ul className="action-list">
                            {recommendedActions.map((action, index) => (
                              <li key={index}>{action}</li>
                            ))}
                          </ul>
                        ) : (
                          <div className="report-card__value">—</div>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* VictoriaMetrics pod metrics */}
                  {vmMetrics && Object.keys(vmMetrics).length > 0 && (
                    <div className="report-section">
                      <div className="report-section__title">
                        <Activity size={14} style={{ verticalAlign: "middle", marginRight: 4 }} />
                        VictoriaMetrics (pod)
                      </div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)" }}>
                        <div className="report-metric">
                          <div className="report-metric__label">Request rate</div>
                          <div className="report-metric__value cell-mono">
                            {vmMetrics.request_rate_rps != null ? vmMetrics.request_rate_rps.toFixed(2) : "—"} req/s
                          </div>
                        </div>
                        <div className="report-metric">
                          <div className="report-metric__label">Error rate (5xx)</div>
                          <div className="report-metric__value cell-mono">
                            {vmMetrics.error_rate_5xx_per_s != null ? vmMetrics.error_rate_5xx_per_s.toFixed(2) : "—"} /s
                          </div>
                        </div>
                        <div className="report-metric">
                          <div className="report-metric__label">Error share</div>
                          <div className="report-metric__value cell-mono">
                            {vmMetrics.error_share_percent != null ? vmMetrics.error_share_percent.toFixed(2) : "—"}%
                          </div>
                        </div>
                        <div className="report-metric">
                          <div className="report-metric__label">P50 latency</div>
                          <div className="report-metric__value cell-mono">
                            {vmMetrics.p50_latency_seconds != null ? (vmMetrics.p50_latency_seconds * 1000).toFixed(1) : "—"} ms
                          </div>
                        </div>
                        <div className="report-metric">
                          <div className="report-metric__label">P95 latency</div>
                          <div className="report-metric__value cell-mono">
                            {vmMetrics.p95_latency_seconds != null ? (vmMetrics.p95_latency_seconds * 1000).toFixed(1) : "—"} ms
                          </div>
                        </div>
                        <div className="report-metric">
                          <div className="report-metric__label">P99 latency</div>
                          <div className="report-metric__value cell-mono">
                            {vmMetrics.p99_latency_seconds != null ? (vmMetrics.p99_latency_seconds * 1000).toFixed(1) : "—"} ms
                          </div>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Elasticsearch log signals */}
                  {esSignals && esSignals.health && esSignals.health.success && (
                    <div className="report-section">
                      <div className="report-section__title">
                        <Search size={14} style={{ verticalAlign: "middle", marginRight: 4 }} />
                        Elasticsearch log signals
                      </div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)" }}>
                        <Badge tone={esSignals.signal_counts > 0 ? "danger" : "success"}>
                          <AlertTriangle size={12} />
                          {esSignals.signal_counts} signals (ERROR/EXCEPTION/TIMEOUT)
                        </Badge>
                        <Badge tone="info">
                          <Database size={12} />
                          {esSignals.log_total} total logs (last 60m)
                        </Badge>
                        {esSignals.pod_logs_tail && (
                          <details className="raw-output" style={{ marginTop: "var(--space-2)" }}>
                            <summary style={{ cursor: "pointer", fontWeight: 500, color: "var(--primary)" }}>
                              Notable error entries
                            </summary>
                            <pre className="code-block code-block--plain" style={{ maxHeight: 200, overflow: "auto", fontSize: 11 }}>
                              {(() => {
                                const lines = esSignals.pod_logs_tail.split("\n");
                                const errors = lines.filter(l => ["error", "exception", "timeout", "failed"].some(tok => l.toLowerCase().includes(tok)));
                                return errors.slice(0, 10).join("\n") || "No ERROR/EXCEPTION/TIMEOUT lines found";
                              })()}
                            </pre>
                          </details>
                        )}
                      </div>
                    </div>
                  )}

                  {gitCorrelation?.suspected_commit && (
                    <div style={{
                      padding: "var(--space-3)",
                      border: "2px solid var(--primary)",
                      borderRadius: 6,
                      background: "var(--bg-muted)",
                      marginTop: "var(--space-3)",
                    }}>
                      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: "var(--space-1)", display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
                        <GitCommit size={13} /> Suspected change-point commit
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
                        <code style={{ fontSize: 13, fontWeight: 600 }}>
                          {gitCorrelation.suspected_commit.sha?.substring(0, 7)}
                        </code>
                        <span style={{ fontSize: 13 }}>
                          {gitCorrelation.suspected_commit.message}
                        </span>
                      </div>
                      <div style={{ fontSize: 12, color: "var(--muted)", marginTop: "var(--space-1)", display: "flex", gap: "var(--space-3)" }}>
                        <span>by {gitCorrelation.suspected_commit.author || "—"}</span>
                        <span>{gitCorrelation.suspected_commit.date ? new Date(gitCorrelation.suspected_commit.date).toLocaleString() : "—"}</span>
                      </div>
                      <div style={{ marginTop: "var(--space-2)" }}>
                        <a
                          href={`https://github.com/${gitCorrelation.repo || ""}/commit/${gitCorrelation.suspected_commit.sha}`}
                          target="_blank"
                          rel="noreferrer"
                          className="btn btn--ghost btn--sm"
                        >
                          <ExternalLink size={12} /> View on GitHub
                        </a>
                      </div>
                    </div>
                  )}

                  {gitCorrelation?.no_commit_found && (
                    <div style={{
                      padding: "var(--space-3)",
                      border: "1px solid var(--border)",
                      borderRadius: 6,
                      background: "var(--bg-muted)",
                      marginTop: "var(--space-3)",
                      fontSize: 13,
                      color: "var(--muted)",
                    }}>
                      No commit found at-or-before the incident start on branch <code>{gitCorrelation.branch || "default"}</code>.
                      Attribution is inconclusive — the incident may pre-date deploy history.
                    </div>
                  )}

                  {report.problem_md && (
                    <ProblemFraming
                      markdown={report.problem_md}
                      cluster={cluster}
                    />
                  )}

                  {report.report && (
                    <ReportFindings markdown={report.report} />
                  )}
                </>
              )}

              {investigation.stdout && (
                <details className="raw-output">
                  <summary>
                    <ChevronDown size={14} style={{ verticalAlign: "middle" }} />
                    Raw CLI output
                  </summary>
                  <pre className="code-block">{investigation.stdout}</pre>
                </details>
              )}
            </>
          )}
        </Card>
      )}

      <Card
        title="Assistant"
        subtitle="Ask OpenSRE questions about the selected workload"
      >
        {targetType === "pod" && cluster && (
          <div className="chat__context">
            <span className="chat__context-chip">
              Cluster <span>{cluster || "—"}</span>
            </span>
            <span className="chat__context-chip">
              Namespace <span>{namespace || "—"}</span>
            </span>
            <span className="chat__context-chip">
              Pod <span>{podName || "—"}</span>
            </span>
          </div>
        )}
        
        {targetType === "pod" && (vmMetrics || esSignals) && (
          <div className="chat__context" style={{ marginTop: "var(--space-2)", fontSize: 12 }}>
            {vmMetrics && (
              <span className="chat__context-chip" style={{ background: "var(--primary)", color: "white" }}>
                <Activity size={10} style={{ marginRight: 2 }} />
                {vmMetrics.request_rate_rps != null ? `${vmMetrics.request_rate_rps.toFixed(1)} req/s` : "no metrics"}
                {vmMetrics.p99_latency_seconds != null ? ` · p99 ${(vmMetrics.p99_latency_seconds * 1000).toFixed(0)}ms` : ""}
              </span>
            )}
            {esSignals && esSignals.health?.success && (
              <span className="chat__context-chip" style={{ background: esSignals.signal_counts > 0 ? "var(--danger)" : "var(--success)", color: "white" }}>
                <Search size={10} style={{ marginRight: 2 }} />
                {esSignals.signal_counts > 0 ? `${esSignals.signal_counts} ES signals` : "ES: clean"}
                {esSignals.log_total ? ` · ${esSignals.log_total} logs` : ""}
              </span>
            )}
          </div>
        )}

        {targetType !== "pod" && (
          <div className="chat__context">
            <span className="chat__context-chip">
              Target <span>{targetType}</span>
            </span>
          </div>
        )}

        <div className="chat__window">
          {chat.length === 0 ? (
            <div className="empty-state" style={{ margin: "auto" }}>
              <BrainCircuit size={30} />
              <div>
                <strong>OpenSRE is ready.</strong>
                <p style={{ marginTop: "var(--space-1)", maxWidth: 420 }}>
                  Ask about the selected pod or this environment — it will
                  collect live evidence before responding.
                </p>
              </div>
            </div>
          ) : (
            chat.map((item, index) => (
              <div
                key={index}
                className={`chat__msg chat__msg--${item.role === "user" ? "user" : "opensre"}`}
              >
                <div className="chat__bubble">{item.content}</div>
                <div className="chat__meta">
                  {item.role === "user" ? "You" : "OpenSRE"}
                </div>
              </div>
            ))
          )}

          {chatLoading && (
            <div className="chat__msg chat__msg--opensre">
              <div className="chat__thinking">
                <span className="chat__typing">
                  <span />
                  <span />
                  <span />
                </span>
                Investigating…
              </div>
            </div>
          )}

          <div ref={chatEndRef} />
        </div>

        <div className="chat__footer">
          <textarea
            className="textarea"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask OpenSRE about this pod…"
            rows={2}
            disabled={chatLoading}
            style={{ flex: 1 }}
          />

          <button
            type="button"
            className="btn btn--primary"
            onClick={sendMessage}
            disabled={chatLoading || !message.trim()}
            aria-label="Send message"
          >
            {chatLoading ? (
              <Loader2 size={16} className="btn__spinner" />
            ) : (
              <Send size={16} />
            )}
          </button>
        </div>
      </Card>
    </>
  );
}