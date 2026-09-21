from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

from app.services import db_investigation
from app.services import investigation
from app.services import opensre_cli
from app.services import yugabyte
from app.services import aerospike
from app.services import kubectl
from app.services import elasticsearch as elasticsearch_service

router = APIRouter(
    prefix="/api/db-investigation",
    tags=["Database Investigation"],
)


class YugabyteQueryRequest(BaseModel):
    sql: str
    limit: int | None = 100
    timeout: int | None = 10


class AerospikeRecordRequest(BaseModel):
    namespace: str
    set: str
    key: str | None = None
    filters: dict | None = None
    limit: int | None = 100


class DataIntegrityRequest(BaseModel):
    namespace: str = "test"
    set: str = "demo"
    required_fields: list[str] | None = None
    unique_fields: list[str] | None = None
    limit: int | None = 100


@router.get("/yugabyte/health")
def yugabyte_health():
    """Quick health check for YugabyteDB."""
    return yugabyte.health()


@router.get("/aerospike/health")
def aerospike_health():
    """Quick health check for Aerospike."""
    return aerospike.health()


@router.get("/yugabyte/evidence")
def yugabyte_evidence(
    schema: bool = Query(True, description="Include schema information"),
    data_integrity: bool = Query(True, description="Include data integrity checks"),
    slow_queries: bool = Query(True, description="Include slow queries"),
    recent_errors: bool = Query(True, description="Include recent errors/active queries"),
    table_stats: bool = Query(True, description="Include table statistics"),
    replication: bool = Query(True, description="Include replication status"),
):
    """Collect comprehensive YugabyteDB investigation evidence."""
    evidence_result = investigation.collect_database_evidence("yugabyte")

    if not evidence_result.get("success"):
        return evidence_result

    evidence = evidence_result["evidence"]
    # Filter based on query parameters
    inv = evidence.get("investigations", {})
    filtered = {"health": inv.get("health")}
    if schema:
        filtered["schema"] = inv.get("schema")
    if data_integrity:
        filtered["data_integrity"] = inv.get("data_integrity")
    if slow_queries:
        filtered["slow_queries"] = inv.get("slow_queries")
    if recent_errors:
        filtered["recent_errors"] = inv.get("recent_errors")
    if table_stats:
        filtered["table_stats"] = inv.get("table_stats")
    if replication:
        filtered["replication"] = inv.get("replication")
    # Always include these
    filtered["cluster_health"] = inv.get("cluster_health")
    filtered["connections"] = inv.get("connections")

    evidence["investigations"] = filtered
    return {"success": True, "evidence": evidence}


@router.get("/aerospike/evidence")
def aerospike_evidence(
    namespaces: bool = Query(True, description="Include namespace information"),
    operation_errors: bool = Query(True, description="Include operation errors"),
    latency: bool = Query(True, description="Include latency information"),
    data_integrity: bool = Query(True, description="Include data integrity checks"),
    demo_stats: bool = Query(True, description="Include demo set stats"),
):
    """Collect comprehensive Aerospike investigation evidence."""
    evidence_result = investigation.collect_database_evidence("aerospike")

    if not evidence_result.get("success"):
        return evidence_result

    evidence = evidence_result["evidence"]
    inv = evidence.get("investigations", {})
    filtered = {"health": inv.get("health")}
    if namespaces:
        filtered["namespaces"] = inv.get("namespaces")
    if operation_errors:
        filtered["operation_errors"] = inv.get("operation_errors")
    if latency:
        filtered["latency"] = inv.get("latency")
    if data_integrity:
        filtered["data_integrity"] = inv.get("data_integrity")
    if demo_stats:
        filtered["demo_set_stats"] = inv.get("demo_set_stats")
    filtered["cluster_health"] = inv.get("cluster_health")

    evidence["investigations"] = filtered
    return {"success": True, "evidence": evidence}


@router.get("/evidence")
def all_database_evidence():
    """Collect investigation evidence for both databases."""
    return investigation.collect_all_database_evidence()


@router.post("/yugabyte/query")
def yugabyte_safe_query(request: YugabyteQueryRequest):
    """Execute a safe read-only SQL query on YugabyteDB."""
    if not request.sql or not request.sql.strip():
        raise HTTPException(status_code=400, detail="SQL query cannot be empty")

    # The yugabyte.service query function already enforces read-only
    return yugabyte.query(request.sql)


@router.post("/aerospike/record")
def aerospike_record(request: AerospikeRecordRequest):
    """Inspect Aerospike record(s) by key or filters."""
    if not request.namespace or not request.set:
        raise HTTPException(status_code=400, detail="Namespace and set are required")

    return aerospike.record_inspection(
        namespace=request.namespace,
        set_name=request.set,
        key=request.key,
        filters=request.filters,
        limit=request.limit or 100,
    )


@router.post("/aerospike/data-integrity")
def aerospike_data_integrity(request: DataIntegrityRequest):
    """Run data integrity checks on Aerospike namespace/set."""
    return aerospike.data_integrity_checks(
        namespace=request.namespace,
        set_name=request.set,
        required_fields=request.required_fields,
        unique_fields=request.unique_fields,
        limit=request.limit or 100,
    )


@router.post("/aerospike/namespace-stats")
def aerospike_namespace_stats(namespace: str = Query(...), set_name: str = Query(...)):
    """Get namespace/set statistics."""
    return aerospike.namespace_set_stats(namespace, set_name)


@router.get("/opensre/investigate/yugabyte")
def opensre_investigate_yugabyte():
    """Run OpenSRE investigation on YugabyteDB evidence."""
    evidence_result = investigation.collect_database_evidence("yugabyte")

    if not evidence_result.get("success"):
        return evidence_result

    evidence = evidence_result["evidence"]
    evidence["question"] = (
        "Investigate this YugabyteDB database for any issues. "
        "Check cluster health, connections, slow queries, errors, schema, "
        "data integrity, and replication status. "
        "Provide root cause, confidence, evidence, timeline, and remediation."
    )

    return opensre_cli.investigate(evidence)


@router.get("/opensre/investigate/aerospike")
def opensre_investigate_aerospike():
    """Run OpenSRE investigation on Aerospike evidence."""
    evidence_result = investigation.collect_database_evidence("aerospike")

    if not evidence_result.get("success"):
        return evidence_result

    evidence = evidence_result["evidence"]
    evidence["question"] = (
        "Investigate this Aerospike database for any issues. "
        "Check cluster health, namespaces, operation errors, latency, "
        "and data integrity. "
        "Provide root cause, confidence, evidence, timeline, and remediation."
    )

    return opensre_cli.investigate(evidence)


@router.get("/opensre/investigate/all")
def opensre_investigate_all_databases():
    """Run OpenSRE investigation on both databases."""
    evidence_result = investigation.collect_all_database_evidence()

    if not evidence_result.get("success"):
        return evidence_result

    evidence = evidence_result["evidence"]
    evidence["question"] = (
        "Investigate both YugabyteDB and Aerospike databases for any issues. "
        "Compare findings across both databases. "
        "Provide root cause, confidence, evidence, timeline, and remediation."
    )

    return opensre_cli.investigate(evidence)


# ------------------------------------------------------------------
# Database pod logs (live tail via kubectl + history via Elasticsearch)
# ------------------------------------------------------------------

DB_LOG_PODS = {
    "yugabyte": "yugabytedb-0",
    "aerospike": "aerospike-0",
}
DB_LOG_NAMESPACE = "databases"


def _db_live_logs(target: str, tail: int, previous: bool):
    """Live `kubectl logs` tail for a database pod (read-only)."""
    pod = DB_LOG_PODS[target]
    result = kubectl.get_pod_logs(
        DB_LOG_NAMESPACE, pod, tail=tail, previous=previous
    )
    if not result.get("success"):
        detail = (
            result.get("stderr") or result.get("error") or "kubectl logs failed"
        )
        if "not found" in detail.lower():
            return {
                "success": False,
                "pod": pod,
                "namespace": DB_LOG_NAMESPACE,
                "error": (
                    f"Pod {pod} not found — the database is likely scaled "
                    f"to 0. Recover it first, then retry."
                ),
                "detail": detail[:500],
            }
        return {
            "success": False,
            "pod": pod,
            "namespace": DB_LOG_NAMESPACE,
            "error": "Could not fetch database logs.",
            "detail": detail[:500],
        }
    lines = (result.get("stdout") or "").splitlines()
    return {
        "success": True,
        "pod": pod,
        "namespace": DB_LOG_NAMESPACE,
        "previous": previous,
        "count": len(lines),
        "lines": lines,
    }


def _db_log_history(target: str, since_minutes: int, limit: int, pattern: str | None):
    """Historical pod logs from Elasticsearch (Fluent Bit ships the
    `databases` namespace into `logs-opensre-*`)."""
    import datetime

    pod = DB_LOG_PODS[target]
    end = datetime.datetime.utcnow().isoformat() + "Z"
    start = (
        datetime.datetime.utcnow()
        - datetime.timedelta(minutes=since_minutes)
    ).isoformat() + "Z"
    res = elasticsearch_service.search_logs(
        start_time=start,
        end_time=end,
        namespace=DB_LOG_NAMESPACE,
        pod=pod,
        pattern=pattern or None,
        limit=limit,
    )
    if not res.get("success") or not res.get("available"):
        return {
            "success": False,
            "pod": pod,
            "namespace": DB_LOG_NAMESPACE,
            "error": res.get("error") or "Elasticsearch unavailable",
            "hint": (
                "Start the Elasticsearch port-forward "
                "(`kubectl port-forward -n observability svc/es-external "
                "9200:9200`) and retry. Live tail below does not need it."
            ),
        }
    return {
        "success": True,
        "pod": pod,
        "namespace": DB_LOG_NAMESPACE,
        "since_minutes": since_minutes,
        "pattern": pattern or None,
        "total": res.get("total", 0),
        "hits": res.get("hits", []),
    }


@router.get("/yugabyte/logs")
def yugabyte_logs(
    tail: int = Query(default=200, ge=10, le=2000),
    previous: bool = Query(default=False, description="Previous container logs (restarts)"),
):
    """Live YugabyteDB pod log tail (read-only kubectl logs)."""
    return _db_live_logs("yugabyte", tail=tail, previous=previous)


@router.get("/aerospike/logs")
def aerospike_logs(
    tail: int = Query(default=200, ge=10, le=2000),
    previous: bool = Query(default=False, description="Previous container logs (restarts)"),
):
    """Live Aerospike pod log tail (read-only kubectl logs)."""
    return _db_live_logs("aerospike", tail=tail, previous=previous)


@router.get("/yugabyte/log-history")
def yugabyte_log_history(
    since_minutes: int = Query(default=60, ge=1, le=1440),
    limit: int = Query(default=50, ge=1, le=100),
    pattern: str | None = Query(default=None, description="Keyword/regex filter"),
):
    """Historical YugabyteDB logs from Elasticsearch."""
    return _db_log_history("yugabyte", since_minutes, limit, pattern)


@router.get("/aerospike/log-history")
def aerospike_log_history(
    since_minutes: int = Query(default=60, ge=1, le=1440),
    limit: int = Query(default=50, ge=1, le=100),
    pattern: str | None = Query(default=None, description="Keyword/regex filter"),
):
    """Historical Aerospike logs from Elasticsearch."""
    return _db_log_history("aerospike", since_minutes, limit, pattern)