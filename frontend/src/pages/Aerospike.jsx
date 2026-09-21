import { useEffect, useState } from "react";
import api, { dbInvestigationApi } from "../api/api";
import Card from "../components/Card";
import Badge from "../components/Badge";
import DbLogs from "../components/DbLogs";
import {
  Database,
  CheckCircle2,
  XCircle,
  Loader2,
  Plus,
  Trash2,
  List,
  Target,
  Zap,
  RotateCcw,
  Search,
} from "lucide-react";

const NAMESPACE = "test";
const SET_NAME = "demo";

export default function Aerospike() {
  const [status, setStatus] = useState(null);
  const [checking, setChecking] = useState(true);
  const [records, setRecords] = useState([]);
  const [loadingRecords, setLoadingRecords] = useState(false);
  const [newRecord, setNewRecord] = useState({ key: "", bins: { name: "", value: "" } });
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  // --- Demo staged flow ---
  const [demoPhase, setDemoPhase] = useState("idle");
  const [demoLoading, setDemoLoading] = useState(false);
  const [demoSeed, setDemoSeed] = useState(null);
  const [demoFault, setDemoFault] = useState(null);
  const [demoInvestigation, setDemoInvestigation] = useState(null);
  const [demoRecovery, setDemoRecovery] = useState(null);
  const [demoError, setDemoError] = useState(null);
  const [healthHint, setHealthHint] = useState(null);
  const [healthPf, setHealthPf] = useState(null);

  const loadHealth = async () => {
    setChecking(true);
    try {
      const res = await api.get("/aerospike/health");
      setStatus(res.data.success ? "connected" : "unreachable");
      setHealthHint(!res.data.success ? res.data.hint || res.data.error || null : null);
      setHealthPf(!res.data.success ? res.data.port_forward || null : null);
    } catch {
      setStatus("backend-offline");
      setHealthHint(null);
      setHealthPf(null);
    } finally {
      setChecking(false);
    }
  };

  useEffect(() => {
    loadHealth();
  }, []);

  // After a recover, rollout + port-forward restart can take a while —
  // keep polling until the backend reports connected (or give up with
  // the latest hint so the user knows exactly what to run).
  const pollHealthUntilConnected = async (tries = 40) => {
    for (let i = 0; i < tries; i++) {
      try {
        const res = await api.get("/aerospike/health");
        if (res.data.success) {
          setStatus("connected");
          setHealthHint(null);
          setHealthPf(null);
          return;
        }
        setStatus("unreachable");
        setHealthHint(res.data.hint || res.data.error || null);
        setHealthPf(res.data.port_forward || null);
      } catch {
        setStatus("backend-offline");
      }
      await new Promise((r) => setTimeout(r, 3000));
    }
  };

  const loadRecords = async () => {
    setLoadingRecords(true);
    try {
      const res = await api.post("/aerospike/scan", { namespace: NAMESPACE, set: SET_NAME });
      if (res.data.success) {
        setRecords(res.data.data || []);
      } else {
        setError(res.data.error);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingRecords(false);
    }
  };

  const handleWrite = async () => {
    if (!newRecord.key || !newRecord.bins.name) return;
    try {
      const res = await api.post("/aerospike/write", {
        namespace: NAMESPACE,
        set: SET_NAME,
        key: newRecord.key,
        bins: { name: newRecord.bins.name, value: newRecord.bins.value || 0 },
      });
      if (res.data.success) {
        setSuccess("Record written");
        setNewRecord({ key: "", bins: { name: "", value: "" } });
        loadRecords();
      } else {
        setError(res.data.error);
      }
    } catch (e) {
      setError(e.message);
    }
  };

  const handleDelete = async (key) => {
    if (!confirm(`Delete record ${key}?`)) return;
    try {
      const res = await api.post("/aerospike/delete", {
        namespace: NAMESPACE,
        set: SET_NAME,
        key,
      });
      if (res.data.success) {
        setSuccess("Record deleted");
        loadRecords();
      } else {
        setError(res.data.error);
      }
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    if (status === "connected") loadRecords();
  }, [status]);

  const tone =
    status === "connected"
      ? "success"
      : status === "unreachable" || status === "backend-offline"
      ? "danger"
      : "neutral";

  const label =
    status === "connected"
      ? "Connected"
      : status === "unreachable"
      ? "Unreachable"
      : status === "backend-offline"
      ? "Backend offline"
      : "Checking\u2026";

  // --- Demo handlers ---
  const handleFail = async () => {
    setDemoLoading(true);
    setDemoError(null);
    setDemoInvestigation(null);
    setDemoRecovery(null);
    try {
      const res = await api.post("/demo/db-failure/fail", { target: "aerospike" });
      if (res.data.success) {
        setDemoPhase("failed");
        setDemoSeed(res.data.seed);
        setDemoFault(res.data.fault);
        setStatus("unreachable");
      } else {
        setDemoError(res.data.error || "Failed to inject fault");
      }
    } catch (e) {
      setDemoError(e.message);
    } finally {
      setDemoLoading(false);
    }
  };

  const handleInvestigate = async () => {
    setDemoLoading(true);
    setDemoError(null);
    try {
      const res = await api.post("/demo/db-failure/investigate", { target: "aerospike" });
      if (res.data.success) {
        setDemoPhase("investigated");
        setDemoInvestigation(res.data.opensre);
      } else {
        setDemoError(
          res.data.opensre?.error ||
            res.data.opensre?.hint ||
            res.data.error ||
            "Investigation failed"
        );
      }
    } catch (e) {
      setDemoError(e.message);
    } finally {
      setDemoLoading(false);
    }
  };

  const handleRecover = async () => {
    setDemoLoading(true);
    setDemoError(null);
    try {
      const res = await api.post("/demo/db-failure/recover", { target: "aerospike" });
      if (res.data.success) {
        setDemoPhase("recovered");
        setDemoRecovery(res.data.recovery);
        // Backend restarts the port-forward during recover — poll until
        // health actually flips to connected.
        pollHealthUntilConnected();
      } else {
        setDemoError(
          res.data.recovery?.port_forward_hint ||
            res.data.error ||
            "Recovery failed"
        );
      }
    } catch (e) {
      setDemoError(e.message);
    } finally {
      setDemoLoading(false);
    }
  };

  const handleReset = () => {
    setDemoPhase("idle");
    setDemoSeed(null);
    setDemoFault(null);
    setDemoInvestigation(null);
    setDemoRecovery(null);
    setDemoError(null);
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Aerospike</h1>
          <p className="page-head__sub">
            High-performance NoSQL database \u00b7 key-value operations
          </p>
        </div>
      </div>

      <div className="grid-2">
        <Card
          title="Connection"
          subtitle={`Namespace: ${NAMESPACE} \u00b7 Set: ${SET_NAME}`}
          actions={
            <Badge tone={tone}>
              {checking ? (
                <Loader2 size={12} className="btn__spinner" />
              ) : tone === "success" ? (
                <CheckCircle2 size={12} />
              ) : (
                <XCircle size={12} />
              )}
              {checking ? "Checking\u2026" : label}
            </Badge>
          }
        >
          <div className="row">
            <div className="health-item__icon">
              <Database size={17} strokeWidth={1.8} />
            </div>
            <div>
              <div className="text-muted" style={{ fontSize: 13 }}>
                Aerospike server: localhost:3001
              </div>
              {healthHint && (
                <div className="alert alert--danger" style={{ marginTop: "var(--space-2)", fontSize: 12 }}>
                  {healthHint}
                  {healthPf && (
                    <pre style={{ marginTop: 6, fontSize: 11, whiteSpace: "pre-wrap" }}>{healthPf}</pre>
                  )}
                </div>
              )}
            </div>
          </div>
        </Card>

        <Card title="Operations" subtitle="Read, write, and delete records">
          <div style={{ display: "flex", gap: "var(--space-3)", marginBottom: "var(--space-4)" }}>
            <input
              type="text"
              placeholder="Key"
              value={newRecord.key}
              onChange={(e) => setNewRecord({ ...newRecord, key: e.target.value })}
              style={{ flex: 1, maxWidth: 180 }}
            />
            <input
              type="text"
              placeholder="Name"
              value={newRecord.bins.name}
              onChange={(e) => setNewRecord({ ...newRecord, bins: { ...newRecord.bins, name: e.target.value } })}
              style={{ flex: 1, maxWidth: 180 }}
            />
            <input
              type="text"
              placeholder="Value"
              value={newRecord.bins.value}
              onChange={(e) => setNewRecord({ ...newRecord, bins: { ...newRecord.bins, value: e.target.value } })}
              style={{ flex: 1, maxWidth: 180 }}
            />
            <button onClick={handleWrite} className="btn btn--primary btn--sm">
              <Plus size={14} /> Write
            </button>
            <button onClick={loadRecords} className="btn btn--ghost btn--sm" disabled={loadingRecords}>
              <List size={14} /> Scan
            </button>
          </div>

          {error && (
            <div className="alert alert--danger" style={{ marginBottom: "var(--space-3)" }}>
              {error}
            </div>
          )}
          {success && (
            <div className="alert alert--success" style={{ marginBottom: "var(--space-3)" }}>
              {success}
            </div>
          )}

          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Key</th>
                  <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Bins</th>
                  <th style={{ textAlign: "left", padding: "var(--space-2)", width: 80 }}></th>
                </tr>
              </thead>
              <tbody>
                {loadingRecords ? (
                  <tr>
                    <td colSpan={3} style={{ padding: "var(--space-4)", textAlign: "center" }}>
                      <Loader2 size={16} className="btn__spinner" /> Loading\u2026
                    </td>
                  </tr>
                ) : records.length === 0 ? (
                  <tr>
                    <td colSpan={3} style={{ padding: "var(--space-4)", textAlign: "center", color: "var(--muted)" }}>
                      No records found. Click "Scan" or write a new record.
                    </td>
                  </tr>
                ) : (
                  records.map((r) => (
                    <tr key={r.key} style={{ borderBottom: "1px solid var(--border)" }}>
                      <td style={{ padding: "var(--space-2)", fontFamily: "monospace", fontSize: 13 }}>
                        {r.key}
                      </td>
                      <td style={{ padding: "var(--space-2)", fontSize: 13 }}>
                        {JSON.stringify(r.bins)}
                      </td>
                      <td style={{ padding: "var(--space-2)" }}>
                        <button
                          onClick={() => handleDelete(r.key)}
                          className="btn btn--ghost btn--sm"
                          style={{ padding: "var(--space-1) var(--space-2)" }}
                        >
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {/* ---- Database failure demo (staged flow) ---- */}
      <Card
        title="Database failure demo"
        subtitle={
          demoPhase === "idle"
            ? "Step 1: Seed instance records and stop the database"
            : demoPhase === "failed"
            ? "Step 2: Database is DOWN \u2014 run OpenSRE investigation"
            : demoPhase === "investigated"
            ? "Step 3: RCA collected \u2014 recover the database"
            : "Done \u2014 database recovered"
        }
        actions={
          demoPhase === "idle" ? (
            <button className="btn btn--primary" onClick={handleFail} disabled={demoLoading}>
              {demoLoading ? <Loader2 size={15} className="btn__spinner" /> : <Zap size={15} />}
              {" "}1. Create & fail instance
            </button>
          ) : demoPhase === "failed" ? (
            <button className="btn btn--primary" onClick={handleInvestigate} disabled={demoLoading}>
              {demoLoading ? <Loader2 size={15} className="btn__spinner" /> : <Search size={15} />}
              {" "}2. Investigate with OpenSRE
            </button>
          ) : demoPhase === "investigated" ? (
            <button className="btn btn--primary" onClick={handleRecover} disabled={demoLoading}>
              {demoLoading ? <Loader2 size={15} className="btn__spinner" /> : <RotateCcw size={15} />}
              {" "}3. Recover
            </button>
          ) : (
            <button className="btn btn--ghost btn--sm" onClick={handleReset}>
              Reset demo
            </button>
          )
        }
      >
        {demoError && (
          <div className="alert alert--danger" style={{ marginBottom: "var(--space-3)" }}>
            {demoError}
          </div>
        )}

        <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", marginBottom: "var(--space-3)" }}>
          <Badge tone={demoPhase === "idle" ? "info" : demoPhase !== "idle" ? "success" : "info"}>
            {demoPhase === "idle" ? "Waiting" : "Seeded"}
          </Badge>
          <Badge tone={demoPhase === "failed" || demoPhase === "investigated" || demoPhase === "recovered" ? "danger" : "info"}>
            {demoPhase === "failed" || demoPhase === "investigated" || demoPhase === "recovered" ? "DOWN" : "Healthy"}
          </Badge>
          {demoPhase === "investigated" && <Badge tone="success">RCA ready</Badge>}
          {demoPhase === "recovered" && <Badge tone="success">Recovered</Badge>}
        </div>

        {demoSeed && (
          <div style={{ marginBottom: "var(--space-3)", fontSize: 13 }}>
            <strong>Seeded:</strong> {demoSeed.inserted?.join(", ") || "\u2014"}
          </div>
        )}

        {demoFault && (
          <div style={{ marginBottom: "var(--space-3)", fontSize: 13 }}>
            <strong>Fault:</strong>{" "}
            <Badge tone="danger">{demoFault.action}</Badge>
            <span className="text-muted" style={{ marginLeft: "var(--space-2)" }}>
              container {demoFault.container_stopped} stopped
            </span>
          </div>
        )}

        {demoRecovery && (
          <div style={{ marginBottom: "var(--space-3)", fontSize: 13 }}>
            <strong>Recovery:</strong>{" "}
            <Badge tone="success">{demoRecovery.action}</Badge>
            <span className="text-muted" style={{ marginLeft: "var(--space-2)" }}>
              container {demoRecovery.container_restarted} restarted
            </span>
          </div>
        )}

        {demoInvestigation && (
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
              {demoInvestigation.stdout || demoInvestigation.stderr || "No output"}
            </pre>
          </div>
        )}
      </Card>

      <DbLogs
        target="aerospike"
        podHint="databases/aerospike-0"
        fetchLive={(params) => dbInvestigationApi.aerospikeLogs(params)}
        fetchHistory={(params) => dbInvestigationApi.aerospikeLogHistory(params)}
      />
    </>
  );
}
