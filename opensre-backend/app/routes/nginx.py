from fastapi import APIRouter, Query
from app.services import investigation
from app.services import nginx as nginx_service
from app.services import opensre_cli

router = APIRouter(prefix="/api/nginx", tags=["Nginx"])

@router.get("/health")
def nginx_health(context: str | None = Query(default=None), namespace: str | None = Query(default=None)):
    """Lightweight health check for Nginx (pods + config validation)."""
    ns = namespace or nginx_service.NGINX_NAMESPACE
    return nginx_service.health(namespace=ns, context=context)

@router.get("/config/validate")
def nginx_config_validate(context: str | None = Query(default=None), namespace: str | None = Query(default=None)):
    """Run `nginx -t` inside the pod — read-only."""
    ns = namespace or nginx_service.NGINX_NAMESPACE
    return nginx_service.nginx_config_test(namespace=ns, context=context)

@router.get("/config/dump")
def nginx_config_dump(context: str | None = Query(default=None), namespace: str | None = Query(default=None)):
    """Run `nginx -T` (effective config dump) — read-only, truncated."""
    ns = namespace or nginx_service.NGINX_NAMESPACE
    return nginx_service.nginx_config_dump(namespace=ns, context=context)

@router.get("/logs")
def nginx_logs(
    context: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
    pod: str | None = Query(default=None),
    tail: int = Query(default=200, ge=10, le=2000),
):
    """Fetch raw nginx logs for a pod."""
    ns = namespace or nginx_service.NGINX_NAMESPACE
    return nginx_service.get_nginx_logs(namespace=ns, pod_name=pod, tail=tail, context=context)

@router.get("/logs/analysis")
def nginx_logs_analysis(
    context: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
    tail: int = Query(default=250, ge=10, le=2000),
):
    """Parsed access/error log analysis (4xx/5xx, 502/503/504, upstream failures, latency)."""
    ns = namespace or nginx_service.NGINX_NAMESPACE
    pods_res = nginx_service.get_nginx_pods(ns, context)
    if not pods_res.get("success"):
        return pods_res
    pods = pods_res.get("pods", [])
    if not pods:
        return {"success": True, "analysis": {"access": {}, "error": {}}, "message": "No nginx pods deployed"}
    # aggregate logs from first pod for quick analysis endpoint
    logs_res = nginx_service.get_nginx_logs(ns, pods[0]["name"], tail=tail, context=context)
    combined = logs_res.get("stdout", "") if logs_res.get("success") else ""
    analysis = nginx_service.analyze_logs(combined) if combined else {"access": {}, "error": {}}
    return {"success": True, "pod": pods[0]["name"], "analysis": analysis, "logs_success": logs_res.get("success")}

@router.get("/evidence")
def nginx_evidence(context: str | None = Query(default=None), tail: int = Query(default=250, ge=10, le=2000)):
    """
    Collect nginx evidence and correlate with Kubernetes, VictoriaMetrics and GitHub.
    Read-only; independent of database integrations.
    """
    return investigation.collect_nginx_evidence(context=context, tail=tail)

@router.get("/investigate")
def nginx_investigate(context: str | None = Query(default=None), tail: int = Query(default=250, ge=10, le=2000)):
    """
    Collect nginx evidence (plus K8s/metrics/git correlation) and run OpenSRE investigation.
    Returns grounded RCA with timeline/recommendation — no hardcoded results.
    """
    evidence_result = investigation.collect_nginx_evidence(context=context, tail=tail)
    if not evidence_result.get("success"):
        return evidence_result
    evidence = evidence_result["evidence"]
    # Add explicit question for the LLM to anchor the RCA
    nginx_summary = (evidence.get("nginx") or {}).get("summary", {}) or {}
    evidence["question"] = (
        "Investigate this Nginx reverse-proxy for failures. "
        f"Summary: 5xx={nginx_summary.get('http_5xx', '?')}, "
        f"502={nginx_summary.get('http_502', '?')}, "
        f"503={nginx_summary.get('http_503', '?')}, "
        f"504={nginx_summary.get('http_504', '?')}, "
        f"connection_refused={nginx_summary.get('connection_refused', '?')}, "
        f"upstream_timeout={nginx_summary.get('upstream_timeout', '?')}, "
        f"dns_failures={nginx_summary.get('dns_failures', '?')}. "
        "Correlate with Kubernetes pod/events, VictoriaMetrics, and GitHub if relevant. "
        "Provide root cause, confidence, evidence, timeline, affected component, and remediation."
    )
    return opensre_cli.investigate(evidence)
