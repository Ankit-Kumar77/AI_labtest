#!/usr/bin/env bash
#
# =============================================================================
#  OpenSRE DEMO - CHAOS RUNBOOK
# =============================================================================
#  Inject failures into YugabyteDB, Aerospike, Kubernetes pods and nodes so
#  they can be observed and analyzed through the OpenSRE dashboard.
#
#  USAGE
#  -----
#    ./chaos/runbook.sh <failure> [options]
#
#  FAILURES (inject)
#  -----------------
#    aerospike-down          Stop the Aerospike container (health & queries fail)
#    yugabyte-down           Stop the YugabyteDB container (SQL fails)
#    pod-crash               Crash the catalog-api container (real crash -> restart)
#    pod-delete              Delete the catalog-api pod (K8s reschedules -> restart)
#    pod-cpu                 Inject a CPU spike into the catalog-api pod
#    pod-memory              Inject a memory spike into the catalog-api pod
#    pod-latency             Add +5s extra latency to ALL catalog-api traffic
#    flaky-latency           Add latency to flaky-service /orders + /slow traffic
#    system-pod-kill         Kill a kube-system pod (e.g. coredns) -> self-healing
#    coredns-kill              Delete one CoreDNS pod (self-heal, restart evidence)
#    coredns-down              Scale CoreDNS deployment to 0 (sustained DNS outage)
#    coredns-latency           Add netem delay to worker egress (DNS probe slows)
#    elk-error                 Inject ERROR/EXCEPTION log signal into catalog-api
#    elk-connection-refused    Inject CONNECTION REFUSED log signal into catalog-api
#    elk-timeout               Inject TIMEOUT log signal into catalog-api
#    node-cordon             Cordon the worker node (no new pods scheduled)
#    node-drain              Drain the worker node (evicts pods)
#    node-network-latency    Add netem latency to the whole worker node egress
#
#  RECOVERY (restore)
#  ------------------
#    ./chaos/runbook.sh recover aerospike-up        Start Aerospike again
#    ./chaos/runbook.sh recover yugabyte-up         Start Yugabyte again
#    ./chaos/runbook.sh recover latency-off         Clear the injected catalog-api latency
#    ./chaos/runbook.sh recover flaky-latency-off   Clear the injected flaky-service latency
#    ./chaos/runbook.sh recover network-latency-off Remove the worker-node netem delay
#    ./chaos/runbook.sh recover coredns-up           Restore CoreDNS replicas
#    ./chaos/runbook.sh recover coredns-latency-off  Remove the DNS-demo netem delay
#    ./chaos/runbook.sh recover elk-recover          Clear ELK demo failure signal
#    ./chaos/runbook.sh recover uncordon            Uncordon the worker node
#    ./chaos/runbook.sh recover all                 Restore everything
#
#  Every inject / recover is recorded to chaos/experiments/events.jsonl (an
#  experiment ID + timestamp), and currently-active faults are tracked in
#  chaos/experiments/active.json for the Chaos dashboard.
#
# =============================================================================

set -euo pipefail

# ---------- Configuration --------------------------------------------------
CLUSTER="${CLUSTER:-kind-opensre-demo}"
CATALOG_DEPLOY="catalog-api"
CATALOG_NS="opensre"
WORKER_NODE="${WORKER_NODE:-opensre-demo-worker}"
NODE_CONTAINER="${NODE_CONTAINER:-opensre-demo-worker}"
NODE_LATENCY_MS="${NODE_LATENCY_MS:-500}"
COREDNS_NS="${COREDNS_NS:-kube-system}"
COREDNS_DEPLOY="${COREDNS_DEPLOY:-coredns}"
COREDNS_LABEL="${COREDNS_LABEL:-k8s-app=kube-dns}"
COREDNS_LATENCY_MS="${COREDNS_LATENCY_MS:-300}"
COREDNS_REPLICAS_DEFAULT="${COREDNS_REPLICAS_DEFAULT:-2}"
SYSTEM_POD_PATTERN="${SYSTEM_POD_PATTERN:-coredns}"

RUNBOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENTS_DIR="${RUNBOOK_DIR}/experiments"

# ---------- Pretty printing ------------------------------------------------
BOLD="\033[1m"
DIM="\033[2m"
RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
CYAN="\033[36m"
RESET="\033[0m"

banner() { printf "${CYAN}\n==============================================\n  %s\n==============================================\n${RESET}\n" "$*"; }
step()   { printf "${BOLD}${YELLOW}» %s${RESET}\n" "$*"; }
ok()     { printf "${GREEN}  ✓ %s${RESET}\n" "$*"; }
warn()   { printf "${YELLOW}  ⚠ %s${RESET}\n" "$*"; }
fail()   { printf "${RED}  ✗ %s${RESET}\n" "$*"; }
note()   { printf "${DIM}    %s${RESET}\n" "$*"; }

usage() {
  sed -n '3,38p' "$0"
  exit 1
}

# ---------- Preflight ------------------------------------------------------
preflight() {
  step "Preflight checks"

  if ! command -v kubectl &>/dev/null; then
    fail "kubectl not found on PATH"; exit 1
  fi

  if ! kubectl config get-contexts -o name 2>/dev/null | grep -q "${CLUSTER}"; then
    warn "Cluster context '${CLUSTER}' not found in kubeconfig"
    note "Available contexts:"
    kubectl config get-contexts -o name 2>/dev/null | sed 's/^/      /' || true
  fi

  if command -v docker &>/dev/null; then
    note "docker detected — using it for container commands"
  elif command -v podman &>/dev/null; then
    warn "podman detected — using it (aliasing docker)"
    docker() { command podman "$@"; }
  else
    warn "Neither docker nor podman found — container failures will be skipped"
  fi

  ok "Preflight done"
  echo
}

# ---------- Container helpers ----------------------------------------------

# ---------- Experiment registry -------------------------------------------
# Every inject / recover is recorded to chaos/experiments/events.jsonl and
# currently-active faults to chaos/experiments/active.json (both flock-guarded).

exp_id() {
  printf 'exp-%s-%04d' "$(date +%Y%m%d%H%M%S)" "$(( RANDOM % 10000 ))"
}

exp_record() {
  # exp_record <kind> <fault> <target> <params> [note]
  local kind="$1" fault="$2" target="${3:-}" params="${4:-}" note="${5:-}"
  mkdir -p "${EXPERIMENTS_DIR}"
  local id ts
  id="$(exp_id)"
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  {
    flock 9
    python3 -c '
import json, sys
id, kind, fault, target, params, note, ts = sys.argv[1:8]
print(json.dumps({"id": id, "kind": kind, "fault": fault, "target": target,
                  "params": params, "note": note, "ts": ts}))
' "$id" "$kind" "$fault" "$target" "$params" "$note" "$ts" \
      >> "${EXPERIMENTS_DIR}/events.jsonl"
  } 9>"${EXPERIMENTS_DIR}/.lock"
  printf '%s' "$id"
}

exp_active_add() {
  # exp_active_add <fault> <id> <params>
  local fault="$1" id="$2" params="${3:-}"
  mkdir -p "${EXPERIMENTS_DIR}"
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  {
    flock 9
    python3 -c '
import json, sys
fault, id, params, ts, path = sys.argv[1:6]
try:
    data = json.load(open(path))
except Exception:
    data = {}
data[fault] = {"id": id, "params": params, "started": ts}
json.dump(data, open(path, "w"), indent=2)
' "$fault" "$id" "$params" "$ts" "${EXPERIMENTS_DIR}/active.json"
  } 9>"${EXPERIMENTS_DIR}/.lock"
}

exp_active_remove() {
  # exp_active_remove <fault>
  local fault="$1"
  {
    flock 9
    if [[ -f "${EXPERIMENTS_DIR}/active.json" ]]; then
      python3 -c '
import json, sys
fault, path = sys.argv[1:3]
try:
    data = json.load(open(path))
except Exception:
    data = {}
data.pop(fault, None)
json.dump(data, open(path, "w"), indent=2)
' "$fault" "${EXPERIMENTS_DIR}/active.json"
    fi
  } 9>"${EXPERIMENTS_DIR}/.lock"
}

exp_active_show() {  if [[ -f "${EXPERIMENTS_DIR}/active.json" ]] \
    && python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));sys.exit(0 if d else 1)' \
      "${EXPERIMENTS_DIR}/active.json" 2>/dev/null; then
    echo "--- Active faults (chaos/experiments/active.json) ---"
    python3 -c '
import json, sys
data = json.load(open(sys.argv[1]))
for fault, info in sorted(data.items()):
    print("  * {}  (id={}, started={})".format(fault, info.get("id"), info.get("started")))
' "${EXPERIMENTS_DIR}/active.json"
    echo
  fi
}

# ---------- Elasticsearch helper for chaos ----------------------------------
# Write a log entry directly to Elasticsearch for testing/verification
es_write_log() {
  local level="$1" message="$2" fault="$3" exp_id="$4"
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
  local payload
  payload=$(python3 -c '
import json, sys
level, msg, fault, exp_id, ts = sys.argv[1:6]
print(json.dumps({
    "@timestamp": ts,
    "level": level,
    "log": msg,
    "chaos_fault": fault,
    "chaos_experiment_id": exp_id,
    "k8s_namespace": "opensre",
    "k8s_pod_name": "chaos-injector",
    "k8s_container_name": "chaos-runbook",
    "k8s_labels_app": "chaos-runbook"
}))
' "$level" "$message" "$fault" "$exp_id" "$ts")
  curl -s -X POST "http://localhost:9200/logs-opensre/_doc" \
    -H "Content-Type: application/json" \
    -d "$payload" >/dev/null 2>&1 || true
}

# Verify ES connectivity
es_check() {
  curl -s -f "http://localhost:9200/_cluster/health" >/dev/null 2>&1
}

# ---------- Failure: Aerospike down ----------------------------------------
aerospike_down() {
  banner "Injecting failure: AEROSPIKE UNAVAILABLE (K8s)"
  step "Scaling Aerospike StatefulSet to 0 replicas"
  kubectl --context "${CLUSTER}" scale statefulset aerospike -n databases --replicas=0
  ok "Aerospike scaled to 0 — pod terminated"
  local id
  id="$(exp_record inject aerospike-down aerospike "" "statefulset scaled to 0")"
  exp_active_add aerospike-down "${id}" "statefulset scaled to 0"
  echo
  echo "  >>> Open the Aerospike page in the dashboard — health should flip to"
  echo "      'Unreachable' and scans/queries will error out."
  echo
  echo "  >>> Then run './chaos/runbook.sh recover aerospike-up' to bring it back."
}

# ---------- Failure: Yugabyte down -----------------------------------------
yugabyte_down() {
  banner "Injecting failure: YUGABYTE UNAVAILABLE (K8s)"
  step "Scaling YugabyteDB StatefulSet to 0 replicas"
  kubectl --context "${CLUSTER}" scale statefulset yugabytedb -n databases --replicas=0
  ok "YugabyteDB scaled to 0 — pod terminated"
  local id
  id="$(exp_record inject yugabyte-down yugabyte "" "statefulset scaled to 0")"
  exp_active_add yugabyte-down "${id}" "statefulset scaled to 0"
  echo
  echo "  >>> Open the YugabyteDB page in the dashboard — health should flip to"
  echo "      'Unreachable' and SQL queries will fail with a connection error."
  echo
  echo "  >>> Then run './chaos/runbook.sh recover yugabyte-up' to bring it back."
}

# ---------- Failure: Pod crash-loop ----------------------------------------
pod_crash() {
  banner "Injecting failure: CATALOG-API POD CRASH"
  local pod
  pod=$(kubectl --context "${CLUSTER}" get pod -n "${CATALOG_NS}" -l app=catalog-api \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [[ -z "${pod}" ]]; then
    warn "No catalog-api pod found — cannot crash it"
    return
  fi
  step "Calling /failure/crash on pod '${pod}' (the container exits 1)"
  # os._exit(1) kills the process mid-request, so the exec connection drops —
  # that is the expected, harmless failure.
  kubectl --context "${CLUSTER}" exec -n "${CATALOG_NS}" "${pod}" -- \
    python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/failure/crash', timeout=10)" \
    >/dev/null 2>&1 || true
  ok "Crash requested — kubelet will restart the container"
  exp_record inject pod-crash "catalog-api" "" "container exited 1"
  echo
  echo "  >>> Watch the Kubernetes page: the pod RESTARTS column will climb,"
  echo "      and events will show the container exited with code 1."
}

# ---------- Failure: Pod delete (reschedule) -------------------------------
pod_delete() {
  banner "Injecting failure: CATALOG-API POD DELETE"
  step "Deleting the catalog-api pod (K8s will reschedule it)"
  local pod
  pod=$(kubectl --context "${CLUSTER}" get pod -n "${CATALOG_NS}" -l app=catalog-api -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [[ -z "${pod}" ]]; then
    warn "No catalog-api pod found — nothing to delete"
    return
  fi
  kubectl --context "${CLUSTER}" delete pod "${pod}" -n "${CATALOG_NS}" --wait=false
  ok "Deleted pod '${pod}'"
  exp_record inject pod-delete "${pod}" "" "pod deleted, K8s reschedules"
  echo
  echo "  >>> Kubernetes will immediately recreate it. This is a good one to show"
  echo "      'self-healing' + the pod restart count / events in OpenSRE."
}

# ---------- Failure: Pod CPU spike -----------------------------------------
pod_cpu() {
  banner "Injecting failure: CATALOG-API CPU SPIKE"
  step "Running the /failure/cpu endpoint"
  local ep
  ep=$(kubectl --context "${CLUSTER}" get pod -n "${CATALOG_NS}" -l app=catalog-api -o jsonpath='{.items[0].status.podIP}' 2>/dev/null || true)
  if [[ -z "${ep}" ]]; then
    warn "No catalog-api pod found — cannot hit CPU endpoint"
    return
  fi
  ok "Hitting http://${ep}:8000/failure/cpu (~20s busy loop)"
  curl -s --max-time 30 "http://${ep}:8000/failure/cpu" || true
  exp_record inject pod-cpu "catalog-api" "cpu spike (20s)"
  echo
  echo "  >>> CPU metric should spike in Grafana / VictoriaMetrics."
}

# ---------- Failure: Pod memory spike --------------------------------------
pod_memory() {
  banner "Injecting failure: CATALOG-API MEMORY SPIKE"
  step "Running the /failure/memory endpoint (allocates ~300 MB)"
  local ep
  ep=$(kubectl --context "${CLUSTER}" get pod -n "${CATALOG_NS}" -l app=catalog-api -o jsonpath='{.items[0].status.podIP}' 2>/dev/null || true)
  if [[ -z "${ep}" ]]; then
    warn "No catalog-api pod found — cannot hit memory endpoint"
    return
  fi
  ok "Hitting http://${ep}:8000/failure/memory"
  curl -s --max-time 30 "http://${ep}:8000/failure/memory" || true
  exp_record inject pod-memory "catalog-api" "memory spike (~300 MB)"
  echo
  echo "  >>> Resident memory metric should climb. Watch for an OOM/terminate".
}

# ---------- Failure: Pod latency spike -------------------------------------
latency_control() {
  # $1 = query string, e.g. "ms=5000" or "ms=0"
  local ep
  ep=$(kubectl --context "${CLUSTER}" get pod -n "${CATALOG_NS}" -l app=catalog-api \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [[ -z "${ep}" ]]; then
    warn "No catalog-api pod found — cannot control latency"
    return 1
  fi
  step "Calling /failure/latency?${1} on pod '${ep}'"
  local out
  out=$(kubectl --context "${CLUSTER}" exec -n "${CATALOG_NS}" "${ep}" -- \
    python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/failure/latency?${1}', timeout=10).read().decode())" 2>/dev/null || true)
  if [[ -z "${out}" ]]; then
    warn "Could not reach /failure/latency"
    note "Rebuild + reload the catalog-api image for this action to work:"
    note "  docker build -f catalog-api/Containerfile -t localhost/catalog-api:v1 catalog-api"
    note "  kind load docker-image localhost/catalog-api:v1 --name opensre-demo"
    note "  kubectl -n opensre rollout restart deploy/catalog-api"
    return 1
  fi
  ok "${out}"
}

pod_latency() {
  banner "Injecting failure: CATALOG-API LATENCY SPIKE"
  local ms="${POD_LATENCY_MS:-5000}"
  step "Adding ${ms}ms extra latency to all catalog-api traffic"
  latency_control "ms=${ms}"
  local id
  id="$(exp_record inject pod-latency "catalog-api" "ms=${ms}" "persistent delay")"
  exp_active_add pod-latency "${id}" "ms=${ms}"
  echo
  echo "  >>> Traffic-gen hits catalog-api /products constantly, so the Latency"
  echo "      page should show catalog-api p50/p95/p99 climbing toward ${ms}ms,"
  echo "      and the per-pod cards will flag it as HIGH."
  echo
  echo "  >>> Then run './chaos/runbook.sh recover latency-off' to clear it."
}

latency_off() {
  banner "Recovery: CLEAR CATALOG-API LATENCY"
  latency_control "ms=0"
  echo
  echo "  >>> catalog-api latency injection cleared — percentile charts should"
  echo "      drop back to the healthy baseline on the next refresh."
}

# ---------- Failure: Flaky-service latency spike ---------------------------
flaky_latency_control() {
  # $1 = query string, e.g. "ms=3000" or "ms=0"
  local ep
  ep=$(kubectl --context "${CLUSTER}" get pod -n "${CATALOG_NS}" -l app=flaky-service \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [[ -z "${ep}" ]]; then
    warn "No flaky-service pod found — cannot control latency"
    return 1
  fi
  step "Calling /latency?${1} on pod '${ep}'"
  local out
  out=$(kubectl --context "${CLUSTER}" exec -n "${CATALOG_NS}" "${ep}" -- \
    python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/latency?${1}', timeout=10).read().decode())" 2>/dev/null || true)
  if [[ -z "${out}" ]]; then
    warn "Could not reach /latency on flaky-service"
    note "Rebuild + reload the flaky-service image for this action to work:"
    note "  podman build -f fault-apps/flaky-service/Containerfile -t localhost/flaky-service:v1 fault-apps/flaky-service"
    note "  kind load docker-image localhost/flaky-service:v1 --name ${CLUSTER}"
    note "  kubectl -n ${CATALOG_NS} rollout restart deploy/flaky-service"
    return 1
  fi
  ok "${out}"
}

flaky_latency() {
  banner "Injecting failure: FLAKY-SERVICE LATENCY SPIKE"
  local ms="${FLAKY_LATENCY_MS:-3000}"
  step "Adding ${ms}ms extra latency to all flaky-service traffic"
  flaky_latency_control "ms=${ms}"
  local id
  id="$(exp_record inject flaky-latency "flaky-service" "ms=${ms}" "persistent delay")"
  exp_active_add flaky-latency "${id}" "ms=${ms}"
  echo
  echo "  >>> traffic-gen keeps hitting /orders + /slow, so flaky-service"
  echo "      p50/p95/p99 should climb toward ${ms}ms on the Latency page."
  echo
  echo "  >>> Then run './chaos/runbook.sh recover flaky-latency-off'."
}

flaky_latency_off() {
  banner "Recovery: CLEAR FLAKY-SERVICE LATENCY"
  flaky_latency_control "ms=0"
  echo
  echo "  >>> flaky-service latency injection cleared."
}

# ---------- Failure: Node network latency (tc netem) -----------------------
node_network_latency() {
  banner "Injecting failure: NODE NETWORK LATENCY (${NODE_CONTAINER})"
  step "Adding ${NODE_LATENCY_MS}ms netem delay to '${NODE_CONTAINER}' eth0"
  docker exec "${NODE_CONTAINER}" tc qdisc replace dev eth0 root netem delay "${NODE_LATENCY_MS}ms"
  ok "netem delay ${NODE_LATENCY_MS}ms applied to node egress"
  local id
  id="$(exp_record inject node-network-latency "${NODE_CONTAINER}" "ms=${NODE_LATENCY_MS}" "tc netem on node egress")"
  exp_active_add node-network-latency "${id}" "ms=${NODE_LATENCY_MS}"
  echo
  echo "  >>> ALL worker-node egress (catalog-api, flaky-service, traffic-gen)"
  echo "      is now delayed — latency percentiles across the board should climb."
  echo
  echo "  >>> Recover with './chaos/runbook.sh recover network-latency-off'"
}

network_latency_off() {
  banner "Recovery: REMOVE NODE NETEM DELAY (${NODE_CONTAINER})"
  if docker exec "${NODE_CONTAINER}" tc qdisc del dev eth0 root 2>/dev/null; then
    ok "netem delay removed"
  else
    warn "No netem qdisc present on ${NODE_CONTAINER}"
  fi
}

# ---------- Failure: Kill a system pod (self-healing) ----------------------
system_pod_kill() {
  banner "Injecting failure: KILL SYSTEM POD (${SYSTEM_POD_PATTERN})"
  step "Finding and deleting a matching kube-system pod"
  local pod
  pod=$(kubectl --context "${CLUSTER}" get pod -n kube-system --no-headers -o custom-columns=NAME:.metadata.name 2>/dev/null | grep -E "^${SYSTEM_POD_PATTERN}" | head -1 | awk '{print $1}' || true)
  if [[ -z "${pod}" ]]; then
    warn "No kube-system pod matching '${SYSTEM_POD_PATTERN}' found"
    return
  fi
  kubectl --context "${CLUSTER}" delete pod "${pod}" -n kube-system --wait=false
  ok "Deleted system pod '${pod}'"
  exp_record inject system-pod-kill "${pod}" "" "kube-system pod deleted, kubelet recreates"
  echo
  echo "  >>> Kubelet will immediately recreate it. A great visual for cluster"
  echo "      self-healing — watch the pod come back in kubectl get pods -A."
}

# ---------- Failure: CoreDNS pod kill (self-healing, restart evidence) -----
coredns_kill() {
  banner "Injecting failure: COREDNS POD KILL"
  step "Deleting one CoreDNS pod in '${COREDNS_NS}' (deployment recreates it)"
  local pod
  pod=$(kubectl --context "${CLUSTER}" get pod -n "${COREDNS_NS}" -l "${COREDNS_LABEL}" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [[ -z "${pod}" ]]; then
    warn "No CoreDNS pod found (label ${COREDNS_LABEL} in ${COREDNS_NS})"
    return
  fi
  kubectl --context "${CLUSTER}" delete pod "${pod}" -n "${COREDNS_NS}" --wait=false
  ok "Deleted CoreDNS pod '${pod}'"
  exp_record inject coredns-kill "${pod}" "" "coredns pod deleted, deployment recreates"
  echo
  echo "  >>> Watch 'kubectl get pods -n kube-system -l ${COREDNS_LABEL}': a new"
  echo "      pod starts and RESTARTS/age reset. Brief DNS blip possible."
  echo
  echo "  >>> No recovery needed (self-heals); 'recover coredns-up' verifies health."
}

# ---------- Failure: CoreDNS down (sustained DNS outage, demo only) --------
coredns_down() {
  banner "Injecting failure: COREDNS DOWN (demo cluster only)"
  local replicas
  replicas=$(kubectl --context "${CLUSTER}" get deploy "${COREDNS_DEPLOY}" -n "${COREDNS_NS}" \
    -o jsonpath='{.spec.replicas}' 2>/dev/null || true)
  [[ "${replicas}" =~ ^[0-9]+$ ]] || replicas="${COREDNS_REPLICAS_DEFAULT}"
  step "Scaling deployment/${COREDNS_DEPLOY} ${replicas} -> 0 (saving replicas=${replicas})"
  kubectl --context "${CLUSTER}" scale deploy "${COREDNS_DEPLOY}" -n "${COREDNS_NS}" --replicas=0
  ok "CoreDNS scaled to 0 — in-cluster DNS resolution now fails"
  local id
  id="$(exp_record inject coredns-down "${COREDNS_DEPLOY}" "replicas=${replicas}->0" "sustained DNS outage")"
  exp_active_add coredns-down "${id}" "replicas=${replicas}->0"
  echo
  echo "  >>> DNS lookups (kube-dns 10.96.0.10) now time out: the CoreDNS page"
  echo "      probe flips to 'failing' and pod logs/events show the outage."
  echo
  echo "  >>> Recover with './chaos/runbook.sh recover coredns-up'"
}

# ---------- Failure: CoreDNS-visible latency (worker netem) -----------------
coredns_latency() {
  banner "Injecting failure: DNS LATENCY (${NODE_CONTAINER})"
  if exp_active_has node-network-latency; then
    warn "node-network-latency already active — both share the worker qdisc;"
    note "recover it first for a clean DNS-latency signal, or continue anyway"
  fi
  step "Adding ${COREDNS_LATENCY_MS}ms netem delay to '${NODE_CONTAINER}' eth0"
  docker exec "${NODE_CONTAINER}" tc qdisc replace dev eth0 root netem delay "${COREDNS_LATENCY_MS}ms"
  ok "netem delay ${COREDNS_LATENCY_MS}ms applied — DNS probe latency climbs"
  local id
  id="$(exp_record inject coredns-latency "${NODE_CONTAINER}" "ms=${COREDNS_LATENCY_MS}" "tc netem for DNS-latency demo")"
  exp_active_add coredns-latency "${id}" "ms=${COREDNS_LATENCY_MS}"
  echo
  echo "  >>> The CoreDNS resolution probe (avg ms) climbs while the fault is"
  echo "      active; CoreDNS pods themselves stay Running (latency, not outage)."
  echo
  echo "  >>> Recover with './chaos/runbook.sh recover coredns-latency-off'"
}

coredns_latency_off() {
  banner "Recovery: REMOVE DNS-LATENCY NETEM DELAY (${NODE_CONTAINER})"
  if exp_active_has node-network-latency; then
    warn "node-network-latency is still marked active — removing the shared"
    warn "qdisc anyway; re-apply that fault if you still need it"
  fi
  if docker exec "${NODE_CONTAINER}" tc qdisc del dev eth0 root 2>/dev/null; then
    ok "netem delay removed"
  else
    warn "No netem qdisc present on ${NODE_CONTAINER}"
  fi
}

# ---------- Failure: ELK log signal injection --------------------------------
elk_error() {
  banner "Injecting failure: ELK ERROR LOG SIGNAL"
  step "Triggering /failure/error on catalog-api to generate ERROR/EXCEPTION logs"
  local pod
  pod=$(kubectl --context "${CLUSTER}" get pod -n "${CATALOG_NS}" -l app=catalog-api \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [[ -z "${pod}" ]]; then
    warn "No catalog-api pod found — cannot inject ELK error signal"
    return
  fi
  kubectl --context "${CLUSTER}" exec -n "${CATALOG_NS}" "${pod}" -- \
    python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/failure/error', timeout=10)" \
    >/dev/null 2>&1 || true
  ok "ERROR/EXCEPTION log signal injected — Fluent Bit forwards to Elasticsearch"

  # Also write directly to ES with experiment ID for traceability
  local id
  id="$(exp_record inject elk-error "${pod}" "" "ERROR/EXCEPTION log signal")"
  if es_check; then
    es_write_log "ERROR" "Chaos injection: elk-error triggered via /failure/error endpoint" "elk-error" "$id"
    ok "Chaos experiment logged directly to Elasticsearch (id=$id)"
  else
    warn "Elasticsearch not reachable — log only in experiment registry"
  fi
  exp_active_add elk-error "${id}" ""
  echo
  echo "  >>> Check Elasticsearch: 'logs-opensre-*' index for ERROR level logs from catalog-api"
  echo "  >>> Also check Kibana Discover for chaos_experiment_id=$id"
  echo "  >>> Then run './chaos/runbook.sh recover elk-recover' to clear state"
}

elk_connection_refused() {
  banner "Injecting failure: ELK CONNECTION REFUSED LOG SIGNAL"
  step "Triggering /failure/connection-refused on catalog-api"
  local pod
  pod=$(kubectl --context "${CLUSTER}" get pod -n "${CATALOG_NS}" -l app=catalog-api \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [[ -z "${pod}" ]]; then
    warn "No catalog-api pod found — cannot inject ELK connection refused signal"
    return
  fi
  kubectl --context "${CLUSTER}" exec -n "${CATALOG_NS}" "${pod}" -- \
    python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/failure/connection-refused', timeout=10)" \
    >/dev/null 2>&1 || true
  ok "CONNECTION REFUSED log signal injected — Fluent Bit forwards to Elasticsearch"

  # Also write directly to ES with experiment ID for traceability
  local id
  id="$(exp_record inject elk-connection-refused "${pod}" "" "CONNECTION REFUSED log signal")"
  if es_check; then
    es_write_log "ERROR" "Chaos injection: elk-connection-refused triggered via /failure/connection-refused endpoint" "elk-connection-refused" "$id"
    ok "Chaos experiment logged directly to Elasticsearch (id=$id)"
  else
    warn "Elasticsearch not reachable — log only in experiment registry"
  fi
  exp_active_add elk-connection-refused "${id}" ""
  echo
  echo "  >>> Check Elasticsearch: 'logs-opensre-*' index for CONNECTION REFUSED logs"
  echo "  >>> Also check Kibana Discover for chaos_experiment_id=$id"
  echo "  >>> Then run './chaos/runbook.sh recover elk-recover' to clear state"
}

elk_timeout() {
  banner "Injecting failure: ELK TIMEOUT LOG SIGNAL"
  step "Triggering /failure/timed-out on catalog-api"
  local pod
  pod=$(kubectl --context "${CLUSTER}" get pod -n "${CATALOG_NS}" -l app=catalog-api \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [[ -z "${pod}" ]]; then
    warn "No catalog-api pod found — cannot inject ELK timeout signal"
    return
  fi
  kubectl --context "${CLUSTER}" exec -n "${CATALOG_NS}" "${pod}" -- \
    python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/failure/timed-out', timeout=10)" \
    >/dev/null 2>&1 || true
  ok "TIMEOUT/TIMED OUT log signal injected — Fluent Bit forwards to Elasticsearch"

  # Also write directly to ES with experiment ID for traceability
  local id
  id="$(exp_record inject elk-timeout "${pod}" "" "TIMEOUT log signal")"
  if es_check; then
    es_write_log "WARNING" "Chaos injection: elk-timeout triggered via /failure/timed-out endpoint" "elk-timeout" "$id"
    ok "Chaos experiment logged directly to Elasticsearch (id=$id)"
  else
    warn "Elasticsearch not reachable — log only in experiment registry"
  fi
  exp_active_add elk-timeout "${id}" ""
  echo
  echo "  >>> Check Elasticsearch: 'logs-opensre-*' index for TIMEOUT/TIMED OUT logs"
  echo "  >>> Also check Kibana Discover for chaos_experiment_id=$id"
  echo "  >>> Then run './chaos/runbook.sh recover elk-recover' to clear state"
}

elk_recover() {
  banner "Recovery: CLEAR ELK DEMO FAILURE STATE"
  ok "ELK demo state cleared — no persistent fault to remove (log signal was transient)"
  exp_active_remove elk-error
  exp_active_remove elk-connection-refused
  exp_active_remove elk-timeout
}

# ---------- Failure: Node cordon -------------------------------------------
node_cordon() {
  banner "Injecting failure: CORDON WORKER NODE (${WORKER_NODE})"
  step "Marking worker node as unschedulable"
  kubectl --context "${CLUSTER}" cordon "${WORKER_NODE}"
  ok "Node '${WORKER_NODE}' cordoned (SchedulingDisabled)"
  local id
  id="$(exp_record inject node-cordon "${WORKER_NODE}" "" "node cordoned")"
  exp_active_add node-cordon "${id}" ""
  echo
  echo "  >>> Show kubectl get nodes — the worker will show 'Ready,SchedulingDisabled'."
  echo "  >>> New pods will no longer schedule onto it."
  echo
  echo "  >>> Recover with './chaos/runbook.sh recover uncordon'"
}

# ---------- Failure: Node drain --------------------------------------------
node_drain() {
  banner "Injecting failure: DRAIN WORKER NODE (${WORKER_NODE})"
  step "Draining node (evicts pods, marks unschedulable)"
  kubectl --context "${CLUSTER}" drain "${WORKER_NODE}" \
    --ignore-daemonsets --delete-emptydir-data --force
  ok "Node drained"
  local id
  id="$(exp_record inject node-drain "${WORKER_NODE}" "" "node drained")"
  exp_active_add node-cordon "${id}" "via node-drain"
  echo
  echo "  >>> Workload pods are evicted from the worker node."
  echo "  >>> Recover with './chaos/runbook.sh recover uncordon' (then re-start any evicted app pods)."
}

# ---------- Recovery -------------------------------------------------------
container_up() {
  docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null | grep -q '^true$'
}

node_ready() {
  local state
  state=$(kubectl --context "${CLUSTER}" get node "${WORKER_NODE}" --no-headers 2>/dev/null | awk '{print $2}' || true)
  [[ "${state}" == "Ready" ]]
}

recover() {
  local target="${1:-all}"
  banner "RECOVERY: ${target}"
  local id

  case "${target}" in
    aerospike-up)
      step "Scaling Aerospike StatefulSet to 1 replica"
      kubectl --context "${CLUSTER}" scale statefulset aerospike -n databases --replicas=1
      if kubectl --context "${CLUSTER}" rollout status statefulset/aerospike -n databases --timeout=120s; then
        ok "Aerospike running (1 replica, rollout complete)"
        id="$(exp_record recover aerospike-up aerospike "" "statefulset scaled to 1")"
        exp_active_remove aerospike-down
      else
        fail "Aerospike rollout did not complete"; return 1
      fi
      ;;
    yugabyte-up)
      step "Scaling YugabyteDB StatefulSet to 1 replica"
      kubectl --context "${CLUSTER}" scale statefulset yugabytedb -n databases --replicas=1
      if kubectl --context "${CLUSTER}" rollout status statefulset/yugabytedb -n databases --timeout=180s; then
        ok "YugabyteDB running (1 replica, rollout complete)"
        id="$(exp_record recover yugabyte-up yugabyte "" "statefulset scaled to 1")"
        exp_active_remove yugabyte-down
      else
        fail "YugabyteDB rollout did not complete"; return 1
      fi
      ;;
    latency-off)
      step "Clearing catalog-api extra latency"
      if latency_control "ms=0"; then
        ok "catalog-api latency cleared"
        id="$(exp_record recover latency-off "catalog-api" "ms=0" "")"
        exp_active_remove pod-latency
      else
        fail "Could not clear catalog-api latency"; return 1
      fi
      ;;
    flaky-latency-off)
      step "Clearing flaky-service extra latency"
      if flaky_latency_control "ms=0"; then
        ok "flaky-service latency cleared"
        id="$(exp_record recover flaky-latency-off "flaky-service" "ms=0" "")"
        exp_active_remove flaky-latency
      else
        fail "Could not clear flaky-service latency"; return 1
      fi
      ;;
    network-latency-off)
      step "Removing node netem delay"
      network_latency_off
      id="$(exp_record recover network-latency-off "${NODE_CONTAINER}" "" "")"
      exp_active_remove node-network-latency
      ;;
    coredns-up)
      step "Restoring CoreDNS deployment replicas"
      want="${COREDNS_REPLICAS_DEFAULT}"
      if exp_active_has coredns-down; then
        saved=$(python3 -c '
import json, re, sys
try:
    info = json.load(open(sys.argv[1])).get("coredns-down", {})
    m = re.search(r"replicas=(\d+)", info.get("params", ""))
    print(m.group(1) if m else "")
except Exception:
    print("")
' "${EXPERIMENTS_DIR}/active.json" 2>/dev/null || true)
        [[ "${saved}" =~ ^[0-9]+$ && "${saved}" != "0" ]] && want="${saved}"
      fi
      kubectl --context "${CLUSTER}" scale deploy "${COREDNS_DEPLOY}" -n "${COREDNS_NS}" --replicas="${want}"
      if kubectl --context "${CLUSTER}" rollout status deploy/"${COREDNS_DEPLOY}" -n "${COREDNS_NS}" --timeout=90s; then
        ok "CoreDNS restored (${want} replicas, rollout complete)"
        id="$(exp_record recover coredns-up "${COREDNS_DEPLOY}" "replicas=${want}" "")"
        exp_active_remove coredns-down
      else
        fail "CoreDNS rollout did not complete"; return 1
      fi
      ;;
    coredns-latency-off)
      step "Removing DNS-latency netem delay"
      coredns_latency_off
      id="$(exp_record recover coredns-latency-off "${NODE_CONTAINER}" "" "")"
      exp_active_remove coredns-latency
      ;;
    elk-recover)
      step "Clearing ELK demo failure state"
      elk_recover
      id="$(exp_record recover elk-recover "elk" "" "")"
      ok "ELK demo state cleared"
      ;;
    uncordon)
      step "Uncordoning worker node"
      kubectl --context "${CLUSTER}" uncordon "${WORKER_NODE}" >/dev/null 2>&1 || true
      if node_ready; then
        ok "Node '${WORKER_NODE}' Ready"
        id="$(exp_record recover uncordon "${WORKER_NODE}" "" "node uncordoned")"
        exp_active_remove node-cordon
      else
        fail "Node '${WORKER_NODE}' not Ready after uncordon"; return 1
      fi
      ;;
    all)
      recover aerospike-up || true
      recover yugabyte-up || true
      recover latency-off || true
      recover flaky-latency-off || true
      recover network-latency-off || true
      exp_active_has coredns-down && { recover coredns-up || true; }
      exp_active_has coredns-latency && { recover coredns-latency-off || true; }
      exp_active_has elk-error && { recover elk-recover || true; }
      exp_active_has elk-connection-refused && { recover elk-recover || true; }
      exp_active_has elk-timeout && { recover elk-recover || true; }
      recover uncordon || true
      kubectl --context "${CLUSTER}" rollout status deploy "${CATALOG_DEPLOY}" -n "${CATALOG_NS}" \
        >/dev/null 2>&1 && ok "catalog-api running" || warn "catalog-api not scaling"
      ;;
    *)
      warn "Unknown recovery target '${target}'"
      usage
      ;;
  esac
  echo
  echo "  >>> Verify:"
  echo "      docker ps            (containers)"
  echo "      kubectl get nodes    (node state)"
  echo "      kubectl get pods -A  (pod state)"
}

# ---------- Status ---------------------------------------------------------
show_status() {
  banner "CURRENT DEMO STATE"

  echo "--- Databases (Kubernetes StatefulSets, namespace/databases) ---"
  kubectl --context "${CLUSTER}" get pods -n databases -o wide 2>/dev/null || warn "databases namespace not found — apply infra/k8s/yugabytedb + infra/k8s/aerospike"

  echo
  echo "--- Nodes ---"
  kubectl --context "${CLUSTER}" get nodes 2>/dev/null || true

  echo
  echo "--- catalog-api pods ---"
  kubectl --context "${CLUSTER}" get pods -n "${CATALOG_NS}" -o wide 2>/dev/null || true

  echo
  echo "--- CoreDNS pods (${COREDNS_NS}) ---"
  kubectl --context "${CLUSTER}" get pods -n "${COREDNS_NS}" -l "${COREDNS_LABEL}" -o wide 2>/dev/null || true

  echo
  echo "--- Sign of life checks ---"
  local aero_ready yb_ready
  aero_ready=$(kubectl --context "${CLUSTER}" get pod aerospike-0 -n databases -o jsonpath='{.status.phase}:{.status.containerStatuses[0].ready}' 2>/dev/null || echo "missing")
  yb_ready=$(kubectl --context "${CLUSTER}" get pod yugabytedb-0 -n databases -o jsonpath='{.status.phase}:{.status.containerStatuses[0].ready}' 2>/dev/null || echo "missing")
  if [[ "${aero_ready}" == "Running:true" ]]; then
    ok "Aerospike (K8s databases/aerospike-0): RUNNING"
  else
    fail "Aerospike (K8s databases/aerospike-0): ${aero_ready:-DOWN} — no docker start needed, use kubectl/chaos recover"
  fi
  if [[ "${yb_ready}" == "Running:true" ]]; then
    ok "YugabyteDB (K8s databases/yugabytedb-0): RUNNING"
  else
    fail "YugabyteDB (K8s databases/yugabytedb-0): ${yb_ready:-DOWN} — no docker start needed, use kubectl/chaos recover"
  fi
  local node_state
  node_state=$(kubectl --context "${CLUSTER}" get nodes "${WORKER_NODE}" --no-headers 2>/dev/null | awk '{print $2}' || true)
  case "${node_state}" in
    *SchedulingDisabled*) fail "Worker node: CORDONED/DRAINED" ;;
    Ready) ok "Worker node: Ready" ;;
    *) warn "Worker node: ${node_state:-unknown}" ;;
  esac
  local coredns_ready
  coredns_ready=$(kubectl --context "${CLUSTER}" get pods -n "${COREDNS_NS}" -l "${COREDNS_LABEL}" --no-headers 2>/dev/null | awk '$3=="Running"' | wc -l | tr -d ' ' || true)
  case "${coredns_ready:-0}" in
    0) fail "CoreDNS: DOWN (0 Running pods)" ;;
    *) ok "CoreDNS: ${coredns_ready} Running pod(s)" ;;
  esac

  echo
  exp_active_show
}

exp_active_has() {
  # exp_active_has <fault> — exit 0 when the fault is currently tracked active
  local fault="$1"
  [[ -f "${EXPERIMENTS_DIR}/active.json" ]] && python3 -c '
import json, sys
fault, path = sys.argv[1:3]
try:
    data = json.load(open(path))
except Exception:
    data = {}
sys.exit(0 if fault in data else 1)
' "$fault" "${EXPERIMENTS_DIR}/active.json" 2>/dev/null
}

# ---------- Failure: YugabyteDB High Latency ---------------------------------
yugabyte_latency() {
  banner "Injecting failure: YUGABYTE HIGH LATENCY"
  step "Inducing query latency via demo API"
  local resp
  resp=$(curl -s -X POST "http://localhost:8001/api/demo/db-scenario/latency/induce" \
    -H "Content-Type: application/json" -d '{"target": "yugabyte"}' || true)
  ok "YugabyteDB latency induced via heavy queries"
  local id
  id="$(exp_record inject yugabyte-latency yugabyte "" "heavy queries induced")"
  exp_active_add yugabyte-latency "${id}" "heavy queries"
  echo
  echo "  >>> Query latency should increase — check the YugabyteDB page and Metrics."
  echo "  >>> Then run './chaos/runbook.sh recover yugabyte-latency-recover' to clear it."
}

yugabyte_latency_recover() {
  banner "Recovery: CLEAR YUGABYTE LATENCY"
  step "Cleaning up latency-inducing data via demo API"
  local resp
  resp=$(curl -s -X POST "http://localhost:8001/api/demo/db-scenario/latency/recover" \
    -H "Content-Type: application/json" -d '{"target": "yugabyte"}' || true)
  ok "YugabyteDB latency test data cleaned up"
  exp_active_remove yugabyte-latency
}

# ---------- Failure: YugabyteDB Connection Pressure --------------------------
yugabyte_connection_pressure() {
  banner "Injecting failure: YUGABYTE CONNECTION PRESSURE"
  step "Inducing connection pressure via demo API"
  local resp
  resp=$(curl -s -X POST "http://localhost:8001/api/demo/db-scenario/connection-pressure/induce" \
    -H "Content-Type: application/json" -d '{"target": "yugabyte"}' || true)
  ok "YugabyteDB connection pressure induced"
  local id
  id="$(exp_record inject yugabyte-connection-pressure yugabyte "" "connection pressure induced")"
  exp_active_add yugabyte-connection-pressure "${id}" "connection pressure"
  echo
  echo "  >>> Connection pool should show pressure — check the YugabyteDB page."
  echo "  >>> Then run './chaos/runbook.sh recover yugabyte-connection-pressure-recover' to clear it."
}

yugabyte_connection_pressure_recover() {
  banner "Recovery: CLEAR YUGABYTE CONNECTION PRESSURE"
  step "Cleaning up connection pressure data via demo API"
  local resp
  resp=$(curl -s -X POST "http://localhost:8001/api/demo/db-scenario/connection-pressure/recover" \
    -H "Content-Type: application/json" -d '{"target": "yugabyte"}' || true)
  ok "YugabyteDB connection pressure test data cleaned up"
  exp_active_remove yugabyte-connection-pressure
}

# ---------- Failure: Aerospike High Latency ----------------------------------
aerospike_latency() {
  banner "Injecting failure: AEROSPIKE HIGH LATENCY"
  step "Inducing latency via demo API"
  local resp
  resp=$(curl -s -X POST "http://localhost:8001/api/demo/db-scenario/latency/induce" \
    -H "Content-Type: application/json" -d '{"target": "aerospike"}' || true)
  ok "Aerospike latency induced via heavy operations"
  local id
  id="$(exp_record inject aerospike-latency aerospike "" "heavy operations induced")"
  exp_active_add aerospike-latency "${id}" "heavy operations"
  echo
  echo "  >>> Operation latency should increase — check the Aerospike page and Metrics."
  echo "  >>> Then run './chaos/runbook.sh recover aerospike-latency-recover' to clear it."
}

aerospike_latency_recover() {
  banner "Recovery: CLEAR AEROSPIKE LATENCY"
  step "Cleaning up latency-inducing data via demo API"
  local resp
  resp=$(curl -s -X POST "http://localhost:8001/api/demo/db-scenario/latency/recover" \
    -H "Content-Type: application/json" -d '{"target": "aerospike"}' || true)
  ok "Aerospike latency test data cleaned up"
  exp_active_remove aerospike-latency
}

# ---------- Dispatch -------------------------------------------------------
main() {
  [[ $# -lt 1 ]] && usage

  local action="$1"; shift

  case "${action}" in
    aerospike-down)   preflight; aerospike_down ;;
    yugabyte-down)    preflight; yugabyte_down ;;
    yugabyte-latency) preflight; yugabyte_latency ;;
    yugabyte-latency-recover) preflight; yugabyte_latency_recover ;;
    yugabyte-connection-pressure) preflight; yugabyte_connection_pressure ;;
    yugabyte-connection-pressure-recover) preflight; yugabyte_connection_pressure_recover ;;
    aerospike-latency) preflight; aerospike_latency ;;
    aerospike-latency-recover) preflight; aerospike_latency_recover ;;
    pod-crash)        preflight; pod_crash ;;
    pod-delete)       preflight; pod_delete ;;
    pod-cpu)          preflight; pod_cpu ;;
    pod-memory)       preflight; pod_memory ;;
    pod-latency)      preflight; pod_latency ;;
    flaky-latency)    preflight; flaky_latency ;;
    coredns-kill)     preflight; coredns_kill ;;
    coredns-down)     preflight; coredns_down ;;
    coredns-latency)  preflight; coredns_latency ;;
    elk-error)        preflight; elk_error ;;
    elk-connection-refused) preflight; elk_connection_refused ;;
    elk-timeout)      preflight; elk_timeout ;;
    system-pod-kill)  preflight; system_pod_kill ;;
    node-cordon)      preflight; node_cordon ;;
    node-drain)       preflight; node_drain ;;
    node-network-latency) preflight; node_network_latency ;;
    status)           preflight; show_status ;;
    recover)          recover "${@:-all}" ;;
    help|--help|-h)   usage ;;
    *)                usage ;;
  esac
}

main "$@"
