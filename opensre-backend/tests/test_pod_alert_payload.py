"""Regression tests for the pod investigation alert payload.

The OpenSRE CLI runs with no live k8s tool integrations in this
deployment (only the runbook/knowledge tool is available), so a pod RCA
can only be grounded on the input payload. `pod_alert_payload` folds the
evidence digest into `annotations.description`; without it the CLI emits
a generic "Unable to determine root cause". These tests pin that shape
and never invoke the OpenSRE LLM.
"""

from app.services import investigation

TARGET = ("opensre", "catalog-api-5f86594b59-cwht7")

EVIDENCE = {
    "cluster": "kind-opensre-demo",
    "kubernetes": {
        "state": {
            "phase": "Running",
            "node": "opensre-demo-worker",
            "pod_ip": "10.244.1.15",
            "containers": [
                {
                    "name": "catalog-api",
                    "ready": True,
                    "restart_count": 38,
                    "state": {"reason": "Running"},
                    "last_state": {"reason": "Unknown", "exit_code": 255},
                }
            ],
        },
        "events_structured": [
            {
                "timestamp": "2026-09-21T08:42:23Z",
                "type": "Warning",
                "reason": "BackOff",
                "count": 3,
                "message": "Back-off restarting failed container",
            }
        ],
        "log_analysis": {
            "signal_counts": {"ERROR": 2, "TIMEOUT": 1},
            "relevant_lines": [
                {
                    "container": "catalog-api",
                    "signal": "TIMEOUT",
                    "line": "dial tcp 10.96.0.1:443: i/o timeout",
                }
            ],
        },
        "namespace": "opensre",
    },
    "metrics": {
        "pod": {
            "request_rate_rps": 0.4,
            "error_share_percent": 0.0,
            "p99_latency_seconds": 0.0235,
            "restarts": 38,
        }
    },
    "elasticsearch": {
        "health": {"success": True},
        "log_total": 35,
        "signal_counts": 3,
        "pod_logs_tail": "ERROR dial tcp 10.96.0.1:443: i/o timeout\n",
    },
    "git": {"success": True, "commit": "c06e8e7", "author": "Ankit Kumar"},
    "question": "Investigate Kubernetes pod catalog-api in namespace opensre.",
}


def test_payload_is_alert_shaped():
    payload = investigation.pod_alert_payload(EVIDENCE, *TARGET)

    assert set(payload) >= {"status", "labels", "annotations", "startsAt", "question"}
    assert payload["labels"]["alertname"].startswith("Kubernetes Pod Unhealthy: catalog-api")
    assert payload["labels"]["namespace"] == "opensre"
    assert payload["labels"]["pod"] == "catalog-api-5f86594b59-cwht7"
    assert payload["labels"]["cluster"] == "kind-opensre-demo"
    assert payload["labels"]["severity"] == "high"


def test_description_embeds_decisive_evidence_facts():
    payload = investigation.pod_alert_payload(EVIDENCE, *TARGET)
    description = payload["annotations"]["description"]

    # Crashes, the concrete log error and the events must reach the model.
    assert "restarts=38" in description
    assert "exitCode=255" in description
    assert "dial tcp 10.96.0.1:443: i/o timeout" in description
    assert "BackOff" in description
    assert "catalog-api" in description


def test_question_is_preserved_and_fallback_built():
    payload = investigation.pod_alert_payload(EVIDENCE, *TARGET)
    assert payload["question"] == EVIDENCE["question"]

    bare = {k: v for k, v in EVIDENCE.items() if k != "question"}
    fallback = investigation.pod_alert_payload(bare, *TARGET)
    assert fallback["question"]  # fallback text present


def test_digest_capped():
    import json

    payload = investigation.pod_alert_payload(EVIDENCE, *TARGET)
    assert len(payload["annotations"]["description"]) <= 2201
    assert len(json.dumps(payload)) < 10000