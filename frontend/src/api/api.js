import axios from "axios";

const api = axios.create({
  baseURL: "http://127.0.0.1:8001/api",
});

// Nginx Demo / Investigation API (read-only evidence, reversible demo)
export const nginxDemoApi = {
  modes: () => api.get("/demo/nginx/modes"),
  status: () => api.get("/demo/nginx/status"),
  health: () => api.get("/nginx/health"),
  fail: (mode) => api.post("/demo/nginx/fail", { mode }),
  investigate: (mode) => api.post("/demo/nginx/investigate", { mode }),
  recover: (mode) => api.post("/demo/nginx/recover", { mode }),
  evidence: () => api.get("/nginx/evidence"),
  opensreInvestigate: () => api.get("/nginx/investigate"),
};

// Database Investigation API
export const dbInvestigationApi = {
  // YugabyteDB
  yugabyteHealth: () => api.get("/db-investigation/yugabyte/health"),
  yugabyteEvidence: (params = {}) => api.get("/db-investigation/yugabyte/evidence", { params }),
  yugabyteQuery: (sql, options = {}) => api.post("/db-investigation/yugabyte/query", { sql, ...options }),
  opensreInvestigateYugabyte: () => api.get("/db-investigation/opensre/investigate/yugabyte"),
  yugabyteLogs: (params = {}) => api.get("/db-investigation/yugabyte/logs", { params }),
  yugabyteLogHistory: (params = {}) => api.get("/db-investigation/yugabyte/log-history", { params }),

  // Aerospike
  aerospikeHealth: () => api.get("/db-investigation/aerospike/health"),
  aerospikeEvidence: (params = {}) => api.get("/db-investigation/aerospike/evidence", { params }),
  aerospikeRecord: (data) => api.post("/db-investigation/aerospike/record", data),
  aerospikeDataIntegrity: (data) => api.post("/db-investigation/aerospike/data-integrity", data),
  aerospikeNamespaceStats: (namespace, set) => api.post("/db-investigation/aerospike/namespace-stats", null, { params: { namespace, set_name: set } }),
  opensreInvestigateAerospike: () => api.get("/db-investigation/opensre/investigate/aerospike"),
  aerospikeLogs: (params = {}) => api.get("/db-investigation/aerospike/logs", { params }),
  aerospikeLogHistory: (params = {}) => api.get("/db-investigation/aerospike/log-history", { params }),

  // All databases
  allDatabaseEvidence: () => api.get("/db-investigation/evidence"),
  opensreInvestigateAll: () => api.get("/db-investigation/opensre/investigate/all"),

  // Demo scenarios
  dbScenarioList: () => api.get("/demo/db-scenario/list"),
  dbScenarioUnavailableFail: (target) => api.post("/demo/db-scenario/unavailable/fail", { target }),
  dbScenarioUnavailableInvestigate: (target) => api.post("/demo/db-scenario/unavailable/investigate", { target }),
  dbScenarioUnavailableRecover: (target) => api.post("/demo/db-scenario/unavailable/recover", { target }),
  dbScenarioLatencyInduce: (target) => api.post("/demo/db-scenario/latency/induce", { target }),
  dbScenarioLatencyInvestigate: (target) => api.post("/demo/db-scenario/latency/investigate", { target }),
  dbScenarioLatencyRecover: (target) => api.post("/demo/db-scenario/latency/recover", { target }),
  dbScenarioDataIntegrityCorrupt: (target) => api.post("/demo/db-scenario/data-integrity/corrupt", { target }),
  dbScenarioDataIntegrityInvestigate: (target) => api.post("/demo/db-scenario/data-integrity/investigate", { target }),
  dbScenarioDataIntegrityRecover: (target) => api.post("/demo/db-scenario/data-integrity/recover", { target }),
  dbScenarioDataIntegrityInsertEmpty: (target) => api.post("/demo/db-scenario/data-integrity/insert-empty", { target }),
  dbScenarioDataIntegrityInsertDuplicates: (target) => api.post("/demo/db-scenario/data-integrity/insert-duplicates", { target }),
  dbScenarioDataIntegrityInsertInvalid: (target) => api.post("/demo/db-scenario/data-integrity/insert-invalid", { target }),
};

// CoreDNS health / evidence API (read-only)
export const corednsApi = {
  health: () => api.get("/coredns/health"),
  probe: () => api.get("/coredns/probe"),
  metrics: () => api.get("/coredns/metrics"),
  evidence: () => api.get("/investigation/evidence/coredns"),
  opensreInvestigate: () => api.get("/opensre/investigate/coredns"),
};

// CoreDNS Demo / Investigation API (controlled failure, reversible)
export const corednsDemoApi = {
  modes: () => api.get("/demo/coredns/modes"),
  status: () => api.get("/demo/coredns/status"),
  fail: (mode) => api.post("/demo/coredns/fail", { mode }),
  investigate: (mode) => api.post("/demo/coredns/investigate", { mode }),
  recover: (mode) => api.post("/demo/coredns/recover", { mode }),
};

// ELK health / evidence API (read-only)
export const elkApi = {
  health: () => api.get("/elasticsearch/health"),
  search: (params) => api.get("/elasticsearch/search", { params }),
  errors: (params) => api.get("/elasticsearch/errors", { params }),
  serviceLogs: (service, params) => api.get(`/elasticsearch/service/${service}`, { params }),
  podLogs: (namespace, pod, params) => api.get(`/elasticsearch/pod/${namespace}/${pod}`, { params }),
  patterns: (params) => api.get("/elasticsearch/patterns", { params }),
  summary: (params) => api.get("/elasticsearch/summary", { params }),
  evidence: (params) => api.get("/elasticsearch/evidence", { params }),
  opensreInvestigate: (params) => api.get("/elasticsearch/investigate", { params }),
  facets: (params) => api.get("/elasticsearch/facets", { params }),
  podsByNamespace: (namespace, params) => api.get(`/elasticsearch/pods-by-namespace`, { params: { namespace, ...params } }),
};

// ELK Demo / Investigation API (controlled failure, reversible)
export const elkDemoApi = {
  modes: () => api.get("/demo/elk/modes"),
  status: () => api.get("/demo/elk/status"),
  fail: (mode) => api.post("/demo/elk/fail", { mode }),
  investigate: (mode) => api.post("/demo/elk/investigate", { mode }),
  recover: (mode) => api.post("/demo/elk/recover", { mode }),
};

// Persisted investigation / incident history (auto-saved server-side on
// every OpenSRE investigation, readable later from the Incident page)
export const incidentsApi = {
  list: (limit = 100) => api.get("/incidents", { params: { limit } }),
  get: (id) => api.get(`/incidents/${encodeURIComponent(id)}`),
  remove: (id) => api.delete(`/incidents/${encodeURIComponent(id)}`),
  clear: () => api.delete("/incidents"),
};

export default api;