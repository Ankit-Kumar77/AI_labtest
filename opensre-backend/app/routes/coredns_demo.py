"""Demo scenario for CoreDNS failures — controlled, reversible, read-only investigation.

Flow:
  1) fail         -> kill a CoreDNS pod (self-heal), scale CoreDNS to 0
                     (sustained outage), or add DNS-visible latency
  2) investigate  -> collect CoreDNS + probe/metrics/affected + git + OpenSRE RCA
  3) recover      -> restore CoreDNS replicas / remove latency, verify health

Failure injection delegates to chaos/runbook.sh (same mechanism as the other
chaos actions); investigation reuses the shared CoreDNS evidence collector so
the RCA is grounded in live evidence, never hardcoded. When no cluster is
present the demo records a synthetic failure and the investigate step injects
synthetic SERVFAIL signal so the flow stays deterministic.

Scope is the demo Kind cluster only (kube-system/coredns + worker netem).
"""

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import coredns as coredns_service
from app.services import investigation
from app.services import opensre_cli
from app.utils.command import run_command

router = APIRouter(prefix="/api/demo/coredns", tags=["Demo - CoreDNS"])

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNBOOK = PROJECT_ROOT / "chaos" / "runbook.sh"
STATE_FILE = PROJECT_ROOT / "chaos" / "experiments" / "coredns_demo_state.json"

MODES = ("kill", "down", "latency")

# Synthetic CoreDNS log signal used when no cluster is available, so the
# investigate step still has DNS failure evidence to reason from.
SYNTHETIC_LOGS = {
    "kill": (
        "[ERROR] plugin/errors: 2 kubernetes.default.svc.cluster.local. A: "
        "read udp 10.244.0.15:45678->10.96.0.10:53: i/o timeout\n"
        "[WARNING] plugin/forward: no reachable backend for "
        "kubernetes.default.svc.cluster.local."
    ),
    "down": (
        "[ERROR] plugin/errors: 5 kubernetes.default.svc.cluster.local. A: "
        "plugin/forward: no reachable backend\n"
        "[ERROR] plugin/errors: 3 kubernetes.default.svc.cluster.local. A: "
        "Server failed (SERVFAIL)"
    ),
    "latency": (
        "[WARNING] plugin/forward: slow upstream for "
        "kubernetes.default.svc.cluster.local. (avg 812ms over 20 queries)\n"
        "[ERROR] plugin/errors: 1 catalog-api.opensre.svc.cluster.local. A: "
        "read udp 10.244.0.15:45678->10.96.0.10:53: i/o timeout"
    ),
}


class DemoRequest(BaseModel):
    mode: str = "kill"  # kill | down | latency
    context: str | None = None


def _save_state(data: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(STATE_FILE)


def _load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def _clear_state():
    try:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
    except Exception:
        pass


def _normalize_mode(mode: str | None) -> str:
    mode = (mode or "").lower()
    return mode if mode in MODES else "kill"


def _run_runbook(*args: str):
    return run_command(["bash", str(RUNBOOK), *args])


def _try_fail(mode: str, context: str | None):
    """Best-effort failure injection via the chaos runbook. Never raises."""
    synthetic = SYNTHETIC_LOGS.get(mode, SYNTHETIC_LOGS["kill"])

    check = run_command(
        ["kubectl"]
        + (["--context", context] if context else [])
        + ["get", "ns", coredns_service.COREDNS_NAMESPACE]
    )
    if not check.get("success"):
        return {
            "success": True,
            "simulated": True,
            "message": f"Cluster unavailable — simulated coredns {mode} "
                       "failure recorded",
            "synthetic_log": synthetic,
        }

    action = {"kill": "coredns-kill", "down": "coredns-down",
              "latency": "coredns-latency"}[mode]
    result = _run_runbook(action)
    return {
        "success": result.get("success", False),
        "stdout": (result.get("stdout") or "")[:2000],
        "stderr": (result.get("stderr") or "")[:2000],
        "synthetic_log": synthetic,
        "simulated": False,
    }


def _try_recover(mode: str, context: str | None):
    """Restore CoreDNS replicas / remove latency. Never raises."""
    check = run_command(
        ["kubectl"]
        + (["--context", context] if context else [])
        + ["get", "ns", coredns_service.COREDNS_NAMESPACE]
    )
    if not check.get("success"):
        return {
            "success": True,
            "simulated": True,
            "message": "No cluster — synthetic demo state cleared",
        }
    recover_action = (
        "coredns-latency-off" if mode == "latency" else "coredns-up"
    )
    result = _run_runbook("recover", recover_action)
    return {
        "success": result.get("success", False),
        "stdout": (result.get("stdout") or "")[:2000],
        "stderr": (result.get("stderr") or "")[:2000],
        "simulated": False,
    }


@router.get("/modes")
def coredns_modes():
    return {
        "modes": [
            {
                "id": "kill",
                "name": "Kill one CoreDNS pod (self-heal)",
                "effect": "restart evidence + brief resolution blip",
            },
            {
                "id": "down",
                "name": "Scale CoreDNS to 0 (sustained outage)",
                "effect": "DNS resolution fails cluster-wide until recovery",
            },
            {
                "id": "latency",
                "name": "DNS-visible latency (worker netem)",
                "effect": "resolution probe latency climbs until recovery",
            },
        ]
    }


@router.get("/status")
def coredns_status(context: str | None = Query(default=None)):
    state = _load_state()
    health = coredns_service.health(context=context)
    probe = None
    try:
        probe = coredns_service.dns_probe(context=context)
    except Exception:
        pass
    summary = None
    try:
        ev_res = investigation.collect_coredns_evidence(context=context)
        if ev_res.get("success"):
            summary = ((ev_res.get("evidence") or {}).get("coredns") or {}).get(
                "summary")
    except Exception:
        pass
    return {
        "success": True,
        "state": state if state else {
            "failed": False,
            "message": "No active CoreDNS demo failure",
        },
        "health": health,
        "probe": probe,
        "summary": summary,
    }


@router.post("/fail")
def coredns_fail(request: DemoRequest):
    mode = _normalize_mode(request.mode)
    result = _try_fail(mode, request.context)
    _save_state(
        {
            "mode": mode,
            "failed": True,
            "simulated": result.get("simulated", False),
            "synthetic_log": result.get("synthetic_log"),
            "kubectl_success": result.get("success"),
            "ts": datetime.utcnow().isoformat() + "Z",
        }
    )
    return {
        "success": True,
        "scenario": "coredns-failure",
        "mode": mode,
        "description": {
            "kill": "One CoreDNS pod deleted — deployment recreates it "
                    "(restart evidence, brief blip)",
            "down": "CoreDNS scaled to 0 replicas — cluster DNS resolution "
                    "fails until recovery",
            "latency": "Worker-egress latency injected — DNS probe latency "
                       "climbs until recovery",
        }.get(mode),
        "result": result,
        "next": "POST /api/demo/coredns/investigate to collect evidence + "
                "run OpenSRE",
    }


@router.post("/investigate")
def coredns_investigate(request: DemoRequest):
    """
    Collect CoreDNS evidence, correlate with metrics/affected workloads and
    GitHub, then run OpenSRE investigation. No hardcoded RCA — evidence
    drives the result.
    """
    mode = _normalize_mode(request.mode or _load_state().get("mode"))
    evidence_result = investigation.collect_coredns_evidence(
        context=request.context
    )
    if not evidence_result.get("success"):
        return evidence_result
    evidence = evidence_result["evidence"]

    # Simulated mode: inject synthetic SERVFAIL/timeout signal so the RCA has
    # DNS evidence even with no real cluster — keeps the demo deterministic.
    state = _load_state()
    if state.get("failed") and state.get("synthetic_log"):
        synthetic = state["synthetic_log"]
        coredns_ev = evidence.get("coredns") or {}
        evidence["coredns"] = coredns_ev
        coredns_ev["synthetic_failure"] = {
            "mode": mode, "synthetic_log": synthetic
        }
        analysis = coredns_service.analyze_logs(synthetic)
        coredns_ev["log_analysis"] = analysis
        try:
            probe = coredns_ev.get("probe") or {}
            if not probe.get("available"):
                coredns_ev["probe"] = {
                    "success": True,
                    "available": True,
                    "simulated": True,
                    "target": coredns_service.COREDNS_PROBE_TARGET,
                    "attempts": 3,
                    "succeeded": 0 if mode == "down" else 2,
                    "latency_ms_avg": 812.0 if mode == "latency" else None,
                    "verdict": ("failing" if mode == "down"
                                else "slow" if mode == "latency"
                                else "degraded"),
                }
            coredns_ev["summary"] = {
                "health_status": ("down" if mode == "down" else "degraded"),
                "probe_verdict": (coredns_ev.get("probe") or {}).get("verdict"),
                "probe_latency_ms_avg": (coredns_ev.get("probe") or {}).get(
                    "latency_ms_avg"),
                "probe_succeeded": (coredns_ev.get("probe") or {}).get("succeeded"),
                "probe_attempts": (coredns_ev.get("probe") or {}).get("attempts"),
                "servfail": analysis.get("counts", {}).get("servfail", 0),
                "dns_timeouts": analysis.get("counts", {}).get("timeout", 0),
                "dns_refused": 0,
                "forward_errors": analysis.get("counts", {}).get(
                    "forward_error", 0),
                "simulated": True,
            }
            evidence["summary"] = coredns_ev["summary"]
        except Exception:
            pass

    summ = (evidence.get("coredns") or {}).get("summary", {}) or {}
    evidence["question"] = (
        f"CoreDNS failure demo mode={mode}. "
        f"CoreDNS evidence: health={summ.get('health_status', '?')}, "
        f"probe={summ.get('probe_verdict', '?')} "
        f"(avg={summ.get('probe_latency_ms_avg', '?')}ms), "
        f"SERVFAIL={summ.get('servfail', '?')}, "
        f"timeouts={summ.get('dns_timeouts', '?')}, "
        f"forward_errors={summ.get('forward_errors', '?')}. "
        "Correlate CoreDNS pod state/logs/events, the live DNS probe, "
        "VictoriaMetrics, affected workloads, and GitHub history if relevant. "
        "Provide root cause, confidence, evidence, timeline, affected "
        "component, and remediation. "
        "Do not assume any relationship to YugabyteDB or Aerospike."
    )
    evidence["demo_mode"] = mode
    evidence["demo_state"] = state

    opensre_result = opensre_cli.investigate(evidence)
    return {
        "success": opensre_result.get("returncode") == 0
        if "returncode" in opensre_result
        else opensre_result.get("success", False),
        "scenario": "coredns-failure",
        "mode": mode,
        "evidence": evidence,
        "opensre": opensre_result,
    }


@router.post("/recover")
def coredns_recover(request: DemoRequest):
    mode = _normalize_mode(request.mode or _load_state().get("mode"))
    result = _try_recover(mode, request.context)
    restored = bool(result.get("success", False))
    if restored:
        _clear_state()
    health = coredns_service.health(context=request.context)
    return {
        "success": restored,
        "error": None if restored else (
            result.get("stderr") or result.get("error")
            or "restore failed — demo state kept so the failure stays visible"
        ),
        "scenario": "coredns-failure",
        "recovery": {
            "action": "coredns-recover",
            "kubectl_result": result,
        },
        "health": health,
        "next": "GET /api/coredns/health to verify green",
    }
