from fastapi import APIRouter, Query

from app.services import elasticsearch
from app.services import investigation
from app.services import opensre_cli

router = APIRouter(prefix="/api/elasticsearch", tags=["Elasticsearch"])


@router.get("/health")
def es_health():
    """Check Elasticsearch connectivity (read-only)."""
    return elasticsearch.elk_health()


@router.get("/search")
def search_logs(
    service: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
    pod: str | None = Query(default=None),
    container: str | None = Query(default=None),
    level: str | None = Query(default=None),
    pattern: str | None = Query(default=None),
    since_minutes: int = Query(default=60, ge=1, le=1440),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Search ELK logs with bounded, read-only filters."""
    end = __import__("datetime").datetime.utcnow().isoformat() + "Z"
    start = (__import__("datetime").datetime.utcnow() - __import__("datetime").timedelta(minutes=since_minutes)).isoformat() + "Z"
    return elasticsearch.search_logs(
        start_time=start, end_time=end,
        service=service, namespace=namespace,
        pod=pod, container=container,
        level=level, pattern=pattern, limit=limit,
        from_offset=offset,
    )


@router.get("/errors")
def recent_errors(
    namespace: str | None = Query(default=None),
    since_minutes: int = Query(default=60, ge=1, le=1440),
    limit: int = Query(default=30, ge=1, le=100),
):
    """Return recent ERROR-level log lines."""
    return elasticsearch.get_recent_errors(
        since_minutes=since_minutes, namespace=namespace, limit=limit,
    )


@router.get("/service/{service}")
def service_logs(
    service: str,
    since_minutes: int = Query(default=60, ge=1, le=1440),
    limit: int = Query(default=50, ge=1, le=100),
):
    """Return logs for a specific service."""
    return elasticsearch.get_service_logs(service, since_minutes=since_minutes, limit=limit)


@router.get("/pod/{namespace}/{pod}")
def pod_logs(
    namespace: str,
    pod: str,
    since_minutes: int = Query(default=60, ge=1, le=1440),
    limit: int = Query(default=50, ge=1, le=100),
):
    """Return logs for a specific pod."""
    return elasticsearch.get_pod_logs(pod, namespace, since_minutes=since_minutes, limit=limit)


@router.get("/patterns")
def error_patterns(
    namespace: str | None = Query(default=None),
    since_minutes: int = Query(default=60, ge=1, le=1440),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Find ERROR/EXCEPTION/FAILED/CONNECTION REFUSED/TIMEOUT lines."""
    return elasticsearch.find_error_patterns(namespace=namespace, since_minutes=since_minutes, limit=limit)


@router.get("/summary")
def error_summary(
    namespace: str | None = Query(default=None),
    since_minutes: int = Query(default=60, ge=1, le=1440),
):
    """Compact summary of error counts per pattern."""
    return elasticsearch.error_summary(namespace=namespace, since_minutes=since_minutes)


@router.get("/evidence")
def elk_evidence(
    namespace: str | None = Query(default=None),
    pod: str | None = Query(default=None),
    service: str | None = Query(default=None),
    since_minutes: int = Query(default=60, ge=1, le=1440),
):
    """
    Collect structured Elasticsearch evidence for investigation.
    Read-only — no write/delete/index operations.
    """
    summary = elasticsearch.error_summary(namespace=namespace, since_minutes=since_minutes)
    patterns = elasticsearch.find_error_patterns(namespace=namespace, since_minutes=since_minutes, limit=20)
    if pod:
        logs = elasticsearch.get_pod_logs(pod, namespace or "opensre", since_minutes=since_minutes, limit=20)
    else:
        logs = {"success": True, "available": True, "hits": []}

    evidence = {
        "target": {"type": "elasticsearch", "name": "elk"},
        "elasticsearch": {
            "health": elasticsearch.elk_health(),
            "summary": summary.get("counts", {}),
            "error_samples": patterns.get("results", [])[:10],
            "logs": logs.get("hits", []),
        },
    }
    return {"success": True, "evidence": evidence}


@router.get("/investigate")
def elk_investigate(
    namespace: str | None = Query(default=None),
    pod: str | None = Query(default=None),
    service: str | None = Query(default=None),
    since_minutes: int = Query(default=60, ge=1, le=1440),
):
    """
    Collect Elasticsearch evidence, correlate with Kubernetes/metrics/DB,
    then run OpenSRE investigation. No hardcoded RCA — evidence drives it.
    """
    es_evidence_result = investigation.collect_elasticsearch_evidence(
        namespace=namespace, pod=pod, service=service, since_minutes=since_minutes,
    )
    if not es_evidence_result.get("success"):
        return es_evidence_result
    evidence = es_evidence_result["evidence"]

    summ = (evidence.get("elasticsearch") or {}).get("summary", {}) or {}
    evidence["question"] = (
        "Investigate this ELK/Evidence layer for ERROR/EXCEPTION/FAILED/CONNECTION REFUSED/TIMEOUT patterns. "
        f"Error summary: {summ}. "
        "Correlate Elasticsearch log evidence with Kubernetes, VictoriaMetrics, "
        "nginx, CoreDNS, database and GitHub evidence. "
        "Provide root cause, confidence, evidence, timeline, affected component, "
        "and recommended remediation."
    )
    return opensre_cli.investigate(evidence)


@router.get("/facets")
def get_facets(
    namespace: str | None = Query(default=None),
    since_minutes: int = Query(default=60, ge=1, le=1440),
    size: int = Query(default=20, ge=1, le=100),
):
    """Get unique values for faceted search (namespaces, pods, services, levels, containers)."""
    return elasticsearch.get_facets(namespace=namespace, since_minutes=since_minutes, size=size)


@router.get("/pods-by-namespace")
def get_pods_by_namespace(
    namespace: str = Query(...),
    since_minutes: int = Query(default=60, ge=1, le=1440),
    size: int = Query(default=100, ge=1, le=500),
):
    """Get unique pod names filtered by namespace."""
    return elasticsearch.get_pods_by_namespace(namespace=namespace, since_minutes=since_minutes, size=size)