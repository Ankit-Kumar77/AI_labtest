import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import api from "../api/api";
import Card from "../components/Card";
import Badge from "../components/Badge";
import Skeleton from "../components/Skeleton";
import { extractReport, stripAnsi, podTone, nodeTone, readyTone } from "../utils/opensre";
import {
  RefreshCw,
  Search,
  Loader2,
  Cpu,
  Boxes,
  AlertTriangle,
  CheckCircle2,
  Terminal,
  FileText,
  Zap,
  RotateCcw,
  Target,
} from "lucide-react";

export default function Kubernetes() {
  const [nodes, setNodes] = useState([]);
  const [pods, setPods] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const [selectedPod, setSelectedPod] = useState(null);
  const [investigation, setInvestigation] = useState(null);
  const [investigating, setInvestigating] = useState(false);

  // --- Node failure demo ---
  const [nodeDemoPhase, setNodeDemoPhase] = useState("idle");
  const [nodeDemoLoading, setNodeDemoLoading] = useState(false);
  const [nodeDemoFault, setNodeDemoFault] = useState(null);
  const [nodeDemoInvestigation, setNodeDemoInvestigation] = useState(null);
  const [nodeDemoRecovery, setNodeDemoRecovery] = useState(null);
  const [nodeDemoError, setNodeDemoError] = useState(null);

  // --- Unhealthy pod demo ---
  const [podDemoPhase, setPodDemoPhase] = useState("idle");
  const [podDemoLoading, setPodDemoLoading] = useState(false);
  const [podDemoFault, setPodDemoFault] = useState(null);
  const [podDemoInvestigation, setPodDemoInvestigation] = useState(null);
  const [podDemoRecovery, setPodDemoRecovery] = useState(null);
  const [podDemoError, setPodDemoError] = useState(null);

  const load = useCallback(async () => {
    const [nodeRes, podRes] = await Promise.all([
      api.get("/kubernetes/nodes"),
      api.get("/kubernetes/pods"),
    ]);

    const splitLines = (output) =>
      (output || "")
        .split("\n")
        .slice(1)
        .map((line) => line.trim())
        .filter(Boolean);

    return {
      nodes: splitLines(nodeRes.data.stdout),
      pods: splitLines(podRes.data.stdout),
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    load().then(
      ({ nodes: nodeLines, pods: podLines }) => {
        if (cancelled) return;
        setNodes(nodeLines);
        setPods(podLines);
        setLoading(false);
      },
      (err) => {
        console.error(err);
        if (!cancelled) setLoading(false);
      }
    );

    return () => {
      cancelled = true;
    };
  }, [load]);

  async function investigatePod(namespace, podName) {
    setSelectedPod(`${namespace}/${podName}`);
    setInvestigation(null);
    setInvestigating(true);

    window.sessionStorage.setItem("opensre:namespace", JSON.stringify(namespace));
    window.sessionStorage.setItem("opensre:pod", JSON.stringify(podName));
    window.sessionStorage.setItem(
      "opensre:investigationTarget",
      JSON.stringify(`${namespace}/${podName}`)
    );

    try {
      const response = await api.get(
        `/opensre/investigate/pod/${namespace}/${podName}`
      );

      const data = response.data;

      if (!data.success) {
        setInvestigation({
          error: data.stderr || "OpenSRE investigation failed.",
        });
      } else {
        const stdout = stripAnsi(data.stdout || "");
        const report = extractReport(stdout);
        setInvestigation({ stdout, report });
        window.sessionStorage.setItem(
          "opensre:investigation",
          JSON.stringify({ stdout, report })
        );
      }
    } catch (err) {
      console.error(err);
      setInvestigation({ error: err.message });
    } finally {
      setInvestigating(false);
    }
  }

  async function investigateNode(nodeName) {
    setSelectedPod(`node/${nodeName}`);
    setInvestigation(null);
    setInvestigating(true);

    window.sessionStorage.setItem("opensre:node", JSON.stringify(nodeName));

    try {
      const response = await api.get(
        `/opensre/investigate/node/${encodeURIComponent(nodeName)}`
      );

      const data = response.data;

      if (!data.success) {
        setInvestigation({
          error: data.stderr || "OpenSRE investigation failed.",
        });
      } else {
        const stdout = stripAnsi(data.stdout || "");
        const report = extractReport(stdout);
        setInvestigation({ stdout, report });
        window.sessionStorage.setItem(
          "opensre:investigation",
          JSON.stringify({ stdout, report })
        );
      }
    } catch (err) {
      console.error(err);
      setInvestigation({ error: err.message });
    } finally {
      setInvestigating(false);
    }
  }

  // --- Node failure demo handlers ---
  const handleNodeFail = async () => {
    setNodeDemoLoading(true);
    setNodeDemoError(null);
    setNodeDemoInvestigation(null);
    setNodeDemoRecovery(null);
    try {
      const res = await api.post("/demo/node-failure/fail", {
        node: "opensre-demo-worker",
        context: "kind-opensre-demo",
      });
      if (res.data.success) {
        setNodeDemoPhase("failed");
        setNodeDemoFault(res.data.fault);
        load().then(({ nodes: nl, pods: pl }) => {
          setNodes(nl);
          setPods(pl);
        });
      } else {
        setNodeDemoError("Failed to inject fault");
      }
    } catch (e) {
      setNodeDemoError(e.message);
    } finally {
      setNodeDemoLoading(false);
    }
  };

  const handleNodeRefail = async () => {
    setNodeDemoLoading(true);
    setNodeDemoError(null);
    setNodeDemoInvestigation(null);
    setNodeDemoRecovery(null);
    try {
      const res = await api.post("/demo/node-failure/re-fail", {
        node: "opensre-demo-worker",
        context: "kind-opensre-demo",
      });
      if (res.data.success) {
        setNodeDemoPhase("failed");
        setNodeDemoFault(res.data.fault);
        load().then(({ nodes: nl, pods: pl }) => {
          setNodes(nl);
          setPods(pl);
        });
      } else {
        setNodeDemoError("Failed to re-fail node");
      }
    } catch (e) {
      setNodeDemoError(e.message);
    } finally {
      setNodeDemoLoading(false);
    }
  };

  const handleNodeInvestigate = async () => {
    setNodeDemoLoading(true);
    setNodeDemoError(null);
    try {
      const res = await api.post("/demo/node-failure/investigate", {
        node: "opensre-demo-worker",
        context: "kind-opensre-demo",
      });
      if (res.data.success) {
        setNodeDemoPhase("investigated");
        setNodeDemoInvestigation(res.data.opensre);
      } else {
        setNodeDemoError("Investigation failed");
      }
    } catch (e) {
      setNodeDemoError(e.message);
    } finally {
      setNodeDemoLoading(false);
    }
  };

  const handleNodeRecover = async () => {
    setNodeDemoLoading(true);
    setNodeDemoError(null);
    try {
      const res = await api.post("/demo/node-failure/recover", {
        node: "opensre-demo-worker",
        context: "kind-opensre-demo",
      });
      if (res.data.success) {
        setNodeDemoPhase("recovered");
        setNodeDemoRecovery(res.data.recovery);
        setTimeout(() => {
          load().then(({ nodes: nl, pods: pl }) => {
            setNodes(nl);
            setPods(pl);
          });
        }, 2000);
      } else {
        setNodeDemoError("Recovery failed");
      }
    } catch (e) {
      setNodeDemoError(e.message);
    } finally {
      setNodeDemoLoading(false);
    }
  };

  const handleNodeReset = () => {
    setNodeDemoPhase("idle");
    setNodeDemoFault(null);
    setNodeDemoInvestigation(null);
    setNodeDemoRecovery(null);
    setNodeDemoError(null);
    load().then(({ nodes: nl, pods: pl }) => {
      setNodes(nl);
      setPods(pl);
    });
  };

  // --- Unhealthy pod demo handlers ---

  const handlePodDeploy = async () => {
    setPodDemoLoading(true);
    setPodDemoError(null);
    try {
      const res = await api.post("/demo/unhealthy-pod/deploy");
      if (res.data.success) {
        setPodDemoPhase("deployed");
        setPodDemoFault({ pod: res.data.pod, namespace: res.data.namespace });
        load().then(({ nodes: nl, pods: pl }) => {
          setNodes(nl);
          setPods(pl);
        });
      } else {
        setPodDemoError("Failed to deploy unhealthy pod");
      }
    } catch (e) {
      setPodDemoError(e.message);
    } finally {
      setPodDemoLoading(false);
    }
  };

  const handlePodInvestigate = async () => {
    setPodDemoLoading(true);
    setPodDemoError(null);
    try {
      const res = await api.post("/demo/node-failure/investigate", {
        node: "opensre-demo-worker",
        context: "kind-opensre-demo",
      });
      if (res.data.success) {
        setPodDemoPhase("investigated");
        setPodDemoInvestigation(res.data.opensre);
      } else {
        setPodDemoError("Investigation failed");
      }
    } catch (e) {
      setPodDemoError(e.message);
    } finally {
      setPodDemoLoading(false);
    }
  };

  const handlePodRecover = async () => {
    setPodDemoLoading(true);
    setPodDemoError(null);
    try {
      const res = await api.post("/demo/unhealthy-pod/delete");
      if (res.data.success) {
        setPodDemoPhase("recovered");
        setPodDemoRecovery({ action: "deleted", pod: res.data.pod });
        setTimeout(() => {
          load().then(({ nodes: nl, pods: pl }) => {
            setNodes(nl);
            setPods(pl);
          });
        }, 2000);
      } else {
        setPodDemoError("Recovery failed");
      }
    } catch (e) {
      setPodDemoError(e.message);
    } finally {
      setPodDemoLoading(false);
    }
  };

  const handlePodReset = () => {
    setPodDemoPhase("idle");
    setPodDemoFault(null);
    setPodDemoInvestigation(null);
    setPodDemoRecovery(null);
    setPodDemoError(null);
  };

  const nodeRows = nodes.map((line) => {
    const cols = line.split(/\s+/);
    return {
      name: cols[0],
      status: cols[1],
      role: cols[2],
      version: cols[4],
      ip: cols[5],
    };
  });

  const podRows = pods.map((line) => {
    const cols = line.split(/\s+/);
    return {
      namespace: cols[0],
      name: cols[1],
      ready: cols[2],
      status: cols[3],
      restarts: cols[4],
      age: cols[5],
    };
  });

  const report = investigation?.report;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Kubernetes</h1>
          <p className="page-head__sub">
            Live inventory of nodes and pods across the cluster.
          </p>
        </div>

        <div className="page-head__actions">
          <button
            type="button"
            className="btn btn--ghost"
            disabled={refreshing}
            onClick={() => {
              setRefreshing(true);
              load()
                .then(({ nodes: nodeLines, pods: podLines }) => {
                  setNodes(nodeLines);
                  setPods(podLines);
                })
                .catch((err) => console.error(err))
                .finally(() => setRefreshing(false));
            }}
          >
            <RefreshCw size={15} className={refreshing ? "btn__spinner" : ""} />{" "}
            Refresh
          </button>
        </div>
      </div>

      <Card
        title="Nodes"
        subtitle="Cluster node inventory"
        actions={
          <Badge tone="info">
            <Cpu size={13} /> {loading ? "…" : nodes.length}
          </Badge>
        }
      >
        {loading ? (
          <div className="stack stack--tight">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} height={44} />
            ))}
          </div>
        ) : nodeRows.length === 0 ? (
          <div className="empty-state">
            <Cpu size={28} /> No nodes detected.
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Status</th>
                  <th>Role</th>
                  <th>Version</th>
                  <th>Internal IP</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {nodeRows.map((node, index) => (
                  <tr key={index}>
                    <td className="cell-strong cell-mono">{node.name}</td>
                    <td>
                      <Badge tone={nodeTone(node.status)}>{node.status}</Badge>
                    </td>
                    <td>{node.role || "—"}</td>
                    <td className="cell-mono cell-muted">{node.version || "—"}</td>
                    <td className="cell-mono cell-muted">{node.ip || "—"}</td>
                    <td className="cell-right">
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        onClick={() => investigateNode(node.name)}
                        disabled={investigating}
                      >
                        {investigating && selectedPod === `node/${node.name}` ? (
                          <Loader2 size={13} className="btn__spinner" />
                        ) : (
                          <Search size={13} />
                        )}{" "}
                        Investigate
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title="Pods"
        subtitle="All workloads in the cluster"
        actions={
          <Badge tone="info">
            <Boxes size={13} /> {loading ? "…" : pods.length}
          </Badge>
        }
      >
        {loading ? (
          <div className="stack stack--tight">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} height={44} />
            ))}
          </div>
        ) : podRows.length === 0 ? (
          <div className="empty-state">
            <Boxes size={28} /> No pods detected.
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Namespace</th>
                  <th>Pod</th>
                  <th>Ready</th>
                  <th>Status</th>
                  <th>Restarts</th>
                  <th>Age</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {podRows.map((pod, index) => (
                  <tr key={index}>
                    <td className="cell-mono cell-muted">{pod.namespace}</td>
                    <td className="cell-strong cell-mono">{pod.name}</td>
                    <td>
                      <Badge tone={readyTone(pod.ready)}>{pod.ready || "—"}</Badge>
                    </td>
                    <td>
                      <Badge tone={podTone(pod.status)}>{pod.status || "—"}</Badge>
                    </td>
                    <td className="cell-muted">{pod.restarts || "—"}</td>
                    <td className="cell-muted">{pod.age || "—"}</td>
                    <td className="cell-end">
                      <button
                        type="button"
                        className="btn btn--primary btn--sm"
                        onClick={() => investigatePod(pod.namespace, pod.name)}
                        disabled={investigating}
                      >
                        {investigating &&
                        selectedPod === `${pod.namespace}/${pod.name}` ? (
                          <>
                            <Loader2 size={14} className="btn__spinner" />
                            Analyzing…
                          </>
                        ) : (
                          <>
                            <Search size={14} /> Investigate
                          </>
                        )}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {selectedPod && investigation && (
        <Card
          title="OpenSRE Investigation"
          subtitle={`Target · ${selectedPod}`}
          actions={
            <>
              <Link to="/incident" className="btn btn--ghost btn--sm">
                <FileText size={13} /> View incident report
              </Link>
              <Badge tone={report ? "success" : "warning"}>
                {report ? (
                  <>
                    <CheckCircle2 size={13} /> Complete
                  </>
                ) : (
                  <>
                    <AlertTriangle size={13} /> Inconclusive
                  </>
                )}
              </Badge>
            </>
          }
        >
          {investigation.error ? (
            <div className="empty-state">
              <AlertTriangle size={28} />
              <div>
                <strong>Investigation failed</strong>
                <p style={{ marginTop: "var(--space-1)" }}>{investigation.error}</p>
              </div>
            </div>
          ) : (
            <>
              {report && (
                <>
                  <div className="report-grid">
                    <div className="report-metric">
                      <div className="report-metric__label">Root cause</div>
                      <div className="report-metric__value">
                        {report.root_cause || "Not determined"}
                      </div>
                    </div>
                    <div className="report-metric">
                      <div className="report-metric__label">Validity score</div>
                      <div className="report-metric__value">
                        {report.validity_score != null
                          ? `${Math.round(report.validity_score * 100)}%`
                          : "—"}
                      </div>
                    </div>
                    <div className="report-metric">
                      <div className="report-metric__label">Classification</div>
                      <div className="report-metric__value">
                        {report.is_noise ? "Noise / low signal" : "Incident"}
                      </div>
                    </div>
                  </div>

                  {report.problem_md && (
                    <div className="report-section">
                      <div className="report-section__title">Problem framing</div>
                      <div className="report-section__body">
                        {stripAnsi(report.problem_md)}
                      </div>
                    </div>
                  )}

                  {report.report && (
                    <div className="report-section">
                      <div className="report-section__title">Findings</div>
                      <div className="report-section__body">
                        {stripAnsi(report.report)}
                      </div>
                    </div>
                  )}
                </>
              )}

              {investigation.stdout && (
                <details className="raw-output">
                  <summary>
                    <Terminal size={14} style={{ verticalAlign: "middle" }} />{" "}
                    Raw CLI output
                  </summary>
                  <pre className="code-block">{investigation.stdout}</pre>
                </details>
              )}
            </>
          )}
        </Card>
      )}

      {/* ---- Node failure demo (staged flow) ---- */}
      <Card
        title="Unhealthy node demo"
        subtitle={
          nodeDemoPhase === "idle"
            ? "Step 1: Worker node is cordoned + tainted + stress pod running"
            : nodeDemoPhase === "failed"
            ? "Step 2: Node is SchedulingDisabled \u2014 run OpenSRE investigation"
            : nodeDemoPhase === "investigated"
            ? "Step 3: RCA collected \u2014 recover the node"
            : nodeDemoPhase === "recovered"
            ? "Node recovered \u2014 click Re-fail to make it unhealthy again"
            : "Done \u2014 node recovered"
        }
        actions={
          nodeDemoPhase === "idle" ? (
            <button className="btn btn--primary" onClick={handleNodeInvestigate} disabled={nodeDemoLoading}>
              {nodeDemoLoading ? <Loader2 size={15} className="btn__spinner" /> : <Search size={15} />}
              {" "}1. Investigate with OpenSRE
            </button>
          ) : nodeDemoPhase === "failed" ? (
            <button className="btn btn--primary" onClick={handleNodeInvestigate} disabled={nodeDemoLoading}>
              {nodeDemoLoading ? <Loader2 size={15} className="btn__spinner" /> : <Search size={15} />}
              {" "}1. Investigate with OpenSRE
            </button>
          ) : nodeDemoPhase === "investigated" ? (
            <button className="btn btn--primary" onClick={handleNodeRecover} disabled={nodeDemoLoading}>
              {nodeDemoLoading ? <Loader2 size={15} className="btn__spinner" /> : <RotateCcw size={15} />}
              {" "}2. Recover
            </button>
          ) : nodeDemoPhase === "recovered" ? (
            <button className="btn btn--primary" onClick={handleNodeRefail} disabled={nodeDemoLoading}>
              {nodeDemoLoading ? <Loader2 size={15} className="btn__spinner" /> : <Zap size={15} />}
              {" "}Re-fail node
            </button>
          ) : (
            <button className="btn btn--ghost btn--sm" onClick={handleNodeReset}>
              Reset demo
            </button>
          )
        }
      >
        {nodeDemoError && (
          <div className="alert alert--danger" style={{ marginBottom: "var(--space-3)" }}>
            {nodeDemoError}
          </div>
        )}

        <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", marginBottom: "var(--space-3)" }}>
          <Badge tone="danger">
            Node unhealthy
          </Badge>
          <Badge tone="danger">
            SchedulingDisabled
          </Badge>
          <Badge tone="warning">
            demo-unhealthy taint
          </Badge>
          {nodeDemoPhase === "investigated" && <Badge tone="success">RCA ready</Badge>}
          {nodeDemoPhase === "recovered" && <Badge tone="success">Recovered</Badge>}
        </div>

        {nodeDemoFault && (
          <div style={{ marginBottom: "var(--space-3)", fontSize: 13 }}>
            <strong>Fault:</strong>{" "}
            <Badge tone="danger">{nodeDemoFault.action}</Badge>
            <span className="text-muted" style={{ marginLeft: "var(--space-2)" }}>
              stress pod {nodeDemoFault.stress_pod} deployed to {nodeDemoFault.namespace}
            </span>
          </div>
        )}

        {nodeDemoRecovery && (
          <div style={{ marginBottom: "var(--space-3)", fontSize: 13 }}>
            <strong>Recovery:</strong>{" "}
            <Badge tone="success">{nodeDemoRecovery.action}</Badge>
            <span className="text-muted" style={{ marginLeft: "var(--space-2)" }}>
              stress pod deleted, node uncordoned
            </span>
          </div>
        )}

        {nodeDemoInvestigation && (
          <div style={{
            padding: "var(--space-3)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            background: "var(--bg-muted)",
            marginTop: "var(--space-3)",
          }}>
            <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: "var(--space-2)", display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
              <Target size={13} /> OpenSRE Investigation Result
            </div>
            <pre style={{
              fontFamily: "monospace",
              fontSize: 12,
              whiteSpace: "pre-wrap",
              maxHeight: 350,
              overflow: "auto",
              background: "var(--bg-tertiary)",
              borderRadius: "var(--radius-sm)",
              padding: "var(--space-3)",
            }}>
              {nodeDemoInvestigation.stdout || nodeDemoInvestigation.stderr || "No output"}
            </pre>
          </div>
        )}
      </Card>

      {/* ---- Unhealthy pod demo (staged flow) ---- */}
      <Card
        title="Unhealthy pod demo"
        subtitle={
          podDemoPhase === "idle"
            ? "Step 1: Deploy a crashing pod (order-service can't reach database)"
            : podDemoPhase === "deployed"
            ? "Step 2: Pod is CrashLoopBackOff \u2014 run OpenSRE investigation"
            : podDemoPhase === "investigated"
            ? "Step 3: RCA collected \u2014 delete the bad pod"
            : "Done \u2014 unhealthy pod removed"
        }
        actions={
          podDemoPhase === "idle" ? (
            <button className="btn btn--primary" onClick={handlePodDeploy} disabled={podDemoLoading}>
              {podDemoLoading ? <Loader2 size={15} className="btn__spinner" /> : <Zap size={15} />}
              {" "}1. Deploy bad pod
            </button>
          ) : podDemoPhase === "deployed" ? (
            <button className="btn btn--primary" onClick={handlePodInvestigate} disabled={podDemoLoading}>
              {podDemoLoading ? <Loader2 size={15} className="btn__spinner" /> : <Search size={15} />}
              {" "}2. Investigate with OpenSRE
            </button>
          ) : podDemoPhase === "investigated" ? (
            <button className="btn btn--primary" onClick={handlePodRecover} disabled={podDemoLoading}>
              {podDemoLoading ? <Loader2 size={15} className="btn__spinner" /> : <RotateCcw size={15} />}
              {" "}3. Recover
            </button>
          ) : (
            <button className="btn btn--ghost btn--sm" onClick={handlePodReset}>
              Reset demo
            </button>
          )
        }
      >
        {podDemoError && (
          <div className="alert alert--danger" style={{ marginBottom: "var(--space-3)" }}>
            {podDemoError}
          </div>
        )}

        <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", marginBottom: "var(--space-3)" }}>
          <Badge tone={podDemoPhase === "idle" ? "info" : "success"}>
            {podDemoPhase === "idle" ? "No bad pod" : "Pod running"}
          </Badge>
          <Badge tone={podDemoPhase === "deployed" || podDemoPhase === "investigated" || podDemoPhase === "recovered" ? "danger" : "info"}>
            {podDemoPhase === "deployed" || podDemoPhase === "investigated" || podDemoPhase === "recovered" ? "CrashLoopBackOff" : "Healthy"}
          </Badge>
          {podDemoPhase === "investigated" && <Badge tone="success">RCA ready</Badge>}
          {podDemoPhase === "recovered" && <Badge tone="success">Recovered</Badge>}
        </div>

        {podDemoFault && (
          <div style={{ marginBottom: "var(--space-3)", fontSize: 13 }}>
            <strong>Fault:</strong>{" "}
            <Badge tone="danger">pod crashed</Badge>
            <span className="text-muted" style={{ marginLeft: "var(--space-2)" }}>
              {podDemoFault.pod} in {podDemoFault.namespace}
            </span>
          </div>
        )}

        {podDemoRecovery && (
          <div style={{ marginBottom: "var(--space-3)", fontSize: 13 }}>
            <strong>Recovery:</strong>{" "}
            <Badge tone="success">{podDemoRecovery.action}</Badge>
            <span className="text-muted" style={{ marginLeft: "var(--space-2)" }}>
              {podDemoRecovery.pod} deleted
            </span>
          </div>
        )}

        {podDemoInvestigation && (
          <div style={{
            padding: "var(--space-3)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            background: "var(--bg-muted)",
            marginTop: "var(--space-3)",
          }}>
            <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: "var(--space-2)", display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
              <Target size={13} /> OpenSRE Investigation Result
            </div>
            <pre style={{
              fontFamily: "monospace",
              fontSize: 12,
              whiteSpace: "pre-wrap",
              maxHeight: 350,
              overflow: "auto",
              background: "var(--bg-tertiary)",
              borderRadius: "var(--radius-sm)",
              padding: "var(--space-3)",
            }}>
              {podDemoInvestigation.stdout || podDemoInvestigation.stderr || "No output"}
            </pre>
          </div>
        )}
      </Card>
    </>
  );
}
