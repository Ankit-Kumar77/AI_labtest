import { useEffect, useState } from "react";
import api, { dbInvestigationApi } from "../api/api";
import Card from "../components/Card";
import Badge from "../components/Badge";
import DbLogs from "../components/DbLogs";
import {
  HardDrive,
  CheckCircle2,
  XCircle,
  Loader2,
  Terminal,
  Play,
  Table,
  List,
  Plus,
  Target,
  Zap,
  RotateCcw,
  Search,
} from "lucide-react";

const DEFAULT_TABLE = "test_table";

export default function Yugabyte() {
  const [status, setStatus] = useState(null);
  const [checking, setChecking] = useState(true);
  const [query, setQuery] = useState("SELECT version();");
  const [queryResult, setQueryResult] = useState(null);
  const [queryError, setQueryError] = useState(null);
  const [queryLoading, setQueryLoading] = useState(false);
  const [tableName, setTableName] = useState(DEFAULT_TABLE);
  const [tableData, setTableData] = useState([]);
  const [tableLoading, setTableLoading] = useState(false);
  const [tableError, setTableError] = useState(null);
  const [insertData, setInsertData] = useState({ id: "", name: "" });
  const [insertResult, setInsertResult] = useState(null);
  const [insertError, setInsertError] = useState(null);
  const [insertLoading, setInsertLoading] = useState(false);

  // --- Demo staged flow ---
  const [demoPhase, setDemoPhase] = useState("idle"); // idle | failed | investigated | recovered
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
      const res = await api.get("/yugabyte/health");
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
  const pollHealthUntilConnected = async (tries = 60) => {
    for (let i = 0; i < tries; i++) {
      try {
        const res = await api.get("/yugabyte/health");
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

  const handleQuery = async () => {
    setQueryLoading(true);
    setQueryError(null);
    setQueryResult(null);
    try {
      const res = await api.post("/yugabyte/query", { sql: query });
      if (res.data.success) {
        setQueryResult(res.data.data);
      } else {
        setQueryError(res.data.error);
      }
    } catch (e) {
      setQueryError(e.message);
    } finally {
      setQueryLoading(false);
    }
  };

  const loadTable = async () => {
    setTableLoading(true);
    setTableError(null);
    setTableData([]);
    try {
      const res = await api.post("/yugabyte/query", {
        sql: `SELECT * FROM ${tableName} LIMIT 50;`,
      });
      if (res.data.success) {
        setTableData(res.data.data || []);
      } else {
        setTableError(res.data.error);
      }
    } catch (e) {
      setTableError(e.message);
    } finally {
      setTableLoading(false);
    }
  };

  const handleInsert = async () => {
    if (!insertData.id || !insertData.name) return;
    setInsertLoading(true);
    setInsertError(null);
    setInsertResult(null);
    try {
      const res = await api.post("/yugabyte/insert", {
        table: tableName,
        data: { id: parseInt(insertData.id), name: insertData.name },
      });
      if (res.data.success) {
        setInsertResult(res.data.data);
        setInsertData({ id: "", name: "" });
        loadTable();
      } else {
        setInsertError(res.data.error);
      }
    } catch (e) {
      setInsertError(e.message);
    } finally {
      setInsertLoading(false);
    }
  };

  const createTable = async () => {
    setQueryLoading(true);
    setQueryError(null);
    try {
      const res = await api.post("/yugabyte/execute", {
        sql: `CREATE TABLE IF NOT EXISTS ${tableName} (id INT PRIMARY KEY, name TEXT);`,
      });
      if (res.data.success) {
        setQueryResult("Table created/verified");
        loadTable();
      } else {
        setQueryError(res.data.error);
      }
    } catch (e) {
      setQueryError(e.message);
    } finally {
      setQueryLoading(false);
    }
  };

  useEffect(() => {
    if (status === "connected") loadTable();
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

  const formatResult = (data) => {
    if (!data) return "No data";
    if (Array.isArray(data)) {
      if (data.length === 0) return "Empty result";
      return JSON.stringify(data, null, 2);
    }
    return JSON.stringify(data, null, 2);
  };

  const renderTable = (data) => {
    if (!data || !Array.isArray(data) || data.length === 0) return null;
    const columns = Object.keys(data[0]);
    return (
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              {columns.map((col) => (
                <th key={col} style={{ textAlign: "left", padding: "var(--space-2)" }}>
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.slice(0, 50).map((row, i) => (
              <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                {columns.map((col) => (
                  <td key={col} style={{ padding: "var(--space-2)", fontFamily: "monospace" }}>
                    {row[col] ?? "NULL"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  // --- Demo handlers ---
  const handleFail = async () => {
    setDemoLoading(true);
    setDemoError(null);
    setDemoInvestigation(null);
    setDemoRecovery(null);
    try {
      const res = await api.post("/demo/db-failure/fail", { target: "yugabyte" });
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
      const res = await api.post("/demo/db-failure/investigate", { target: "yugabyte" });
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
      const res = await api.post("/demo/db-failure/recover", { target: "yugabyte" });
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
          <h1>YugabyteDB (YSQL)</h1>
          <p className="page-head__sub">
            Distributed PostgreSQL-compatible database \u00b7 SQL operations
          </p>
        </div>
      </div>

      <div className="grid-2">
        <Card
          title="Connection"
          subtitle="YSQL API \u00b7 PostgreSQL wire protocol"
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
              <HardDrive size={17} strokeWidth={1.8} />
            </div>
            <div>
              <div className="text-muted" style={{ fontSize: 13 }}>
                YugabyteDB server: localhost:5433
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

        <Card title="Quick Actions" subtitle="Common database operations">
          <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", marginBottom: "var(--space-4)" }}>
            <button onClick={createTable} className="btn btn--primary btn--sm" disabled={queryLoading}>
              <Table size={14} /> Create Table
            </button>
            <button onClick={loadTable} className="btn btn--ghost btn--sm" disabled={tableLoading}>
              <List size={14} /> Refresh Table
            </button>
          </div>
        </Card>
      </div>

      <div className="grid-2">
        <Card title="SQL Query" subtitle="Execute arbitrary SQL">
          <div style={{ display: "flex", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}>
            <textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              rows={4}
              style={{ flex: 1, fontFamily: "monospace", fontSize: 13 }}
              placeholder="SELECT * FROM test_table;"
            />
            <button onClick={handleQuery} className="btn btn--primary" disabled={queryLoading} style={{ alignSelf: "flex-end" }}>
              <Play size={14} /> Run
            </button>
          </div>

          {queryError && (
            <div className="alert alert--danger" style={{ marginBottom: "var(--space-3)" }}>
              {queryError}
            </div>
          )}
          {queryResult !== null && (
            <div style={{ background: "var(--bg-tertiary)", borderRadius: "var(--radius-sm)", padding: "var(--space-3)", fontFamily: "monospace", fontSize: 12, whiteSpace: "pre-wrap", maxHeight: 300, overflow: "auto" }}>
              {formatResult(queryResult)}
            </div>
          )}
        </Card>

        <Card title={`Table: ${tableName}`} subtitle="Data browser & insert">
          <div style={{ marginBottom: "var(--space-3)" }}>
            <input
              type="text"
              value={tableName}
              onChange={(e) => setTableName(e.target.value)}
              placeholder="Table name"
              style={{ width: "100%", maxWidth: 200, marginBottom: "var(--space-2)" }}
            />
            <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
              <input
                type="text"
                placeholder="ID (int)"
                value={insertData.id}
                onChange={(e) => setInsertData({ ...insertData, id: e.target.value })}
                style={{ width: 80 }}
              />
              <input
                type="text"
                placeholder="Name"
                value={insertData.name}
                onChange={(e) => setInsertData({ ...insertData, name: e.target.value })}
                style={{ flex: 1, minWidth: 120 }}
              />
              <button onClick={handleInsert} className="btn btn--primary btn--sm" disabled={insertLoading}>
                <Plus size={14} /> Insert
              </button>
            </div>
          </div>

          {insertError && (
            <div className="alert alert--danger" style={{ marginBottom: "var(--space-3)" }}>
              {insertError}
            </div>
          )}
          {insertResult && (
            <div className="alert alert--success" style={{ marginBottom: "var(--space-3)" }}>
              Inserted: {JSON.stringify(insertResult)}
            </div>
          )}

          {tableError && (
            <div className="alert alert--danger" style={{ marginBottom: "var(--space-3)" }}>
              {tableError}
            </div>
          )}

          {tableLoading ? (
            <div style={{ textAlign: "center", padding: "var(--space-4)" }}>
              <Loader2 size={16} className="btn__spinner" /> Loading\u2026
            </div>
          ) : (
            renderTable(tableData)
          )}
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

        {/* Phase badges */}
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

        {/* Seed info */}
        {demoSeed && (
          <div style={{ marginBottom: "var(--space-3)", fontSize: 13 }}>
            <strong>Seeded:</strong> {demoSeed.inserted?.join(", ") || "\u2014"}
          </div>
        )}

        {/* Fault info */}
        {demoFault && (
          <div style={{ marginBottom: "var(--space-3)", fontSize: 13 }}>
            <strong>Fault:</strong>{" "}
            <Badge tone="danger">{demoFault.action}</Badge>
            <span className="text-muted" style={{ marginLeft: "var(--space-2)" }}>
              container {demoFault.container_stopped} stopped
            </span>
          </div>
        )}

        {/* Recovery info */}
        {demoRecovery && (
          <div style={{ marginBottom: "var(--space-3)", fontSize: 13 }}>
            <strong>Recovery:</strong>{" "}
            <Badge tone="success">{demoRecovery.action}</Badge>
            <span className="text-muted" style={{ marginLeft: "var(--space-2)" }}>
              container {demoRecovery.container_restarted} restarted
            </span>
          </div>
        )}

        {/* OpenSRE investigation result */}
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
        target="yugabyte"
        podHint="databases/yugabytedb-0"
        fetchLive={(params) => dbInvestigationApi.yugabyteLogs(params)}
        fetchHistory={(params) => dbInvestigationApi.yugabyteLogHistory(params)}
      />
    </>
  );
}
