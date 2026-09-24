from app.services import grounding


def _target_evidence(desired=0, event_reason="SuccessfulDelete"):
    return {
        "target": {"type": "aerospike", "name": "aerospike"},
        "service": {
            "display_name": "Aerospike",
            "endpoint": "127.0.0.1:3001",
            "health": {"success": False, "error": "(-10, 'Failed to connect', 'as_cluster.c')"},
        },
        "container": {"state": {"success": False, "error": "pod not found"}},
        "kubernetes": {
            "statefulset": "aerospike",
            "namespace": "databases",
            "statefulset_status": {
                "desired_replicas": desired,
                "ready_replicas": 0,
                "current_replicas": 0,
                "available_replicas": 0,
            },
            "statefulset_pods": [],
            "events": [
                {
                    "reason": "Killing",
                    "type": "Normal",
                    "message": "Stopping container aerospike",
                    "object": "Pod/aerospike-0",
                },
                {
                    "reason": event_reason,
                    "type": "Normal",
                    "message": "delete Pod aerospike-0 in StatefulSet aerospike successful",
                    "object": "StatefulSet/aerospike",
                },
            ],
        },
    }


def test_build_context_surfaces_scaled_to_zero_fact():
    ctx = grounding.build_context(_target_evidence(desired=0))
    assert ctx["healthy"] is False
    assert ctx["phase"] == "down"
    assert "desired_replicas=0" in ctx["facts"]
    assert "scaled to zero" in ctx["facts"]
    assert "SuccessfulDelete" in ctx["facts"]
    assert "delete Pod aerospike-0" in ctx["facts"]


def test_build_context_reports_scaled_up_workload_without_down_phase():
    ctx = grounding.build_context(_target_evidence(desired=1))
    assert ctx["phase"] is None
    assert "desired_replicas=1" in ctx["facts"]
    assert "scaled to zero" not in ctx["facts"]


def test_build_context_missing_kubernetes_is_safe():
    ev = _target_evidence()
    del ev["kubernetes"]
    ctx = grounding.build_context(ev)
    assert "desired_replicas=0" not in ctx["facts"]
    assert ctx["phase"] is None