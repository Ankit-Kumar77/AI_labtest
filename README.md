# OpenSRE Dashboard

An AI-assisted Kubernetes observability platform that unifies metrics, logs, and traces behind a single React console, then layers evidence-grounded root cause analysis on top via the OpenSRE CLI.

The whole stack — Kubernetes, the observability pipeline, and the databases — runs locally in a [Kind](https://kind.sigs.k8s.io/) cluster, so it is reproducible on a laptop and safe to break.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Alert-driven investigations](#alert-driven-investigations)
- [Technology Stack](#technology-stack)
- [Prerequisites](#prerequisites)
- [First-Time Setup](#first-time-setup)
- [Daily Startup](#daily-startup)
- [Access Points](#access-points)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Chaos Engineering](#chaos-engineering)
- [API Reference](#api-reference)
- [Dashboard Pages](#dashboard-pages)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)
- [Security Notes](#security-notes)

---

## Overview

OpenSRE Demo answers a narrow question well: **what does an AI SRE assistant actually need to see to produce a trustworthy incident report?**

A bare alert payload is not enough. An LLM handed `"target down"` will confidently invent a root cause. So the backend's primary job is **evidence collection** — it queries live cluster state, container exit codes, previous container logs, request/error/latency series, and indexed container logs, then embeds a compact digest into the alert description that the OpenSRE agent reads and cites. If evidence collection fails, the API returns an error rather than a thin, confident report.

On top of that evidence base, OpenSRE produces a structured RCA: root cause, supporting evidence, impact, timeline, recommendation, and a confidence rating.

### What is included

| Layer | Components |
|---|---|
| **Cluster** | Kind cluster (control-plane + worker), namespaced workloads, deliberately broken fixtures |
| **Sample app** | `catalog-api` (FastAPI), `flaky-service`, `nginx`, `traffic-gen` load probe |
| **Databases** | YugabyteDB (distributed SQL) and Aerospike (NoSQL), both as Kubernetes StatefulSets |
| **Metrics** | VictoriaMetrics (storage), vmagent (scraping), kube-state-metrics, node-exporter |
| **Traces** | OpenTelemetry Collector, sidecar-injected into instrumented pods |
| **Logs** | Fluent Bit (DaemonSet shipper) → Elasticsearch → Kibana |
| **Visualization** | Grafana, Kibana, and a custom React dashboard |
| **AI** | OpenSRE CLI agent loop, driven by the FastAPI backend |
| **Integration** | GitHub API (commits, branches, workflows, issues) for change correlation |

### Deliberately broken workloads

Four pods in the `opensre` namespace are **intentionally unhealthy**. They are the raw material for investigations and are not defects:

| Pod | State | Why it exists |
|---|---|---|
| `crashloop` | `CrashLoopBackOff` | Unrecoverable container crash |
| `memory-hog` | `CrashLoopBackOff` | OOMKilled under memory pressure |
| `imagepull` | `ImagePullBackOff` | Unresolvable image reference |
| `pending-pod` | `Pending` | Unsatisfiable scheduling request |

---

## Architecture

```
                    Browser
                       │
                       ▼
        ┌──────────────────────────────┐
        │   React + Vite Dashboard     │
        │   :5173                      │
        └──────────────┬───────────────┘
                       │ REST / JSON
                       ▼
        ┌──────────────────────────────┐
        │   FastAPI Backend  :8001     │
        │  ──────────────────────────  │
        │  • evidence collectors       │
        │  • OpenSRE CLI orchestrator  │
        │  • data-source connectors    │
        └──────┬────────┬────────┬─────┘
               │        │        │
    ┌──────────▼──┐  ┌──▼─────┐  ├──────────────┐
    │ Kubernetes  │  │  VM    │  │ Elasticsearch│
    │  (kubectl)  │  │ 8428   │  │    9200      │
    └──────────┬──┘  └────────┘  └──────┬───────┘
               │                        │
               │                 ┌──────▼───────┐
               │                 │  Fluent Bit  │
               │                 │  (DaemonSet) │
               │                 └──────┬───────┘
               │                        │
               ▼                        ▼
    ┌─────────────────────┐    ┌────────────────────┐
    │ YugabyteDB  :5433   │    │  VictoriaMetrics   │
    │ Aerospike   :3001   │    │  + Grafana  :3000  │
    │ (StatefulSets)      │    │  + Kibana   :5601  │
    └─────────────────────┘    └────────────────────┘
```

The backend runs **outside** the cluster on the host, and reaches every in-cluster service through `kubectl port-forward`. This keeps the local development loop simple and makes each dependency independently restartable.

### Request flow for an investigation

```
Alert payload
     │
     ▼
Target detection ──► infer pod / node / database from alert labels
     │
     ▼
Evidence collection
  ├─ kubectl: pod state, container reasons, exit codes, events
  ├─ kubectl: current AND --previous container logs
  ├─ VictoriaMetrics: request rate, 5xx, p50 / p95 / p99
  ├─ Elasticsearch: ERROR / EXCEPTION / TIMEOUT signals
  ├─ CoreDNS health
  └─ GitHub: recent commits near the incident window
     │
     ▼
Evidence digest embedded into alert description
     │
     ▼
OpenSRE CLI agent loop ──► structured RCA (root cause, evidence, impact,
                            timeline, recommendation, confidence)
```

---

## Alert-driven investigations

The dashboard used to require you to click **Investigate** by hand. It now also
picks alerts up on its own, end to end:

```
VictoriaMetrics
     │
     ▼
vmalert          (infra/k8s/alerting/vmalert.yaml)
  └─ 8 rules: latency, error rate, CPU, memory, restarts, readiness
     │  fires after `for:` so transient noise never triggers an RCA
     ▼
Alertmanager     (infra/k8s/alerting/alertmanager.yaml)
  └─ groups alerts, waits 10s, dedups repeat notifications
     │  POST http://opensre-backend.opensre.svc.cluster.local:8001/api/alerts/alertmanager
     ▼
Backend          (opensre-backend/app/routes/alerts.py)
  ├─ fingerprint-deduplicate  → one incident per alert, not per notification
  ├─ trigger the SAME evidence + OpenSRE CLI RCA path used by the UI
  └─ publish lifecycle event ──► SSE ──► navbar bell
     │                              └──► Slack (optional)
     ▼
Incidents page   (persisted, survives the browser session)
```

### Why the backend runs in-cluster

Alertmanager is a pod, so it must reach the backend over the cluster network.
It posts to the `opensre-backend` **Service DNS name** — never `localhost`,
which would resolve to the Alertmanager pod itself.

Alerts and incidents are written to the `opensre-backend-data` PVC, so the store
survives pod rollouts and restarts.

### Pointing the UI at the in-cluster backend

The frontend talks to `http://127.0.0.1:8001/api`. To see the **same** store the
alerting pipeline writes to, forward the in-cluster Service instead of starting a
second local backend process:

```bash
setsid kubectl -n opensre port-forward svc/opensre-backend 8001:8001 \
  >/tmp/opensre-backend-pf.log 2>&1 &
```

This matters: running a local backend process as well gives you two independent
stores, and alerts ingested in-cluster will not appear in the UI.

> Rootless Podman Kind isolates host and cluster networks, so `ClusterIP` and
> `NodePort` are not reachable from the host. `port-forward` is the supported
> path.

### Alert rules

| Rule | Fires when |
| --- | --- |
| `HighLatency` | p99 > 2s for 1m |
| `CriticalLatency` | p99 > 10s for 2m |
| `HighErrorRate` | 5xx > 5% of requests for 2m |
| `HighCPU` | throttled > 50% of CFS periods for 5m |
| `HighMemory` | working set > 90% of the container limit for 5m |
| `HighMemoryAbsolute` | working set > 256Mi for 5m (catches BestEffort pods, which declare no limit) |
| `PodRestart` | container restarts ≥ 3 within 10m |
| `ContainerNotReady` | container not ready for 2m |

### Changing a threshold

`infra/k8s/alerting/rules/thresholds.env` is the **single source of truth** for
every threshold. `alert-rules.yaml` (structure) and `vmalert.yaml` (rendered
manifest) are both inputs/outputs of the generator, so there is no second copy
to keep in sync.

```bash
python3 infra/k8s/alerting/render.py           # rewrite vmalert.yaml
python3 infra/k8s/alerting/render.py --check    # non-zero if stale (used by tests/CI)
kubectl apply -f infra/k8s/alerting/            # alerting + rendered rules
```

`--stdout` prints the manifest instead of writing it, and any variable can be
overridden for a one-off render (e.g. `LATENCY_HIGH_SECONDS=0.5 python3 ...`).
`opensre-backend/tests/test_alert_rules_sync.py` fails if `vmalert.yaml` no
longer matches the sources.

### The alert bell

The navbar bell is a notification center: it lists recent alerts, badges
unread events, and shows a `live` / `polling` indicator. It consumes the SSE
feed at `GET /api/alerts/stream` and reconciles against `GET /alerts`, falling
back to polling if the stream drops.

### Alert API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/alerts/alertmanager` | Alertmanager webhook (validated) |
| `GET` | `/api/alerts` | Recent alert lifecycles |
| `GET` | `/api/alerts/active` | Firing only |
| `GET` | `/api/alerts/{fingerprint}` | One lifecycle |
| `GET` | `/api/alerts/stream` | SSE lifecycle events |
| `DELETE` | `/api/alerts` | Clear the store |

### Slack (optional)

Set `SLACK_WEBHOOK_URL` in `opensre-backend/.env` (local) or on the
`opensre-backend-secrets` Secret (in-cluster) to mirror every firing and
resolved alert to a channel. Leave it empty to disable Slack. Delivery is
asynchronous and failures are logged, never raised — a Slack outage cannot
block ingestion or the RCA pipeline. The webhook URL is backend-only and is
never sent to the browser.

### Validating the pipeline by hand

```bash
# Fire a synthetic alert through the real webhook
curl -s -X POST http://127.0.0.1:8001/api/alerts/alertmanager \
  -H 'Content-Type: application/json' \
  -d '{"alerts":[{"status":"firing","labels":{"alertname":"ManualProbe",
       "namespace":"opensre","pod":"probe-pod","severity":"warning"},
       "annotations":{"summary":"manual probe"}}]}'

curl -s http://127.0.0.1:8001/api/alerts/active
```

The bell should light up within a second. Then watch a real one:

```bash
CLUSTER=kind-opensre-demo POD_LATENCY_MS=5000 ./chaos/runbook.sh pod-latency
```

---

## Technology Stack

| Category | Technology | Version |
|---|---|---|
| Frontend | React, Vite, React Router, Axios | 19.2 / 8.2 / 7.18 |
| Backend | FastAPI, Uvicorn, Pydantic | 0.141 / 0.52 / 2.13 |
| Kubernetes client | `kubernetes` Python SDK | 36.0 |
| Container runtime | Podman (rootless) | 5.7 |
| Local cluster | Kind | 0.27 |
| Kubernetes | kubectl | 1.37 |
| Metrics store | VictoriaMetrics Single | chart 0.45.0 |
| Metrics scraper | vmagent | chart 0.46.0 |
| Cluster metrics | kube-state-metrics / node-exporter | chart 8.4.1 / 4.56.3 |
| Tracing | OpenTelemetry Collector | chart 0.172.0 |
| Dashboards | Grafana | chart 10.5.15 |
| Log store | Elasticsearch | chart 8.5.1 |
| Log UI | Kibana | chart 8.5.1 |
| Log shipper | Fluent Bit | DaemonSet manifest |
| Distributed SQL | YugabyteDB | `yugabyte:latest` |
| NoSQL | Aerospike | 12.0 (Python client) |
| AI agent | OpenSRE CLI | 0.1.2026.9.1 |
| Source control | Git, GitHub API | — |

---

## Prerequisites

| Tool | Tested version | Purpose |
|---|---|---|
| Python | 3.13+ (3.14 verified) | Backend |
| Node.js | 24+ (26 verified) | Frontend build |
| Podman or Docker | 5.7+ | Kind node containers |
| Kind | 0.27+ | Local Kubernetes cluster |
| kubectl | 1.37+ | Cluster control plane |
| Helm | 3.16+ | Observability stack |
| OpenSRE CLI | 0.1.2026.9.1+ | AI investigation agent |
| Git | any | Version control |

Verify the toolchain:

```bash
python3 --version && node -v && npm -v
podman --version && kind version
kubectl version --client && helm version --short
opensre --version
```

> **Rootless Podman works.** The Kind cluster and the full stack have been verified
> running entirely rootless, so `sudo` is not required if your Podman is configured
> for rootless operation. Use `sudo` only if your setup requires it.

---

## First-Time Setup

Run this sequence once per fresh clone.

### 1. Create the cluster

```bash
kind create cluster \
  --name opensre-demo \
  --config infra/kind/kind-config.yaml
```

Verify both nodes reach `Ready`:

```bash
kubectl get nodes
```

```
NAME                         STATUS   ROLES           AGE   VERSION
opensre-demo-control-plane   Ready    control-plane   27d   v1.32.2
opensre-demo-worker          Ready    <none>          27d   v1.32.2
```

### 2. Build and load the sample application image

Kind nodes have no access to a local registry, so the image must be built and
explicitly loaded into the cluster.

```bash
cd catalog-api
podman build -t localhost/catalog-api:v1 .
podman save localhost/catalog-api:v1 -o catalog-api.tar
kind load image-archive catalog-api.tar --name opensre-demo
rm catalog-api.tar
cd ..
```

### 3. Deploy the application and databases

```bash
kubectl apply -f infra/k8s/namespace.yaml
kubectl apply -f infra/k8s/catalog-api-deployment.yaml
kubectl apply -f infra/k8s/catalog-api-service.yaml
kubectl apply -f infra/k8s/nginx-configmap.yaml
kubectl apply -f infra/k8s/nginx-deployment.yaml
kubectl apply -f infra/k8s/nginx-service.yaml
kubectl apply -f infra/k8s/yugabytedb/
kubectl apply -f infra/k8s/aerospike/
kubectl apply -f infra/k8s/faults/
```

`faults/` installs the intentionally broken workloads described above.

> **Do not run `docker start yugabyte` or `docker start aerospike`.** Those containers
> are vestigial and deleted. Both databases now run as StatefulSets in the
> `databases` namespace, and the dashboard reads Kubernetes state — not Docker state.

Confirm the databases are up. YugabyteDB takes 2–3 minutes on first start:

```bash
kubectl get pods -n databases
```

```
NAME            READY   STATUS    RESTARTS   AGE
aerospike-0     1/1     Running   0          2m
yugabytedb-0    1/1     Running   0          3m
```

### 4. Deploy the observability stack

A single idempotent script installs and configures everything, with pinned chart
versions for reproducibility. It is safe to re-run.

```bash
./observability/install.sh
```

| Chart | Pinned version | Values file |
|---|---|---|
| `vm/victoria-metrics-single` | 0.45.0 | `observability/vm-values.yaml` |
| `vm/victoria-metrics-agent` | 0.46.0 | `observability/vmagent-values.yaml` |
| `grafana/grafana` | 10.5.15 | `observability/grafana-values.yaml` |
| `open-telemetry/opentelemetry-collector` | 0.172.0 | `observability/otel-values.yaml` |
| `prometheus-community/kube-state-metrics` | 8.4.1 | defaults |
| `prometheus-community/prometheus-node-exporter` | 4.56.3 | defaults |
| `elastic/elasticsearch` | 8.5.1 | `observability/es-values.yaml` |
| `elastic/kibana` | 8.5.1 | values inline in `install.sh` |

The script also provisions:

- An **ILM policy** for log retention (hot 1d → warm 7d → delete 30d)
- An **index template** matching `logs-opensre-*`
- A **Kibana index pattern**, set as the default data view
- A Grafana datasource for VictoriaMetrics and a pre-provisioned
  **"Catalog API Overview"** dashboard, embedded live in the Metrics page

#### Metrics pipeline

`vmagent-values.yaml` configures four scrape jobs:

| Job | Source | Signals |
|---|---|---|
| `kubernetes-state` | kube-state-metrics | Pod status, restart counts, waiting reasons |
| `node` | node-exporter | `node_cpu_seconds_total`, `node_memory_MemAvailable_bytes` |
| `kubernetes-nodes-cadvisor` | kubelet `/metrics/cadvisor` | Container CPU, memory, file descriptors |
| `kubernetes-pods` | annotated pods | OpenTelemetry `/metrics` from `catalog-api`, `flaky-service`, `traffic-gen` |

Two details worth knowing if you extend the config:

- The pod-scraper job keeps only targets matching `.+:\d+$` on `__address__`. This
  drops the bogus `:80` sidecar targets that multi-container pods produce.
- Node and cAdvisor relabeling use a **full-match** RE2 (`([^:]+):.*`) to strip the
  `IP:10250` discovery label before re-targeting `$1:9100` / `$1:10250`. A partial
  match silently fails here.

No extra kubelet RBAC is required — the vmagent ClusterRole already grants
`GET`/`LIST`/`WATCH` on `nodes` and `nodes/metrics`.

#### Single-node caveats

This is a demo topology, not a production one:

- VictoriaMetrics runs with **1-day retention** and a **16 Gi** persistent volume.
- Elasticsearch is a single node holding all roles, with security disabled
  (`xpack.security.enabled: false`) and a 1 Gi heap.
- Elasticsearch and Kibana use anti-affinity `soft`, so both can land on the same node.

### 5. Set up the backend

```bash
cd opensre-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then edit, see Configuration
cd ..
```

### 6. Set up the frontend

```bash
cd frontend
npm install
cd ..
```

---

## Daily Startup

The cluster, observability stack, and databases survive between sessions, but
**port-forwards and the two app servers do not**. Restart them in this order —
the backend must start *after* the port-forwards, because it caches its
Elasticsearch client on startup.

### Step 1 — start the cluster

If the Kind nodes are stopped (for example after a reboot):

```bash
podman start opensre-demo-control-plane opensre-demo-worker
kubectl get nodes
```

Wait for both nodes to report `Ready` before continuing.

### Step 2 — start all port-forwards

Run each in its own terminal — or in one, backgrounded and detached:

```bash
# One per terminal (simplest)
kubectl port-forward -n observability svc/es-external 9200:9200
kubectl port-forward -n observability svc/opensre-kibana-kibana 5601:5601
kubectl port-forward -n observability svc/victoriametrics-victoria-metrics-single-server 8428:8428
kubectl port-forward -n observability svc/otel-collector-opentelemetry-collector 4317:4317 4318:4318
kubectl port-forward -n databases      svc/yugabytedb 5433:5433
kubectl port-forward -n databases      svc/aerospike 3001:3000
kubectl port-forward -n observability svc/grafana 3000:80
```

> **Backgrounding matters.** If you launch these with `&` from a script, wrap each in
> `setsid`. Plain background jobs are killed when the parent shell exits, which
> silently breaks every downstream service:
>
> ```bash
> setsid nohup kubectl port-forward -n observability svc/es-external 9200:9200 \
>   > /tmp/es-pf.log 2>&1 < /dev/null &
> ```

### Step 3 — start the backend

```bash
cd opensre-backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8001 --host 0.0.0.0
```

### Step 4 — start the frontend

```bash
cd frontend
npm run dev
```

### Stopping

```bash
pkill -f "uvicorn app.main:app"
pkill -f "kubectl port-forward"
pkill -f "vite"

# Optional: stop the cluster
podman stop opensre-demo-control-plane opensre-demo-worker
```

---

## Access Points

| Service | URL | Notes |
|---|---|---|
| Dashboard | http://localhost:5173 | React app |
| Backend API | http://localhost:8001 | |
| Swagger UI | http://localhost:8001/docs | Interactive API reference |
| Grafana | http://localhost:3000 | `admin` / `admin123` |
| Kibana | http://localhost:5601 | |
| Elasticsearch | http://localhost:9200 | No auth |
| VictoriaMetrics | http://localhost:8428 | |
| OTel gRPC / HTTP | `localhost:4317` / `localhost:4318` | OTLP ingest |
| YugabyteDB (YSQL) | localhost:5433 | `yugabyte` / `yugabyte` |
| Aerospike | localhost:3001 | namespace `test` |

### Port conflicts worth knowing

Two port-mapping mechanisms overlap, and only one is reliable:

- **Port-forwards (canonical).** Every service above is reached via
  `kubectl port-forward`. This works on any cluster and is what the verified
  setup uses.
- **Kind `extraPortMappings` (optional).** `infra/kind/kind-config.yaml` maps
  host `3000 → 30300` (Grafana), `9200 → 30920` (Elasticsearch), and
  `3001 → 30001` (Kibana). These only take effect on clusters created *with*
  the current config, and are commonly inactive under rootless Podman.

Two traps follow from this:

1. **Kibana on host port 3001 collides with the Aerospike port-forward.** If
   Aerospike is on 3001, reach Kibana on **5601**.
2. If host port 9200 is already held by a Kind mapping, the Elasticsearch
   port-forward cannot bind. Use the Kind mapping or the port-forward, not both.

### In-cluster addresses

Useful for running commands inside a pod:

| Service | DNS name |
|---|---|
| YugabyteDB (YSQL) | `yugabytedb.databases.svc.cluster.local:5433` |
| YugabyteDB (YCQL) | `yugabytedb.databases.svc.cluster.local:9042` |
| Aerospike | `aerospike.databases.svc.cluster.local:3000` |
| Elasticsearch | `opensre-es-master.observability.svc.cluster.local:9200` |
| VictoriaMetrics | `victoriametrics-victoria-metrics-single-server.observability.svc.cluster.local:8428` |

---

## Project Structure

```
.
├── catalog-api/              # Sample FastAPI service (OpenTelemetry-instrumented)
├── fault-apps/               # Flaky-service, traffic-gen probe
├── opensre-backend/
│   ├── app/
│   │   ├── main.py           # FastAPI entrypoint
│   │   ├── routes/           # HTTP route modules, one per data source
│   │   ├── services/         # Connectors, evidence collectors, OpenSRE orchestration
│   │   │   └── alert_store.py  # Fingerprint-deduplicated alert lifecycle store
│   │   ├── models/           # Pydantic schemas
│   │   ├── core/             # Config, logging
│   │   └── utils/            # Shared helpers
│   ├── tests/                # pytest suite
│   ├── Dockerfile            # In-cluster backend image
│   └── .env                  # Local config (git-ignored)
├── frontend/
│   └── src/
│       ├── pages/            # 13 routed pages
│       ├── components/       # Shared UI (incl. AlertNotifications.jsx bell)
│       ├── api/              # Axios client
│       └── hooks/
├── infra/
│   ├── kind/kind-config.yaml # Cluster topology + port mappings
│   └── k8s/
│       ├── yugabytedb/       # StatefulSet + services
│       ├── aerospike/        # StatefulSet + services
│       ├── alerting/         # vmalert + Alertmanager + alert rules
│       ├── opensre-backend/  # Backend Deployment, Service, RBAC, Secret example
│       └── faults/           # Deliberately broken workloads
├── observability/
│   ├── install.sh            # Idempotent, pinned-version stack installer
│   ├── es-values.yaml        # Elasticsearch single-node demo config
│   ├── vm-values.yaml
│   ├── vmagent-values.yaml
│   ├── grafana-values.yaml
│   ├── otel-values.yaml
│   └── fluent-bit.yaml
├── chaos/
│   ├── runbook.sh            # 20+ fault injection scenarios
│   ├── seed-data.sh
│   ├── productionize.sh
│   └── experiments/          # Inject/recover event log (git-ignored)
├── scripts/
│   └── investigate-alert.sh  # Terminal-driven RCA, no UI required
├── SETUP_GUIDE.txt
└── guide for running
```

There is no `docker-compose.yml`: the databases and Elasticsearch run inside
the Kind cluster, and `chaos/runbook.sh` drives them from there.

---

## Configuration

Backend configuration lives in `opensre-backend/.env`, which is git-ignored. Copy
from `.env.example` and adjust:

```ini
# AI agent
OPENSRE_BINARY=/path/to/opensre

# Metrics
VICTORIA_METRICS_URL=http://localhost:8428
GRAFANA_URL=http://localhost:3000

# Aerospike (host port from the port-forward)
AEROSPIKE_HOSTS=127.0.0.1:3001
AEROSPIKE_NAMESPACE=test

# YugabyteDB (host port from the port-forward)
YUGABYTE_HOST=127.0.0.1
YUGABYTE_PORT=5433
YUGABYTE_DATABASE=yugabyte
YUGABYTE_USER=yugabyte
YUGABYTE_PASSWORD=yugabyte

# GitHub integration
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
GITHUB_REPO=owner/repo
GITHUB_API_URL=https://api.github.com
```

All defaults point at `127.0.0.1`, because the backend runs on the host and
reaches in-cluster services through port-forwards.

**GitHub token.** Create a [personal access token](https://github.com/settings/tokens)
with scope `repo` (private repositories) or `public_repo` (public only). The token
is used for commit/workflow correlation during investigations and is optional —
GitHub features degrade gracefully without it.

Restart the backend after editing `.env`; values are read at startup.

---

## Chaos Engineering

Fault injection is the core of this project. The platform is only as
interesting as the incidents you can reproduce on demand.

`chaos/runbook.sh` exposes 20+ scenarios. Inject, watch the signal propagate
across metrics and logs, run an investigation, then recover.

Run `./chaos/runbook.sh help` for the authoritative, current list. Full guide:
[`chaos/README.md`](chaos/README.md)

### Database scenarios

Both databases are Kubernetes StatefulSets, so "unavailable" means scaling to
zero replicas.

| Scenario | Inject | Recover | Effect |
|---|---|---|---|
| YugabyteDB unavailable | `yugabyte-down` | `yugabyte-up` | Scales StatefulSet to 0 |
| YugabyteDB high latency | `yugabyte-latency` | `yugabyte-latency-recover` | Heavy query load |
| YugabyteDB connection pressure | `yugabyte-connection-pressure` | `yugabyte-connection-pressure-recover` | Exhausts the connection pool |
| Aerospike unavailable | `aerospike-down` | `aerospike-up` | Scales StatefulSet to 0 |
| Aerospike high latency | `aerospike-latency` | `aerospike-latency-recover` | Heavy read load |

> **After an unavailable/recover cycle, restart that database's port-forward.**
> The port-forward process dies together with the pod when the StatefulSet scales
> to zero, so the backend cannot reconnect until you re-establish it. Then
> re-check `/api/yugabyte/health` or `/api/aerospike/health`.

### Cluster scenarios

| Group | Scenarios |
|---|---|
| **Pod** | `pod-crash`, `pod-delete`, `pod-cpu`, `pod-memory`, `pod-latency` |
| **Flaky service** | `flaky-latency`, `flaky-latency-off` |
| **CoreDNS** | `coredns-kill`, `coredns-down`, `coredns-latency`, `coredns-latency-off`, `coredns-up` |
| **ELK** | `elk-error`, `elk-connection-refused`, `elk-timeout`, `elk-recover` |
| **Node** | `node-cordon`, `node-drain`, `node-network-latency`, `uncordon` |
| **System** | `system-pod-kill` |
| **Utility** | `status`, `recover <target>`, `recover all`, `help` |

```bash
./chaos/runbook.sh status
./chaos/runbook.sh yugabyte-down
./chaos/runbook.sh pod-crash
./chaos/runbook.sh recover all
```

### Latency injection details

- `pod-latency` / `flaky-latency` call the target's `/failure/latency` control
  endpoint, injecting a sustained delay into every request (5s catalog, 3s
  flaky). Override with `POD_LATENCY_MS` / `FLAKY_LATENCY_MS`. Clear with
  `latency-off` / `flaky-latency-off`.
- `node-network-latency` applies a `netem` 500ms egress delay on the worker node
  (`NODE_LATENCY_MS`), which lifts latency for every in-cluster caller. Clear
  with `network-latency-off`.

### UI-driven workflow

1. Open http://localhost:5173/chaos
2. Use **Database Failure Injection** to pick a scenario
3. Inject, then watch database health flip to **Unreachable** on the database page
4. Click **Investigate with OpenSRE**
5. Review the RCA: root cause, evidence, impact, timeline, recommendation, confidence
6. Recover

### Game-day experiments

The **Game-day** card on the Chaos page (or `/api/chaos/game-day`) runs a full
steady-state experiment: baseline → inject → hold ≥60s → measure → recover →
report, with a degraded/recovered verdict. Every inject and recover is appended to
`chaos/experiments/events.jsonl`; active faults are tracked in
`chaos/experiments/active.json`.

---

## API Reference

Interactive documentation is available at http://localhost:8001/docs.

Alert endpoints (`/api/alerts/*`, including the Alertmanager webhook and the
SSE stream) are listed in
[Alert-driven investigations](#alert-api).

### Health

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Backend liveness |
| `GET` | `/api/metrics/health` | VictoriaMetrics reachability |
| `GET` | `/api/elasticsearch/health` | ES version and cluster status |
| `GET` | `/api/yugabyte/health` | YugabyteDB connectivity |
| `GET` | `/api/aerospike/health` | Aerospike connectivity |
| `GET` | `/api/github/health` | GitHub auth and resolved repository |

### Kubernetes

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/kubernetes/nodes` | All nodes |
| `GET` | `/api/kubernetes/pods` | All pods |
| `GET` | `/api/kubernetes/services` | All services |
| `GET` | `/api/kubernetes/deployments` | All deployments |

### Logs (Elasticsearch)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/elasticsearch/facets?since_minutes=60` | Available namespaces, pods, services |
| `GET` | `/api/elasticsearch/logs` | Query container logs by namespace, pod, level, text |

The Logs page populates its namespace and pod dropdowns from `/facets`. An empty
dropdown almost always means the Elasticsearch port-forward is down or was
started after the backend.

### Metrics

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/metrics/latency?window=180&step=30&instance=&pod=` | p50/p95/p99 series (`window` 30–3600s, `step` 5–300s) |
| `GET` | `/api/metrics/latency/pods` | Latest percentiles and request rate per pod |

`/latency/pods` sets a `high` flag when a pod's p99 exceeds 1s, which drives the
Latency page per-pod cards, the focus selector, and the alert banner.

### OpenSRE

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/opensre/version` | Installed CLI version |
| `GET` | `/api/opensre/doctor` | Output of `opensre doctor` |
| `POST` | `/api/opensre/investigate` | Grounded RCA from an alert payload |
| `GET` | `/api/opensre/investigate/node/{node}` | Node-level RCA with `vm_metrics` and `es_signals` |
| `POST` | `/api/opensre/chat` | Conversational investigation |

**Target detection.** Alert labels `pod`, `kubernetes_pod_name`, `namespace`, and
`kubernetes_namespace_name` select a pod and trigger deep per-pod evidence
collection. Without a pod, a broader full-stack crash story is attached instead
(cluster metrics, scrape health, degraded pod counts).

**Node investigations** collect `kubectl get/describe node`, raw and structured
node events, every pod on the node with deep-dive on degraded or restarted ones,
node-exporter and kube-state-metrics figures (load, CPU, memory, filesystem,
network, page faults, pressure conditions), CoreDNS health, and GitHub
correlation. Elasticsearch signals are aggregated per degraded pod, because ES
indexes no `k8s_node_name` field.

Run the same flow from a terminal with:

```bash
./scripts/investigate-alert.sh /tmp/grafana-alert.json kind-opensre-demo
```

### Databases

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/yugabyte/query` | Read SQL query |
| `POST` | `/api/yugabyte/execute` | Arbitrary SQL statement |
| `POST` | `/api/yugabyte/insert` | Insert and echo a row |
| `POST` | `/api/yugabyte/update` | Update by `where` clause |
| `POST` | `/api/yugabyte/delete` | Delete by `where` clause |
| `GET` | `/api/aerospike/query?namespace=&set=&key=` | Fetch record by key |
| `POST` | `/api/aerospike/write` | Write record |
| `POST` | `/api/aerospike/scan` | Scan a set |
| `POST` | `/api/aerospike/delete` | Delete record |

### GitHub

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/github/repo` | Repository metadata |
| `GET` | `/api/github/branches` | List branches |
| `GET` | `/api/github/commits?sha=&limit=&since=&until=` | Commit history |
| `GET` | `/api/github/workflows?limit=` | Recent Actions runs |
| `GET` | `/api/github/issues?state=&limit=` | Repository issues |

### Chaos

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/chaos/status` | Cluster and database state |
| `GET` | `/api/chaos/active` | Currently active faults |
| `GET` | `/api/chaos/history` | Past experiments |
| `POST` | `/api/chaos/inject` | Inject a named scenario |
| `POST` | `/api/chaos/recover` | Recover a named scenario |
| `POST` | `/api/chaos/game-day` | Run a steady-state experiment |
| `GET` | `/api/demo/db-scenario/list` | Available database scenarios |
| `POST` | `/api/demo/db-scenario/{unavailable,latency,connection-pressure}/{action}` | Drive a scenario directly |

---

## Dashboard Pages

| Route | Page | Contents |
|---|---|---|
| `/` | Dashboard | Cluster overview: node, pod, service, deployment counts, health summary |
| `/kubernetes` | Kubernetes | Node and pod inventory, resource usage, per-node **Investigate** action |
| `/logs` | Logs | Elasticsearch-backed log search with namespace/pod/level filters |
| `/metrics` | Metrics | VictoriaMetrics health, live embedded Grafana dashboard |
| `/latency` | Latency | Live p50/p95/p99 tiles, auto-refreshing chart, per-pod cards |
| `/aerospike` | Aerospike | Connection status, record browser (query/scan/write/delete) |
| `/yugabyte` | YugabyteDB | Connection status, SQL console (query/execute/insert/update/delete) |
| `/chaos` | Chaos | Fault injection, database scenarios, game-day experiments |
| `/incident` | Incident | Investigation list and RCA reports |
| `/analysis` | AI Analysis | OpenSRE version/doctor output, target picker for pod and node RCA |
| `/github` | GitHub | Repo overview, branches, commits, workflows, issues |
| `/settings` | Settings | Component status, quick links to Grafana and Swagger |

The Latency page plots the high-resolution
`http_request_duration_highr_seconds` histogram, offers 5s/10s/30s refresh with
pause/resume, and shows **no data** for pods that lack the
`prometheus.io/scrape: "true"` annotation.

---

## Verification

Run these after startup. All should succeed.

```bash
# Cluster
kubectl get nodes
kubectl get pods -A
kubectl get pods -n databases

# Backend
curl -s http://localhost:8001/api/health
# {"status":"UP","service":"OpenSRE Backend"}

# Databases
curl -s http://localhost:8001/api/yugabyte/health
# {"success":true,"status":"connected"}
curl -s http://localhost:8001/api/aerospike/health
# {"success":true,"status":"connected"}

# Elasticsearch — expect a green cluster
curl -s http://localhost:8001/api/elasticsearch/health
# {"success":true,"available":true,"version":"8.5.1","cluster":"opensre-es"}
curl -s "http://localhost:8001/api/elasticsearch/facets?since_minutes=60"
# Expect namespaces and pods arrays to be populated

# Observability stack
curl -s http://localhost:8428/-/healthy
# VictoriaMetrics is Healthy.
curl -s http://localhost:3000/api/health
curl -s http://localhost:5601/api/status

# Frontend
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5173/
# 200
```

A healthy Logs page shows populated namespace and pod dropdowns. A healthy Chaos
page lists the database StatefulSets — if it says "local container" instead, the
backend is reading stale Docker state.

---

## Troubleshooting

### 1. `connection refused` from kubectl

The Kind nodes are stopped. If they were created with `sudo`, the kubeconfig may
have been written to root's home directory.

```bash
podman start opensre-demo-control-plane opensre-demo-worker

# Only if created with sudo:
mkdir -p ~/.kube
sudo cp /root/.kube/config ~/.kube/config
sudo chown $USER:$USER ~/.kube/config
chmod 600 ~/.kube/config

kubectl get nodes
```

### 2. `catalog-api` stuck in `ErrImageNeverPull`

The image was never loaded into the Kind cluster.

```bash
cd catalog-api
podman build -t localhost/catalog-api:v1 .
podman save localhost/catalog-api:v1 -o catalog-api.tar
kind load image-archive catalog-api.tar --name opensre-demo
rm catalog-api.tar
kubectl rollout restart deployment/catalog-api -n opensre
cd ..
```

### 3. Logs page has empty dropdowns

The Elasticsearch port-forward is down, or the backend started before it and
cached a broken client. Restart the backend:

```bash
pkill -f "uvicorn app.main:app"
cd opensre-backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8001 --host 0.0.0.0
```

### 4. Kibana will not open

Confirm you are not colliding with the Aerospike port-forward on host port 3001.
Kibana is on **5601**:

```bash
kubectl port-forward -n observability svc/opensre-kibana-kibana 5601:5601
```

### 5. Databases show "Unreachable" without a deliberate injection

```bash
kubectl get pods -n databases          # both should be 1/1 Running
ss -tlnp | grep -E '5433|3001'         # both port-forwards should be listening
```

If YugabyteDB is `0/1`, wait 2–3 minutes and check its internal status:

```bash
kubectl exec -n databases yugabytedb-0 -- bin/yugabyted status
```

### 6. Database unreachable after an inject/recover cycle

Expected behavior: the port-forward dies with the pod. Re-establish it.

```bash
kubectl port-forward -n databases svc/yugabytedb 5433:5433
# or
kubectl port-forward -n databases svc/aerospike 3001:3000
```

### 7. Metrics page is empty

```bash
kubectl get pods -n observability   # vmagent and VictoriaMetrics should be Running
curl -s http://localhost:8428/-/healthy
```

### 8. Port-forward dies when the script exits

Background jobs are killed with the parent shell. Use `setsid` — see
[Daily Startup](#step-2--start-all-port-forwards) for the pattern.

### 9. Investigation returns "investigation failed"

Usually an LLM provider limit, not an evidence problem. The backend defaults to
OpenRouter (`LLM_PROVIDER=openrouter`); Gemini's free tier is capped at roughly
20 requests/day per model, which one multi-step agent loop exhausts on its own.

The alert API returns a specific `error` and `hint` for provider failures:

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `quota exhausted`, `429`, `RESOURCE_EXHAUSTED` | Daily/rate budget gone | Space out investigations or use a funded key |
| `rejected the API key`, `403 PERMISSION_DENIED` | Key not valid for that provider | Re-issue the key and update the Secret |
| `can only afford N` (OpenRouter `402`) | Per-request ceiling, **not** a depleted balance | Raise the key's total limit in the OpenRouter dashboard — see below |
| `exceed your available credits given your current in-flight requests` | Free-tier concurrency limit | Already retried with backoff and serialised automatically |

OpenSRE asks for a **fixed 4096 output tokens** per call and that value is not
configurable — `config set max_tokens` and the usual base-URL env vars are all
ignored. So on a provider that cannot afford one full request, every
investigation fails with:

```
HTTP 402 - "You requested up to 4096 tokens, but can only afford 3540."
```

That is a **per-request ceiling, not a depleted balance**, and it is not fixable
from this repo: the real fix is to raise the key's total limit at the provider.

### The token-ceiling shim

Until the key is funded, the backend pod runs an `llm-ceiling-proxy` sidecar
(`opensre-backend/app/services/llm_ceiling_proxy.py`). The CLI talks OpenAI
protocol to `http://127.0.0.1:8900/v1` through its `custom-openai` provider, and
the shim forwards to the real provider with the real key, clamping
`max_tokens`:

- **Self-tuning**: on a `can only afford N` rejection it lowers the cap to
  `N - 64` and replays the call once.
- **Waits out in-flight 402s**: the free tier also rejects overlapping requests,
  which clears on its own, so it backs off instead of failing the run.
- **IPv4-only**: a provider hostname that returns an AAAA record fails with
  `ENETUNREACH` on a Kind cluster (no IPv6 route) roughly every other request.
  The shim drops IPv6 candidates.
- **Degrades to a pass-through**: if the key is funded the cap simply rises, and
  the sidecar plus the three `CUSTOM_OPENAI_*` keys can be deleted with
  `LLM_PROVIDER=openrouter`.

It has a floor, though: a free tier that affords only a few hundred tokens per
call cannot sustain a multi-step agent loop. The shim makes the RCA step work
with a *small* budget, not a zero one. Add credits or raise the key's limit for
reliable RCA.

Verify evidence collection still works independently:

```bash
curl -s http://localhost:8001/api/opensre/investigate/node/opensre-demo-worker
```

Because a failed LLM step never discards collected evidence, the same response
still carries the evidence digest for manual review.

### 10. OpenTelemetry collector in `CrashLoopBackOff`

```bash
helm upgrade otel-collector open-telemetry/opentelemetry-collector \
  -n observability -f observability/otel-values.yaml
```

---

## Security Notes

This is a local demo environment. Several deliberate simplifications are **not**
suitable for production:

- **Elasticsearch and Kibana run with security disabled.** No authentication, no
  TLS, no network isolation.
- **Grafana runs with anonymous Viewer access enabled** and default credentials
  (`admin` / `admin123`).
- **The database connectors accept arbitrary SQL and ad-hoc queries.** They are
  not intended to sit behind an untrusted network.
- **There is no authentication or RBAC on the backend API.** Anyone who can reach
  port 8001 can inject faults, run queries, and read cluster state.
- **`.env` holds a GitHub personal access token in plaintext.** It is git-ignored
  and untracked, but rotate the token if it is ever exposed or committed.

Before adapting this stack beyond a laptop: enable ES/Kibana security, replace
default Grafana credentials, add authentication to the FastAPI backend, and
restrict the database connectors to a read-only role.
