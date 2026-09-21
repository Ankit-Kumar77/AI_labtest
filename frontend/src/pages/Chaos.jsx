import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api, { dbInvestigationApi, nginxDemoApi, corednsDemoApi, elkDemoApi, elkApi } from "../api/api";
import Card from "../components/Card";
import Badge from "../components/Badge";
import {
  Loader2,
  Database,
  HardDrive,
  Server,
  Fuel,
  RotateCcw,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  Terminal,
  Timer,
  Activity,
  ListChecks,
  Copy,
  Trash2,
  AlertCircle,
  Globe,
  ChevronDown,
  Search,
} from "lucide-react";

const FAILURES = [
  // Database failures
  { action: "aerospike-down", label: "Aerospike Unavailable", desc: "scale Aerospike StatefulSet to 0 (K8s)", icon: Database, risk: "db", category: "database" },
  { action: "yugabyte-down", label: "YugabyteDB Unavailable", desc: "scale YugabyteDB StatefulSet to 0 (K8s)", icon: HardDrive, risk: "db", category: "database" },
  { action: "yugabyte-latency", label: "YugabyteDB High Latency", desc: "induce slow queries via heavy workload", icon: Timer, risk: "db", category: "database" },
  { action: "yugabyte-connection-pressure", label: "YugabyteDB Connection Pressure", desc: "simulate connection pool exhaustion", icon: Activity, risk: "db", category: "database" },
  { action: "aerospike-latency", label: "Aerospike High Latency", desc: "induce slow operations via heavy workload", icon: Timer, risk: "db", category: "database" },
  // Pod failures (shown in "Pod failures" group below)
  { action: "pod-crash", label: "Pod crash", desc: "crash the catalog-api container (real restart)", icon: Server, risk: "pod", category: "pod" },
  { action: "pod-delete", label: "Pod delete", desc: "delete the catalog-api pod (self-heal)", icon: Server, risk: "pod", category: "pod" },
  { action: "pod-cpu", label: "CPU spike", desc: "busy-loop the catalog-api CPU", icon: Fuel, risk: "pod", category: "pod" },
  { action: "pod-memory", label: "Memory spike", desc: "inflate catalog-api memory", icon: Server, risk: "pod", category: "pod" },
  { action: "pod-latency", label: "Latency spike (catalog)", desc: "add +5s latency to catalog-api traffic", icon: Timer, risk: "pod", category: "pod" },
  { action: "flaky-latency", label: "Latency spike (flaky)", desc: "add +3s latency to flaky-service traffic", icon: Timer, risk: "pod", category: "pod" },
  // Cluster / node failures (shown in "Cluster failures" group below)
  { action: "system-pod-kill", label: "Kill system pod", desc: "delete a kube-system pod (coredns)", icon: Server, risk: "cluster", category: "cluster" },
  { action: "node-cordon", label: "Cordon node", desc: "mark worker unschedulable", icon: Server, risk: "cluster", category: "cluster" },
  { action: "node-drain", label: "Drain node", desc: "evict all pods off the worker", icon: Server, risk: "cluster", category: "cluster" },
  { action: "node-network-latency", label: "Node network latency", desc: "netem delay on worker egress", icon: Activity, risk: "cluster", category: "cluster" },
  // NOTE: CoreDNS + ELK failures have dedicated cards above (CoreDNS failure,
  // ELK log signal demo) and are intentionally NOT repeated here.
  { action: "coredns-kill", label: "CoreDNS pod kill", desc: "delete one CoreDNS pod (self-heals)", icon: Globe, risk: "dns", category: "dns" },
  { action: "coredns-down", label: "CoreDNS down", desc: "scale CoreDNS to 0 (DNS outage)", icon: Globe, risk: "dns", category: "dns" },
  { action: "coredns-latency", label: "DNS latency", desc: "netem delay — DNS probe slows", icon: Timer, risk: "dns", category: "dns" },
  { action: "elk-error", label: "ELK error signal", desc: "inject ERROR/EXCEPTION logs", icon: AlertCircle, risk: "elk", category: "elk" },
  { action: "elk-connection-refused", label: "ELK connection refused", desc: "inject CONNECTION REFUSED logs", icon: AlertCircle, risk: "elk", category: "elk" },
  { action: "elk-timeout", label: "ELK timeout", desc: "inject TIMEOUT/TIMED OUT logs", icon: AlertCircle, risk: "elk", category: "elk" },
];

// Failures with dedicated cards above (database section, CoreDNS card, ELK
// card) are excluded from the generic grid to avoid duplicates.
const GENERIC_CATEGORIES = [
  { id: "pod", title: "Pod failures", hint: "catalog-api / flaky-service workloads" },
  { id: "cluster", title: "Cluster / node failures", hint: "worker node and system pods" },
];

// Game-day only supports metric-visible runbook faults.
const GAMEDAY_FAULTS = FAILURES.filter((f) => f.category === "pod" || f.category === "cluster");

const RECOVERY = [
  { action: "aerospike-up", label: "Aerospike up", icon: Database },
  { action: "yugabyte-up", label: "YugabyteDB up", icon: HardDrive },
  { action: "yugabyte-latency-recover", label: "Clear YugabyteDB latency", icon: Timer },
  { action: "yugabyte-connection-pressure-recover", label: "Clear YugabyteDB connection pressure", icon: Activity },
  { action: "aerospike-latency-recover", label: "Clear Aerospike latency", icon: Timer },
  { action: "latency-off", label: "Clear catalog latency", icon: Timer },
  { action: "flaky-latency-off", label: "Clear flaky latency", icon: Timer },
  { action: "network-latency-off", label: "Clear netem delay", icon: Activity },
  { action: "coredns-up", label: "CoreDNS up", icon: Globe },
  { action: "coredns-latency-off", label: "Clear DNS latency", icon: Timer },
  { action: "elk-recover", label: "Clear ELK demo", icon: Search },
  { action: "uncordon", label: "Uncordon node", icon: Server },
  { action: "all", label: "Recover all", icon: RotateCcw },
];

function stateBadge(state) {
  if (state === "running" || state === "ready") {
    return <Badge tone="success"><CheckCircle2 size={12} /> {state}</Badge>;
  }
  if (state === "stopped" || state === "cordoned") {
    return <Badge tone="danger"><AlertTriangle size={12} /> {state}</Badge>;
  }
  return <Badge tone="neutral">{state}</Badge>;
}

function fmtSeconds(value) {
  if (value === null || value === undefined) return "—";
  const ms = value < 1 ? value * 1000 : value;
  return value < 1 ? `${ms.toFixed(0)} ms` : `${value.toFixed(2)} s`;
}

function signalRow(label, signals) {
  return (
    <div className="row" style={{ justifyContent: "space-between" }}>
      <span className="text-muted" style={{ fontSize: 13 }}>{label}</span>
      <span style={{ fontSize: 13, fontFamily: "var(--font-mono)" }}>
        req/s {signals.req_s === null || signals.req_s === undefined ? "—" : signals.req_s.toFixed(2)} · 5xx {signals["5xx_pct"] ?? "—"}% · p50 {fmtSeconds(signals.p50_s)} · p95 {fmtSeconds(signals.p95_s)} · p99 {fmtSeconds(signals.p99_s)}
      </span>
    </div>
  );
}

export default function Chaos() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(null);
  const [log, setLog] = useState("");
  const [error, setError] = useState(null);
  const [activeFaults, setActiveFaults] = useState({});
  const [history, setHistory] = useState([]);
  const [gdFault, setGdFault] = useState("flaky-latency");
  const [gdDuration, setGdDuration] = useState(60);
  const [gdRunning, setGdRunning] = useState(false);
  const [gdReport, setGdReport] = useState(null);
  const [gdError, setGdError] = useState(null);
  const [nginxState, setNginxState] = useState(null);
  const [nginxHealth, setNginxHealth] = useState(null);
  const [corednsState, setCorednsState] = useState(null);
  const [elkState, setElkState] = useState(null);
  const [k8sPod, setK8sPod] = useState("");
  const [k8sInvestigating, setK8sInvestigating] = useState(false);

  const refreshStatus = async () => {
    try {
      const [s, a, h, ns, nh, cs, es] = await Promise.all([
        api.get("/chaos/status"),
        api.get("/chaos/active"),
        api.get("/chaos/history"),
        nginxDemoApi.status().catch(() => ({ data: null })),
        nginxDemoApi.health().catch(() => ({ data: null })),
        corednsDemoApi.status().catch(() => ({ data: null })),
        elkDemoApi.status().catch(() => ({ data: null })),
      ]);
      if (s.data.success) setStatus(s.data);
      else setError(s.data.error);
      setActiveFaults(a.data.data || {});
      setHistory(h.data.data || []);
      if (ns?.data?.success) setNginxState(ns.data);
      if (nh?.data) setNginxHealth(nh.data);
      if (cs?.data?.success) setCorednsState(cs.data);
      if (es?.data?.success) setElkState(es.data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshStatus();
  }, []);

  const runAction = async (kind, action, label) => {
    if (
      !confirm(
        `Run "${label}"?\n\nThis injects a failure / recovery action against the demo cluster and databases.`
      )
    )
      return;

    setRunning(`${kind}:${action}`);
    setError(null);
    setLog("");
    try {
      const url = kind === "recover" ? "/chaos/recover" : kind === "seed" ? "/chaos/seed" : "/chaos/inject";
      const body = kind === "seed" ? {} : { action };
      const res = await api.post(url, body);
      if (res.data.success) {
        setLog(
          [res.data.stdout || "(no output)", res.data.port_forward_hint]
            .filter(Boolean)
            .join("\n\n")
        );
      } else {
        setError(res.data.error || "Action failed");
        setLog(res.data.stdout || "");
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(null);
      refreshStatus();
    }
  };

  const NGINX_MODES = [
    { mode: "unavailable", label: "Timeout 504", desc: "upstream timeout — proxied server too slow", status: "504" },
    { mode: "dns", label: "DNS 502", desc: "upstream DNS failure — host not found", status: "502" },
    { mode: "invalid_config", label: "Invalid config 500", desc: "nginx config error — invalid directive", status: "500" },
  ];

  const runNginx = async (op, mode, label) => {
    if (
      !confirm(
        `Run "${label}"?\n\nThis injects a reversible Nginx failure (mode=${mode}) for OpenSRE investigation. Recover with "Recover Nginx".`
      )
    )
      return;

    setRunning(`nginx:${op}:${mode}`);
    setError(null);
    setLog("");
    try {
      const res =
        op === "fail"
          ? await nginxDemoApi.fail(mode)
          : op === "investigate"
            ? await nginxDemoApi.investigate(mode)
            : await nginxDemoApi.recover(mode);
      if (res.data.success) {
        const summary = res.data.evidence?.nginx?.summary;
        setLog(
          (res.data.result?.stdout ||
            res.data.recovery?.kubectl_result?.stdout ||
            res.data.opensre?.stdout ||
            JSON.stringify(res.data, null, 2)) +
            (summary ? `\n\nNginx summary: 5xx=${summary.http_5xx} 502=${summary.http_502} 503=${summary.http_503} 504=${summary.http_504}` : "")
        );
      } else {
        setError(
          res.data.opensre?.error || res.data.error || "Nginx action failed"
        );
        setLog(
          [res.data.opensre?.hint, JSON.stringify(res.data, null, 2)]
            .filter(Boolean)
            .join("\n\n")
        );
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(null);
      refreshStatus();
    }
  };

  const COREDNS_MODES = [
    { mode: "kill", label: "Pod kill", desc: "delete one CoreDNS pod — self-heals, restart evidence" },
    { mode: "down", label: "Down (scale 0)", desc: "sustained DNS outage until recovery" },
    { mode: "latency", label: "Latency", desc: "DNS probe slows until recovery" },
  ];

  const runCoredns = async (op, mode, label) => {
    if (
      !confirm(
        `Run "${label}"?\n\nThis injects a reversible CoreDNS failure (mode=${mode}) in the demo cluster for OpenSRE investigation. Recover with "Recover CoreDNS".`
      )
    )
      return;

    setRunning(`coredns:${op}:${mode}`);
    setError(null);
    setLog("");
    try {
      const res =
        op === "fail"
          ? await corednsDemoApi.fail(mode)
          : op === "investigate"
            ? await corednsDemoApi.investigate(mode)
            : await corednsDemoApi.recover(mode);
      if (res.data.success) {
        const summary = res.data.evidence?.coredns?.summary || res.data.summary;
        setLog(
          (res.data.result?.stdout ||
            res.data.recovery?.kubectl_result?.stdout ||
            res.data.opensre?.stdout ||
            JSON.stringify(res.data, null, 2)) +
            (summary ? `\n\nCoreDNS summary: health=${summary.health_status} probe=${summary.probe_verdict} SERVFAIL=${summary.servfail} timeouts=${summary.dns_timeouts}` : "")
        );
      } else {
        setError(
          res.data.opensre?.error || res.data.error || "CoreDNS action failed"
        );
        setLog(
          [res.data.opensre?.hint, JSON.stringify(res.data, null, 2)]
            .filter(Boolean)
            .join("\n\n")
        );
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(null);
      refreshStatus();
    }
  };

  const ELK_MODES = [
    { mode: "error", label: "ERROR/EXCEPTION", desc: "inject ERROR + EXCEPTION log signals" },
    { mode: "connection-refused", label: "CONNECTION REFUSED", desc: "inject CONNECTION REFUSED log signals" },
    { mode: "timeout", label: "TIMEOUT", desc: "inject TIMEOUT/TIMED OUT log signals" },
  ];

  const runElk = async (op, mode, label) => {
    if (
      !confirm(
        `Run "${label}"?\n\nThis injects an ELK log signal (mode=${mode}) for OpenSRE investigation. Recover with "Clear ELK demo".`
      )
    )
      return;

    setRunning(`elk:${op}:${mode}`);
    setError(null);
    setLog("");
    try {
      const res =
        op === "fail"
          ? await elkDemoApi.fail(mode)
          : op === "investigate"
            ? await elkDemoApi.investigate(mode)
            : await elkDemoApi.recover(mode);
      if (res.data.success) {
        const summary = res.data.evidence?.elasticsearch?.summary || res.data.summary;
        setLog(
          (res.data.result?.stdout ||
            res.data.recovery?.kubectl_result?.stdout ||
            res.data.opensre?.stdout ||
            JSON.stringify(res.data, null, 2)) +
            (summary ? `\n\nELK summary: ${JSON.stringify(summary)}` : "")
        );
      } else {
        setError(
          res.data.opensre?.error || res.data.error || "ELK action failed"
        );
        setLog(
          [res.data.opensre?.hint, JSON.stringify(res.data, null, 2)]
            .filter(Boolean)
            .join("\n\n")
        );
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(null);
      refreshStatus();
    }
  };

  const degradedPods = (status?.pods || []).filter((p) => p.status !== "Running");

  const runK8sInvestigate = async () => {
    const target = k8sPod || degradedPods[0]?.name;
    if (!target) {
      setError("No degraded pod to investigate — inject a failure first (e.g. Pod crash).");
      return;
    }
    if (
      !confirm(
        `Investigate pod "opensre/${target}"?\n\nCollects pod state, relevant log signals, Kubernetes events, metrics and GitHub correlation, then runs OpenSRE (read-only). This can take a few minutes.`
      )
    )
      return;

    setK8sInvestigating(true);
    setRunning("k8s:investigate");
    setError(null);
    setLog("");
    try {
      const res = await api.get(
        `/opensre/investigate/pod/opensre/${encodeURIComponent(target)}`
      );
      if (res.data.success) {
        setLog(res.data.stdout || "(no output)");
      } else {
        setError(res.data.stderr || res.data.error || "Investigation failed");
        setLog(res.data.stdout || "");
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setK8sInvestigating(false);
      setRunning(null);
      refreshStatus();
    }
  };

  const runGameDay = async () => {
    setGdRunning(true);
    setGdReport(null);
    setGdError(null);
    try {
      const res = await api.post("/chaos/game-day", {
        action: gdFault,
        duration_s: gdDuration,
      });
      if (res.data.success) setGdReport(res.data.report);
      else setGdError(res.data.error || "Game-day failed");
    } catch (e) {
      setGdError(e.message);
    } finally {
      setGdRunning(false);
      refreshStatus();
    }
  };

  const healthy = (status?.pods || []).filter((p) => p.status === "Running").length;
  const broken = (status?.pods || []).length - healthy;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Chaos Engineering</h1>
          <p className="page-head__sub">
            Inject and recover from failures with one click — no terminal needed
          </p>
        </div>
        <button
          onClick={() => { setLoading(true); refreshStatus(); }}
          className="btn btn--ghost btn--sm"
          disabled={loading}
        >
          <RefreshCw size={14} className={loading ? "btn__spinner" : ""} /> Refresh
        </button>
      </div>

      {Object.keys(activeFaults).length > 0 && (
        <div
          className="alert alert--warning"
          style={{ marginBottom: "var(--space-4)", display: "flex", gap: "var(--space-2)", alignItems: "center", flexWrap: "wrap" }}
        >
          <span className="text-muted" style={{ fontSize: 13 }}>Active faults:</span>
          {Object.entries(activeFaults).map(([fault, info]) => (
            <Badge key={fault} tone="danger">
              <AlertTriangle size={11} /> {fault} · {info.params || ""} · {info.started}
            </Badge>
          ))}
        </div>
      )}

      {error && (
        <div className="alert alert--danger" style={{ marginBottom: "var(--space-4)" }}>
          {error}
        </div>
      )}

      <div className="grid-3" style={{ marginBottom: "var(--space-4)" }}>
        <Card
          title="Aerospike"
          actions={loading ? <Loader2 size={14} className="btn__spinner" /> : stateBadge(status?.containers?.aerospike)}
        >
          <div className="row">
            <div className="health-item__icon"><Database size={16} /></div>
            <div className="text-muted" style={{ fontSize: 13 }}>Kubernetes StatefulSet (databases/aerospike-0)</div>
          </div>
        </Card>

        <Card
          title="YugabyteDB"
          actions={loading ? <Loader2 size={14} className="btn__spinner" /> : stateBadge(status?.containers?.yugabyte)}
        >
          <div className="row">
            <div className="health-item__icon"><HardDrive size={16} /></div>
            <div className="text-muted" style={{ fontSize: 13 }}>Kubernetes StatefulSet (databases/yugabytedb-0)</div>
          </div>
        </Card>

        <Card
          title="Worker node"
          actions={loading ? <Loader2 size={14} className="btn__spinner" /> : stateBadge(status?.node?.state)}
        >
          <div className="row">
            <div className="health-item__icon"><Server size={16} /></div>
            <div className="text-muted" style={{ fontSize: 13 }}>
              opensre-demo-worker · {broken} of {(status?.pods || []).length} pods degraded
            </div>
          </div>
        </Card>
      </div>

      {/* Database Failure Injection Section */}
      <div style={{ marginBottom: "var(--space-4)" }}>
        <Card
          title="Database Failure Injection"
          subtitle="Inject realistic database incidents into K8s-deployed YugabyteDB & Aerospike, then investigate with OpenSRE"
          actions={
            loading ? <Loader2 size={14} className="btn__spinner" /> : (
              <>
                <span className="text-muted" style={{ fontSize: 12, marginRight: "var(--space-2)" }}>YugabyteDB:</span>
                {stateBadge(status?.containers?.yugabyte)}
                <span className="text-muted" style={{ fontSize: 12, marginLeft: "var(--space-2)", marginRight: "var(--space-2)" }}>Aerospike:</span>
                {stateBadge(status?.containers?.aerospike)}
              </>
            )
          }
        >
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
            <div>
              <div className="text-muted" style={{ fontSize: 13, marginBottom: "var(--space-2)" }}>
                <HardDrive size={13} style={{ verticalAlign: "middle", marginRight: 4 }} />
                YugabyteDB
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
                  gap: "var(--space-2)",
                }}
              >
                {FAILURES.filter(f => f.category === "database" && f.label.includes("Yugabyte")).map((f) => (
                  <button
                    key={f.action}
                    className="btn btn--primary btn--sm"
                    onClick={() => runAction("inject", f.action, f.label)}
                    disabled={running !== null}
                    style={{ justifyContent: "space-between" }}
                    title={f.desc}
                  >
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <f.icon size={14} />
                      {f.label}
                    </span>
                    {running === `inject:${f.action}` && <Loader2 size={13} className="btn__spinner" />}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <div className="text-muted" style={{ fontSize: 13, marginBottom: "var(--space-2)" }}>
                <Database size={13} style={{ verticalAlign: "middle", marginRight: 4 }} />
                Aerospike
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
                  gap: "var(--space-2)",
                }}
              >
                {FAILURES.filter(f => f.category === "database" && f.label.includes("Aerospike")).map((f) => (
                  <button
                    key={f.action}
                    className="btn btn--primary btn--sm"
                    onClick={() => runAction("inject", f.action, f.label)}
                    disabled={running !== null}
                    style={{ justifyContent: "space-between" }}
                    title={f.desc}
                  >
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <f.icon size={14} />
                      {f.label}
                    </span>
                    {running === `inject:${f.action}` && <Loader2 size={13} className="btn__spinner" />}
                  </button>
                ))}
              </div>
            </div>
            <div style={{ marginTop: "var(--space-2)", paddingTop: "var(--space-2)", borderTop: "1px solid var(--border)" }}>
              <div className="text-muted" style={{ fontSize: 12 }}>
                Flow: Inject failure → Database becomes unhealthy → Click "Investigate with OpenSRE" on the YugabyteDB/Aerospike pages
                or use the Incident page → OpenSRE collects DB + K8s + Metrics + Logs evidence → Evidence-grounded RCA → Click Recover
              </div>
            </div>
          </div>
        </Card>
      </div>

      <div style={{ marginBottom: "var(--space-4)" }}>
        <Card
          title="Nginx failure (for OpenSRE investigation)"
          subtitle="Break the Nginx reverse-proxy on purpose, investigate it, then recover"
          actions={
            loading ? (
              <Loader2 size={14} className="btn__spinner" />
            ) : nginxState?.state?.failed ? (
              <span title={nginxState.state.simulated ? "Demo failure recorded without cluster changes (simulated 502 + connection-refused logs are injected at investigate time)" : "Live demo failure: Nginx ConfigMap patched with a broken upstream. Pods stay Running, requests return 502. Recover to restore."}>
              <Badge tone="danger">
                <AlertTriangle size={11} /> failed · {nginxState.state.mode}
                {nginxState.state.simulated ? " · simulated" : " · live"}
              </Badge>
              </span>
            ) : nginxHealth?.status ? (
              <span title={nginxHealth.status === "healthy" ? "Nginx pod Running, containers ready, nginx -t valid" : nginxHealth.status === "not_deployed" ? "No pods with label app=nginx in namespace opensre — apply infra/k8s/nginx-deployment.yaml + nginx-service.yaml" : "Nginx pods exist but are not all Running/ready, or nginx -t failed — see GET /api/nginx/health for details"}>
              <Badge tone={nginxHealth.status === "healthy" ? "success" : "neutral"}>
                {nginxHealth.status === "healthy" ? <CheckCircle2 size={11} /> : <Globe size={11} />} {nginxHealth.status}
              </Badge>
              </span>
            ) : (
              <Badge tone="neutral"><Globe size={11} /> nginx</Badge>
            )
          }
        >
          <div style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap", alignItems: "center" }}>
            <button
              className="btn btn--primary btn--sm"
              onClick={() => runNginx("fail", "unavailable", "Nginx 502 (upstream unavailable)")}
              disabled={running !== null}
              title="Point Nginx at an unavailable upstream (127.0.0.1:59999) — produces 502 + connection refused"
            >
              {running === "nginx:fail:unavailable" ? <Loader2 size={13} className="btn__spinner" /> : <Globe size={14} />}
              Nginx 502 (upstream unavailable)
            </button>
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => runNginx("investigate", nginxState?.state?.mode || "unavailable", "Investigate Nginx")}
              disabled={running !== null}
              title="Collect Nginx + K8s/metrics/logs + GitHub evidence and run OpenSRE investigation"
            >
              {running?.startsWith("nginx:investigate") ? <Loader2 size={13} className="btn__spinner" /> : <Search size={14} />}
              Investigate Nginx
            </button>
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => runNginx("recover", nginxState?.state?.mode || "unavailable", "Recover Nginx")}
              disabled={running !== null}
              title="Restore the valid Nginx config and clear the demo failure"
            >
              {running?.startsWith("nginx:recover") ? <Loader2 size={13} className="btn__spinner" /> : <RotateCcw size={14} />}
              Recover Nginx
            </button>
            {nginxState?.summary && (
              <span className="text-muted" style={{ fontSize: 12, fontFamily: "var(--font-mono)" }}>
                5xx {nginxState.summary.http_5xx} · 502 {nginxState.summary.http_502} · 504 {nginxState.summary.http_504}
              </span>
            )}
          </div>
          <div className="text-muted" style={{ fontSize: 12, marginTop: "var(--space-2)" }}>
            Tip: fail/recover finish in seconds. Investigate first collects evidence, then waits on the OpenSRE model — allow several minutes; if it spins, check the backend log and retry.
          </div>

          {!nginxState?.state?.failed && nginxHealth?.status && nginxHealth.status !== "healthy" && (
            <div className="text-muted" style={{ fontSize: 12, marginTop: "var(--space-2)" }}>
              {nginxHealth.status === "not_deployed"
                ? "Nginx Deployment/Service are not applied — run: kubectl apply -f infra/k8s/nginx-configmap.yaml -f infra/k8s/nginx-deployment.yaml -f infra/k8s/nginx-service.yaml"
                : "Nginx is present but unhealthy — check GET /api/nginx/health (pod phase, readiness, nginx -t) before investigating."}
            </div>
          )}

          <details style={{ marginTop: "var(--space-3)" }}>
            <summary
              style={{ cursor: "pointer", fontSize: 13, color: "var(--muted)", display: "inline-flex", alignItems: "center", gap: 4 }}
            >
              <ChevronDown size={13} /> More Nginx faults
            </summary>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
                gap: "var(--space-2)",
                marginTop: "var(--space-2)",
              }}
            >
              {NGINX_MODES.map((m) => (
                <button
                  key={m.mode}
                  className="btn btn--ghost btn--sm"
                  onClick={() => runNginx("fail", m.mode, `Nginx ${m.label}`)}
                  disabled={running !== null}
                  title={m.desc}
                >
                  {running === `nginx:fail:${m.mode}` ? <Loader2 size={13} className="btn__spinner" /> : <Globe size={13} style={{ marginRight: 4 }} />}
                  {m.label}
                </button>
              ))}
            </div>
          </details>
        </Card>
      </div>

      <div style={{ marginBottom: "var(--space-4)" }}>
        <Card
          title="CoreDNS failure (for OpenSRE investigation)"
          subtitle="Break cluster DNS on purpose, investigate it, then recover"
          actions={
            loading ? (
              <Loader2 size={14} className="btn__spinner" />
            ) : corednsState?.state?.failed ? (
              <span title={corednsState.state.simulated ? "Demo failure recorded without cluster changes (synthetic SERVFAIL/timeout logs are injected at investigate time)" : "Live demo failure in the demo cluster. Recover to restore CoreDNS."}>
              <Badge tone="danger">
                <AlertTriangle size={11} /> failed · {corednsState.state.mode}
                {corednsState.state.simulated ? " · simulated" : " · live"}
              </Badge>
              </span>
            ) : corednsState?.health?.status ? (
              <span title={corednsState.health.status === "healthy" ? "CoreDNS pods Running and ready" : "CoreDNS pods missing, not Running/ready, or restarts climbing — see GET /api/coredns/health for details"}>
              <Badge tone={corednsState.health.status === "healthy" ? "success" : corednsState.health.status === "degraded" ? "warning" : corednsState.health.status === "down" ? "danger" : "neutral"}>
                {corednsState.health.status === "healthy" ? <CheckCircle2 size={11} /> : <Globe size={11} />} {corednsState.health.status}
              </Badge>
              </span>
            ) : (
              <Badge tone="neutral"><Globe size={11} /> coredns</Badge>
            )
          }
        >
          <div style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap", alignItems: "center" }}>
            {COREDNS_MODES.map((m) => (
              <button
                key={m.mode}
                className="btn btn--primary btn--sm"
                onClick={() => runCoredns("fail", m.mode, `CoreDNS ${m.label}`)}
                disabled={running !== null}
                title={m.desc}
              >
                {running === `coredns:fail:${m.mode}` ? <Loader2 size={13} className="btn__spinner" /> : <Globe size={13} style={{ marginRight: 4 }} />}
                {m.label}
              </button>
            ))}
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => runCoredns("investigate", corednsState?.state?.mode || "kill", "Investigate CoreDNS")}
              disabled={running !== null}
              title="Collect CoreDNS + probe/metrics/affected evidence and run OpenSRE investigation"
            >
              {running?.startsWith("coredns:investigate") ? <Loader2 size={13} className="btn__spinner" /> : <Search size={14} />}
              Investigate CoreDNS
            </button>
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => runCoredns("recover", corednsState?.state?.mode || "kill", "Recover CoreDNS")}
              disabled={running !== null}
              title="Restore CoreDNS replicas / remove latency and clear the demo failure"
            >
              {running?.startsWith("coredns:recover") ? <Loader2 size={13} className="btn__spinner" /> : <RotateCcw size={14} />}
              Recover CoreDNS
            </button>
            {corednsState?.probe?.available && (
              <span className="text-muted" style={{ fontSize: 12, fontFamily: "var(--font-mono)" }}>
                probe {corednsState.probe.verdict}
                {corednsState.probe.latency_ms_avg !== null && corednsState.probe.latency_ms_avg !== undefined ? ` · avg ${corednsState.probe.latency_ms_avg} ms` : ""}
                {` · ${corednsState.probe.succeeded}/${corednsState.probe.attempts} ok`}
              </span>
            )}
            {corednsState?.summary && (
              <span className="text-muted" style={{ fontSize: 12, fontFamily: "var(--font-mono)" }}>
                SERVFAIL {corednsState.summary.servfail} · timeouts {corednsState.summary.dns_timeouts} · restarts {corednsState.summary.restart_count_total}
              </span>
            )}
          </div>
          <div className="text-muted" style={{ fontSize: 12, marginTop: "var(--space-2)" }}>
            Flow: fail (kill/down/latency) → probe flips to degraded/slow/failing → Investigate → OpenSRE RCA names CoreDNS/DNS → Recover.
            Tip: investigate first collects evidence, then waits on the OpenSRE model — allow several minutes.
          </div>
        </Card>
      </div>

      {/* ELK Card */}
      <div style={{ marginBottom: "var(--space-4)" }}>
        <Card
          title="ELK log signal demo"
          subtitle="Inject ERROR/EXCEPTION/CONNECTION REFUSED/TIMEOUT logs → Fluent Bit → Elasticsearch → OpenSRE"
          actions={
            elkState?.state?.failed ? (
              <Badge tone="danger">
                <AlertTriangle size={11} /> failed · {elkState.state.mode}
                {elkState.state.simulated ? " · simulated" : " · live"}
              </Badge>
            ) : elkState?.elasticsearch_health?.available ? (
              <Badge tone="success">
                <CheckCircle2 size={11} /> ES connected · {elkState.error_summary?.ERROR || 0} errors
              </Badge>
            ) : (
              <Badge tone="warning">
                <AlertTriangle size={11} /> ES unavailable
              </Badge>
            )
          }
        >
          <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", alignItems: "center", marginBottom: "var(--space-2)" }}>
            <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
              {ELK_MODES.map((m) => (
                <button
                  key={m.mode}
                  className="btn btn--ghost btn--sm"
                  onClick={() => runElk("fail", m.mode, `Fail ELK ${m.label}`)}
                  disabled={running !== null || (elkState?.state?.failed && !elkState.state.simulated)}
                  style={{ fontSize: 12 }}
                  title={m.desc}
                >
                  {m.label}
                </button>
              ))}
            </div>
            {elkState?.state?.failed && (
              <>
                <button
                  className="btn btn--primary btn--sm"
                  onClick={() => runElk("investigate", elkState.state.mode, `Investigate ELK ${elkState.state.mode}`)}
                  disabled={running !== null}
                  style={{ fontSize: 12 }}
                >
                  Investigate
                </button>
                <button
                  className="btn btn--success btn--sm"
                  onClick={() => runElk("recover", elkState.state.mode, `Recover ELK ${elkState.state.mode}`)}
                  disabled={running !== null}
                  style={{ fontSize: 12 }}
                >
                  Recover
                </button>
              </>
            )}
          </div>
          {elkState?.elasticsearch_health?.available && (
            <span style={{ fontSize: 12 }}>
              Elasticsearch: <code>{elkState.elasticsearch_health.version}</code> · cluster: <code>{elkState.elasticsearch_health.cluster}</code>
            </span>
          )}
          {elkState?.error_summary && Object.keys(elkState.error_summary).length > 0 && (
            <div style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap", marginTop: "var(--space-2)" }}>
              {Object.entries(elkState.error_summary).map(([k, v]) => (
                <span key={k} className="text-muted" style={{ fontSize: 11, fontFamily: "var(--font-mono)" }}>
                  {k}: {v}
                </span>
              ))}
            </div>
          )}
          <div className="text-muted" style={{ fontSize: 12, marginTop: "var(--space-2)" }}>
            Flow: fail (error/connection-refused/timeout) → Fluent Bit ships structured logs to ES → Investigate → OpenSRE RCA correlates ELK evidence → Recover.
          </div>
        </Card>
      </div>

      <div style={{ marginBottom: "var(--space-4)" }}>
        <Card
          title="Kubernetes failure investigation"
          subtitle="Pick a degraded pod, collect log + event evidence, run OpenSRE"
          actions={
            degradedPods.length > 0 ? (
              <Badge tone="danger">
                <AlertTriangle size={11} /> {degradedPods.length} degraded
              </Badge>
            ) : (
              <Badge tone="success">
                <CheckCircle2 size={11} /> all Running
              </Badge>
            )
          }
        >
          <div style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap", alignItems: "center" }}>
            <select
              className="btn btn--ghost btn--sm"
              value={k8sPod || degradedPods[0]?.name || ""}
              onChange={(e) => setK8sPod(e.target.value)}
              disabled={running !== null || degradedPods.length === 0}
              style={{ minWidth: 260 }}
              title="Non-Running pods in namespace opensre"
            >
              {degradedPods.length === 0 && <option value="">No degraded pods</option>}
              {degradedPods.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name} · {p.status} · restarts {p.restarts}
                </option>
              ))}
            </select>
            <button
              className="btn btn--primary btn--sm"
              onClick={runK8sInvestigate}
              disabled={running !== null || degradedPods.length === 0}
              title="Collect pod state, relevant log signals, events, metrics and run OpenSRE (read-only)"
            >
              {k8sInvestigating ? <Loader2 size={13} className="btn__spinner" /> : <Search size={14} />}
              {k8sInvestigating ? "Investigating…" : "Investigate pod"}
            </button>
            {(k8sPod || degradedPods[0]?.name) && (
              <Link
                className="btn btn--ghost btn--sm"
                to={`/incident?namespace=opensre&pod=${encodeURIComponent(k8sPod || degradedPods[0].name)}`}
              >
                Open in Incident report
              </Link>
            )}
          </div>
          <div className="text-muted" style={{ fontSize: 12, marginTop: "var(--space-2)" }}>
            Flow: inject “Pod crash” above → pod restarts climb → pick it here → Investigate.
            Evidence covers container restarts, relevant ERROR/exception/timeout log lines, BackOff/Unhealthy events and metrics.
          </div>
        </Card>
      </div>

      <div className="grid-2" style={{ marginBottom: "var(--space-4)" }}>
        <Card
          title="Failure injection"
          subtitle="Click a failure to inject it now"
          actions={
            <Badge tone={broken > 0 ? "warning" : "success"}>
              {broken > 0 ? `${broken} degraded` : "healthy"}
            </Badge>
          }
        >
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
            <div className="text-muted" style={{ fontSize: 12 }}>
              Database, CoreDNS and ELK faults live in their dedicated cards above — this grid covers the rest, grouped by category.
            </div>
            {GENERIC_CATEGORIES.map((group) => (
              <div key={group.id}>
                <div className="text-muted" style={{ fontSize: 13, marginBottom: "var(--space-2)" }}>
                  {group.title} <span style={{ opacity: 0.7 }}>· {group.hint}</span>
                </div>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
                    gap: "var(--space-3)",
                  }}
                >
                  {FAILURES.filter((f) => f.category === group.id).map((f) => (
                    <button
                      key={f.action}
                      className="btn btn--primary btn--sm"
                      onClick={() => runAction("inject", f.action, f.label)}
                      disabled={running !== null}
                      style={{ justifyContent: "space-between" }}
                      title={f.desc}
                    >
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                        <f.icon size={14} />
                        {f.label}
                      </span>
                      {running === `inject:${f.action}` && <Loader2 size={13} className="btn__spinner" />}
                    </button>
                  ))}
                </div>
              </div>
            ))}

            <div style={{ marginTop: "var(--space-3)" }}>
              <button
                className="btn btn--ghost btn--sm"
                onClick={() => runAction("seed", null, "Seed database data")}
                disabled={running !== null}
              >
                {running === "seed:null" ? <Loader2 size={14} className="btn__spinner" /> : <Database size={14} />}
                Seed database data (Yugabyte + Aerospike)
              </button>
            </div>

            <div style={{ marginTop: "var(--space-3)", paddingTop: "var(--space-3)", borderTop: "1px solid var(--border)" }}>
              <div className="text-muted" style={{ fontSize: 13, marginBottom: "var(--space-2)" }}>
                <AlertCircle size={13} style={{ verticalAlign: "middle", marginRight: 4 }} />
                Data Integrity Testing — inject bad data for investigation
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
                  gap: "var(--space-2)",
                }}
              >
                <button
                  key="insert-empty"
                  className="btn btn--ghost btn--sm"
                  onClick={() => runAction("inject", "insert-empty-yugabyte", "Insert empty fields (Yugabyte)")}
                  disabled={running !== null}
                  title="Insert NULL/empty required fields into YugabyteDB"
                >
                  <HardDrive size={13} style={{ marginRight: 4 }} />
                  Empty fields (Yugabyte)
                </button>
                <button
                  key="insert-empty-aero"
                  className="btn btn--ghost btn--sm"
                  onClick={() => runAction("inject", "insert-empty-aerospike", "Insert empty fields (Aerospike)")}
                  disabled={running !== null}
                  title="Insert missing required fields into Aerospike"
                >
                  <Database size={13} style={{ marginRight: 4 }} />
                  Empty fields (Aerospike)
                </button>
                <button
                  key="insert-duplicates"
                  className="btn btn--ghost btn--sm"
                  onClick={() => runAction("inject", "insert-duplicates-yugabyte", "Insert duplicates (Yugabyte)")}
                  disabled={running !== null}
                  title="Insert duplicate emails and PKs into YugabyteDB"
                >
                  <HardDrive size={13} style={{ marginRight: 4 }} />
                  Duplicates (Yugabyte)
                </button>
                <button
                  key="insert-duplicates-aero"
                  className="btn btn--ghost btn--sm"
                  onClick={() => runAction("inject", "insert-duplicates-aerospike", "Insert duplicates (Aerospike)")}
                  disabled={running !== null}
                  title="Insert duplicate logical records into Aerospike"
                >
                  <Database size={13} style={{ marginRight: 4 }} />
                  Duplicates (Aerospike)
                </button>
                <button
                  key="insert-invalid"
                  className="btn btn--ghost btn--sm"
                  onClick={() => runAction("inject", "insert-invalid-yugabyte", "Insert invalid values (Yugabyte)")}
                  disabled={running !== null}
                  title="Insert negative counts, invalid emails into YugabyteDB"
                >
                  <HardDrive size={13} style={{ marginRight: 4 }} />
                  Invalid values (Yugabyte)
                </button>
                <button
                  key="insert-invalid-aero"
                  className="btn btn--ghost btn--sm"
                  onClick={() => runAction("inject", "insert-invalid-aerospike", "Insert invalid values (Aerospike)")}
                  disabled={running !== null}
                  title="Insert negative counts, invalid data into Aerospike"
                >
                  <Database size={13} style={{ marginRight: 4 }} />
                  Invalid values (Aerospike)
                </button>
              </div>
            </div>
          </div>
        </Card>

        <Card title="Recovery" subtitle="Restore the environment">
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
              gap: "var(--space-3)",
            }}
          >
            {RECOVERY.map((r) => (
              <button
                key={r.action}
                className="btn btn--ghost btn--sm"
                onClick={() => runAction("recover", r.action, r.label)}
                disabled={running !== null}
              >
                {running === `recover:${r.action}` ? <Loader2 size={14} className="btn__spinner" /> : <r.icon size={14} />}
                {r.label}
              </button>
            ))}
          </div>

          <div style={{ marginTop: "var(--space-4)" }}>
            <div className="text-muted" style={{ fontSize: 13, marginBottom: "var(--space-2)" }}>
              <Terminal size={13} style={{ verticalAlign: "middle", marginRight: 4 }} />
              Command output
            </div>
            <pre
              style={{
                background: "var(--surface-1)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: "var(--space-3)",
                fontSize: 12,
                maxHeight: 200,
                overflow: "auto",
                whiteSpace: "pre-wrap",
              }}
            >
              {log || "Run an action to see its output here."}
            </pre>
          </div>
        </Card>
      </div>

      <div className="grid-2">
        <Card
          title="Game-day"
          subtitle="Automated baseline → inject → measure → recover → report"
          actions={gdReport ? (
            <Badge tone={gdReport.verdict?.degraded ? "danger" : "neutral"}>
              {gdReport.verdict?.degraded ? "degraded" : "steady"}
            </Badge>
          ) : null}
        >
          <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center", flexWrap: "wrap" }}>
            <select
              className="btn btn--ghost btn--sm"
              value={gdFault}
              onChange={(e) => setGdFault(e.target.value)}
              disabled={gdRunning}
              style={{ minWidth: 220 }}
            >
              {GAMEDAY_FAULTS.map((f) => (
                <option key={f.action} value={f.action}>{f.label}</option>
              ))}
            </select>
            <input
              type="number"
              min={60}
              max={600}
              step={15}
              value={gdDuration}
              onChange={(e) => setGdDuration(Number(e.target.value))}
              className="btn btn--ghost btn--sm"
              style={{ width: 90 }}
              disabled={gdRunning}
            />
            <span className="text-muted" style={{ fontSize: 13 }}>s fault window</span>
            <button
              className="btn btn--primary btn--sm"
              onClick={runGameDay}
              disabled={gdRunning}
            >
              {gdRunning ? <Loader2 size={14} className="btn__spinner" /> : <Activity size={14} />}
              {gdRunning ? "Running game-day…" : "Run game-day"}
            </button>
          </div>

          {gdRunning && (
            <div className="text-muted" style={{ fontSize: 13, marginTop: "var(--space-3)" }}>
              Injecting, holding the fault, then recovering and re-measuring after
              the 1-minute metrics window flushes. Expect ~3 minutes per run.
            </div>
          )}

          {gdError && (
            <div className="alert alert--danger" style={{ marginTop: "var(--space-3)" }}>
              {gdError}
            </div>
          )}

          {gdReport && (
            <div style={{ marginTop: "var(--space-3)" }}>
              <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center", marginBottom: "var(--space-3)" }}>
                <Badge tone="neutral">exp {gdReport.id}</Badge>
                <span className="text-muted" style={{ fontSize: 13 }}>{gdReport.pod_target} · {gdReport.started}</span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                {signalRow("Baseline (before)", gdReport.baseline)}
                {signalRow("During (fault)", gdReport.during)}
                {signalRow("After (recovered)", gdReport.after)}
              </div>
              <div style={{ marginTop: "var(--space-3)", display: "flex", gap: "var(--space-2)", flexWrap: "wrap", alignItems: "center" }}>
                <span className="text-muted" style={{ fontSize: 13 }}>Steady-state hypothesis:</span>
                <Badge tone={gdReport.verdict?.degraded ? "danger" : "success"}>
                  {gdReport.verdict?.degraded ? "degraded" : "steady"} (p99 &gt; {gdReport.verdict?.threshold_s}s)
                </Badge>
                <Badge tone={gdReport.verdict?.recovered ? "success" : "warning"}>
                  {gdReport.verdict?.recovered ? "recovered" : "not-recovered"}
                </Badge>
                {gdReport.recovery && (
                  <Badge tone={gdReport.recovery.success ? "success" : "danger"}>
                    recovery {gdReport.recovery.success ? "ok" : "failed"}
                  </Badge>
                )}
              </div>
            </div>
          )}
        </Card>

        <Card
          title="Experiment history"
          subtitle="chaos/experiments/events.jsonl"
          actions={<ListChecks size={14} />}
        >
          {history.length === 0 ? (
            <div className="text-muted" style={{ fontSize: 13 }}>
              No experiments recorded yet — inject or run a game-day to build the timeline.
            </div>
          ) : (
            <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "var(--space-2)", maxHeight: 300, overflow: "auto" }}>
              {history.slice(0, 40).map((event) => (
                <li key={event.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--space-2)" }}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13 }}>
                    <Badge tone={event.kind === "game-day" ? "warning" : "neutral"}>{event.kind}</Badge>
                    <span style={{ fontFamily: "var(--font-mono)" }}>{event.fault}</span>
                  </span>
                  <span className="text-muted" style={{ fontSize: 12 }}>
                    {event.ts} · {event.params || event.note || ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}