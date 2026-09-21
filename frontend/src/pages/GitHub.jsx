import { useEffect, useState } from "react";
import api from "../api/api";
import Card from "../components/Card";
import Badge from "../components/Badge";
import {
  GitBranch,
  CheckCircle2,
  XCircle,
  Loader2,
  AlertCircle,
  GitCommit,
  GitMerge,
  Clock,
  User,
  ExternalLink,
  Search,
  ChevronDown,
  ChevronRight,
  Rocket,
  AlertTriangle,
  RefreshCw,
} from "lucide-react";

const TABS = [
  { id: "commits", label: "Commits", icon: GitCommit },
  { id: "workflows", label: "Workflows", icon: GitMerge },
  { id: "issues", label: "Issues", icon: AlertCircle },
  { id: "correlation", label: "Incident Correlation", icon: Search },
];

export default function GitHub() {
  const [status, setStatus] = useState(null);
  const [checking, setChecking] = useState(true);
  const [repoInfo, setRepoInfo] = useState(null);
  const [branches, setBranches] = useState([]);
  const [selectedBranch, setSelectedBranch] = useState("");
  const [activeTab, setActiveTab] = useState("commits");

  const [commits, setCommits] = useState([]);
  const [workflows, setWorkflows] = useState([]);
  const [issues, setIssues] = useState([]);
  const [loading, setLoading] = useState({ commits: false, workflows: false, issues: false });
  const [error, setError] = useState(null);

  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [issueState, setIssueState] = useState("open");

  const [incidentStart, setIncidentStart] = useState("");
  const [correlationResult, setCorrelationResult] = useState(null);
  const [correlationLoading, setCorrelationLoading] = useState(false);
  const [correlationError, setCorrelationError] = useState(null);

  const [selectedRun, setSelectedRun] = useState(null);

  const refreshGitHub = async () => {
    setChecking(true);
    try {
      const [healthRes, repoRes, branchesRes] = await Promise.all([
        api.get("/github/health"),
        api.get("/github/repo"),
        api.get("/github/branches"),
      ]);
      const health = healthRes.data;
      if (health.success) {
        setStatus("connected");
      } else {
        setStatus(health.error || "unreachable");
      }
      if (repoRes.data.success) setRepoInfo(repoRes.data.data);
      if (branchesRes.data.success) {
        setBranches(branchesRes.data.data || []);
        if (branchesRes.data.data?.length > 0) {
          setSelectedBranch(branchesRes.data.data[0]);
        }
      }
    } catch (e) {
      setStatus(e.message || "backend-offline");
    } finally {
      setChecking(false);
    }
  };
  const [jobs, setJobs] = useState([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [expandedJob, setExpandedJob] = useState(null);
  const [investigation, setInvestigation] = useState(null);
  const [investigating, setInvestigating] = useState(false);
  const [investError, setInvestError] = useState(null);

  useEffect(() => {
    async function load() {
      setChecking(true);
      try {
        const [healthRes, repoRes, branchesRes] = await Promise.all([
          api.get("/github/health"),
          api.get("/github/repo"),
          api.get("/github/branches"),
        ]);
        const health = healthRes.data;
        if (health.success) {
          setStatus("connected");
        } else {
          setStatus(health.error || "unreachable");
        }
        if (repoRes.data.success) setRepoInfo(repoRes.data.data);
        if (branchesRes.data.success) {
          setBranches(branchesRes.data.data || []);
          if (branchesRes.data.data?.length > 0) {
            setSelectedBranch(branchesRes.data.data[0]);
          }
        }
      } catch (e) {
        setStatus(e.message || "backend-offline");
      } finally {
        setChecking(false);
      }
    }
    load();
  }, []);

  const loadCommits = async (branch) => {
    setLoading((prev) => ({ ...prev, commits: true }));
    setError(null);
    try {
      const params = new URLSearchParams();
      if (branch) params.append("sha", branch);
      if (since) params.append("since", since);
      if (until) params.append("until", until);
      params.append("limit", "100");
      const res = await api.get(`/github/commits?${params.toString()}`);
      if (res.data.success) setCommits(res.data.data || []);
      else setError(res.data.error);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading((prev) => ({ ...prev, commits: false }));
    }
  };

  const loadWorkflows = async () => {
    setLoading((prev) => ({ ...prev, workflows: true }));
    setError(null);
    try {
      const res = await api.get("/github/workflows?limit=30");
      if (res.data.success) setWorkflows(res.data.data || []);
      else setError(res.data.error);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading((prev) => ({ ...prev, workflows: false }));
    }
  };

  const loadIssues = async () => {
    setLoading((prev) => ({ ...prev, issues: true }));
    setError(null);
    try {
      const res = await api.get(`/github/issues?state=${issueState}&limit=30`);
      if (res.data.success) setIssues(res.data.data || []);
      else setError(res.data.error);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading((prev) => ({ ...prev, issues: false }));
    }
  };

  const loadJobs = async (run) => {
    setSelectedRun(run);
    setJobs([]);
    setJobsLoading(true);
    setInvestigation(null);
    setInvestError(null);
    setExpandedJob(null);
    try {
      const res = await api.get(`/github/workflow-runs/${run.id}/jobs`);
      if (res.data.success) setJobs(res.data.data || []);
    } catch (e) {
      console.error(e);
    } finally {
      setJobsLoading(false);
    }
  };

  const investigateRun = async (run) => {
    setInvestigating(true);
    setInvestigation(null);
    setInvestError(null);
    try {
      const res = await api.get(`/github/workflow-runs/${run.id}/investigate`);
      const data = res.data;
      if (!data.success) {
        setInvestError(data.stderr || data.error || "Investigation failed");
      } else {
        const stdout = data.stdout || "";
        setInvestigation({ run, stdout, report: extractReport(stdout) });
      }
    } catch (e) {
      setInvestError(e.message);
    } finally {
      setInvestigating(false);
    }
  };

  const loadCorrelation = async () => {
    setCorrelationLoading(true);
    setCorrelationError(null);
    try {
      const params = new URLSearchParams();
      if (incidentStart) {
        params.append("incident_start", new Date(incidentStart).toISOString());
      }
      if (selectedBranch) params.append("branch", selectedBranch);
      params.append("limit", "10");
      const res = await api.get(`/investigation/git-correlation?${params.toString()}`);
      if (res.data.success) setCorrelationResult(res.data);
      else setCorrelationError(res.data.error || "Correlation failed");
    } catch (e) {
      setCorrelationError(e.message);
    } finally {
      setCorrelationLoading(false);
    }
  };

  useEffect(() => {
    if (status === "connected" && selectedBranch) {
      if (activeTab === "commits") loadCommits(selectedBranch);
      else if (activeTab === "workflows") loadWorkflows();
      else if (activeTab === "issues") loadIssues();
    }
  }, [status, activeTab, selectedBranch, issueState]);

  const extractReport = (stdout) => {
    const report = {};
    const rootMatch = stdout.match(/"root_cause":\s*"([\s\S]*?)"\s*,/);
    if (rootMatch) report.root_cause = rootMatch[1].replace(/\\n/g, "\n").replace(/\\"/g, '"');
    const reportMatch = stdout.match(/"report":\s*"([\s\S]*?)"\s*,/);
    if (reportMatch) {
      report.report_md = reportMatch[1]
        .replace(/\\n/g, "\n").replace(/\\"/g, '"')
        .replace(/\\u2022/g, "•").replace(/\\u2014/g, "—");
    }
    return report;
  };

  const formatDate = (d) => (d ? new Date(d).toLocaleString() : "—");

  const getWorkflowStatus = (r) => {
    if (r.conclusion === "success") return { label: "Passed", tone: "success", icon: CheckCircle2 };
    if (r.conclusion === "failure") return { label: "Failed", tone: "danger", icon: XCircle };
    if (r.conclusion === "cancelled") return { label: "Cancelled", tone: "neutral", icon: XCircle };
    if (r.status === "in_progress") return { label: "Running", tone: "info", icon: Loader2 };
    if (r.status === "queued") return { label: "Queued", tone: "neutral", icon: Clock };
    return { label: r.conclusion || r.status, tone: "neutral", icon: Clock };
  };

  const getIssueState = (i) => {
    if (i.state === "open") return { label: "Open", tone: "success", icon: AlertCircle };
    return { label: "Closed", tone: "neutral", icon: CheckCircle2 };
  };

  const tone = status === "connected" ? "success" : status === "unreachable" || status === "backend-offline" ? "danger" : "neutral";
  const label = status === "connected" ? "Connected" : status === "unreachable" ? "Unreachable" : status === "backend-offline" ? "Backend offline" : "Checking…";

  const pipelineStats = {
    passed: workflows.filter((r) => r.conclusion === "success").length,
    failed: workflows.filter((r) => r.conclusion === "failure").length,
    running: workflows.filter((r) => r.status === "in_progress").length,
  };

  const renderCommits = () => {
    if (loading.commits) return <div className="loading"><Loader2 size={16} className="btn__spinner" /> Loading commits…</div>;
    if (!commits.length) return <div className="empty">No commits found</div>;
    return (
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Commit</th>
              <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Message</th>
              <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Author</th>
              <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Date</th>
              <th style={{ textAlign: "left", padding: "var(--space-2)", width: 40 }}></th>
            </tr>
          </thead>
          <tbody>
            {commits.map((c) => (
              <tr key={c.sha} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "var(--space-2)", fontFamily: "monospace", fontSize: 12 }}>
                  {c.sha.substring(0, 7)}
                </td>
                <td style={{ padding: "var(--space-2)", maxWidth: 400, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {c.commit?.message?.split("\n")[0] || "—"}
                </td>
                <td style={{ padding: "var(--space-2)" }}>
                  <User size={14} style={{ marginRight: 4, verticalAlign: "middle" }} />
                  {c.commit?.author?.name || c.author?.login || "—"}
                </td>
                <td style={{ padding: "var(--space-2)", whiteSpace: "nowrap", color: "var(--muted)" }}>
                  {formatDate(c.commit?.author?.date)}
                </td>
                <td style={{ padding: "var(--space-2)" }}>
                  <a href={c.html_url} target="_blank" rel="noreferrer" className="btn btn--ghost btn--sm" style={{ padding: "var(--space-1)" }}>
                    <ExternalLink size={12} />
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  const renderWorkflows = () => {
    if (loading.workflows) return <div className="loading"><Loader2 size={16} className="btn__spinner" /> Loading workflows…</div>;
    if (!workflows.length) return <div className="empty">No workflow runs found</div>;
    return (
      <div>
        <div style={{ display: "flex", gap: "var(--space-4)", marginBottom: "var(--space-3)", fontSize: 13 }}>
          <div>
            <span className="text-muted">Passed: </span>
            <span style={{ fontWeight: 600, color: "var(--color-success)" }}>{pipelineStats.passed}</span>
          </div>
          <div>
            <span className="text-muted">Failed: </span>
            <span style={{ fontWeight: 600, color: "var(--color-danger)" }}>{pipelineStats.failed}</span>
          </div>
          <div>
            <span className="text-muted">Running: </span>
            <span style={{ fontWeight: 600, color: "var(--color-info)" }}>{pipelineStats.running}</span>
          </div>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Run</th>
                <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Workflow</th>
                <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Branch</th>
                <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Commit</th>
                <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Status</th>
                <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Started</th>
                <th style={{ textAlign: "left", padding: "var(--space-2)", width: 80 }}></th>
              </tr>
            </thead>
            <tbody>
              {workflows.slice(0, 50).map((w) => {
                const si = getWorkflowStatus(w);
                const Icon = si.icon;
                const isFailed = w.conclusion === "failure";
                const isSelected = selectedRun?.id === w.id;
                return (
                  <tr
                    key={w.id}
                    style={{
                      borderBottom: "1px solid var(--border)",
                      background: isSelected ? "var(--bg-muted)" : undefined,
                      cursor: "pointer",
                    }}
                    onClick={() => loadJobs(w)}
                  >
                    <td style={{ padding: "var(--space-2)", fontFamily: "monospace", fontSize: 12, color: "var(--muted)" }}>
                      #{w.run_number || w.id}
                    </td>
                    <td style={{ padding: "var(--space-2)", fontWeight: 500 }}>{w.name}</td>
                    <td style={{ padding: "var(--space-2)" }}>
                      <GitBranch size={12} style={{ marginRight: 4, verticalAlign: "middle" }} />
                      {w.head_branch}
                    </td>
                    <td style={{ padding: "var(--space-2)", fontFamily: "monospace", fontSize: 12 }}>
                      {w.head_sha?.substring(0, 7)}
                    </td>
                    <td style={{ padding: "var(--space-2)" }}>
                      <Badge tone={si.tone}><Icon size={12} /> {si.label}</Badge>
                    </td>
                    <td style={{ padding: "var(--space-2)", whiteSpace: "nowrap", color: "var(--muted)" }}>
                      {formatDate(w.run_started_at)}
                    </td>
                    <td style={{ padding: "var(--space-2)" }} onClick={(e) => e.stopPropagation()}>
                      <div style={{ display: "flex", gap: "var(--space-1)" }}>
                        {isFailed && (
                          <button
                            className="btn btn--ghost btn--sm"
                            style={{ padding: "var(--space-1)" }}
                            onClick={() => investigateRun(w)}
                            disabled={investigating}
                          >
                            {investigating && investigation?.run?.id === w.id ? (
                              <Loader2 size={12} className="btn__spinner" />
                            ) : (
                              <Search size={12} />
                            )}
                          </button>
                        )}
                        <a href={w.html_url} target="_blank" rel="noreferrer" className="btn btn--ghost btn--sm" style={{ padding: "var(--space-1)" }}>
                          <ExternalLink size={12} />
                        </a>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {selectedRun && (
          <div style={{ marginTop: "var(--space-4)" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-3)" }}>
              <div>
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>
                  Jobs — Run #{selectedRun.run_number || selectedRun.id}
                </h3>
                <p style={{ fontSize: 12, color: "var(--muted)", margin: 0 }}>
                  {selectedRun.name} on {selectedRun.head_branch}
                </p>
              </div>
              {selectedRun.conclusion === "failure" && (
                <button
                  className="btn btn--primary btn--sm"
                  onClick={() => investigateRun(selectedRun)}
                  disabled={investigating}
                >
                  {investigating ? <Loader2 size={14} className="btn__spinner" /> : <Rocket size={14} />}
                  {" "}Investigate with OpenSRE
                </button>
              )}
            </div>

            {jobsLoading ? (
              <div className="loading"><Loader2 size={16} className="btn__spinner" /> Loading jobs…</div>
            ) : (
              <div style={{ display: "grid", gap: "var(--space-2)" }}>
                {jobs.map((job) => {
                  const jobFailed = job.conclusion === "failure";
                  const isExpanded = expandedJob === job.id;
                  return (
                    <div
                      key={job.id}
                      style={{
                        border: `1px solid ${jobFailed ? "var(--color-danger)" : "var(--border)"}`,
                        borderRadius: 6,
                        overflow: "hidden",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "var(--space-2)",
                          padding: "var(--space-2) var(--space-3)",
                          background: jobFailed ? "rgba(var(--color-danger-rgb, 220,53,69), 0.05)" : undefined,
                          cursor: job.logs_snippet ? "pointer" : "default",
                        }}
                        onClick={() => job.logs_snippet && setExpandedJob(isExpanded ? null : job.id)}
                      >
                        {job.logs_snippet ? (
                          isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />
                        ) : <span style={{ width: 14 }} />}
                        <span style={{ fontWeight: 500, flex: 1 }}>{job.name}</span>
                        <Badge tone={job.conclusion === "success" ? "success" : jobFailed ? "danger" : "neutral"}>
                          {job.conclusion === "success" ? <CheckCircle2 size={12} /> : jobFailed ? <XCircle size={12} /> : <Clock size={12} />}
                          {" "}{job.conclusion || job.status}
                        </Badge>
                      </div>

                      {job.steps?.length > 0 && (
                        <div style={{ padding: "0 var(--space-3) var(--space-2)", display: "flex", gap: "var(--space-2)", flexWrap: "wrap", fontSize: 12 }}>
                          {job.steps.map((step, i) => (
                            <span
                              key={i}
                              style={{
                                display: "inline-flex",
                                alignItems: "center",
                                gap: 4,
                                color: step.conclusion === "failure" ? "var(--color-danger)" : step.conclusion === "success" ? "var(--color-success)" : "var(--muted)",
                              }}
                            >
                              {step.conclusion === "success" ? <CheckCircle2 size={11} /> : step.conclusion === "failure" ? <XCircle size={11} /> : <Clock size={11} />}
                              {" "}{step.name}
                            </span>
                          ))}
                        </div>
                      )}

                      {isExpanded && job.logs_snippet && (
                        <div style={{ borderTop: "1px solid var(--border)", padding: "var(--space-2) var(--space-3)", background: "var(--bg-muted)" }}>
                          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: "var(--space-1)", fontWeight: 500 }}>Failure logs</div>
                          <pre style={{ fontSize: 11, maxHeight: 250, overflow: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all", margin: 0, fontFamily: "monospace" }}>
                            {job.logs_snippet}
                          </pre>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {investError && (
          <div className="alert alert--danger" style={{ marginTop: "var(--space-3)" }}>{investError}</div>
        )}

        {investigation && (
          <div style={{ marginTop: "var(--space-4)" }}>
            <Card
              title="Investigation Report"
              subtitle={`Run #${investigation.run.run_number || investigation.run.id} · ${investigation.run.name} · ${investigation.run.conclusion}`}
              actions={
                <Badge tone={investigation.report?.root_cause ? "success" : "warning"}>
                  {investigation.report?.root_cause ? <><CheckCircle2 size={12} /> Root cause identified</> : <><AlertTriangle size={12} /> Inconclusive</>}
                </Badge>
              }
            >
              {investigation.report?.root_cause && (
                <div style={{ padding: "var(--space-3)", border: "2px solid var(--primary)", borderRadius: 6, background: "var(--bg-muted)", marginBottom: "var(--space-3)" }}>
                  <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: "var(--space-1)", fontWeight: 500 }}>Root Cause</div>
                  <div style={{ fontSize: 13, lineHeight: 1.5 }}>{investigation.report.root_cause}</div>
                </div>
              )}

              {investigation.run.head_sha && (
                <div style={{ padding: "var(--space-3)", border: "2px solid var(--primary)", borderRadius: 6, background: "var(--bg-muted)", marginBottom: "var(--space-3)" }}>
                  <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: "var(--space-1)", display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
                    <GitCommit size={13} /> Suspected change-point commit
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
                    <code style={{ fontSize: 13, fontWeight: 600 }}>{investigation.run.head_sha?.substring(0, 7)}</code>
                  </div>
                  <div style={{ marginTop: "var(--space-2)" }}>
                    <a href={investigation.run.html_url} target="_blank" rel="noreferrer" className="btn btn--ghost btn--sm">
                      <ExternalLink size={12} /> View on GitHub
                    </a>
                  </div>
                </div>
              )}

              {investigation.report?.report_md && (
                <details className="raw-output" open>
                  <summary><ChevronDown size={14} style={{ verticalAlign: "middle" }} /> Full investigation</summary>
                  <pre className="code-block" style={{ fontSize: 12 }}>{investigation.report.report_md}</pre>
                </details>
              )}

              {investigation.stdout && (
                <details className="raw-output">
                  <summary><ChevronDown size={14} style={{ verticalAlign: "middle" }} /> Raw CLI output</summary>
                  <pre className="code-block" style={{ fontSize: 11 }}>{investigation.stdout}</pre>
                </details>
              )}
            </Card>
          </div>
        )}
      </div>
    );
  };

  const renderIssues = () => {
    if (loading.issues) return <div className="loading"><Loader2 size={16} className="btn__spinner" /> Loading issues…</div>;
    if (!issues.length) return <div className="empty">No issues found</div>;
    return (
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Issue</th>
              <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Title</th>
              <th style={{ textAlign: "left", padding: "var(--space-2)" }}>State</th>
              <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Author</th>
              <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Updated</th>
              <th style={{ textAlign: "left", padding: "var(--space-2)", width: 40 }}></th>
            </tr>
          </thead>
          <tbody>
            {issues.slice(0, 50).map((i) => {
              const si = getIssueState(i);
              const Icon = si.icon;
              return (
                <tr key={i.id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "var(--space-2)", fontFamily: "monospace", color: "var(--primary)" }}>#{i.number}</td>
                  <td style={{ padding: "var(--space-2)", maxWidth: 400, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{i.title}</td>
                  <td style={{ padding: "var(--space-2)" }}><Badge tone={si.tone}><Icon size={12} /> {si.label}</Badge></td>
                  <td style={{ padding: "var(--space-2)" }}><User size={14} style={{ marginRight: 4, verticalAlign: "middle" }} />{i.user?.login || "—"}</td>
                  <td style={{ padding: "var(--space-2)", whiteSpace: "nowrap", color: "var(--muted)" }}>{formatDate(i.updated_at)}</td>
                  <td style={{ padding: "var(--space-2)" }}>
                    <a href={i.html_url} target="_blank" rel="noreferrer" className="btn btn--ghost btn--sm" style={{ padding: "var(--space-1)" }}><ExternalLink size={12} /></a>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  };

  const renderCorrelation = () => (
    <div>
      <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "flex-end", marginBottom: "var(--space-4)", flexWrap: "wrap" }}>
        <div>
          <label className="text-muted" style={{ fontSize: 12, display: "block", marginBottom: "var(--space-1)" }}>Incident start (UTC)</label>
          <input type="datetime-local" value={incidentStart} onChange={(e) => setIncidentStart(e.target.value)} style={{ width: 220 }} />
        </div>
        <button onClick={loadCorrelation} className="btn btn--primary btn--sm" disabled={correlationLoading}>
          {correlationLoading ? <><Loader2 size={14} className="btn__spinner" /> Correlating…</> : <><Search size={14} /> Find change-point</>}
        </button>
      </div>

      {correlationError && <div className="alert alert--danger" style={{ marginBottom: "var(--space-4)" }}>{correlationError}</div>}

      {correlationResult && (
        <div style={{ display: "grid", gap: "var(--space-3)" }}>
          {correlationResult.no_commit_found ? (
            <div style={{ padding: "var(--space-3)", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg-muted)" }}>
              <div style={{ fontWeight: 600, marginBottom: "var(--space-1)" }}>No commit found before incident</div>
              <div style={{ fontSize: 13, color: "var(--muted)" }}>
                The incident start ({correlationResult.incident_start || "not specified"}) predates all commits on
                branch <code>{correlationResult.branch || "default"}</code>. Attribution is inconclusive.
              </div>
            </div>
          ) : correlationResult.suspected_commit ? (
            <>
              <div style={{ padding: "var(--space-3)", border: "2px solid var(--primary)", borderRadius: 6, background: "var(--bg-muted)" }}>
                <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: "var(--space-1)" }}>Suspected change-point commit</div>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
                  <code style={{ fontSize: 13, fontWeight: 600 }}>{correlationResult.suspected_commit.sha?.substring(0, 7)}</code>
                  <span style={{ fontSize: 13 }}>{correlationResult.suspected_commit.message}</span>
                </div>
                <div style={{ fontSize: 12, color: "var(--muted)", marginTop: "var(--space-1)", display: "flex", gap: "var(--space-3)" }}>
                  <span><User size={12} style={{ verticalAlign: "middle" }} /> {correlationResult.suspected_commit.author || "—"}</span>
                  <span>{formatDate(correlationResult.suspected_commit.date)}</span>
                </div>
                <div style={{ marginTop: "var(--space-2)" }}>
                  <a href={`https://github.com/${correlationResult.repo || ""}/commit/${correlationResult.suspected_commit.sha}`} target="_blank" rel="noreferrer" className="btn btn--ghost btn--sm">
                    <ExternalLink size={12} /> View on GitHub
                  </a>
                </div>
              </div>

              {correlationResult.window_before_incident?.length > 0 && (
                <div>
                  <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: "var(--space-1)" }}>Commits before incident</div>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid var(--border)" }}>
                        <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Commit</th>
                        <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Message</th>
                        <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Author</th>
                        <th style={{ textAlign: "left", padding: "var(--space-2)" }}>Date</th>
                      </tr>
                    </thead>
                    <tbody>
                      {correlationResult.window_before_incident.map((c) => (
                        <tr key={c.sha} style={{ borderBottom: "1px solid var(--border)" }}>
                          <td style={{ padding: "var(--space-2)", fontFamily: "monospace", fontSize: 12 }}>{c.sha?.substring(0, 7)}</td>
                          <td style={{ padding: "var(--space-2)", maxWidth: 400, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.message}</td>
                          <td style={{ padding: "var(--space-2)" }}><User size={12} style={{ marginRight: 4, verticalAlign: "middle" }} />{c.author || "—"}</td>
                          <td style={{ padding: "var(--space-2)", whiteSpace: "nowrap", color: "var(--muted)" }}>{formatDate(c.date)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          ) : (
            <div style={{ padding: "var(--space-3)", fontSize: 13, color: "var(--muted)" }}>No result returned from correlation service.</div>
          )}
        </div>
      )}

      {!correlationResult && !correlationError && !correlationLoading && (
        <div className="empty-state" style={{ padding: "var(--space-6) 0" }}>
          <Search size={26} />
          <div>
            <strong>Correlate an incident with git history</strong>
            <p style={{ marginTop: "var(--space-1)", maxWidth: 420, fontSize: 13 }}>
              Enter the incident start time and click "Find change-point" to identify which commit was likely deployed before the incident began.
            </p>
          </div>
        </div>
      )}
    </div>
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>GitHub & CI/CD</h1>
          <p className="page-head__sub">
            Repository changes, workflow pipelines, and incident correlation
          </p>
        </div>
      </div>

      <div className="grid-2">
        <Card
          title="Connection"
          subtitle={repoInfo ? `Repository: ${repoInfo.full_name}` : "Configure GITHUB_TOKEN and GITHUB_REPO"}
          actions={
            <>
              <Badge tone={tone}>
                {checking ? <Loader2 size={12} className="btn__spinner" /> : tone === "success" ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
                {checking ? "Checking…" : label}
              </Badge>
              {!checking && (
                <button
                  onClick={refreshGitHub}
                  className="btn btn--ghost btn--sm"
                  title="Re-check GitHub connection"
                  style={{ marginLeft: "var(--space-2)" }}
                >
                  <RefreshCw size={12} />
                </button>
              )}
            </>
          }
        >
          <div className="row">
            <div className="health-item__icon"><GitBranch size={17} strokeWidth={1.8} /></div>
            <div>
              {repoInfo && (
                <>
                  <div className="text-muted" style={{ fontSize: 13, marginBottom: "var(--space-1)" }}>
                    {repoInfo.description || "No description"}
                  </div>
                  <div className="row" style={{ gap: "var(--space-4)", fontSize: 12, color: "var(--muted)" }}>
                    <span>⭐ {repoInfo.stargazers_count}</span>
                    <span>🍴 {repoInfo.forks_count}</span>
                    <span>👁 {repoInfo.watchers_count}</span>
                    <span>{repoInfo.private ? "Private" : "Public"}</span>
                  </div>
                </>
              )}
            </div>
          </div>
        </Card>

        <Card title="Filters" subtitle="Filter data by branch, date range, and state">
          <div style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap", alignItems: "flex-end" }}>
            {branches.length > 0 && (
              <div>
                <label className="text-muted" style={{ fontSize: 12, display: "block", marginBottom: "var(--space-1)" }}>Branch</label>
                <select value={selectedBranch} onChange={(e) => setSelectedBranch(e.target.value)} style={{ padding: "var(--space-1) var(--space-2)", minWidth: 150 }}>
                  {branches.map((b) => <option key={b} value={b}>{b}</option>)}
                </select>
              </div>
            )}
            {activeTab === "commits" && (
              <>
                <div>
                  <label className="text-muted" style={{ fontSize: 12, display: "block", marginBottom: "var(--space-1)" }}>Since</label>
                  <input type="datetime-local" value={since} onChange={(e) => setSince(e.target.value)} style={{ width: 200 }} />
                </div>
                <div>
                  <label className="text-muted" style={{ fontSize: 12, display: "block", marginBottom: "var(--space-1)" }}>Until</label>
                  <input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} style={{ width: 200 }} />
                </div>
              </>
            )}
            {activeTab === "issues" && (
              <div>
                <label className="text-muted" style={{ fontSize: 12, display: "block", marginBottom: "var(--space-1)" }}>Issue State</label>
                <select value={issueState} onChange={(e) => setIssueState(e.target.value)} style={{ padding: "var(--space-1) var(--space-2)" }}>
                  <option value="open">Open</option>
                  <option value="closed">Closed</option>
                  <option value="all">All</option>
                </select>
              </div>
            )}
            <button
              onClick={() => {
                if (activeTab === "commits") loadCommits(selectedBranch);
                else if (activeTab === "workflows") loadWorkflows();
                else if (activeTab === "issues") loadIssues();
                else if (activeTab === "correlation") loadCorrelation();
              }}
              className="btn btn--primary btn--sm"
              disabled={loading[activeTab] || (activeTab === "correlation" && correlationLoading)}
            >
              <Search size={14} /> Refresh
            </button>
          </div>
        </Card>
      </div>

      <div style={{ marginTop: "var(--space-4)" }}>
        <div style={{ display: "flex", gap: "var(--space-1)", marginBottom: "var(--space-4)", borderBottom: "1px solid var(--border)", paddingBottom: "var(--space-2)" }}>
          {TABS.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`btn btn--sm ${activeTab === tab.id ? "btn--primary" : "btn--ghost"}`}
                style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}
              >
                <Icon size={14} /> {tab.label}
              </button>
            );
          })}
        </div>

        {error && <div className="alert alert--danger" style={{ marginBottom: "var(--space-4)" }}>{error}</div>}

        <Card>
          {activeTab === "commits" && renderCommits()}
          {activeTab === "workflows" && renderWorkflows()}
          {activeTab === "issues" && renderIssues()}
          {activeTab === "correlation" && renderCorrelation()}
        </Card>
      </div>
    </>
  );
}
