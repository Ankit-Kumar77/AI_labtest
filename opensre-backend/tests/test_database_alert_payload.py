"""Regression tests for the database investigation alert payload.

A database RCA can only be grounded on the input payload (the OpenSRE CLI has
no live database or k8s tools in this deployment). The database path used to
pass a raw probe blob, which is not alert-shaped, so every run - including a
perfectly healthy one - collapsed into the parser's generic "Unable to
determine root cause".

These tests pin the contract the user-visible behaviour depends on:

  healthy database  -> resolved / "Database Healthy"
  injected failure  -> firing    / "Database Unhealthy"
  recovered         -> resolved  / "Database Healthy" again

The failure cases deliberately include a *Running* pod with a broken database,
which is the false-negative that container-readiness-based health would miss.
These tests never invoke the OpenSRE LLM.
"""

import pytest

from app.services import investigation

TARGET = ("aerospike", "aerospike-0")


def _evidence(*, probe_ok=True, running=True, phase="Running", phase_present=True,
              operation_errors=None, latency=None, integrity=None, restarts=1,
              last_run=None):
    """Build a database evidence blob matching the collector's real shape."""
    state = {
        "success": True,
        "pod": "aerospike-0",
        "namespace": "databases",
        "running": running,
        "status": phase,
        "restart_count": restarts,
        "oom_killed": False,
        "exit_code": None if running else 1,
        "error": None if running else "Error",
    }
    if last_run:
        state["last_state"] = {
            "reason": "Error",
            "exit_code": 165,
            "finished_at": "2026-09-24T12:13:24Z",
            "note": "historical: describes a PREVIOUS container run",
        }

    health = (
        {"success": True, "status": "connected"}
        if probe_ok
        else {"success": False, "error": "(-10, 'Failed to connect', 'as_cluster.c', 414, False)"}
    )

    return {
        "database": "aerospike",
        "target": {"type": "aerospike", "name": "aerospike"},
        "investigations": {
            "health": health,
            "cluster_health": {"success": True, "data": {"cluster_size": 1}},
            "namespaces": {"success": True, "data": {"test": {"stats": {}}}},
            "operation_errors": operation_errors or {
                "success": True,
                "data": {"nodes_checked": 1, "connection_errors": [],
                         "timeouts": [], "client_errors": [], "server_errors": []},
            },
            "latency": latency or {
                "success": True,
                "data": [{"node": "n1", "read_latency_gt_1ms": 0, "write_latency_gt_1ms": 0}],
            },
            "data_integrity": integrity or {
                "success": True,
                "data": {"namespace": "test", "set": "demo", "records_scanned": 0,
                         "missing_required_fields": [], "duplicate_logical_records": [],
                         "invalid_field_values": []},
            },
        },
        "kubernetes": (
            {"pod_state": {"phase": phase, "node": "opensre-demo-worker", "pod_ip": "10.244.1.17"}}
            if phase_present else {}
        ),
        "container": {
            "state": state if running or last_run else {"success": False, "error": "not found"},
            "logs": "link eth0 state up in 0",
        },
        "elasticsearch": {"health": {"success": True, "available": True}},
        "metrics": {"health": {"success": True, "status": "OK"}},
        "git": {"success": True, "commit": "c06e8e7"},
        "question": "Investigate this Aerospike database for any issues.",
    }


def _build(evidence):
    return investigation.database_alert_payload(evidence, *TARGET)


# --------------------------------------------------------------------------
# Healthy
# --------------------------------------------------------------------------

def test_healthy_database_is_reported_as_healthy():
    """The reported bug: a healthy database returned 'unable to determine'."""
    payload = _build(_evidence())

    assert payload["status"] == "resolved"
    assert payload["labels"]["alertname"] == "Database Healthy: aerospike"
    assert payload["labels"]["severity"] == "info"
    assert "healthy" in payload["annotations"]["summary"].lower()


def test_healthy_database_never_frames_a_failure():
    payload = _build(_evidence())
    blob = str(payload).lower()

    assert "not healthy" not in blob
    assert "unhealthy" not in blob
    assert "Database Unhealthy" not in blob


def test_historical_restart_does_not_make_a_healthy_database_unhealthy():
    """A prior run that exited 165 must not imply a current outage."""
    payload = _build(_evidence(last_run=True, restarts=1))
    description = payload["annotations"]["description"]

    assert payload["status"] == "resolved"
    # The restart is still reported, but explicitly labelled historical.
    assert "exitCode=165" in description
    assert "HISTORICAL" in description


# --------------------------------------------------------------------------
# Injected failures
# --------------------------------------------------------------------------

def test_database_down_is_firing():
    """Chaos scales the StatefulSet to 0: pod gone and probe refused."""
    evidence = _evidence(
        probe_ok=False, running=False, phase="Missing", phase_present=False
    )
    evidence["container"]["state"] = {"success": False, "error": "Container/pod 'aerospike' not found"}
    payload = _build(evidence)

    assert payload["status"] == "firing"
    assert payload["labels"]["alertname"] == "Database Unhealthy: aerospike"
    assert "Failed to connect" in payload["annotations"]["summary"]


def test_running_pod_with_failing_probe_is_still_unhealthy():
    """Regression: container readiness must not mask a broken database.

    The pod is Running/ready, yet every client probe is refused. Reporting
    that as healthy is the false negative this whole change exists to close.
    """
    payload = _build(_evidence(probe_ok=False, running=True, phase="Running"))

    assert payload["status"] == "firing"
    assert "NOT healthy" in payload["annotations"]["summary"]


def test_running_pod_with_latency_spike_is_unhealthy():
    """`aerospike-latency` degrades the database while the pod stays Running."""
    evidence = _evidence(running=True, latency={
        "success": True,
        "data": [{"node": "n1", "read_latency_gt_1ms": 41203,
                  "read_latency_gt_64ms": 3981, "write_latency_gt_4ms": 990}],
    })
    payload = _build(evidence)

    assert payload["status"] == "firing"


def test_running_pod_with_connection_errors_is_unhealthy():
    evidence = _evidence(running=True, operation_errors={
        "success": True,
        "data": {"nodes_checked": 1, "connection_errors": ["dial tcp 10.96.166.141:3000: connect: connection refused"],
                 "timeouts": [], "client_errors": [], "server_errors": []},
    })
    payload = _build(evidence)

    assert payload["status"] == "firing"


def test_running_pod_with_integrity_violation_is_unhealthy():
    """`insert-invalid-aerospike` / `insert-duplicates-aerospike`."""
    evidence = _evidence(running=True, integrity={
        "success": True,
        "data": {"namespace": "test", "set": "demo", "records_scanned": 3,
                 "missing_required_fields": ["OrderService-001"],
                 "duplicate_logical_records": [], "invalid_field_values": []},
    })
    payload = _build(evidence)

    assert payload["status"] == "firing"


# --------------------------------------------------------------------------
# The cycle the user asked for
# --------------------------------------------------------------------------

def test_healthy_inject_recover_cycle():
    """healthy -> failure -> healthy must flip the verdict and back."""
    healthy = _build(_evidence())
    assert healthy["status"] == "resolved"

    broken = _build(_evidence(probe_ok=False, running=False, phase="Missing",
                              phase_present=False))
    assert broken["status"] == "firing"

    recovered = _build(_evidence())
    assert recovered["status"] == "resolved"
    assert recovered["labels"]["alertname"] == healthy["labels"]["alertname"]


# --------------------------------------------------------------------------
# Evidence reachability
# --------------------------------------------------------------------------

@pytest.mark.parametrize("marker", [
    "database probe results",   # client-side health/latency/integrity verdicts
    "operation errors",
    "ES log archive",
    "metrics backend",
    "git history",
    "pod phase=Running",
])
def test_every_evidence_source_reaches_the_agent(marker):
    """The agent can only ground on the description, so each source must appear."""
    payload = _build(_evidence())
    assert marker in payload["annotations"]["description"]


def test_description_reports_error_counts_when_present():
    evidence = _evidence(operation_errors={
        "success": True,
        "data": {"nodes_checked": 1, "connection_errors": ["connection refused"],
                 "timeouts": [], "client_errors": [], "server_errors": []},
    })
    description = _build(evidence)["annotations"]["description"]

    assert "connection_errors" in description
    assert "nodes_checked=1" in description


def test_payload_stays_bounded():
    import json

    payload = _build(_evidence(last_run=True))
    assert len(payload["annotations"]["description"]) <= 2201
    assert len(json.dumps(payload)) < 12000


def test_probe_degradation_helper_directly():
    clean = {"operation_errors": {"success": True, "data": {"timeouts": []}},
             "latency": {"success": True, "data": [{"read_latency_gt_1ms": 0}]},
             "data_integrity": {"success": True, "data": {"missing_required_fields": []}}}
    assert investigation._probe_indicates_degradation(clean) is False

    dirty = {"latency": {"success": True, "data": [{"read_latency_gt_64ms": 12}]}}
    assert investigation._probe_indicates_degradation(dirty) is True


def test_every_integrity_probe_is_scanned():
    """The collector scans more than one set; all of them must count.

    The corruption scenarios write to `test/integrity` while the original
    probe only scanned `test/demo`, so injected corruption was invisible.
    """
    degraded = {
        "data_integrity": {"success": True, "data": {"missing_required_fields": []}},
        "data_integrity_injected": {
            "success": True,
            "data": {"invalid_field_values": [{"field": "count", "value": -10}]},
        },
    }
    assert investigation._probe_indicates_degradation(degraded) is True


def test_corrupt_values_reach_the_agent_with_readable_labels():
    """Regression: the probe label was shadowed by an inner loop variable, so
    lines rendered as 'count:' and '?' instead of the probe name."""
    evidence = _evidence(
        running=True,
        integrity={
            "success": True,
            "data": {"namespace": "test", "set": "integrity", "records_scanned": 10,
                     "missing_required_fields": [],
                     "duplicate_logical_records": [],
                     "invalid_field_values": [
                         {"key": "invalid-count-301", "field": "count", "value": -301},
                         {"key": "invalid-count-302", "field": "count", "value": -302},
                     ]},
        },
    )
    evidence["investigations"]["cluster_health"] = {
        "success": True,
        "data": {"cluster_size": 1, "nodes": [{"node_name": "n1", "status": "active"}]},
    }
    payload = _build(evidence)
    description = payload["annotations"]["description"]

    assert payload["status"] == "firing"
    # Probe names, not shadowed inner values.
    assert "  cluster health:" in description
    assert "  data integrity:" in description
    assert "\n  ?:" not in description
    assert "\n  count:" not in description
    # The offending values themselves, compactly rendered.
    assert "count=-301" in description
    assert "node_name=n1" in description


def test_digest_does_not_truncate_mid_value():
    """The digest is capped; a raw dict repr used to overflow it and leave the
    description ending mid-token, which the model reads as broken evidence."""
    evidence = _evidence(
        running=True,
        integrity={
            "success": True,
            "data": {"namespace": "test", "set": "integrity", "records_scanned": 200,
                     "invalid_field_values": [
                         {"key": f"invalid-count-{i}", "field": "count", "value": -i}
                         for i in range(1, 60)
                     ]},
        },
    )
    description = _build(evidence)["annotations"]["description"]

    assert len(description) <= 2201
    assert not description.rstrip().endswith(("{", "[", ","))
    assert "count=-1" in description
