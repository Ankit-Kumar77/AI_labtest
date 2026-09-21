import json

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import investigation
from app.services import opensre_cli


class InvestigationRequest(BaseModel):
    alert_payload: str


class ChatRequest(BaseModel):
    message: str
    cluster: str | None = None
    namespace: str | None = None
    pod: str | None = None
    target_type: str | None = None


router = APIRouter(
    prefix="/api/opensre",
    tags=["OpenSRE"],
)


@router.get("/version")
def version():
    return opensre_cli.version()


@router.get("/doctor")
def doctor():
    return opensre_cli.doctor()


@router.get("/status")
def status():
    return opensre_cli.status()


@router.post("/investigate")
def investigate(request: InvestigationRequest):
    alert = _parse_alert_payload(request.alert_payload)

    if alert is None:
        return {
            "success": False,
            "error": "alert_payload must be a JSON object describing an alert",
        }

    result = investigation.collect_alert_evidence(alert)

    if not result.get("success"):
        # Never forward a bare alert: that produces unverifiable
        # "Non-Validated Claims" triage instead of a grounded RCA.
        return result

    return opensre_cli.investigate(result["payload"])


def _parse_alert_payload(alert_payload: str):
    """
    Normalize inbound alert payloads (Grafana notification, Alertmanager
    webhook, or a bare alert object) into a single alert dict.
    """
    if not alert_payload:
        return None

    try:
        data = json.loads(alert_payload)
    except (TypeError, ValueError):
        data = None

    if isinstance(data, dict):
        # Alertmanager webhook wrapping multiple alerts.
        if isinstance(data.get("alerts"), list) and data["alerts"]:
            data = data["alerts"][0]

        if "labels" in data or "alertname" in data or "annotations" in data:
            return data

        return None

    if isinstance(data, list):
        for candidate in data:
            if isinstance(candidate, dict) and (
                "labels" in candidate
                or "alertname" in candidate
                or "annotations" in candidate
            ):
                return candidate

        return None

    return None


@router.get("/investigate/pod/{namespace}/{pod_name}")
def investigate_pod(
    namespace: str,
    pod_name: str,
    context: str | None = None,
    tail: int = 200,
):
    evidence_result = investigation.collect_pod_evidence(
        namespace,
        pod_name,
        context,
        tail=max(10, min(tail, 500)),
    )

    if not evidence_result.get("success"):
        return evidence_result

    # Run investigation via CLI to get the markdown report
    cli_result = opensre_cli.investigate(evidence_result["evidence"])

    # Enrich the response with VictoriaMetrics pod metrics and ES log signals
    enriched = {
        "success": cli_result.get("success"),
        "stdout": cli_result.get("stdout"),
        "stderr": cli_result.get("stderr"),
        "returncode": cli_result.get("returncode"),
        "incident_id": cli_result.get("incident_id"),
        # VictoriaMetrics pod metrics (request rate, error rate, latency percentiles)
        "vm_metrics": ((evidence_result.get("evidence") or {}).get("metrics") or {}).get("pod", {}),
        # Elasticsearch log signals (ERROR/EXCEPTION/TIMEOUT counts + sample logs)
        "es_signals": (evidence_result.get("evidence") or {}).get("elasticsearch", {}) or {},
    }
    return enriched


@router.get("/investigate/target/{target_type}")
def investigate_target(
    target_type: str,
):
    evidence_result = investigation.collect_target_evidence(target_type)

    if not evidence_result.get("success"):
        return evidence_result

    return opensre_cli.investigate(
        evidence_result["evidence"]
    )


@router.get("/investigate/stack")
def investigate_stack(
    context: str | None = None,
):
    evidence_result = investigation.collect_stack_evidence(context)

    if not evidence_result.get("success"):
        return evidence_result

    return opensre_cli.investigate(
        evidence_result["evidence"]
    )


@router.get("/investigate/coredns")
def investigate_coredns(
    context: str | None = None,
    tail: int = 150,
):
    evidence_result = investigation.collect_coredns_evidence(
        context,
        tail=max(10, min(tail, 1000)),
    )

    if not evidence_result.get("success"):
        return evidence_result

    return opensre_cli.investigate(
        evidence_result["evidence"]
    )


@router.get("/investigate/elk")
def investigate_elk(
    namespace: str | None = None,
    pod: str | None = None,
    service: str | None = None,
    since_minutes: int = 60,
):
    evidence_result = investigation.collect_elasticsearch_evidence(
        namespace=namespace, pod=pod, service=service,
        since_minutes=since_minutes,
    )
    if not evidence_result.get("success"):
        return evidence_result
    evidence = evidence_result["evidence"]
    summ = (evidence.get("elasticsearch") or {}).get("summary", {}) or {}
    evidence["question"] = (
        "Investigate ELK/Evidence layer for ERROR/EXCEPTION/FAILED/"
        "CONNECTION REFUSED/TIMEOUT patterns. "
        f"Error summary: {summ}. "
        "Correlate Elasticsearch log evidence with Kubernetes, "
        "VictoriaMetrics, nginx, CoreDNS, database and GitHub evidence. "
        "Provide root cause, confidence, evidence, timeline, "
        "affected component, and remediation."
    )
    return opensre_cli.investigate(evidence)


@router.post("/chat")
def chat(request: ChatRequest):
    # Host service target (Aerospike / YugabyteDB) selected in the UI.
    if request.target_type:
        if request.target_type == investigation.STACK_TARGET:
            evidence_result = investigation.collect_stack_evidence(
                request.cluster
            )
        elif request.target_type == "coredns":
            evidence_result = investigation.collect_coredns_evidence(
                request.cluster
            )
        elif request.target_type == "elk":
            evidence_result = investigation.collect_elasticsearch_evidence()
        else:
            evidence_result = investigation.collect_target_evidence(
                request.target_type
            )

        if not evidence_result.get("success"):
            return evidence_result

        evidence = evidence_result["evidence"]

        # Add the user's actual question to the evidence.
        evidence["question"] = request.message

        return opensre_cli.investigate(evidence, source="chat")

    # If a pod is selected, collect real Kubernetes evidence
    # from the selected cluster before sending the request to OpenSRE.
    if request.namespace and request.pod:
        evidence_result = investigation.collect_pod_evidence(
            request.namespace,
            request.pod,
            request.cluster,
        )

        if not evidence_result.get("success"):
            return evidence_result

        evidence = evidence_result["evidence"]

        # Add the user's actual question to the evidence.
        evidence["question"] = request.message

        return opensre_cli.investigate(evidence, source="chat")

    # Fallback when no pod is selected.
    return opensre_cli.chat(
        {
            "message": request.message,
            "context": {
                "cluster": request.cluster,
                "namespace": request.namespace,
                "pod": request.pod,
            },
        }
    )
