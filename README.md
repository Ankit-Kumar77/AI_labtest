# 🚀 OpenSRE Dashboard

An AI-powered Kubernetes Observability Platform that combines **OpenSRE**, **Kubernetes**, **VictoriaMetrics**, **OpenTelemetry**, and **Grafana** into a single dashboard for monitoring, troubleshooting, and AI-assisted incident analysis.

The project demonstrates how modern observability tools can be integrated with AI to simplify Kubernetes operations and provide a centralized monitoring experience.

---

## ✨ Features

### Infrastructure

- Kubernetes Cluster (Kind)
- Sample FastAPI Application
- OpenTelemetry Collector
- VictoriaMetrics
- vmagent
- Grafana
- Aerospike Database
- YugabyteDB Database
- **Elasticsearch & Kibana (ELK Stack for Logs)**

### Backend

- FastAPI REST APIs
- Kubernetes Integration
- OpenSRE CLI Integration
- VictoriaMetrics Health Check
- Command Execution Layer
- GitHub Integration (commits, branches, workflows, issues)
- Aerospike Connector
- YugabyteDB Connector

### Frontend

- Dashboard
- Kubernetes Overview
- Metrics Page
- Latency Page (live p50/p95/p99, per-pod view, auto-refreshing)
- AI Analysis
- GitHub Integration
- Aerospike Console
- YugabyteDB Console
- Settings Page

---

# 🏗 Architecture

```
                        +----------------------+
                        |     React Frontend   |
                        |     (Dashboard)      |
                        +----------+-----------+
                                   |
                          REST API Calls
                                   |
                                   v
                     +---------------------------+
                     |     FastAPI Backend       |
                     |  OpenSRE API Layer        |
                     +------------+--------------+
                                  |
          +-----------------------+-----------------------+
          |                       |                       |
          |                       |                       |
          v                       v                       v

  Kubernetes Cluster      OpenSRE CLI          VictoriaMetrics

          |                                        |
          |                                        |
          +-------------------+--------------------+
                              |
                              v
                         OpenTelemetry
                              |
                              v
                           Grafana
```

---

# 🛠 Tech Stack

| Category | Technology |
|----------|------------|
| Frontend | React + Vite |
| Backend | FastAPI |
| Container Runtime | Podman |
| Kubernetes | Kind |
| Metrics | VictoriaMetrics |
| Metrics Collection | vmagent |
| Telemetry | OpenTelemetry Collector |
| Visualization | Grafana |
| **Log Storage** | **Elasticsearch** |
| **Log Visualization** | **Kibana** |
| Log Shipper | Fluent Bit |
| NoSQL Database | Aerospike |
| Distributed SQL Database | YugabyteDB |
| Source Control Integration | GitHub API |
| AI | OpenSRE |
| Language | Python 3.13 |
| Package Manager | pip |
| API Testing | Bruno |
| Version Control | Git & GitHub |

---

# 📂 Project Structure

```
opensre-demo/
│
├── catalog-api/
│
├── frontend/
│
├── opensre-backend/
│
├── infra/
│   ├── kind/
│   └── k8s/
│
├── observability/
│   ├── install.sh              # Full stack installer (metrics + logs)
│   ├── vm-values.yaml
│   ├── vmagent-values.yaml
│   ├── grafana-values.yaml
│   ├── otel-values.yaml
│   ├── es-values.yaml          # Elasticsearch Helm values
│   └── fluent-bit.yaml         # Fluent Bit DaemonSet
│
├── chaos/
│   ├── runbook.sh
│   └── README.md
│
├── docker-compose.yml          # Local databases + ELK stack
│
└── README.md
```

---

# ⚙️ Prerequisites

Install the following tools before starting.

- Ubuntu 24.04/26.04 LTS
- Python 3.13+
- Node.js 24+
- Git
- Podman
- Kind
- kubectl
- Helm
- OpenSRE CLI

Verify the installation:

```bash
python3 --version
node -v
npm -v
podman --version
kubectl version --client
kind version
helm version
opensre --version
```
# 🚀 Quick Start

## 1. Clone the Repository

```bash
git clone https://github.com/<YOUR_USERNAME>/opensre-demo.git
cd opensre-demo
```

---

## 2. Create the Kubernetes Cluster

```bash
sudo kind create cluster \
  --name opensre-demo \
  --config infra/kind/kind-config.yaml
```

Verify the cluster:

```bash
kubectl get nodes
```

Expected output:

```
NAME                         STATUS   ROLES
opensre-demo-control-plane   Ready    control-plane
opensre-demo-worker          Ready
```

---

## 3. Build the Sample Application

```bash
cd catalog-api

sudo podman build -t localhost/catalog-api:v1 .
```

Export the image:

```bash
sudo podman save localhost/catalog-api:v1 -o catalog-api.tar
```

Load the image into Kind:

```bash
sudo kind load image-archive catalog-api.tar --name opensre-demo
```

Remove the archive:

```bash
rm catalog-api.tar
```

---

## 4. Deploy the Application

```bash
cd ..

kubectl apply -f infra/k8s/
```

Verify:

```bash
kubectl get all -n opensre
```

Expected:

- catalog-api Deployment
- catalog-api Pod
- catalog-api Service

---

# 📊 Deploy Observability Stack

Create the namespace:

```bash
kubectl create namespace observability
```

## VictoriaMetrics (install script)

All observability charts are installed idempotently via a single script with
pinned versions:

```bash
./observability/install.sh
```

This creates the **`observability`** namespace, installs the repo charts
listed below, and applies the custom values files. To reinstall/upgrade,
run it again — it uses `helm upgrade --install`.

| Chart | Pinned version | Values file |
|---|---|---|
| `vm/victoria-metrics-single` | `0.45.0` | `observability/vm-values.yaml` |
| `vm/victoria-metrics-agent` | `0.46.0` | `observability/vmagent-values.yaml` |
| `grafana/grafana` | `10.5.15` | `observability/grafana-values.yaml` |
| `open-telemetry/opentelemetry-collector` | `0.172.0` | `observability/otel-values.yaml` |
| `prometheus-community/kube-state-metrics` | `8.4.1` | (defaults) |
| `prometheus-community/prometheus-node-exporter` | `4.56.3` | (defaults) |
| `elastic/elasticsearch` | `8.5.1` | `observability/es-values.yaml` |
| `elastic/kibana` | `8.5.1` | (values in install.sh) |

`vm-values.yaml` keeps the single-node VictoriaMetrics at **1-day
retention** (`retentionPeriod: "1"`), a **16 Gi persistent volume**, and
resource requests/limits tuned for the demo.

`vmagent-values.yaml` scrapes four jobs: **kube-state-metrics** (pod
status/restarts), **node** (machine CPU/RAM), **kubernetes-nodes-cadvisor**
(container CPU/memory/file descriptors) and **kubernetes-pods** (the
OpenTelemetry-injected `/metrics` endpoint on catalog-api, flaky-service, and
the traffic-gen probe). The pod-scraper drops the bogus `:80` sidecar target
(the `.+:\d+$` keep regex on `__address__`), and node/cadvisor relabels use a
full-match RE2 (`([^:]+):.*`) so the `IP:10250` discovery label is correctly
stripped.

To install individually (without the script):

```bash
helm install victoriametrics vm/victoria-metrics-single -n observability -f observability/vm-values.yaml
helm install vmagent vm/victoria-metrics-agent -n observability -f observability/vmagent-values.yaml
```

The Grafana chart is deployed with a VictoriaMetrics datasource and a pre-provisioned **"Catalog API Overview"** dashboard (viewable live inside the Metrics page):

```bash
helm install grafana grafana/grafana \
  -n observability \
  -f observability/grafana-values.yaml
```

The values file exposes Grafana as a `NodePort` on **30300** and maps `localhost:3000` through
kind (`infra/kind/kind-config.yaml`, `extraPortMappings`), so the embedded Metrics dashboard works
without a manual port-forward after the cluster is created with the updated config. It also enables
anonymous Viewer access and iframe embedding so the dashboard charts can be rendered live in the React
frontend. Login with `admin` / `admin123` for full access.

If you are running an older cluster (created before the port mapping), reach Grafana with:

```bash
kubectl port-forward -n observability svc/grafana 3000:80
```

---

## ELK Stack (Elasticsearch + Kibana + Fluent Bit)

The observability stack includes a full ELK stack for log aggregation and visualization. Deploy it using the install script (recommended):

```bash
./observability/install.sh
```

This single script installs/upgrades the entire observability stack including:
- **VictoriaMetrics** (metrics storage)
- **Grafana** (metrics visualization)
- **OpenTelemetry Collector** (telemetry collection)
- **vmagent** (metrics scraping)
- **Elasticsearch** (log storage) - single-node demo cluster
- **Kibana** (log visualization) - exposed on NodePort 30001 (mapped to localhost:3001 via Kind)
- **Fluent Bit** (log shipper) - DaemonSet that tails container logs and ships to Elasticsearch

The install script is idempotent and safe to re-run. It also:
- Creates an ILM policy for log retention (hot 1d, warm 7d, delete 30d)
- Creates an index template for `logs-opensre-*` indices
- Creates a Kibana index pattern and sets it as default

To install ELK components individually:

```bash
# Elasticsearch
helm install opensre-es elastic/elasticsearch \
  -n observability \
  --version 8.5.1 \
  -f observability/es-values.yaml

# Kibana (after Elasticsearch is ready)
helm install opensre-kibana elastic/kibana \
  -n observability \
  --version 8.5.1 \
  --set service.type=NodePort \
  --set service.nodePort=30001 \
  --set elasticsearchHosts="http://opensre-es-master:9200" \
  --no-hooks

# Fluent Bit
kubectl apply -f observability/fluent-bit.yaml
```

Access URLs (via Kind port mappings):
- **Elasticsearch**: http://localhost:9200
- **Kibana**: http://localhost:3001
- **Logs Explorer (Frontend)**: http://localhost:5173/logs

---

## OpenTelemetry Collector

```bash
helm install otel-collector open-telemetry/opentelemetry-collector \
  -n observability \
  -f observability/otel-values.yaml
```

---

## vmagent (scrape pipeline)

Installed by the install script. The `vmagent-values.yaml` file configures
the scrape jobs:

- **kube-state-metrics** — pod status, restart counts, waiting reasons
- **node** — `node_cpu_seconds_total`, `node_memory_MemAvailable_bytes`
- **kubernetes-nodes-cadvisor** — `container_cpu_usage_seconds_total`,
  `container_memory_working_set_bytes`, `container_file_descriptors`
- **kubernetes-pods** — OpenTelemetry `/metrics` endpoint (keeps only
  annotated targets matching `.+:\d+$` to drop the `:80` sidecar metric
  noise from multi-container pods)

Node discovery uses `role: node` (`IP:10250`); the relabel regex
`([^:]+):.*` (full-match) strips the port before re-targeting
`$1:9100` (node-exporter) or `$1:10250` (cAdvisor). No extra kubelet
RBAC is needed — the `vmagent-victoria-metrics-agent` ClusterRole already
has `GET/LIST/WATCH` on `nodes` and `nodes/metrics`.

---

Verify all services:

```bash
kubectl get pods -n observability
```

Expected:

```
grafana
victoriametrics
otel-collector
vmagent
opensre-es-master-0
opensre-kibana-xxx
fluent-bit-xxx
```

All pods should be in the **Running** state.

---

# 🔍 Verify Kubernetes

Check all namespaces:

```bash
kubectl get pods -A
```

Check application:

```bash
kubectl get pods -n opensre
```

Check observability stack:

```bash
kubectl get pods -n observability
```

At this point, the Kubernetes cluster, sample application, and observability stack should all be running successfully.

# ▶️ Running the Project

## Start the Backend

Navigate to the backend directory:

```bash
cd opensre-backend
```

Activate the virtual environment:

```bash
source .venv/bin/activate
```

Start the FastAPI server:

```bash
uvicorn app.main:app --reload --port 8001
```

Backend API:

```
http://localhost:8001
```

Swagger Documentation:

```
http://localhost:8001/docs
```

---

## Start the Frontend

Navigate to the frontend directory:

```bash
cd frontend
```

Install dependencies:

```bash
npm install
```

Start the development server:

```bash
npm run dev
```

Frontend URL:

```
http://localhost:5173
```

---

## Start the Databases (Aerospike, YugabyteDB, Elasticsearch & Kibana)

### Elasticsearch & Kibana (Local via Docker Compose)
The backend connects to Elasticsearch for the Logs Explorer. Start them with Docker Compose:

```bash
docker compose up -d elasticsearch kibana
```

Or with Podman:
```bash
podman run -d --name elasticsearch -p 9200:9200 -p 9300:9300 \
  -e "discovery.type=single-node" -e "xpack.security.enabled=false" \
  -e "ES_JAVA_OPTS=-Xms512m -Xmx512m" \
  -v elasticsearch-data:/usr/share/elasticsearch/data \
  docker.elastic.co/elasticsearch/elasticsearch:8.15.0
podman run -d --name kibana -p 5601:5601 \
  -e "ELASTICSEARCH_HOSTS=http://elasticsearch:9200" \
  docker.elastic.co/kibana/kibana:8.15.0
```

Elasticsearch on `localhost:9200`, Kibana on `localhost:5601`.

### YugabyteDB & Aerospike (Running in Kubernetes — NO docker start)
**Important**: YugabyteDB and Aerospike run as StatefulSets inside the Kind Kubernetes cluster (namespace `databases`). Do NOT use `docker start yugabyte/aerospike` — those containers are deleted and the dashboard no longer reads docker state.

Deploy them:

```bash
kubectl apply -f infra/k8s/yugabytedb/
kubectl apply -f infra/k8s/aerospike/
kubectl get pods -n databases
# yugabytedb-0 1/1 Running, aerospike-0 1/1 Running
# (YugabyteDB takes ~2-3 min on first start)
```

This creates:
- YugabyteDB StatefulSet (1 replica) with services `yugabytedb.databases.svc.cluster.local:5433` (YSQL) and `:9042` (YCQL)
- Aerospike StatefulSet (1 replica) with service `aerospike.databases.svc.cluster.local:3000`

The backend runs outside the cluster, so it reaches K8s DBs via port-forwards (start before backend):

```bash
kubectl port-forward -n databases svc/yugabytedb 5433:5433
kubectl port-forward -n databases svc/aerospike 3001:3000
```

`.env` is already set for this (`YUGABYTE_HOST=127.0.0.1:5433`, `AEROSPIKE_HOSTS=127.0.0.1:3001`).

To verify database connectivity from the backend:
```bash
curl http://localhost:8001/api/yugabyte/health
curl http://localhost:8001/api/aerospike/health
```

---

### Configure GitHub

Copy the integration credentials into `opensre-backend/.env`:

```
GITHUB_TOKEN=ghp_xxxxx
GITHUB_REPO=owner/repo-name
```

Create a Personal Access Token at https://github.com/settings/tokens (scope: `repo` for private repos or `public_repo` for public repos).

Restart the backend after editing `.env`.

---

# 🎭 Chaos Engineering / Failure Demo

This project ships with a chaos runbook to inject real failures into YugabyteDB,
Aerospike, and the Kubernetes cluster so they can be observed and analyzed live
through the OpenSRE dashboard. See [`chaos/README.md`](chaos/README.md) for the
full guide.

## Database Failure Injection (New!)

YugabyteDB and Aerospike now run as StatefulSets in Kubernetes. The Chaos Engineering dashboard provides a **Database Failure Injection** section to inject realistic database incidents and investigate them with OpenSRE.

### Five Database Incident Scenarios

| Scenario | Inject Action | Recover Action | Description |
|----------|--------------|----------------|-------------|
| **YugabyteDB Unavailable** | `yugabyte-unavailable` | `yugabyte-up` | Scales YugabyteDB StatefulSet to 0 replicas |
| **YugabyteDB High Latency** | `yugabyte-latency` | `yugabyte-latency-recover` | Induces slow queries via heavy workload |
| **YugabyteDB Connection Pressure** | `yugabyte-connection-pressure` | `yugabyte-connection-pressure-recover` | Simulates connection pool exhaustion |
| **Aerospike Unavailable** | `aerospike-unavailable` | `aerospike-up` | Scales Aerospike StatefulSet to 0 replicas |
| **Aerospike High Latency** | `aerospike-latency` | `aerospike-latency-recover` | Induces slow operations via heavy workload |

### UI-Driven Workflow (No Terminal Required)

1. **Open the Chaos page** → `http://localhost:5173/chaos`
2. **Navigate to "Database Failure Injection"** section
3. **Click "Inject"** on any scenario (e.g., "YugabyteDB Unavailable")
4. **Watch the database health flip to "Unreachable"** on the YugabyteDB/Aerospike pages
5. **Click "Investigate with OpenSRE"** on the database page or Incident page
6. **OpenSRE automatically gathers evidence** from:
   - Database (health, connections, slow queries, errors, replication)
   - Kubernetes (pod state, events, logs)
   - VictoriaMetrics (metrics, latency, error rates)
   - Elasticsearch (logs, ERROR/EXCEPTION/TIMEOUT signals)
   - GitHub (recent commits if relevant)
7. **OpenSRE correlates evidence** and produces evidence-grounded RCA with:
   - Root Cause
   - Supporting Evidence
   - Impact
   - Timeline
   - Recommendation
   - Confidence (High/Medium/Low)
8. **Click "Recover"** to restore the database to healthy state

### CLI Access (Alternative)

```bash
# Show current state
./chaos/runbook.sh status

# Inject database failures
./chaos/runbook.sh yugabyte-unavailable      # YugabyteDB unavailable (K8s)
./chaos/runbook.sh yugabyte-latency          # YugabyteDB high latency
./chaos/runbook.sh yugabyte-connection-pressure  # YugabyteDB connection pressure
./chaos/runbook.sh aerospike-unavailable     # Aerospike unavailable (K8s)
./chaos/runbook.sh aerospike-latency         # Aerospike high latency

# Inject other failures (existing)
./chaos/runbook.sh pod-crash
./chaos/runbook.sh pod-cpu
./chaos/runbook.sh pod-latency
./chaos/runbook.sh coredns-down
./chaos/runbook.sh node-network-latency
# ... etc

# Recover
./chaos/runbook.sh recover yugabyte-up
./chaos/runbook.sh recover yugabyte-latency-recover
./chaos/runbook.sh recover yugabyte-connection-pressure-recover
./chaos/runbook.sh recover aerospike-up
./chaos/runbook.sh recover aerospike-latency-recover
./chaos/runbook.sh recover all
```

### OpenSRE Investigation API

```bash
# Investigate a database directly
curl -X POST http://localhost:8001/api/opensre/investigate \
  -H "Content-Type: application/json" \
  -d '{"alert_payload": "{\"labels\": {\"alertname\": \"YugabyteDown\", \"database\": \"yugabyte\"}}"}'

# Or use the database investigation endpoint
curl http://localhost:8001/api/investigation/evidence/target/yugabyte
curl http://localhost:8001/api/investigation/evidence/target/aerospike
```

> **Latency spike**: `pod-latency` and `flaky-latency` call the target service's
> `/failure/latency` (or `/latency`) control endpoint, which injects a sustained
> delay into every request (5s for catalog, 3s for flaky; override with
> `POD_LATENCY_MS` or `FLAKY_LATENCY_MS`). Watch the **Latency** page focus the
> affected pod and flag it **HIGH**, then clear it with `latency-off` /
> `flaky-latency-off`. `node-network-latency` applies a `netem` 500ms egress
> delay on the worker node (`NODE_LATENCY_MS` to override), which lifts every
> in-cluster caller's latency; recover with `network-latency-off`.
>
> **Game-day**: run an automated steady-state experiment from the **Chaos** page
> (Game-day card) or via the API — baseline → inject → hold ≥ 60s → measure →
> recover → report, with a degraded/recovered verdict. Every inject/recover
> writes to `chaos/experiments/events.jsonl`; active faults are tracked in
> `chaos/experiments/active.json`.

---

# 📡 Available API Endpoints

## Health

```
GET /api/health
```

Checks whether the backend service is running.

---

## Kubernetes

```
GET /api/kubernetes/nodes
```

Returns all Kubernetes nodes.

```
GET /api/kubernetes/pods
```

Returns all running pods.

```
GET /api/kubernetes/services
```

Returns all Kubernetes services.

```
GET /api/kubernetes/deployments
```

Returns all deployments.

---

## Metrics

```
GET /api/metrics/health
```

Checks VictoriaMetrics connectivity.

```
GET /api/metrics/latency?window=180&step=30&instance=<name>&pod=<name>
```

Returns p50 / p95 / p99 request-latency time series over the last `window`
seconds at `step` intervals (`window` 30–3600s, `step` 5–300s). Used by the
Latency page, which polls this endpoint every few seconds to keep the charts
live. Optional `instance` / `pod` query params scope the percentiles to one
target/pod.

```
GET /api/metrics/latency/pods
```

Returns the latest p50 / p95 / p99 and request rate **per scraped pod**, with a
`high` flag when p99 exceeds 1s. Powers the Latency page's per-pod cards, the
pod **Focus** selector, and the high-latency alert banner.

---

---

## OpenSRE

```
GET /api/opensre/version
```

Returns the installed OpenSRE version.

```
GET /api/opensre/doctor
```

Runs `opensre doctor` and returns the diagnostic output.

### Evidence-grounded investigations

The `opensre` CLI has no built-in Kubernetes tooling, so an RCA fed only a bare
alert produces unverifiable triage ("Non-Validated Claims"). The backend
collects **live cluster evidence** first (pod state, container reasons/exit
codes, events, logs incl. `--previous`, per-pod request/5xx/p50-p95-p99 from
VictoriaMetrics, namespace degraded counts) and embeds a compact digest into the
alert `description` that the CLI's report reads and cites.

- `POST /api/opensre/investigate` — accepts an alert payload (Grafana/Alertmanager
  or a bare alert object), auto-attaches evidence, and runs a grounded RCA.
  Returns an error instead of a thin report if evidence collection fails.
- `scripts/investigate-alert.sh <alert.json> [kind-context]` — same flow from the
  terminal (the OpenSRE CLI's own workflow):

```bash
./scripts/investigate-alert.sh /tmp/grafana-alert.json kind-opensre-demo
```

Target detection: the alert `labels` `pod` / `kubernetes_pod_name` /
`namespace` / `kubernetes_namespace_name` pick the pod (deep evidence); without
a pod, a full-stack crash story (cluster metrics, scrape health, degraded pods)
is attached instead.

### Node-level investigations

Node analysis is available from the **AI Analysis** page (Target →
`Kubernetes node`) and the **Kubernetes** page (an `Investigate` button on every
node row). Both funnel into one evidence collector
(`investigation.collect_node_evidence`) covering:

- `kubectl get node` state (conditions / taints / allocatable),
  `describe node`, and both raw + structured node events (timeline-ready)
- every pod scheduled on the node, with deep-dive signals on degraded or
  restarted pods (state reasons, current + `--previous` container logs)
- node-exporter + kube-state-metrics numbers: load1/5/15, CPU and memory
  utilization, largest backing-filesystem usage, network RX/TX and disk rates,
  major page faults, KSM-reported conditions/pressure, allocatable/capacity
- Elasticsearch signals aggregated across the node's degraded pods (ES has no
  `k8s_node_name` field, so per-pod queries are grouped)
- CoreDNS/DNS health and GitHub commit correlation

Routes: `GET /api/opensre/investigate/node/{node_name}` (RCA + `vm_metrics` +
`es_signals`), `POST /api/demo/node-failure/investigate`, and the `node` target
in `POST /api/opensre/chat`.

> **Model quota note:** the OpenSRE CLI uses the Gemini free tier, which is
> hard-capped (e.g. 20 requests/day per model plus per-minute token limits). A
> single investigation runs a multi-step agent loop and can exhaust the daily
> budget; evidence collection still works and the UI surfaces the 429 as an
> "investigation failed" report. Space investigations out or switch the CLI to
> a paid provider for high-volume triage.

---

## Aerospike

```
GET /api/aerospike/health
```

Checks Aerospike connectivity.

```
GET /api/aerospike/query?namespace=test&set=users&key=1
```

Fetches a record by key.

```
POST /api/aerospike/write
```

Writes a record.

```
POST /api/aerospike/scan
```

Scans all records in a set.

```
POST /api/aerospike/delete
```

Deletes a record.

---

## YugabyteDB

```
GET /api/yugabyte/health
```

Checks YugabyteDB connectivity.

```
POST /api/yugabyte/query
```

Runs a read SQL query.

```
POST /api/yugabyte/execute
```

Runs any SQL statement.

```
POST /api/yugabyte/insert
```

Inserts a row and returns it.

```
POST /api/yugabyte/update
```

Updates rows matching a `where` clause.

```
POST /api/yugabyte/delete
```

Deletes rows matching a `where` clause.

---

## GitHub

```
GET /api/github/health
```

Checks GitHub connectivity and returns the connected repository.

```
GET /api/github/branches
```

Lists all repository branches.

```
GET /api/github/commits?sha=main&limit=50
```

Lists commits, optionally filtered by branch/SHA and date range (`since`, `until`).

```
GET /api/github/workflows?limit=20
```

Lists recent GitHub Actions workflow runs.

```
GET /api/github/issues?state=open&limit=30
```

Lists repository issues, filtered by state.

```
GET /api/github/repo
```

Returns repository metadata (stars, forks, description, visibility).

---

# 🖥 Dashboard Overview

The React dashboard consists of the following pages:

### 📊 Dashboard

- Cluster overview
- Node count
- Pod count
- Service count
- Deployment count
- Cluster health summary

---

### ☸ Kubernetes

- Node information
- Pod information
- Cluster resource overview

---

### 📈 Metrics

- VictoriaMetrics health status
- Grafana integration
- Live embedded Grafana dashboard (Catalog API Overview) with real-time panels

### ⏱ Latency

- Live P50 / P95 / P99 latency tiles (tail-risk spread = p99 − p50)
- Auto-refreshing line chart of latency percentiles (5s / 10s / 30s selectable, pause/resume)
- Plots the high-resolution `http_request_duration_highr_seconds` histogram
- Per-pod latency cards (each pod's p50/p95/p99 + req/s) with a **HIGH** flag when p99 > 1s
- Pod **Focus** selector covers every `opensre` namespace pod — pods without the
  instrumentation scrape (`prometheus.io/scrape: "true"`) show as **no data**
- High-latency alert banner when any scraped pod's p99 exceeds 1s

---

### 🗄 Aerospike

- Connection status
- Record browser (query / scan / write / delete)

---

### 🗄 YugabyteDB

- Connection status
- SQL console (query / execute / insert / update / delete)

---

### 🤖 AI Analysis

- OpenSRE Version
- OpenSRE Doctor Output
- Backend integration status

---

### 🔗 GitHub

- Repository overview
- Branch selector
- Commit history
- GitHub Actions workflow runs
- Issues feed

---

### ⚙ Settings

- Backend status
- Kubernetes status
- VictoriaMetrics status
- OpenSRE status
- Quick links to Grafana and Swagger

---

# ✅ Verification

Verify that everything is running correctly.

Backend:

```bash
curl http://localhost:8001/api/health
```

Frontend:

Open:

```
http://localhost:5173
```

Grafana:

```
http://localhost:3000
```

Elasticsearch:

```
http://localhost:9200
```

Kibana:

```
http://localhost:3001
```

OpenSRE:

```bash
opensre --version
```

Kubernetes:

```bash
kubectl get nodes
kubectl get pods -A
```

If all commands execute successfully and all pods are in the **Running** state, the platform has been deployed successfully.

# 🔧 Troubleshooting

## 1. Kubernetes Cluster Not Reachable

**Error**

```text
The connection to the server 127.0.0.1:<PORT> was refused
```

### Solution

If the Kind cluster was recreated using `sudo`, refresh your kubeconfig:

```bash
mkdir -p ~/.kube

sudo cp /root/.kube/config ~/.kube/config

sudo chown $USER:$USER ~/.kube/config

chmod 600 ~/.kube/config
```

Verify:

```bash
kubectl get nodes
```

---

## 2. catalog-api Pod Stuck in `ErrImageNeverPull`

This happens because the Docker/Podman image hasn't been loaded into the Kind cluster.

Rebuild the image:

```bash
cd catalog-api

sudo podman build -t localhost/catalog-api:v1 .
```

Export the image:

```bash
sudo podman save localhost/catalog-api:v1 -o catalog-api.tar
```

Load it into Kind:

```bash
sudo kind load image-archive catalog-api.tar --name opensre-demo
```

Restart the deployment:

```bash
kubectl rollout restart deployment/catalog-api -n opensre
```

---

## 3. OpenTelemetry Collector CrashLoopBackOff

If the collector fails to start, verify the configuration file:

```bash
observability/otel-values.yaml
```

Apply the updated configuration:

```bash
helm upgrade otel-collector open-telemetry/opentelemetry-collector \
  -n observability \
  -f observability/otel-values.yaml
```

---

## 4. vmagent Installation Failed

Verify the configuration file exists:

```bash
ls observability/
```

Expected:

```
otel-values.yaml
vmagent-values.yaml
```

Install vmagent again:

```bash
helm install vmagent vm/victoria-metrics-agent \
  -n observability \
  -f observability/vmagent-values.yaml
```

---

## 5. Frontend Cannot Connect to Backend

Verify the backend is running:

```bash
curl http://localhost:8001/api/health
```

If not, restart it:

```bash
cd opensre-backend

source .venv/bin/activate

uvicorn app.main:app --reload --port 8001
```

---

## 6. React Dashboard Shows No Data

Verify Kubernetes:

```bash
kubectl get nodes

kubectl get pods -A
```

Verify Backend:

```
http://localhost:8001/docs
```

Verify Frontend:

```
http://localhost:5173
```

---

# 📸 Screenshots

Screenshots of the application will be added after the dashboard UI is finalized.

- Dashboard
- Kubernetes
- Metrics
- AI Analysis
- Settings

---

# 🚀 Future Improvements

- AI-powered incident investigation
- One-click root cause analysis
- AlertManager integration
- Historical incident timeline
- Dashboard charts and graphs
- Authentication & RBAC
- Production deployment on AWS

---

# ✅ Current Status

- ✔ Kubernetes Cluster
- ✔ FastAPI Sample Application
- ✔ React Dashboard
- ✔ OpenSRE Backend
- ✔ VictoriaMetrics
- ✔ vmagent
- ✔ OpenTelemetry Collector
- ✔ Grafana
- ✔ **Elasticsearch (ELK Stack)**
- ✔ **Kibana (Log Visualization)**
- ✔ **Fluent Bit (Log Shipper)**
- ✔ Kubernetes REST APIs
- ✔ OpenSRE CLI Integration
- ✔ Aerospike Connector
- ✔ YugabyteDB Connector
- ✔ GitHub Integration
- ✔ Live Latency Page (p50/p95/p99)
- ✔ **Logs Explorer with Elasticsearch/Kibana**

---

## ⭐ Notes

This project is intended as a local development and learning environment for exploring Kubernetes observability and AI-assisted operations using OpenSRE. It provides a reproducible setup for experimenting with monitoring, telemetry collection, and dashboard development before deploying to a production environment.