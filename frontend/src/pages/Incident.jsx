import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import api, { incidentsApi } from "../api/api";
import Card from "../components/Card";
import Badge from "../components/Badge";
import Skeleton from "../components/Skeleton";
import ProblemFraming from "../components/ProblemFraming";
import ReportFindings from "../components/ReportFindings";
import useSessionState from "../hooks/useSessionState";
import {
  extractReport,
  extractRecommendedActions,
  stripAnsi,
  podTone,
  deriveSeverity,
  severityTone,
  parseRestarts,
} from "../utils/opensre";
import {
  Activity,
  AlertTriangle,
  Boxes,
  CheckCircle2,
  Download,
  FileText,
  Gauge,
  Loader2,
  RefreshCw,
  Search,
  ShieldAlert,
  Target,
  Terminal,
} from "lucide-react";

const METRIC_ITEMS = [
  { key: "up", label: "Instance up", unit: "" },
  { key: "memory", label: "Resident memory", unit: "bytes" },
  { key: "cpu", label: "CPU seconds", unit: "seconds" },
  { key: "requests", label: "HTTP requests", unit: "count" },
];

const POD_METRIC_ITEMS = [
  { key: "request_rate_rps", label: "Request rate", unit: "req/s" },
  { key: "error_rate_5xx_per_s", label: "5xx error rate", unit: "errors/s" },
  { key: "error_share_percent", label: "5xx error share", unit: "%" },
  { key: "p50_latency_seconds", label: "P50 latency", unit: "seconds" },
  { key: "p95_latency_seconds", label: "P95 latency", unit: "seconds" },
  { key: "p99_latency_seconds", label: "P99 latency", unit: "seconds" },
];

const SEVERITY_LABEL = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
};

function formatMetricValue(value, unit) {
  const num = Number(value);
  if (!Number.isFinite(num)) return value || "—";

  if (unit === "bytes") {
    if (Math.abs(num) >= 1024 * 1024 * 1024)
      return `${(num / (1024 * 1024 * 1024)).toFixed(2)} GiB`;
    if (Math.abs(num) >= 1024 * 1024)
      return `${(num / (1024 * 1024)).toFixed(2)} MiB`;
    if (Math.abs(num) >= 1024) return `${(num / 1024).toFixed(1)} KiB`;
  }

  return num.toLocaleString();
}

function parseEvents(stdout = "") {
  const lines = (stdout || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  if (lines.length <= 1) return [];

  return lines.slice(1).map((line) => {
    const tokens = line.split(/\s+/);
    return {
      lastSeen: tokens[0] || "—",
      type: tokens[1] || "—",
      reason: tokens[2] || "—",
      object: tokens[3] || "—",
      message: tokens.slice(4).join(" ") || "—",
    };
  });
}

function parseStatusRow(stdout = "") {
  const lines = (stdout || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  if (lines.length < 2) return null;

  const cols = lines[1].split(/\s+/);
  return {
    name: cols[0],
    ready: cols[1],
    status: cols[2],
    restarts: cols[3],
    age: cols[4],
  };
}

function toHistoryEntry(item) {
  return {
    id: item.id,
    timestamp: item.ts,
    target_type: item.target_type,
    target_label: item.target_label,
    namespace: item.namespace,
    pod: item.pod,
    cluster: item.cluster,
    question: item.question,
    source: item.source,
    report:
      item.report && Object.keys(item.report).length ? item.report : null,
    stdout: item.stdout_preview || "",
    _previewOnly: true,
  };
}

function needsFullRecord(entry) {
  return (
    !!entry &&
    entry._previewOnly &&
    entry.id != null &&
    !String(entry.id).startsWith("local-")
  );
}

export default function Incident() {
  const [clusters, setClusters] = useSessionState("opensre:clusters", []);
  const [cluster, setCluster] = useSessionState("opensre:cluster", "");

  const [pods, setPods] = useSessionState("opensre:pods", []);
  const [namespace, setNamespace] = useSessionState("opensre:namespace", "");
  const [podName, setPodName] = useSessionState("opensre:pod", "");

  const [investigation, setInvestigation] = useSessionState(
    "opensre:investigation",
    null
  );
  const [investigationTarget, setInvestigationTarget] = useSessionState(
    "opensre:investigationTarget",
    ""
  );

  const [evidence, setEvidence] = useState(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState(null);
  const [podsLoading, setPodsLoading] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [observedAt, setObservedAt] = useState(null);

  const [report, setReport] = useSessionState("opensre:incidentReport", null);
  const [generating, setGenerating] = useState(false);

  const [investigationHistory, setInvestigationHistory] = useSessionState(
    "opensre:investigationHistory",
    []
  );
  const [expandedHistoryIdx, setExpandedHistoryIdx] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [pendingReportId, setPendingReportId] = useState(null);

  const [searchParams, setSearchParams] = useSearchParams();

  // Deep-link support: /incident?namespace=<ns>&pod=<name> (e.g. from the
  // Chaos failure-injection page) pre-selects the incident target once.
  // /incident?report=<id> (e.g. from AI Analysis) expands that history entry.
  useEffect(() => {
    const ns = searchParams.get("namespace");
    const pod = searchParams.get("pod");
    const rep = searchParams.get("report");
    if (ns || pod) {
      if (ns) setNamespace(ns);
      if (pod) setPodName(pod);
    }
    if (rep) setPendingReportId(rep);
    if (ns || pod || rep) setSearchParams({}, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persisted history (server-side): every OpenSRE investigation —
  // including runs from the AI Analysis dashboard — auto-saves here.
  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const res = await incidentsApi.list(100);
      if (res.data?.success) {
        const serverEntries = (res.data.data || []).map(toHistoryEntry);
        setInvestigationHistory((prev) => {
          const prevList = Array.isArray(prev) ? prev : [];
          const localOnly = prevList.filter(
            (e) =>
              e._local &&
              !serverEntries.some((s) => String(s.id) === String(e.id))
          );
          const merged = [...localOnly, ...serverEntries];
          merged.sort(
            (a, b) => new Date(b.timestamp) - new Date(a.timestamp)
          );
          return merged;
        });
      }
    } catch (err) {
      console.error("Failed to load incident history:", err);
    } finally {
      setHistoryLoading(false);
    }
  }, [setInvestigationHistory]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const fetchFullEntry = useCallback(
    async (entryId) => {
      try {
        const res = await incidentsApi.get(entryId);
        if (res.data?.success && res.data.data) {
          const full = res.data.data;
          const fullReport =
            full.report && Object.keys(full.report).length
              ? full.report
              : null;
          setInvestigationHistory((prev) =>
            (Array.isArray(prev) ? prev : []).map((e) =>
              String(e.id) === String(entryId)
                ? {
                    ...e,
                    stdout: full.stdout || e.stdout,
                    report: e.report || fullReport,
                    _previewOnly: false,
                  }
                : e
            )
          );
        }
      } catch (err) {
        console.error("Failed to load incident detail:", err);
      }
    },
    [setInvestigationHistory]
  );

  function toggleHistoryEntry(idx, entries) {
    const isExpanded = expandedHistoryIdx === idx;
    setExpandedHistoryIdx(isExpanded ? null : idx);
    if (!isExpanded) {
      const entry = entries[idx];
      if (needsFullRecord(entry)) fetchFullEntry(entry.id);
    }
  }

  // Expand a deep-linked history entry once it has loaded.
  useEffect(() => {
    if (!pendingReportId || investigationHistory.length === 0) return;
    const idx = investigationHistory.findIndex(
      (e) => String(e.id) === String(pendingReportId)
    );
    if (idx < 0) return;
    setExpandedHistoryIdx(idx);
    if (needsFullRecord(investigationHistory[idx])) {
      fetchFullEntry(investigationHistory[idx].id);
    }
    setPendingReportId(null);
  }, [pendingReportId, investigationHistory, fetchFullEntry]);

  async function deleteHistoryEntry(entryId) {
    setInvestigationHistory((prev) =>
      (Array.isArray(prev) ? prev : []).filter(
        (e) => String(e.id) !== String(entryId)
      )
    );
    if (entryId != null && !String(entryId).startsWith("local-")) {
      try {
        await incidentsApi.remove(entryId);
      } catch (err) {
        console.error("Failed to delete incident:", err);
        loadHistory();
      }
    }
  }

  async function clearHistory() {
    try {
      await incidentsApi.clear();
    } catch (err) {
      console.error("Failed to clear incident history:", err);
    }
    setInvestigationHistory([]);
  }

  const initialLoadRef = useRef({
    clusters: clusters.length,
  });
  const podsLoadedRef = useRef(null);

  useEffect(() => {
    if (clusters.length > 0 || initialLoadRef.current.clusters > 0) return;

    async function loadClusters() {
      try {
        const res = await api.get("/kubernetes/clusters");
        const lines = (res.data.stdout || "")
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean);
        setClusters(lines);
        if (!cluster && lines.length > 0) setCluster(lines[0]);
      } catch (err) {
        console.error("Failed to load clusters:", err);
      }
    }

    loadClusters();
  }, [clusters.length, cluster, setClusters, setCluster]);

  const loadPods = useCallback(async () => {
    if (!cluster) return;
    setPodsLoading(true);

    try {
      const res = await api.get("/kubernetes/pods", {
        params: { context: cluster },
      });
      const lines = (res.data.stdout || "")
        .split("\n")
        .slice(1)
        .filter(Boolean);
      const data = lines.map((line) => {
        const cols = line.trim().split(/\s+/);
        return {
          namespace: cols[0],
          name: cols[1],
          ready: cols[2],
          status: cols[3],
          restarts: cols[4],
          age: cols[5],
        };
      });
      setPods(data);
    } catch (err) {
      console.error("Failed to load pods:", err);
      setPods([]);
    } finally {
      setPodsLoading(false);
    }
  }, [cluster, setPods]);

  useEffect(() => {
    if (!cluster) return;
    // Always refresh from the live cluster on mount / cluster change instead
    // of trusting the sessionStorage snapshot, which can hold pods that no
    // longer exist (e.g. deleted debug pods like as-inspect / asd-bisect).
    if (podsLoadedRef.current === cluster) return;
    podsLoadedRef.current = cluster;
    loadPods();
  }, [cluster, loadPods]);

  useEffect(() => {
    if (!namespace || !podName) return;

    async function loadEvidence() {
      setEvidenceLoading(true);
      setEvidenceError(null);
      setEvidence(null);
      setReport(null);
      setObservedAt(new Date().toISOString());

      try {
        const res = await api.get(
          `/investigation/evidence/pod/${encodeURIComponent(namespace)}/${encodeURIComponent(podName)}`,
          { params: { context: cluster || undefined } }
        );

        if (res.data && res.data.success) {
          setEvidence(res.data.evidence);
        } else {
          setEvidenceError(
            res.data?.stderr ||
              res.data?.error ||
              "Failed to collect Kubernetes evidence."
          );
        }
      } catch (err) {
        console.error("Failed to collect evidence:", err);
        setEvidenceError(err.message || "Failed to collect evidence.");
      } finally {
        setEvidenceLoading(false);
      }
    }

    loadEvidence();
  }, [namespace, podName, cluster, setReport]);

  async function runInvestigation() {
    if (!namespace || !podName) return;

    setAiLoading(true);
    setInvestigation(null);

    try {
      const res = await api.get(
        `/opensre/investigate/pod/${namespace}/${podName}`,
        { params: { context: cluster } }
      );

      const data = res.data;

      if (!data.success) {
        setInvestigation({
          error: data.stderr || "OpenSRE investigation failed.",
        });
      } else {
        const stdout = stripAnsi(data.stdout || "");
        const reportData = extractReport(stdout);
        setInvestigation({ stdout, report: reportData });

        // The backend auto-saved this run to the persisted incident
        // history; keep the full local copy too and re-sync so the id
        // matches the server record.
        const entry = {
          id: data.incident_id || `local-${Date.now()}`,
          pod: podName,
          namespace,
          target_type: "pod",
          target_label: `${namespace}/${podName}`,
          cluster: cluster || null,
          timestamp: new Date().toISOString(),
          report: reportData,
          stdout,
          _local: true,
        };
        setInvestigationHistory((prev) => {
          const prevList = Array.isArray(prev) ? prev : [];
          return [entry, ...prevList.filter((e) => String(e.id) !== String(entry.id))];
        });
        loadHistory();
      }
    } catch (err) {
      console.error("Investigation failed:", err);
      setInvestigation({ error: err.message });
    } finally {
      setInvestigationTarget(`${namespace}/${podName}`);
      setAiLoading(false);
    }
  }

  const namespaces = useMemo(
    () => [...new Set(pods.map((pod) => pod.namespace))],
    [pods]
  );
  const selectedNamespacePods = useMemo(
    () => pods.filter((pod) => pod.namespace === namespace),
    [pods, namespace]
  );

  const evidenceStatus = parseStatusRow(
    evidence?.kubernetes?.pod_status || ""
  );
  const selectedPod = pods.find(
    (pod) => pod.namespace === namespace && pod.name === podName
  );

  const status = selectedPod?.status || evidenceStatus?.status || "—";
  const restarts = parseRestarts(
    selectedPod?.restarts || evidenceStatus?.restarts
  );
  const severity = deriveSeverity(status, restarts);
  const severityLabel = SEVERITY_LABEL[severity] || severity;

  const investigationMatches =
    !!investigation &&
    investigationTarget === `${namespace}/${podName}`;

  const events = parseEvents(evidence?.kubernetes?.events || "");
  const endpoint = evidence?.kubernetes?.endpoint;
  const metrics = evidence?.metrics || {};
  const logAnalysis = evidence?.kubernetes?.log_analysis || null;
  const relevantLogLines = logAnalysis?.relevant_lines || [];
  const signalCounts = logAnalysis?.signal_counts || {};
  const structuredEvents = evidence?.kubernetes?.events_structured || [];
  const timeline = evidence?.kubernetes?.timeline || [];
  const logsTail = evidence?.kubernetes?.logs_tail || "";
  const logsPrevious = evidence?.kubernetes?.logs_previous || "";
  const logsPreviousAvailable = !!evidence?.kubernetes?.logs_previous_available;
  const logsError = evidence?.kubernetes?.logs_error || null;

  const reportObj = investigationMatches ? investigation.report : null;
  const validityScore =
    reportObj?.validity_score != null
      ? Math.round(reportObj.validity_score * 100)
      : null;

  const recommendedActions =
    reportObj ? extractRecommendedActions(reportObj) : [];

  function metricsRows() {
    const rows = METRIC_ITEMS.map(({ key, label, unit }) => {
      const entry = metrics[key];

      if (!entry || entry.success === false) {
        return {
          key,
          label,
          status: "unavailable",
          detail: entry?.error
            ? stripAnsi(entry.error)
            : "No metrics collected for this pod.",
        };
      }

      const result = entry.data?.data?.result || [];
      if (result.length === 0) {
        return {
          key,
          label,
          status: "empty",
          detail: "No series returned for this instance.",
        };
      }

      return {
        key,
        label,
        status: "ok",
        detail: result
          .map((item) => {
            const name = item.metric?.__name__ || "";
            const value = formatMetricValue(item.value?.[1], unit);
            return name ? `${name} = ${value}` : value;
          })
          .join(" · "),
      };
    });

    const podMetrics = metrics.pod || {};
    POD_METRIC_ITEMS.forEach(({ key, label, unit }) => {
      const value = podMetrics[key];
      if (value != null) {
        rows.push({
          key: `pod_${key}`,
          label,
          status: "ok",
          detail: formatMetricValue(value, unit === "req/s" || unit === "errors/s" ? "" : unit),
        });
      }
    });

    return rows;
  }

  function buildReport() {
    const now = new Date();
    const rows = metricsRows();

    const metricsBlock = rows
      .map((row) => {
        const badge =
          row.status === "ok"
            ? "available"
            : row.status === "empty"
              ? "no data"
              : "unavailable";
        return `| ${row.label} | ${badge} | ${row.detail.replace(/\n/g, " ")} |`;
      })
      .join("\n");

    const aiLines = [];

    if (investigationMatches) {
      if (investigation.error) {
        aiLines.push(`- **Status:** Failed — ${investigation.error.replace(/\n/g, " ")}`);
      } else {
        aiLines.push(
          `- **Status:** ${reportObj ? "Analyzed" : "Inconclusive"}`
        );
        aiLines.push(
          `- **Root cause:** ${reportObj?.root_cause || "Not determined"}`
        );
        aiLines.push(
          `- **Validity score:** ${validityScore != null ? `${validityScore}%` : "—"}`
        );
        aiLines.push(
          `- **Classification:** ${reportObj?.is_noise ? "Noise / low signal" : "Incident"}`
        );
        aiLines.push(
          `- **Summary:** ${
            reportObj?.summary ||
            reportObj?.problem_md ||
            "Not captured by OpenSRE"
          }`
        );
        aiLines.push(
          `- **Impact:** ${reportObj?.impact || "Not captured by OpenSRE"}`
        );
        aiLines.push(
          `- **Evidence supporting RCA:** ${
            reportObj?.evidence?.length
              ? reportObj.evidence
                  .map((item) => `"${item}"`)
                  .join(", ")
              : reportObj?.evidence || "Not captured by OpenSRE"
          }`
        );
        aiLines.push(
          `- **Recommended remediation:** ${
            recommendedActions.length > 0
              ? recommendedActions.map((item) => `"${item}"`).join(", ")
              : "Not captured by OpenSRE"
          }`
        );
      }
    } else {
      aiLines.push("- **Status:** No AI investigation for this pod yet.");
    }

    const lines = [
      "# Incident Report",
      "",
      `**Generated:** ${now.toLocaleString()}`,
      `**Observed at:** ${observedAt ? new Date(observedAt).toLocaleString() : "—"}`,
      "",
      "## Incident",
      "",
      `- **Cluster:** ${cluster || "—"}`,
      `- **Namespace:** ${namespace || "—"}`,
      `- **Pod:** ${podName || "—"}`,
      `- **Pod status:** ${status}`,
      `- **Restart count:** ${restarts}`,
      `- **Severity:** ${severityLabel}`,
      "",
      "## Kubernetes Evidence",
      "",
      "### Pod status",
      "",
      evidence?.kubernetes?.pod_status
        ? "```\n" + evidence.kubernetes.pod_status.trimEnd() + "\n```"
        : "_Pod status unavailable._",
      "",
      "### Pod details",
      "",
      evidence?.kubernetes?.pod_details
        ? "```\n" + evidence.kubernetes.pod_details.trimEnd() + "\n```"
        : "_Pod details unavailable._",
      "",
      "### Events",
      "",
      evidence?.kubernetes?.events
        ? "```\n" + evidence.kubernetes.events.trimEnd() + "\n```"
        : "_Pod events unavailable._",
      "",
      "### Relevant log signals",
      "",
      (evidence?.kubernetes?.log_analysis?.relevant_lines || []).length
        ? evidence.kubernetes.log_analysis.relevant_lines
            .map(
              (e) =>
                `- [${e.container || "?"}${e.previous ? "/previous" : ""}][${e.signal}]${e.ts ? ` ${e.ts}` : ""} ${e.line}`
            )
            .join("\n")
        : "_No ERROR / exception / connection / timeout signals found._",
      "",
      "### Timeline",
      "",
      (evidence?.kubernetes?.timeline || []).length
        ? evidence.kubernetes.timeline
            .map((t) => `- ${t.ts || "—"} [${t.source}] ${t.text}`)
            .join("\n")
        : "_No timeline entries._",
      "",
      "## Metrics",
      "",
      "| Metric | Status | Value |",
      "|---|---|---|",
      metricsBlock,
      "",
      "## AI Investigation",
      "",
      ...aiLines,
    ];

    if (investigationMatches && investigation.stdout) {
      lines.push(
        "",
        "## Raw CLI output",
        "",
        "```\n" + investigation.stdout.trimEnd() + "\n```"
      );
    }

    return lines.join("\n");
  }

  function generateReport() {
    if (!namespace || !podName) return;
    setGenerating(true);
    const md = buildReport();
    setReport(md);
    setGenerating(false);
  }

  function downloadReport() {
    if (!report) return;

    const blob = new Blob([report], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");

    link.href = url;
    link.download = `incident-report-${namespace}-${podName}-${stamp}.md`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  const hasTarget = namespace && podName;
  const metricsRowsList = metricsRows();

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Incident Report</h1>
          <p className="page-head__sub">
            Consolidated view of incident details, Kubernetes evidence, metrics
            and the OpenSRE investigation.
          </p>
        </div>

        <div className="page-head__actions">
          <button
            type="button"
            className="btn btn--primary"
            onClick={generateReport}
            disabled={generating || !hasTarget}
          >
            {generating ? (
              <>
                <Loader2 size={15} className="btn__spinner" /> Generating…
              </>
            ) : (
              <>
                <FileText size={15} /> Generate Incident Report
              </>
            )}
          </button>
        </div>
      </div>

      <Card
        title="Incident target"
        subtitle="Workload under review — shared with the AI Analysis page"
      >
        <div className="form-grid">
          <div className="field">
            <label htmlFor="incident-cluster">Cluster</label>
            <select
              id="incident-cluster"
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
            <label htmlFor="incident-namespace">Namespace</label>
            <select
              id="incident-namespace"
              className="select"
              value={namespace}
              onChange={(e) => {
                const next = e.target.value;
                setNamespace(next);
                setPodName(
                  pods.find((pod) => pod.namespace === next)?.name || ""
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
            <label htmlFor="incident-pod">Pod</label>
            <select
              id="incident-pod"
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
        </div>

        {podsLoading && (
          <p
            className="text-muted"
            style={{ marginTop: "var(--space-3)", fontSize: 13 }}
          >
            <Loader2 size={13} className="btn__spinner" /> Loading pods from
            selected cluster…
          </p>
        )}
      </Card>

      {!hasTarget ? (
        <Card title="No incident selected">
          <div className="empty-state">
            <ShieldAlert size={30} />
            <div>
              <strong>Select a pod to investigate</strong>
              <p style={{ marginTop: "var(--space-1)", maxWidth: 440 }}>
                Choose a workload below, or run an investigation from the{" "}
                <Link to="/analysis" className="link">
                  AI Analysis
                </Link>{" "}
                page to pre-select one.
              </p>
            </div>
          </div>
        </Card>
      ) : (
        <>
          <Card
            title="Incident details"
            subtitle="Target workload under investigation"
            actions={
              <Badge tone={severityTone(severity)}>
                <ShieldAlert size={12} /> {severityLabel} severity
              </Badge>
            }
          >
            <div className="incident-meta">
              <div className="report-metric">
                <div className="report-metric__label">Cluster</div>
                <div className="report-metric__value">{cluster || "—"}</div>
              </div>
              <div className="report-metric">
                <div className="report-metric__label">Namespace</div>
                <div className="report-metric__value cell-mono">
                  {namespace}
                </div>
              </div>
              <div className="report-metric">
                <div className="report-metric__label">Pod</div>
                <div className="report-metric__value cell-mono">{podName}</div>
              </div>
              <div className="report-metric">
                <div className="report-metric__label">Pod status</div>
                <div className="report-metric__value">
                  {status === "—" ? (
                    "—"
                  ) : (
                    <Badge tone={podTone(status)}>{status}</Badge>
                  )}
                </div>
              </div>
              <div className="report-metric">
                <div className="report-metric__label">Restart count</div>
                <div className="report-metric__value">
                  {restarts}
                  {restarts > 0 && (
                    <span className="incident-meta__hint"> in {selectedPod?.age || evidenceStatus?.age || "current"} age</span>
                  )}
                </div>
              </div>
              <div className="report-metric">
                <div className="report-metric__label">Observed at</div>
                <div className="report-metric__value">
                  {observedAt ? new Date(observedAt).toLocaleString() : "—"}
                </div>
              </div>
            </div>
          </Card>

          <Card
            title="Kubernetes evidence"
            subtitle="Live state collected from the selected cluster"
            actions={
              evidenceLoading ? (
                <Badge tone="neutral">
                  <Loader2 size={12} className="btn__spinner" /> Collecting…
                </Badge>
              ) : evidenceError ? (
                <Badge tone="danger">
                  <AlertTriangle size={12} /> Unavailable
                </Badge>
              ) : (
                <Badge tone="success">
                  <CheckCircle2 size={12} /> Collected
                </Badge>
              )
            }
          >
            {evidenceLoading ? (
              <div className="stack stack--tight">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} height={44} />
                ))}
              </div>
            ) : evidenceError ? (
              <div className="empty-state">
                <AlertTriangle size={26} />
                <div>
                  <strong>Evidence collection failed</strong>
                  <p style={{ marginTop: "var(--space-1)" }}>{evidenceError}</p>
                </div>
              </div>
            ) : evidence ? (
              <div className="stack">
                <div className="report-grid">
                  <div className="report-metric">
                    <div className="report-metric__label">Pod endpoint</div>
                    <div className="report-metric__value cell-mono">
                      {endpoint || "No exposed port"}
                    </div>
                  </div>
                  <div className="report-metric">
                    <div className="report-metric__label">Status</div>
                    <div className="report-metric__value">
                      {evidenceStatus?.status || "—"}
                    </div>
                  </div>
                  <div className="report-metric">
                    <div className="report-metric__label">Restarts</div>
                    <div className="report-metric__value">
                      {evidenceStatus?.restarts || "—"}
                    </div>
                  </div>
                  <div className="report-metric">
                    <div className="report-metric__label">Ready</div>
                    <div className="report-metric__value">
                      {evidenceStatus?.ready || "—"}
                    </div>
                  </div>
                </div>

                <details className="raw-output">
                  <summary>
                    <Boxes size={14} style={{ verticalAlign: "middle" }} /> Pod
                    details
                  </summary>
                  <pre className="code-block code-block--plain">
                    {evidence.kubernetes?.pod_details ||
                      evidence.kubernetes?.pod_details_error ||
                      "Pod details unavailable."}
                  </pre>
                </details>

                <div className="report-section">
                  <div className="report-section__title">Events</div>
                  {events.length === 0 ? (
                    <p className="text-muted">
                      No Kubernetes events recorded for this pod.
                    </p>
                  ) : (
                    <div className="table-wrap">
                      <table className="table">
                        <thead>
                          <tr>
                            <th>Last seen</th>
                            <th>Type</th>
                            <th>Reason</th>
                            <th>Object</th>
                            <th>Message</th>
                          </tr>
                        </thead>
                        <tbody>
                          {events.map((event, index) => (
                            <tr key={index}>
                              <td className="cell-mono cell-muted">
                                {event.lastSeen}
                              </td>
                              <td>
                                <Badge
                                  tone={
                                    event.type === "Warning"
                                      ? "warning"
                                      : "info"
                                  }
                                >
                                  {event.type}
                                </Badge>
                              </td>
                              <td className="cell-strong">{event.reason}</td>
                              <td className="cell-mono cell-muted">
                                {event.object}
                              </td>
                              <td>{event.message}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                <div className="report-section">
                  <div className="report-section__title">
                    Relevant pod logs{" "}
                    {Object.keys(signalCounts).length > 0 && (
                      <span className="text-muted" style={{ fontWeight: 400 }}>
                        ({Object.entries(signalCounts).map(([s, c]) => `${s}=${c}`).join(", ")})
                      </span>
                    )}
                  </div>
                  {relevantLogLines.length === 0 ? (
                    <p className="text-muted">
                      No ERROR / exception / connection / timeout signals in the
                      collected logs{logAnalysis ? "" : " (log analysis unavailable)"}.
                      {(logAnalysis?.per_container || []).length > 0 && (
                        <> Checked: {logAnalysis.per_container.map((c) => `${c.container}${c.previous_available ? " (+previous)" : ""}`).join(", ")}</>
                      )}
                    </p>
                  ) : (
                    <div className="table-wrap">
                      <table className="table">
                        <thead>
                          <tr>
                            <th>Container</th>
                            <th>Signal</th>
                            <th>Timestamp</th>
                            <th>Line</th>
                          </tr>
                        </thead>
                        <tbody>
                          {relevantLogLines.map((entry, index) => (
                            <tr key={index}>
                              <td className="cell-mono cell-muted">
                                {entry.container || "—"}
                                {entry.previous ? " (prev)" : ""}
                              </td>
                              <td>
                                <Badge tone="danger">{entry.signal}</Badge>
                              </td>
                              <td className="cell-mono cell-muted">
                                {entry.ts || "—"}
                              </td>
                              <td className="cell-mono">{entry.line}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                <div className="report-section">
                  <div className="report-section__title">
                    Recent log tail
                  </div>
                  {logsError && !logsTail && (
                    <p className="text-muted">{logsError}</p>
                  )}
                  {!logsTail && !logsError ? (
                    <p className="text-muted">
                      No log output collected from this pod (the container may
                      exit without logging anything).
                    </p>
                  ) : logsTail ? (
                    <details className="raw-output" open>
                      <summary style={{ cursor: "pointer" }}>
                        Current container logs ({logsTail.split("\n").filter(Boolean).length} lines
                        {logsPreviousAvailable ? " · previous included" : ""})
                      </summary>
                      <pre
                        className="code-block"
                        style={{ maxHeight: 320, overflow: "auto" }}
                      >
                        {logsTail}
                      </pre>
                    </details>
                  ) : null}
                  {logsPrevious && (
                    <details className="raw-output" style={{ marginTop: "var(--space-2)" }}>
                      <summary style={{ cursor: "pointer" }}>
                        Previous container logs (before last restart)
                      </summary>
                      <pre
                        className="code-block"
                        style={{ maxHeight: 320, overflow: "auto" }}
                      >
                        {logsPrevious}
                      </pre>
                    </details>
                  )}
                </div>

                {timeline.length > 0 && (
                  <div className="report-section">
                    <div className="report-section__title">Timeline</div>
                    <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                      {timeline.map((item, index) => (
                        <li key={index} style={{ display: "flex", gap: "var(--space-2)", fontSize: 13, alignItems: "baseline" }}>
                          <span className="cell-mono cell-muted" style={{ whiteSpace: "nowrap" }}>
                            {item.ts || "—"}
                          </span>
                          <Badge tone={item.source === "event" ? "warning" : "danger"}>{item.source}</Badge>
                          <span>{item.text}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ) : (
              <div className="empty-state">
                <Boxes size={26} /> Waiting for evidence…
              </div>
            )}
          </Card>

          <Card
            title="Metrics"
            subtitle="VictoriaMetrics data for the selected pod"
            actions={
              <Badge tone="info">
                <Activity size={12} /> {endpoint || "no endpoint"}
              </Badge>
            }
          >
            <div className="metrics-grid">
              {metricsRowsList.map((row) => (
                <div key={row.key} className="metric-item">
                  <div className="metric-item__head">
                    <div className="metric-item__label">{row.label}</div>
                    {row.status === "ok" ? (
                      <Badge tone="success">Available</Badge>
                    ) : row.status === "empty" ? (
                      <Badge tone="neutral">No data</Badge>
                    ) : (
                      <Badge tone="warning">Unavailable</Badge>
                    )}
                  </div>
                  <div className="metric-item__value">
                    {row.status === "ok" ? row.detail : row.detail}
                  </div>
                </div>
              ))}
            </div>

            {metricsRowsList.every((row) => row.status === "unavailable") && (
              <p className="text-muted" style={{ marginTop: "var(--space-3)", fontSize: 13 }}>
                VictoriaMetrics is not reachable or no metrics are scraped for
                this instance. No synthetic data is shown.
              </p>
            )}
          </Card>

          <Card
            title="AI investigation"
            subtitle="OpenSRE root-cause analysis"
            actions={
              investigationMatches ? (
                reportObj ? (
                  <Badge tone="success">
                    <CheckCircle2 size={12} /> Analyzed
                  </Badge>
                ) : (
                  <Badge tone="warning">
                    <AlertTriangle size={12} /> Inconclusive
                  </Badge>
                )
              ) : (
                <Badge tone="neutral">Not run</Badge>
              )
            }
          >
            {aiLoading ? (
              <div className="empty-state">
                <Loader2 size={26} className="btn__spinner" />
                <div>
                  <strong>OpenSRE is investigating…</strong>
                  <p style={{ marginTop: "var(--space-1)" }}>
                    This collects live evidence and can take a minute.
                  </p>
                </div>
              </div>
            ) : investigationMatches && investigation.error ? (
              <div className="empty-state">
                <AlertTriangle size={26} />
                <div>
                  <strong>Investigation failed</strong>
                  <p style={{ marginTop: "var(--space-1)" }}>
                    {investigation.error}
                  </p>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    style={{ marginTop: "var(--space-3)" }}
                    onClick={runInvestigation}
                    disabled={aiLoading}
                  >
                    <RefreshCw size={15} /> Retry investigation
                  </button>
                </div>
              </div>
            ) : investigationMatches && reportObj ? (
              <>
                <div className="report-cards">
                  <div className="report-card report-card--root">
                    <div className="report-card__icon">
                      <Target size={18} />
                    </div>
                    <div>
                      <div className="report-card__label">Root cause</div>
                      <div className="report-card__value">
                        {reportObj.root_cause || "Not determined"}
                      </div>
                      <div className="report-card__meta">
                        <Badge tone={reportObj.is_noise ? "warning" : "info"}>
                          {reportObj.is_noise
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
                      <div className="report-card__label">Validity score</div>
                      <div className="report-card__value">
                        {validityScore != null ? `${validityScore}%` : "—"}
                      </div>
                      <div className="score-bar">
                        <div
                          className={`score-bar__track score-bar__track--${validityScore >= 75 ? "success" : validityScore >= 40 ? "warning" : "danger"}`}
                        >
                          <div
                            className="score-bar__fill"
                            style={{ width: `${validityScore ?? 0}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="report-card report-card--actions">
                    <div className="report-card__icon">
                      <Target size={18} />
                    </div>
                    <div>
                      <div className="report-card__label">
                        Recommended remediation
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

                {reportObj.summary && (
                  <div className="report-section">
                    <div className="report-section__title">Summary</div>
                    <div className="report-section__body">
                      {stripAnsi(reportObj.summary)}
                    </div>
                  </div>
                )}

                {reportObj.impact && (
                  <div className="report-section">
                    <div className="report-section__title">Impact</div>
                    <div className="report-section__body">
                      {stripAnsi(reportObj.impact)}
                    </div>
                  </div>
                )}

                {reportObj.problem_md && (
                    <ProblemFraming
                      markdown={reportObj.problem_md}
                      cluster={cluster}
                    />
                  )}

                {reportObj.report && (
                  <ReportFindings markdown={reportObj.report} />
                )}

                {reportObj.evidence && (
                  <div className="report-section">
                    <div className="report-section__title">
                      Evidence supporting the RCA
                    </div>
                    {Array.isArray(reportObj.evidence) ? (
                      <ul className="action-list">
                        {reportObj.evidence.map((item, index) => (
                          <li key={index}>{item}</li>
                        ))}
                      </ul>
                    ) : (
                      <div className="report-section__body">
                        {stripAnsi(reportObj.evidence)}
                      </div>
                    )}
                  </div>
                )}

                {investigation.stdout && (
                  <details className="raw-output">
                    <summary>
                      <Terminal
                        size={14}
                        style={{ verticalAlign: "middle" }}
                      />{" "}
                      Raw CLI output
                    </summary>
                    <pre className="code-block">
                      {investigation.stdout}
                    </pre>
                  </details>
                )}
              </>
            ) : (
              <div className="empty-state">
                <Search size={26} />
                <div>
                  <strong>No investigation for this pod yet</strong>
                  <p style={{ marginTop: "var(--space-1)", maxWidth: 440 }}>
                    Run an OpenSRE root-cause investigation to populate the
                    incident with AI-driven findings.
                  </p>
                  <button
                    type="button"
                    className="btn btn--primary"
                    style={{ marginTop: "var(--space-3)" }}
                    onClick={runInvestigation}
                    disabled={aiLoading}
                  >
                    {aiLoading ? (
                      <>
                        <Loader2 size={15} className="btn__spinner" />
                        Investigating…
                      </>
                    ) : (
                      <>
                        <Search size={15} /> Run investigation
                      </>
                    )}
                  </button>
                </div>
              </div>
            )}
          </Card>

          {(investigationHistory.length > 0 || historyLoading) && (
            <Card
              title="Investigation history"
              subtitle={
                historyLoading && investigationHistory.length === 0
                  ? "Loading saved investigations…"
                  : `${investigationHistory.length} past investigation${investigationHistory.length === 1 ? "" : "s"} stored (persisted server-side)`
              }
              actions={
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={clearHistory}
                >
                  Clear history
                </button>
              }
            >
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                {investigationHistory.map((entry, idx) => {
                  const isExpanded = expandedHistoryIdx === idx;
                  const rootCause = entry.report?.root_cause;
                  const validity = entry.report?.validity_score != null
                    ? Math.round(entry.report.validity_score * 100)
                    : null;
                  const ts = new Date(entry.timestamp);
                  const label =
                    entry.target_label ||
                    (entry.namespace && entry.pod
                      ? `${entry.namespace}/${entry.pod}`
                      : entry.pod || entry.namespace || "Investigation");
                  return (
                    <div
                      key={entry.id}
                      style={{
                        border: "1px solid var(--border)",
                        borderRadius: 6,
                        overflow: "hidden",
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => toggleHistoryEntry(idx, investigationHistory)}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "var(--space-2)",
                          width: "100%",
                          padding: "var(--space-3)",
                          background: isExpanded ? "var(--bg-muted)" : "transparent",
                          border: "none",
                          cursor: "pointer",
                          textAlign: "left",
                          fontSize: 13,
                        }}
                      >
                        <Badge tone={rootCause ? "success" : "warning"}>
                          {rootCause ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />}
                          {" "}{rootCause ? "Analyzed" : "Inconclusive"}
                        </Badge>
                        <span style={{ fontWeight: 500 }}>{label}</span>
                        {entry.source === "chat" && (
                          <Badge tone="info">Chat</Badge>
                        )}
                        <span className="text-muted" style={{ marginLeft: "auto" }}>
                          {ts.toLocaleDateString()} {ts.toLocaleTimeString()}
                        </span>
                        {validity != null && (
                          <Badge tone={validity >= 75 ? "success" : validity >= 40 ? "warning" : "danger"}>
                            {validity}%
                          </Badge>
                        )}
                        <span
                          role="button"
                          tabIndex={0}
                          title="Delete this entry"
                          onClick={(e) => {
                            e.stopPropagation();
                            deleteHistoryEntry(entry.id);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.stopPropagation();
                              deleteHistoryEntry(entry.id);
                            }
                          }}
                          style={{
                            cursor: "pointer",
                            color: "var(--muted)",
                            display: "inline-flex",
                            padding: 2,
                          }}
                        >
                          ×
                        </span>
                      </button>
                      {isExpanded && (
                        <div style={{
                          padding: "0 var(--space-3) var(--space-3)",
                          borderTop: "1px solid var(--border)",
                          fontSize: 13,
                        }}>
                          {entry.question && (
                            <div style={{ marginBottom: "var(--space-2)" }} className="text-muted">
                              <strong>Question:</strong> {entry.question}
                            </div>
                          )}
                          {rootCause && (
                            <div style={{ marginBottom: "var(--space-2)" }}>
                              <strong>Root cause:</strong> {rootCause}
                            </div>
                          )}
                          {entry.report?.summary && (
                            <div style={{ marginBottom: "var(--space-2)" }}>
                              <strong>Summary:</strong> {stripAnsi(entry.report.summary)}
                            </div>
                          )}
                          {entry.report?.impact && (
                            <div style={{ marginBottom: "var(--space-2)" }}>
                              <strong>Impact:</strong> {entry.report.impact}
                            </div>
                          )}
                          {entry.report?.evidence && (
                            <div style={{ marginBottom: "var(--space-2)" }}>
                              <strong>Evidence:</strong>
                              {Array.isArray(entry.report.evidence) ? (
                                <ul style={{ margin: "var(--space-1) 0 0 var(--space-4)" }}>
                                  {entry.report.evidence.map((item, i) => (
                                    <li key={i}>{item}</li>
                                  ))}
                                </ul>
                              ) : (
                                <span> {entry.report.evidence}</span>
                              )}
                            </div>
                          )}
                          {entry.stdout && (
                            <details className="raw-output" style={{ marginTop: "var(--space-2)" }}>
                              <summary>
                                <Terminal size={14} style={{ verticalAlign: "middle" }} /> Raw CLI output
                              </summary>
                              <pre className="code-block">{entry.stdout}</pre>
                            </details>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </Card>
          )}
        </>
      )}

      {report && (
        <Card
          title="Consolidated incident report"
          subtitle="Generated from the displayed incident details, evidence, metrics and AI investigation"
          actions={
            <button
              type="button"
              className="btn btn--ghost"
              onClick={downloadReport}
            >
              <Download size={15} /> Download Report
            </button>
          }
        >
          <pre className="code-block code-block--plain report-output">
            {report}
          </pre>
        </Card>
      )}
    </>
  );
}