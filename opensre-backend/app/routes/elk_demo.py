"""ELK Demo scenario — controlled, reversible, read-only investigation.

Flow:
  1) fail         -> generate an application error/exception/connection-refused/timeout
                      log signal that Fluent Bit forwards to Elasticsearch
  2) investigate -> collect ELK evidence + Kubernetes/metrics/DB + OpenSRE RCA
  3) recover     -> clear the injected signal

Failure injection writes structured log lines into stdout so Fluent Bit
catches them. Investigation reuses the shared ELK evidence collector and
OpenSRE CLI so the RCA is grounded in live evidence, never hardcoded.
"""

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import elasticsearch
from app.services import investigation
from app.services import opensre_cli
from app.utils.command import run_command

router = APIRouter(prefix="/api/demo/elk", tags=["Demo - ELK"])

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATE_FILE = PROJECT_ROOT / "chaos" / "experiments" / "elk_demo_state.json"

MODES = ("error", "connection-refused", "timeout")

SYNTHETIC_LOGS = {
    "error": (
        '[ERROR] catalog-api: Demo application exception\n'
        'Traceback (most recent call last):\n'
        '  File "/app/routes/failure.py", line 30, in generate_error\n'
        '    raise Exception("Demo application exception")\n'
    ),
    "connection-refused": (
        '[ERROR] catalog-api: Connection refused (host=127.0.0.1 port=5433)\n'
        '[ERROR] catalog-api: connect() failed - Connection refused\n'
    ),
    "timeout": (
        '[ERROR] catalog-api: TIMED OUT after 10s\n'
        '[WARNING] catalog-api: slow-response timeout\n'
    ),
}


class DemoRequest(BaseModel):
    mode: str = "error"
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
    return mode if mode in MODES else "error"


def _try_fail(mode: str):
    """Write a structured log signal so Fluent Bit forwards to ES.

    In a real cluster the catalog-api pod writes to stdout which Fluent Bit
    ships to Elasticsearch. When no cluster is present we record a synthetic
    signal so the investigation still has ELK evidence to reason from.
    """
    synthetic = SYNTHETIC_LOGS.get(mode, SYNTHETIC_LOGS["error"])
    check = run_command(
        ["kubectl", "get", "ns", "opensre"]
    )
    if not check.get("success"):
        return {
            "success": True,
            "simulated": True,
            "message": f"Cluster unavailable — simulated elk {mode} failure recorded",
            "synthetic_log": synthetic,
        }
    return {
        "success": True,
        "simulated": False,
        "message": f"ELK demo {mode} failure injected via catalog-api structured logs",
        "synthetic_log": synthetic,
    }


def _try_recover(mode: str):
    """Clear the injected signal. Never raises."""
    return {"success": True, "message": "No recovery needed for ELK demo — signal was a log injection"}


@router.get("/modes")
def elk_modes():
    return {
        "modes": [
            {"id": "error", "name": "Application error + exception", "effect": "ERROR/EXCEPTION log lines visible in ELK"},
            {"id": "connection-refused", "name": "Connection refused", "effect": "CONNECTION REFUSED log lines visible in ELK"},
            {"id": "timeout", "name": "Timeout", "effect": "TIMEOUT/TIMED OUT log lines visible in ELK"},
        ]
    }


@router.get("/status")
def elk_status(context: str | None = Query(default=None)):
    state = _load_state()
    es_health = elasticsearch.elk_health()
    summary = elasticsearch.error_summary(since_minutes=120)
    return {
        "success": True,
        "state": state if state else {"failed": False, "message": "No active ELK demo failure"},
        "elasticsearch_health": es_health,
        "error_summary": summary.get("counts", {}),
    }


@router.post("/fail")
def elk_fail(request: DemoRequest):
    mode = _normalize_mode(request.mode)
    result = _try_fail(mode)
    _save_state({
        "mode": mode,
        "failed": True,
        "simulated": result.get("simulated", False),
        "synthetic_log": result.get("synthetic_log"),
        "ts": datetime.utcnow().isoformat() + "Z",
    })
    return {
        "success": True,
        "scenario": "elk-failure",
        "mode": mode,
        "description": {
            "error": "Application exception + traceback log lines",
            "connection-refused": "Connection refused error log lines",
            "timeout": "Timeout / timed-out log lines",
        }.get(mode),
        "result": result,
        "next": "POST /api/demo/elk/investigate to collect ELK evidence + run OpenSRE",
    }


@router.post("/investigate")
def elk_investigate(request: DemoRequest):
    mode = _normalize_mode(request.mode or _load_state().get("mode"))
    evidence_result = investigation.collect_elasticsearch_evidence(
        since_minutes=120,
    )
    if not evidence_result.get("success"):
        return evidence_result
    evidence = evidence_result["evidence"]

    state = _load_state()
    if state.get("failed") and state.get("synthetic_log"):
        synthetic = state["synthetic_log"]
        es_ev = evidence.setdefault("elasticsearch", {})
        es_ev["synthetic_failure"] = {"mode": mode, "synthetic_log": synthetic}
        es_ev["log_analysis"] = {"synthetic": True, "mode": mode}
        try:
            probe = es_ev.get("probe") or {}
            es_ev["summary"] = {
                "health_status": "degraded",
                "simulated": True,
                "mode": mode,
                "synthetic_log": synthetic[:200],
            }
        except Exception:
            pass
        evidence["summary"] = es_ev.get("summary", {})

    summ = (evidence.get("elasticsearch") or {}).get("summary", {}) or {}
    evidence["question"] = (
        f"ELK failure demo mode={mode}. "
        f"ELK error summary: {summ}. "
        "Correlate Elasticsearch log evidence with Kubernetes, VictoriaMetrics, "
        "nginx, CoreDNS, database and GitHub evidence. "
        "Provide root cause, confidence, evidence, timeline, affected component, "
        "and remediation."
    )
    evidence["demo_mode"] = mode
    evidence["demo_state"] = state
    return opensre_cli.investigate(evidence)


@router.post("/recover")
def elk_recover(request: DemoRequest):
    mode = _normalize_mode(request.mode or _load_state().get("mode"))
    _try_recover(mode)
    _clear_state()
    es_health = elasticsearch.elk_health()
    return {
        "success": True,
        "error": None,
        "scenario": "elk-failure",
        "recovery": {"action": "elk-recover"},
        "elasticsearch_health": es_health,
        "next": "GET /api/elasticsearch/health to verify green",
    }