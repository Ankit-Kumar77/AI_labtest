"""Database Investigation Service.

Orchestrates read-only database investigations for YugabyteDB and Aerospike,
providing structured evidence for OpenSRE root-cause analysis.
"""

import json

from app.services import aerospike
from app.services import containers
from app.services import elasticsearch
from app.services import kubectl
from app.services import victoriametrics
from app.services import yugabyte
from app.core.config import settings
from app.utils.command import run_command


def investigate_yugabyte():
    """Run comprehensive YugabyteDB investigation."""
    evidence = {
        "database": "yugabyte",
        "endpoint": f"{settings.YUGABYTE_HOST}:{settings.YUGABYTE_PORT}",
        "investigations": {},
    }

    # Health check
    evidence["investigations"]["health"] = yugabyte.health()

    # Cluster health
    evidence["investigations"]["cluster_health"] = yugabyte.cluster_health()

    # Connection status
    evidence["investigations"]["connections"] = yugabyte.connection_status()

    # Slow queries
    evidence["investigations"]["slow_queries"] = yugabyte.slow_queries(limit=20)

    # Recent errors/active queries
    evidence["investigations"]["recent_errors"] = yugabyte.recent_errors(limit=50)

    # Schema information
    evidence["investigations"]["schema"] = yugabyte.schema_info("public")

    # Table statistics
    evidence["investigations"]["table_stats"] = yugabyte.table_statistics("public")

    # Data integrity checks
    evidence["investigations"]["data_integrity"] = yugabyte.data_integrity_checks("public")

    # Replication status (YugabyteDB-specific)
    evidence["investigations"]["replication"] = yugabyte.replication_status()

    # Kubernetes-level evidence. The k8s collector returns a wrapper of the
    # shape {"database": ..., "kubernetes": {...}}; assign the wrapper and the
    # real pod state ends up at kubernetes.kubernetes.pod_state, where
    # grounding.build_context() (which reads kubernetes.pod_state) never sees
    # it — so the phase guard silently reported phase=None. Flatten it.
    evidence["kubernetes"] = (collect_yugabytedb_k8s_evidence() or {}).get("kubernetes") or {}

    evidence.update(_collect_observability_evidence("yugabyte", "yugabytedb-0"))

    return evidence


def investigate_aerospike():
    """Run comprehensive Aerospike investigation."""
    evidence = {
        "database": "aerospike",
        "endpoint": settings.AEROSPIKE_HOSTS,
        "investigations": {},
    }

    # Health check
    evidence["investigations"]["health"] = aerospike.health()

    # Cluster health
    evidence["investigations"]["cluster_health"] = aerospike.cluster_health()

    # Namespace information
    evidence["investigations"]["namespaces"] = aerospike.namespace_info()

    # Operation errors
    evidence["investigations"]["operation_errors"] = aerospike.operation_errors()

    # Latency information
    evidence["investigations"]["latency"] = aerospike.latency_info()

    # Data integrity. The demo/chaos corruption scenarios write to the
    # `integrity` set (see routes/demo.py db-scenario/data-integrity/*), but
    # this probe only scanned `demo` - so injected missing fields, duplicate
    # keys and invalid values were never scanned for and the investigation
    # reported "no integrity issues" while the data was provably corrupt.
    # Scan every set the scenarios write to.
    evidence["investigations"]["data_integrity"] = aerospike.data_integrity_checks(
        namespace="test",
        set_name="demo",
        required_fields=["name", "status"],
        unique_fields=["_key"],
        limit=100
    )
    evidence["investigations"]["data_integrity_injected"] = aerospike.data_integrity_checks(
        namespace="test",
        set_name="integrity",
        required_fields=["name", "status"],
        unique_fields=["_key"],
        limit=200
    )

    # Namespace/set stats for demo
    evidence["investigations"]["demo_set_stats"] = aerospike.namespace_set_stats("test", "demo")

    # Kubernetes-level evidence. Flatten the collector wrapper so pod_state
    # lands at kubernetes.pod_state where the grounding guard reads it
    # (see investigate_yugabyte for the same defect).
    evidence["kubernetes"] = (collect_aerospike_k8s_evidence() or {}).get("kubernetes") or {}

    # Container, log-history and metrics layers. These were absent from this
    # path entirely, so the OpenSRE agent had namespace/client counters but no
    # runtime state, no historical errors and no timings to reason over.
    evidence.update(_collect_observability_evidence("aerospike", "aerospike-0"))

    return evidence


# ===================================================================
# Cross-cutting observability evidence (container + logs + ES + metrics)
# ===================================================================

def _collect_observability_evidence(database, pod_name, namespace="databases"):
    """Collect the evidence layers the database probes alone cannot provide.

    The client-side probes (`health`, `namespaces`, `latency`, ...) only work
    while the port-forward is alive and describe the database's own view. The
    RCA also needs to know whether the pod is actually running right now, what
    the previous run logged (the only explanation for a restart), what
    Elasticsearch archived, and whether metrics are even being scraped. Every
    collector is best-effort: a failed source is reported as an explicit error
    entry rather than dropped, so the agent can tell "absent" from "broken".
    """
    out = {"container": {}, "elasticsearch": {}, "metrics": {}}

    # --- Container runtime state (current + historical) ---
    try:
        state = containers.container_state(database)
        out["container"]["state"] = state
        last = state.get("last_state") if state.get("success") else None
        if last and last.get("reason"):
            out["container"]["historical_restart"] = last
    except Exception as exc:
        out["container"]["state_error"] = str(exc)

    # --- Container logs: current run, and the previous run when restarted ---
    try:
        current = containers.container_logs(database, tail=120)
        if current.get("success"):
            out["container"]["logs"] = (current.get("stdout") or "")[-4000:]
        else:
            out["container"]["logs_error"] = current.get("stderr") or current.get("error")
    except Exception as exc:
        out["container"]["logs_error"] = str(exc)

    try:
        state = out["container"].get("state") or {}
        if state.get("success") and (state.get("restart_count") or 0) > 0:
            prev = containers.container_logs(database, tail=80, previous=True)
            if prev.get("success") and (prev.get("stdout") or "").strip():
                out["container"]["previous_logs"] = (prev.get("stdout") or "")[-3000:]
    except Exception as exc:
        out["container"]["previous_logs_error"] = str(exc)

    # --- Elasticsearch log history ---
    try:
        out["elasticsearch"]["health"] = elasticsearch.elk_health()
        errors = elasticsearch.get_recent_errors(
            namespace=namespace, since_minutes=180, limit=30
        )
        out["elasticsearch"]["recent_errors"] = errors
        pod_logs = elasticsearch.get_pod_logs(
            pod=pod_name, namespace=namespace, since_minutes=180, limit=30
        )
        if pod_logs.get("success"):
            entries = pod_logs.get("entries") or pod_logs.get("logs") or []
            if entries:
                out["elasticsearch"]["pod_log_entries"] = entries
    except Exception as exc:
        out["elasticsearch"]["error"] = str(exc)

    # --- VictoriaMetrics ---
    try:
        out["metrics"]["health"] = victoriametrics.health()
    except Exception as exc:
        out["metrics"]["health_error"] = str(exc)

    return out


def investigate_all_databases():
    """Run investigation on both databases."""
    return {
        "yugabyte": investigate_yugabyte(),
        "aerospike": investigate_aerospike(),
    }


# ===================================================================
# Kubernetes-level database evidence collection
# ===================================================================

def collect_yugabytedb_k8s_evidence():
    """Collect Kubernetes-level evidence for YugabyteDB."""
    evidence = {
        "database": "yugabyte",
        "kubernetes": {},
    }
    
    # Pod state
    pod_result = kubectl.get_pod_state("databases", "yugabytedb-0")
    if pod_result.get("success"):
        evidence["kubernetes"]["pod_state"] = pod_result.get("state")
    else:
        evidence["kubernetes"]["pod_state_error"] = pod_result.get("stderr")
    
    # Pod details
    pod_details = kubectl.get_pod_details("databases", "yugabytedb-0")
    if pod_details.get("success"):
        evidence["kubernetes"]["pod_details"] = pod_details.get("stdout")
    
    # Pod events
    events = kubectl.get_pod_events("databases", "yugabytedb-0")
    if events.get("success"):
        evidence["kubernetes"]["events"] = events.get("stdout")
    
    # Structured events
    events_json = kubectl.get_pod_events_json("databases", "yugabytedb-0")
    if events_json.get("success"):
        evidence["kubernetes"]["events_structured"] = events_json.get("items", [])
    
    # Pod logs
    logs = kubectl.get_pod_logs("databases", "yugabytedb-0", tail=200)
    if logs.get("success"):
        evidence["kubernetes"]["logs_tail"] = logs.get("stdout", "")[-4000:]
    
    # Service endpoints
    svc_result = run_command(["kubectl", "get", "svc", "yugabytedb", "-n", "databases", "-o", "json"])
    if svc_result.get("success"):
        try:
            svc = json.loads(svc_result.get("stdout", "{}"))
            evidence["kubernetes"]["service"] = {
                "name": svc.get("metadata", {}).get("name"),
                "namespace": svc.get("metadata", {}).get("namespace"),
                "ports": svc.get("spec", {}).get("ports"),
                "cluster_ip": svc.get("spec", {}).get("clusterIP"),
            }
        except Exception:
            pass
    
    # Endpoints
    ep_result = run_command(["kubectl", "get", "endpoints", "yugabytedb", "-n", "databases", "-o", "json"])
    if ep_result.get("success"):
        try:
            ep = json.loads(ep_result.get("stdout", "{}"))
            evidence["kubernetes"]["endpoints"] = ep.get("subsets", [])
        except Exception:
            pass
    
    return evidence


def collect_aerospike_k8s_evidence():
    """Collect Kubernetes-level evidence for Aerospike."""
    evidence = {
        "database": "aerospike",
        "kubernetes": {},
    }
    
    # Pod state
    pod_result = kubectl.get_pod_state("databases", "aerospike-0")
    if pod_result.get("success"):
        evidence["kubernetes"]["pod_state"] = pod_result.get("state")
    else:
        evidence["kubernetes"]["pod_state_error"] = pod_result.get("stderr")
    
    # Pod details
    pod_details = kubectl.get_pod_details("databases", "aerospike-0")
    if pod_details.get("success"):
        evidence["kubernetes"]["pod_details"] = pod_details.get("stdout")
    
    # Pod events
    events = kubectl.get_pod_events("databases", "aerospike-0")
    if events.get("success"):
        evidence["kubernetes"]["events"] = events.get("stdout")
    
    # Structured events
    events_json = kubectl.get_pod_events_json("databases", "aerospike-0")
    if events_json.get("success"):
        evidence["kubernetes"]["events_structured"] = events_json.get("items", [])
    
    # Pod logs
    logs = kubectl.get_pod_logs("databases", "aerospike-0", tail=200)
    if logs.get("success"):
        evidence["kubernetes"]["logs_tail"] = logs.get("stdout", "")[-4000:]
    
    # Service endpoints
    svc_result = run_command(["kubectl", "get", "svc", "aerospike", "-n", "databases", "-o", "json"])
    if svc_result.get("success"):
        try:
            svc = json.loads(svc_result.get("stdout", "{}"))
            evidence["kubernetes"]["service"] = {
                "name": svc.get("metadata", {}).get("name"),
                "namespace": svc.get("metadata", {}).get("namespace"),
                "ports": svc.get("spec", {}).get("ports"),
                "cluster_ip": svc.get("spec", {}).get("clusterIP"),
            }
        except Exception:
            pass
    
    # Endpoints
    ep_result = run_command(["kubectl", "get", "endpoints", "aerospike", "-n", "databases", "-o", "json"])
    if ep_result.get("success"):
        try:
            ep = json.loads(ep_result.get("stdout", "{}"))
            evidence["kubernetes"]["endpoints"] = ep.get("subsets", [])
        except Exception:
            pass
    
    return evidence
