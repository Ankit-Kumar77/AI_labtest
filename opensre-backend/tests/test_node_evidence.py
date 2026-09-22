"""Regression tests for node-level evidence, ES gating and metric parsing.

All tests mock the live infrastructure (kubectl, VM, ES) so they run
deterministically offline and never invoke the OpenSRE LLM.
"""

from app.services import investigation, kubectl


# ---------------------------------------------------------------------------
# Fixtures: canned responses for the live dependencies
# ---------------------------------------------------------------------------

NODE_STATE = {
    "success": True,
    "node": {
        "name": "opensre-demo-worker",
        "unschedulable": False,
        "conditions": [
            {"type": "Ready", "status": "True", "reason": "KubeletReady"},
            {"type": "MemoryPressure", "status": "False"},
        ],
    },
}

NODE_DETAILS = {
    "success": True,
    "stdout": "Name:               opensre-demo-worker\nRoles:              <none>\n",
}

NODE_EVENTS_JSON = {
    "success": True,
    "items": [
        {
            "metadata": {"name": "node1", "creationTimestamp": "2026-09-21T06:00:00Z"},
            "involvedObject": {"kind": "Node", "name": "opensre-demo-worker"},
            "reason": "NodeNotReady",
            "type": "Warning",
            "message": "Node is not ready",
        }
    ],
}

POD_USAGE = {
    "success": True,
    "pods": [
        {"namespace": "opensre", "name": "catalog-api-abc", "phase": "Running", "restarts": 3},
        {"namespace": "opensre", "name": "nginx-xyz", "phase": "Pending", "restarts": 0},
    ],
    "restarts_total": 3,
}

POD_STATE = {
    "success": True,
    "state": {
        "phase": "Running",
        "node": "opensre-demo-worker",
        "pod_ip": "10.244.1.18",
        "containers": [
            {
                "name": "catalog-api",
                "ready": False,
                "restart_count": 3,
                "image": "localhost/catalog-api:v1",
                "state": {"kind": "waiting", "reason": "CrashLoopBackOff", "message": "", "exit_code": None, "started_at": None},
                "last_state": {"kind": "terminated", "reason": "Error", "message": "", "exit_code": 1, "started_at": "2026-09-21T06:00:00Z"},
            }
        ],
    },
}

POD_LOGS = {
    "success": True,
    "stdout": (
        "2026-09-21T06:00:00Z ERROR crashed: connection refused\n"
        "2026-09-21T06:00:01Z INFO started\n"
    ),
}


def _patch_live_deps(monkeypatch):
    """Wire up canned responses for every external dependency."""
    monkeypatch.setattr(kubectl, "run_command", lambda *a, **k: {"success": True, "stdout": "{}"})
    monkeypatch.setattr(investigation.kubectl, "get_node_state", lambda *a, **k: NODE_STATE)
    monkeypatch.setattr(investigation.kubectl, "get_node_details", lambda *a, **k: NODE_DETAILS)
    monkeypatch.setattr(investigation.kubectl, "get_node_events", lambda *a, **k: {"success": True, "stdout": "event lines"})
    monkeypatch.setattr(investigation.kubectl, "get_node_events_json", lambda *a, **k: NODE_EVENTS_JSON)
    monkeypatch.setattr(investigation.kubectl, "get_node_resource_usage", lambda *a, **k: POD_USAGE)
    monkeypatch.setattr(investigation.kubectl, "get_pod_state", lambda *a, **k: POD_STATE)
    monkeypatch.setattr(
        investigation.kubectl,
        "get_pod_logs_container",
        lambda *a, **k: POD_LOGS,
    )
    monkeypatch.setattr(
        investigation,
        "_collect_node_metrics",
        lambda node: {
            "load1": 1.5,
            "cpu_utilization_percent": 12.5,
            "mem_utilization_percent": 66.6,
            "kube_node_pressure": [],
        },
    )
    monkeypatch.setattr(
        investigation,
        "_collect_coredns_summary",
        lambda *a, **k: {"summary": "coredns ok", "pods": ["coredns-1"]},
    )
    monkeypatch.setattr(
        investigation.elasticsearch_service,
        "elk_health",
        lambda: {"success": True, "available": True, "cluster": "opensre-es"},
    )
    monkeypatch.setattr(
        investigation.elasticsearch_service,
        "error_summary",
        lambda since_minutes=60: {"counts": {"ERROR": 4, "TIMEOUT": 1}},
    )
    monkeypatch.setattr(
        investigation.elasticsearch_service,
        "get_pod_logs",
        lambda *a, **k: {"success": True, "available": True, "total": 5, "hits": []},
    )
    monkeypatch.setattr(
        investigation.elasticsearch_service,
        "find_error_patterns",
        lambda *a, **k: {"patterns_found": 2},
    )
    monkeypatch.setattr(
        investigation.git_correlation,
        "correlate_commits",
        lambda incident_start=None, limit=10: {"suspected_commit": {"sha": "abc123"}},
    )


# ---------------------------------------------------------------------------
# collect_node_evidence structure
# ---------------------------------------------------------------------------

def test_collect_node_evidence_shape(monkeypatch):
    _patch_live_deps(monkeypatch)

    result = investigation.collect_node_evidence(
        "opensre-demo-worker", "kind-opensre-demo", tail=40
    )

    assert result["success"] is True
    evidence = result["evidence"]

    assert evidence["target"]["type"] == "node"
    assert evidence["node"]["name"] == "opensre-demo-worker"

    k8s = evidence["kubernetes"]
    assert k8s["state"]["conditions"][0]["type"] == "Ready"
    assert "node_details" in k8s
    assert k8s["node_usage"]["pod_count"] == 2
    assert k8s["node_usage"]["restarts_total"] == 3
    # Deep-dive only touches degraded/restarted pods.
    assert len(k8s["pod_signals"]) >= 1
    assert k8s["pod_signals"][0]["state_reasons"]
    assert k8s["pod_signals"][0]["log_signals"]
    assert "events" in k8s
    assert len(k8s["events_structured"]) == 1
    assert k8s["log_analysis"]["signal_counts"]["connection_refused"] >= 1
    assert "timeline" in k8s

    metrics = evidence["metrics"]["node"]
    assert metrics["load1"] == 1.5
    assert metrics["cpu_utilization_percent"] == 12.5

    es = evidence["elasticsearch"]
    assert es["health"]["success"] is True
    assert es["error_counts"]["ERROR"] == 4
    assert es["errors_total"] == 5
    assert len(es["flagged_pods"]) == 2
    assert es["flagged_pods"][0]["pod"] == "catalog-api-abc"
    assert es["flagged_pods"][0]["error_signals"] == 2

    assert evidence["coredns"]["summary"] == "coredns ok"
    assert "git" in evidence
    assert "opensre-demo-worker" in evidence["question"]


def test_collect_node_evidence_handles_kubectl_failure(monkeypatch):
    _patch_live_deps(monkeypatch)
    monkeypatch.setattr(
        investigation.kubectl, "get_node_state",
        lambda *a, **k: {"success": False, "stderr": "connection refused"},
    )

    result = investigation.collect_node_evidence("opensre-demo-worker")
    assert result["success"] is True
    assert "state_error" in result["evidence"]["kubernetes"]


# ---------------------------------------------------------------------------
# KSM condition parsing (status is a label; value is the active bit)
# ---------------------------------------------------------------------------

def test_ksm_condition_parsing_uses_status_label(monkeypatch):
    series = [
        {"metric": {"condition": "DiskPressure", "status": "false"}, "value": "1"},
        {"metric": {"condition": "DiskPressure", "status": "true"}, "value": "0"},
        {"metric": {"condition": "MemoryPressure", "status": "true"}, "value": "1"},
        {"metric": {"condition": "Ready", "status": "true"}, "value": "1"},
    ]
    monkeypatch.setattr(investigation, "_vm_series", lambda q: series)
    monkeypatch.setattr(investigation, "_vm_scalar", lambda q: None)
    monkeypatch.setattr(investigation.victoriametrics, "query", lambda *a, **k: {})

    metrics = investigation._collect_node_metrics("opensre-demo-worker")

    conditions = metrics["kube_node_conditions"]
    assert conditions == [
        {"type": "DiskPressure", "status": "false"},
        {"type": "MemoryPressure", "status": "true"},
        {"type": "Ready", "status": "true"},
    ]
    # MemoryPressure is the only true, non-Ready condition.
    assert metrics["kube_node_pressure"] == ["MemoryPressure"]


def test_node_metrics_derived_utilization(monkeypatch):
    scalar_map = {
        "count(node_cpu_seconds_total":{"mode=": None},
    }
    monkeypatch.setattr(investigation, "_vm_series", lambda q: [])

    def fake_scalar(query):
        if "node_load1" in query:
            return 0.5
        if 'mode="idle"' in query:
            return 4.0
        if 'mode!="idle"' in query:
            return 1.0
        if "MemAvailable" in query:
            return 75.0
        if "MemTotal" in query:
            return 100.0
        return None

    monkeypatch.setattr(investigation, "_vm_scalar", fake_scalar)
    monkeypatch.setattr(investigation.victoriametrics, "query", lambda *a, **k: {})

    metrics = investigation._collect_node_metrics("opensre-demo-worker")

    assert metrics["load1"] == 0.5
    assert metrics["cpu_cores"] == 4.0
    assert metrics["cpu_utilization_percent"] == 25.0
    assert metrics["mem_utilization_percent"] == 25.0


# ---------------------------------------------------------------------------
# _es_tail truncation
# ---------------------------------------------------------------------------

def test_es_tail_respects_limit_and_chars(monkeypatch):
    hits = [
        {"_source": {"log": f"line-{i} " + "x" * 200}} for i in range(50)
    ]

    tail = investigation._es_tail(hits, limit=10, max_chars=100)
    assert len(tail.splitlines()) <= 10
    assert len(tail) <= 110  # 100 chars + partial line slack


def test_es_tail_skips_empty_hits():
    assert investigation._es_tail([]) == ""
    assert investigation._es_tail([{"_source": {}}]) == ""


# ---------------------------------------------------------------------------
# Pod evidence ES gating (healthy-but-quiet pod => clean)
# ---------------------------------------------------------------------------

def test_pod_evidence_es_clean_when_zero_logs(monkeypatch):
    _patch_live_deps(monkeypatch)
    # ES reachable but the pod has shipped zero logs in the window.
    monkeypatch.setattr(
        investigation.elasticsearch_service,
        "get_pod_logs",
        lambda *a, **k: {"success": True, "available": True, "total": 0, "hits": []},
    )

    result = investigation.collect_pod_evidence(
        "opensre", "nginx-xyz", "kind-opensre-demo", tail=20
    )
    es = result["evidence"]["elasticsearch"]
    assert es["clean"] is True
    assert "pod_logs_tail" not in es
    assert es["log_total"] == 0


def test_pod_evidence_es_unavailable_is_not_clean(monkeypatch):
    _patch_live_deps(monkeypatch)
    monkeypatch.setattr(
        investigation.elasticsearch_service,
        "get_pod_logs",
        lambda *a, **k: {"success": False, "error": "ES not reachable"},
    )

    result = investigation.collect_pod_evidence(
        "opensre", "catalog-api-abc", "kind-opensre-demo", tail=20
    )
    es = result["evidence"]["elasticsearch"]
    assert es.get("clean") is not True
    assert "error" in es


# ---------------------------------------------------------------------------
# kubectl.get_node_events_json parsing
# ---------------------------------------------------------------------------

def test_get_node_events_json_parses_items(monkeypatch):
    payload = {
        "kind": "EventList",
        "items": NODE_EVENTS_JSON["items"],
    }
    monkeypatch.setattr(
        kubectl,
        "run_command",
        lambda command, *a, **k: (
            {"success": True, "stdout": __import__("json").dumps(payload)}
        ),
    )

    result = kubectl.get_node_events_json("opensre-demo-worker")
    assert result["success"] is True
    assert result["items"][0]["reason"] == "NodeNotReady"


def test_get_node_events_json_handles_bad_json(monkeypatch):
    monkeypatch.setattr(
        kubectl,
        "run_command",
        lambda command, *a, **k: {"success": True, "stdout": "not-json"},
    )

    result = kubectl.get_node_events_json("opensre-demo-worker")
    assert result["success"] is False