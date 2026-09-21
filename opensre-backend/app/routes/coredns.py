from fastapi import APIRouter, Query
from app.services import investigation
from app.services import coredns as coredns_service
from app.services import opensre_cli

router = APIRouter(prefix="/api/coredns", tags=["CoreDNS"])


@router.get("/health")
def coredns_health(
    context: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
):
    """Lightweight health check for CoreDNS (pods + readiness + restarts)."""
    ns = namespace or coredns_service.COREDNS_NAMESPACE
    return coredns_service.health(namespace=ns, context=context)


@router.get("/probe")
def coredns_probe(
    context: str | None = Query(default=None),
    target: str | None = Query(
        default=None,
        description="DNS name to resolve (default: kubernetes.default).",
    ),
):
    """Live in-cluster DNS resolution probe (latency + success, read-only)."""
    return coredns_service.dns_probe(context=context, target=target)


@router.get("/metrics")
def coredns_metrics(
    context: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
):
    """CoreDNS metrics from VictoriaMetrics (best-effort, read-only)."""
    ns = namespace or coredns_service.COREDNS_NAMESPACE
    return coredns_service.coredns_vm_metrics(namespace=ns)


@router.get("/evidence")
def coredns_evidence(
    context: str | None = Query(default=None),
    tail: int = Query(default=150, ge=10, le=1000),
):
    """
    Collect CoreDNS + DNS probe + metrics + affected-workload evidence
    without running an AI investigation. Independent of databases.
    """
    return investigation.collect_coredns_evidence(context=context, tail=tail)


@router.get("/investigate")
def coredns_investigate(
    context: str | None = Query(default=None),
    tail: int = Query(default=150, ge=10, le=1000),
):
    """
    Collect CoreDNS evidence (plus metrics/affected workloads/git
    correlation) and run OpenSRE investigation. Returns grounded RCA with
    timeline/recommendation — no hardcoded results.
    """
    evidence_result = investigation.collect_coredns_evidence(
        context=context, tail=tail
    )
    if not evidence_result.get("success"):
        return evidence_result
    evidence = evidence_result["evidence"]
    summary = (evidence.get("coredns") or {}).get("summary", {}) or {}
    evidence["question"] = (
        "Investigate this CoreDNS/DNS layer for latency or resolution "
        "failures. "
        f"Summary: health={summary.get('health_status', '?')}, "
        f"pods={summary.get('running_pods', '?')}/"
        f"{summary.get('total_pods', '?')} running, "
        f"restarts={summary.get('restart_count_total', '?')}, "
        f"probe={summary.get('probe_verdict', '?')} "
        f"(avg={summary.get('probe_latency_ms_avg', '?')}ms), "
        f"SERVFAIL={summary.get('servfail', '?')}, "
        f"timeouts={summary.get('dns_timeouts', '?')}, "
        f"forward_errors={summary.get('forward_errors', '?')}. "
        "Correlate CoreDNS pod state/logs/events, the live DNS probe, "
        "VictoriaMetrics, affected workloads, and GitHub history if relevant. "
        "Provide root cause, confidence, evidence, timeline, affected "
        "component, and remediation."
    )
    return opensre_cli.investigate(evidence)
