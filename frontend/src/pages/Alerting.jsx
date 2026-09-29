import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  BellRing,
  RefreshCw,
  Loader2,
  ServerCog,
  Webhook,
  ShieldAlert,
  FileText,
  CheckCircle2,
  AlertTriangle,
  Radio,
  ScrollText,
  Save,
} from "lucide-react";
import { alertmanagerApi, alertsApi } from "../api/api";
import Card from "../components/Card";
import Badge from "../components/Badge";
import Skeleton from "../components/Skeleton";

const AUTO_REFRESH_MS = 15000;

function stateTone(state) {
  if (state === "active") return "danger";
  if (state === "suppressed") return "warning";
  if (state === "unprocessed") return "info";
  return "neutral";
}

function rcaTone(status) {
  if (status === "resolved") return "success";
  if (status === "failed" || status === "interrupted") return "danger";
  if (status === "pending") return "warning";
  return "neutral";
}

function logTone(line = "") {
  const lower = line.toLowerCase();
  if (
    lower.includes("err") ||
    lower.includes("fail") ||
    lower.includes("fatal") ||
    lower.includes("panic") ||
    lower.includes("refused")
  )
    return "danger";
  if (lower.includes("warn") || lower.includes("timeout"))
    return "warning";
  if (lower.includes("level=debug") || lower.includes("level=info"))
    return null;
  return null;
}

/**
 * Alerting page.
 *
 * Makes the alerting chain inspectable end to end: vmalert evaluates the
 * rules, Alertmanager groups and routes, and this backend pulls the result
 * into incidents that trigger an OpenSRE RCA. Each stage is shown
 * separately so a demo can point at the stage that is actually working --
 * or the one that is not.
 */
export default function Alerting() {
  const [overview, setOverview] = useState(null);
  const [incidents, setIncidents] = useState([]);
  const [reports, setReports] = useState([]);
  const [logs, setLogs] = useState(null);
  const [logsError, setLogsError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [syncing, setSyncing] = useState(false);
  const [injecting, setInjecting] = useState(false);
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    try {
      const [ov, inc, reps] = await Promise.all([
        alertmanagerApi.overview(),
        alertsApi.active(),
        alertmanagerApi.reports(50),
      ]);
      setOverview(ov.data);
      setIncidents(Array.isArray(inc.data?.alerts) ? inc.data.alerts : []);
      setReports(Array.isArray(reps.data?.reports) ? reps.data.reports : []);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    // Seed from an async callback rather than synchronously in the effect
    // body: the initial fetch updates state, and doing that inline cascades
    // renders (same pattern as AlertNotifications).
    Promise.resolve().then(() => {
      if (cancelled) return;
      load();
    });

    const timer = setInterval(() => {
      if (!cancelled) load();
    }, AUTO_REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [load]);

  // Live Alertmanager log tail. Refreshed on its own faster cadence so the
  // demo visibly shows alerts arriving continuously, independent of the
  // slower incident-table poll above.
  useEffect(() => {
    let cancelled = false;

    async function pollLogs() {
      try {
        const { data } = await alertmanagerApi.logs(150);
        if (cancelled) return;
        if (data.success) {
          setLogs(data);
          setLogsError(null);
        } else {
          setLogsError(data.error || "Could not read Alertmanager logs.");
        }
      } catch (err) {
        if (!cancelled) setLogsError(err.message);
      }
    }

    pollLogs();
    const timer = setInterval(pollLogs, 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  // Surface sync results briefly, then let the next poll take over.
  useEffect(() => {
    if (!notice) return undefined;
    const timer = setTimeout(() => setNotice(null), 8000);
    return () => clearTimeout(timer);
  }, [notice]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const { data } = await alertmanagerApi.sync();
      setNotice({
        tone: "success",
        text: data.skipped
          ? "Sync already in progress."
          : `Polled ${data.polled ?? 0} alert(s) from Alertmanager · ${data.firing ?? 0} firing · ${data.new_incidents ?? 0} new incident(s).`,
      });
      await load();
    } catch (err) {
      setNotice({ tone: "danger", text: `Sync failed: ${err.message}` });
    } finally {
      setSyncing(false);
    }
  };

  const handleInject = async () => {
    setInjecting(true);
    try {
      const { data } = await alertmanagerApi.testAlert();
      setNotice({ tone: "success", text: data.detail || "Test alert injected." });
      // Give Alertmanager a moment to route, then pull it into an incident.
      setTimeout(() => load(), 5000);
    } catch (err) {
      setNotice({ tone: "danger", text: `Injection failed: ${err.message}` });
    } finally {
      setInjecting(false);
    }
  };

  const health = overview?.health;
  const reachable = Boolean(overview?.success);
  const status = overview?.status;
  const amAlerts = overview?.alerts?.alerts || [];
  const activeCount = amAlerts.filter((a) => a.state === "active").length;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Alerting</h1>
          <p className="page-head__sub">
            vmalert evaluates rules, Alertmanager groups and routes, and every
            alert becomes an incident with an OpenSRE root-cause analysis.
          </p>
        </div>

        <div className="page-head__actions">
          <button
            type="button"
            className="btn btn--ghost"
            onClick={load}
            disabled={loading}
          >
            <RefreshCw size={15} className={loading ? "btn__spinner" : ""} />{" "}
            Refresh
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={handleSync}
            disabled={syncing || !reachable}
          >
            {syncing ? (
              <Loader2 size={15} className="btn__spinner" />
            ) : (
              <RefreshCw size={15} />
            )}{" "}
            Sync now
          </button>
        </div>
      </div>

      {error && (
        <div className="alert alert--danger" style={{ marginBottom: "var(--space-3)" }}>
          Could not reach the backend: {error}
        </div>
      )}

      {notice && (
        <div className={`alert alert--${notice.tone}`} style={{ marginBottom: "var(--space-3)" }}>
          {notice.text}
        </div>
      )}

      {/* ---- Pipeline status ---- */}
      <Card
        title="Alerting pipeline"
        subtitle="Each stage below is read live from the running system"
      >
        <div className="stat-grid stat-grid--4" style={{ marginBottom: "var(--space-3)" }}>
          <div className="stat-card">
            <div className="stat-card__top">
              <div className="stat-card__icon">
                <ServerCog size={17} strokeWidth={1.8} />
              </div>
              <span className="stat-card__label">Alertmanager</span>
            </div>
            {loading ? (
              <Skeleton width={70} height={30} />
            ) : (
              <div
                className={`stat-card__value stat-card__value--${reachable ? "success" : "danger"}`}
              >
                {reachable ? "Healthy" : "Down"}
              </div>
            )}
            <div className="stat-card__trend">
              {overview?.url || "http://localhost:9093"}
            </div>
          </div>

          <div className="stat-card">
            <div className="stat-card__top">
              <div className="stat-card__icon">
                <Radio size={17} strokeWidth={1.8} />
              </div>
              <span className="stat-card__label">Firing in Alertmanager</span>
            </div>
            {loading ? (
              <Skeleton width={40} height={30} />
            ) : (
              <div
                className={`stat-card__value${activeCount ? " stat-card__value--danger" : ""}`}
              >
                {activeCount}
              </div>
            )}
            <div className="stat-card__trend">{amAlerts.length} total known</div>
          </div>

          <div className="stat-card">
            <div className="stat-card__top">
              <div className="stat-card__icon">
                <BellRing size={17} strokeWidth={1.8} />
              </div>
              <span className="stat-card__label">Open incidents</span>
            </div>
            {loading ? (
              <Skeleton width={40} height={30} />
            ) : (
              <div
                className={`stat-card__value${incidents.length ? " stat-card__value--danger" : ""}`}
              >
                {incidents.length}
              </div>
            )}
            <div className="stat-card__trend">In the incident store</div>
          </div>

          <div className="stat-card">
            <div className="stat-card__top">
              <div className="stat-card__icon">
                <Webhook size={17} strokeWidth={1.8} />
              </div>
              <span className="stat-card__label">Receiver</span>
            </div>
            {loading ? (
              <Skeleton width={90} height={30} />
            ) : (
              <div className="stat-card__value">
                {status?.route?.receiver || "—"}
              </div>
            )}
            <div className="stat-card__trend">
              {status?.receivers?.[0]?.webhooks ?? 0} webhook configured
            </div>
          </div>
        </div>

        {!reachable && !loading && (
          <div className="alert alert--warning">
            <div style={{ marginBottom: "var(--space-2)" }}>
              <strong>Alertmanager is not reachable from the backend.</strong> Alerts
              are pulled from it over a port-forward, so the forward must be running:
            </div>
            <code className="inline-code">
              {overview?.port_forward?.command ||
                "kubectl port-forward -n observability svc/alertmanager 9093:9093"}
            </code>
            {health?.error && (
              <div style={{ marginTop: "var(--space-2)", opacity: 0.8 }}>
                {health.error}
              </div>
            )}
          </div>
        )}
      </Card>

      {/* ---- Demonstrate the chain ---- */}
      <Card
        title="Trigger an alert"
        subtitle="Injects a synthetic alert into Alertmanager to prove the full chain"
        actions={
          <button
            type="button"
            className="btn btn--primary"
            onClick={handleInject}
            disabled={injecting || !reachable}
          >
            {injecting ? (
              <Loader2 size={15} className="btn__spinner" />
            ) : (
              <ShieldAlert size={15} />
            )}{" "}
            Inject test alert
          </button>
        }
      >
        <ol className="action-list">
          <li>
            <strong>Alertmanager receives it.</strong> Posted to{" "}
            <code className="inline-code">/api/v2/alerts</code>, so it travels the
            real routing path — grouping and inhibition included.
          </li>
          <li>
            <strong>The backend syncs it into an incident.</strong> The host backend
            pulls Alertmanager every 20s, because Alertmanager's own webhook targets
            the in-cluster pod that this dashboard cannot receive from. Use{" "}
            <strong>Sync now</strong> to pull immediately.
          </li>
          <li>
            <strong>OpenSRE investigates.</strong> Evidence is collected and the
            existing investigation engine produces a root-cause report, shown as
            the <strong>RCA</strong> badge in the incidents table below.
          </li>
        </ol>
      </Card>

      {/* ---- Incidents ---- */}
      <Card
        title="Alert-driven incidents"
        subtitle="Alerts this backend has ingested, each with its RCA status"
        actions={
          <Badge tone={incidents.length ? "danger" : "success"}>
            <BellRing size={13} /> {loading ? "…" : incidents.length}
          </Badge>
        }
      >
        {loading ? (
          <div className="stack stack--tight">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} height={44} />
            ))}
          </div>
        ) : incidents.length === 0 ? (
          <div className="empty-state">
            <CheckCircle2 size={28} /> No firing incidents.
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Alert</th>
                  <th>Severity</th>
                  <th>Target</th>
                  <th>RCA</th>
                  <th>Seen</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {incidents.map((a) => (
                  <tr key={a.fingerprint}>
                    <td className="cell-strong cell-mono">{a.alertname}</td>
                    <td>
                      <Badge tone={a.severity === "critical" ? "danger" : "warning"}>
                        {a.severity}
                      </Badge>
                    </td>
                    <td className="cell-mono cell-muted">{a.pod || a.namespace || "—"}</td>
                    <td>
                      <div
                        title={a.investigation_error || undefined}
                        style={{ display: "flex", alignItems: "center", gap: 6 }}
                      >
                        <Badge tone={rcaTone(a.investigation_status)}>
                          {a.investigation_status || "pending"}
                        </Badge>
                        {a.investigation_success && a.incident_id && (
                          <CheckCircle2 size={13} className="cell-muted" />
                        )}
                      </div>
                    </td>
                    <td className="cell-muted">{a.first_seen?.slice(0, 19).replace("T", " ")}</td>
                    <td className="cell-end">
                      <Link
                        to={a.incident_id ? `/incident?report=${a.incident_id}` : "/incident"}
                        className="btn btn--ghost btn--sm"
                        onClick={() => {
                          window.sessionStorage.setItem(
                            "opensre:alert",
                            JSON.stringify(a)
                          );
                        }}
                      >
                        <FileText size={13} /> Report
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ---- Saved RCA reports ---- */}
      <Card
        title="Saved RCA reports"
        subtitle="Every alert-driven investigation auto-saves when it finishes — including failures (e.g. exhausted token), which are recorded so nothing is lost"
        actions={
          <Badge tone={reports.length ? "info" : "neutral"}>
            <Save size={13} /> {loading ? "…" : reports.length} saved
          </Badge>
        }
      >
        {loading ? (
          <div className="stack stack--tight">
            {[0, 1].map((i) => (
              <Skeleton key={i} height={44} />
            ))}
          </div>
        ) : reports.length === 0 ? (
          <div className="empty-state">
            <Save size={28} /> No saved reports yet. Trigger an alert to
            auto-generate and persist one.
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Saved</th>
                  <th>Alert</th>
                  <th>Target</th>
                  <th>Status</th>
                  <th>Outcome</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {reports.map((r) => {
                  let statusTone = "success";
                  let statusLabel = "Analyzed";
                  let detail = r.report?.root_cause || r.report?.summary || "";
                  if (!r.success) {
                    statusTone = "danger";
                    statusLabel = "Failed";
                    detail = r.error || r.hint || "";
                  } else if (!detail) {
                    statusLabel = "Inconclusive";
                    statusTone = "warning";
                  }
                  return (
                    <tr key={r.id || r.fingerprint}>
                      <td className="cell-muted">
                        {r.ts?.slice(0, 19).replace("T", " ")}
                      </td>
                      <td className="cell-strong cell-mono">{r.alertname}</td>
                      <td className="cell-mono cell-muted">{r.pod || r.namespace || "—"}</td>
                      <td>
                        <Badge tone={statusTone}>{statusLabel}</Badge>
                      </td>
                      <td className="cell-muted" style={{ maxWidth: 340 }}>
                        {detail ? (
                          <span
                            title={r.hint || detail}
                            style={{
                              display: "-webkit-box",
                              WebkitLineClamp: 2,
                              WebkitBoxOrient: "vertical",
                              overflow: "hidden",
                            }}
                          >
                            {detail}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="cell-end">
                        <Link
                          to={`/incident?report=${r.id}`}
                          className="btn btn--ghost btn--sm"
                        >
                          <FileText size={13} /> Open report
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ---- Alertmanager's own view ---- */}
      <Card
        title="Alerts known to Alertmanager"
        subtitle="Read from Alertmanager's API, before they reach the incident store"
        actions={
          <Badge tone="info">
            <AlertTriangle size={13} /> {loading ? "…" : amAlerts.length}
          </Badge>
        }
      >
        {loading ? (
          <div className="stack stack--tight">
            {[0, 1].map((i) => (
              <Skeleton key={i} height={40} />
            ))}
          </div>
        ) : amAlerts.length === 0 ? (
          <div className="empty-state">
            <AlertTriangle size={28} /> No alerts are firing or pending.
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Alert</th>
                  <th>Severity</th>
                  <th>Target</th>
                  <th>State</th>
                  <th>Since</th>
                </tr>
              </thead>
              <tbody>
                {amAlerts.map((a) => (
                  <tr key={a.fingerprint}>
                    <td className="cell-strong cell-mono">{a.alertname}</td>
                    <td>
                      <Badge tone={a.severity === "critical" ? "danger" : "warning"}>
                        {a.severity}
                      </Badge>
                    </td>
                    <td className="cell-mono cell-muted">{a.pod || a.namespace || "—"}</td>
                    <td>
                      <Badge tone={stateTone(a.state)}>{a.state}</Badge>
                    </td>
                    <td className="cell-muted">
                      {a.startsAt?.slice(0, 19).replace("T", " ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ---- Live Alertmanager logs ---- */}
      <Card
        title="Alertmanager live stream"
        subtitle={
          logs?.pod
            ? `Container logs from ${logs.pod} — every inbound alert and delivery appears below as it happens`
            : "Container logs from the Alertmanager pod"
        }
        actions={
          logs ? (
            <Badge tone="success">
              <Radio size={12} /> Live · {logs.tail} lines
            </Badge>
          ) : (
            <Badge tone="neutral">
              <Loader2 size={12} className="btn__spinner" /> Tail…
            </Badge>
          )
        }
      >
        {logsError && !logs && (
          <div className="alert alert--warning" style={{ marginBottom: "var(--space-3)" }}>
            <ScrollText size={14} style={{ verticalAlign: "middle" }} /> {logsError}
          </div>
        )}
        <pre
          className="code-block code-block--plain"
          style={{
            maxHeight: 420,
            overflow: "auto",
            fontSize: 12,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {!logs && !logsError ? (
            "Reading the Alertmanager log tail…"
          ) : !logs ? (
            logsError
          ) : (logs.lines || []).length === 0 ? (
            "The Alertmanager log tail is empty (no deliveries yet)."
          ) : (
            (logs.lines || []).map((line, i) => {
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
            })
          )}
        </pre>
        <div
          className="text-muted"
          style={{ marginTop: "var(--space-2)", fontSize: 12 }}
        >
          <Radio size={12} style={{ verticalAlign: "middle" }} /> Auto-refreshes
          every 5s from{" "}
          <code className="inline-code">kubectl logs -c alertmanager</code>.
        </div>
      </Card>
    </>
  );
}
