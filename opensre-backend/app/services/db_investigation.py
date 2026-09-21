"""Database Investigation Service.

Orchestrates read-only database investigations for YugabyteDB and Aerospike,
providing structured evidence for OpenSRE root-cause analysis.
"""

from app.services import aerospike
from app.services import yugabyte
from app.services import kubectl
from app.core.config import settings


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

    # Kubernetes-level evidence
    evidence["kubernetes"] = collect_yugabytedb_k8s_evidence()

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

    # Data integrity checks for demo namespace/set
    evidence["investigations"]["data_integrity"] = aerospike.data_integrity_checks(
        namespace="test",
        set_name="demo",
        required_fields=["name", "status"],
        unique_fields=["_key"],
        limit=100
    )

    # Namespace/set stats for demo
    evidence["investigations"]["demo_set_stats"] = aerospike.namespace_set_stats("test", "demo")

    # Kubernetes-level evidence
    evidence["kubernetes"] = collect_aerospike_k8s_evidence()

    return evidence


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


def collect_all_database_k8s_evidence():
    """Collect Kubernetes-level evidence for both databases."""
    return {
        "yugabyte": collect_yugabytedb_k8s_evidence(),
        "aerospike": collect_aerospike_k8s_evidence(),
    }


from app.utils.command import run_command
import json


def investigate_yugabyte_targeted(
    include_schema: bool = True,
    include_data_integrity: bool = True,
    include_slow_queries: bool = True,
    include_recent_errors: bool = True,
    include_table_stats: bool = True,
    include_replication: bool = True,
):
    """Run targeted YugabyteDB investigation with configurable checks."""
    evidence = {
        "database": "yugabyte",
        "endpoint": f"{settings.YUGABYTE_HOST}:{settings.YUGABYTE_PORT}",
        "investigations": {},
    }

    evidence["investigations"]["health"] = yugabyte.health()
    evidence["investigations"]["cluster_health"] = yugabyte.cluster_health()
    evidence["investigations"]["connections"] = yugabyte.connection_status()

    if include_slow_queries:
        evidence["investigations"]["slow_queries"] = yugabyte.slow_queries(limit=20)
    if include_recent_errors:
        evidence["investigations"]["recent_errors"] = yugabyte.recent_errors(limit=50)
    if include_schema:
        evidence["investigations"]["schema"] = yugabyte.schema_info("public")
    if include_table_stats:
        evidence["investigations"]["table_stats"] = yugabyte.table_statistics("public")
    if include_data_integrity:
        evidence["investigations"]["data_integrity"] = yugabyte.data_integrity_checks("public")
    if include_replication:
        evidence["investigations"]["replication"] = yugabyte.replication_status()

    return evidence


def investigate_aerospike_targeted(
    include_namespaces: bool = True,
    include_operation_errors: bool = True,
    include_latency: bool = True,
    include_data_integrity: bool = True,
    include_demo_stats: bool = True,
):
    """Run targeted Aerospike investigation with configurable checks."""
    evidence = {
        "database": "aerospike",
        "endpoint": settings.AEROSPIKE_HOSTS,
        "investigations": {},
    }

    evidence["investigations"]["health"] = aerospike.health()
    evidence["investigations"]["cluster_health"] = aerospike.cluster_health()

    if include_namespaces:
        evidence["investigations"]["namespaces"] = aerospike.namespace_info()
    if include_operation_errors:
        evidence["investigations"]["operation_errors"] = aerospike.operation_errors()
    if include_latency:
        evidence["investigations"]["latency"] = aerospike.latency_info()
    if include_data_integrity:
        evidence["investigations"]["data_integrity"] = aerospike.data_integrity_checks(
            namespace="test",
            set_name="demo",
            required_fields=["name", "status"],
            unique_fields=["_key"],
            limit=100
        )
    if include_demo_stats:
        evidence["investigations"]["demo_set_stats"] = aerospike.namespace_set_stats("test", "demo")

    return evidence