import json

from app.utils.command import run_command


def get_clusters():
    return run_command(
        [
            "kubectl",
            "config",
            "get-contexts",
            "-o",
            "name",
        ]
    )


def get_nodes(context: str | None = None):
    command = [
        "kubectl",
    ]

    if context:
        command.extend(["--context", context])

    command.extend(
        [
            "get",
            "nodes",
            "-o",
            "wide",
        ]
    )

    return run_command(command)


def get_pods(context: str | None = None):
    command = [
        "kubectl",
    ]

    if context:
        command.extend(["--context", context])

    command.extend(
        [
            "get",
            "pods",
            "-A",
        ]
    )

    return run_command(command)


def get_services(context: str | None = None):
    command = [
        "kubectl",
    ]

    if context:
        command.extend(["--context", context])

    command.extend(
        [
            "get",
            "svc",
            "-A",
        ]
    )

    return run_command(command)


def get_deployments(context: str | None = None):
    command = [
        "kubectl",
    ]

    if context:
        command.extend(["--context", context])

    command.extend(
        [
            "get",
            "deployments",
            "-A",
        ]
    )

    return run_command(command)


def get_pod_details(
    namespace: str,
    pod_name: str,
    context: str | None = None,
):
    command = [
        "kubectl",
    ]

    if context:
        command.extend(["--context", context])

    command.extend(
        [
            "describe",
            "pod",
            pod_name,
            "-n",
            namespace,
        ]
    )

    return run_command(command)


def get_pod_status(
    namespace: str,
    pod_name: str,
    context: str | None = None,
):
    command = [
        "kubectl",
    ]

    if context:
        command.extend(["--context", context])

    command.extend(
        [
            "get",
            "pod",
            pod_name,
            "-n",
            namespace,
            "-o",
            "wide",
        ]
    )

    return run_command(command)


def get_pod_events(
    namespace: str,
    pod_name: str,
    context: str | None = None,
):
    command = [
        "kubectl",
    ]

    if context:
        command.extend(["--context", context])

    command.extend(
        [
            "get",
            "events",
            "-n",
            namespace,
            "--field-selector",
            f"involvedObject.name={pod_name}",
            "--sort-by=.metadata.creationTimestamp",
        ]
    )

    return run_command(command)


def get_pod_endpoint(
    namespace: str,
    pod_name: str,
    context: str | None = None,
):
    command = [
        "kubectl",
    ]

    if context:
        command.extend(["--context", context])

    command.extend(
        [
            "get",
            "pod",
            pod_name,
            "-n",
            namespace,
            "-o",
            "jsonpath={.status.podIP}:{.spec.containers[0].ports[0].containerPort}",
        ]
    )

    result = run_command(command)
    endpoint = (result.get("stdout") or "").strip()

    if result.get("success") and endpoint:
        ip, _, port = endpoint.partition(":")
        if not port:
            result["stdout"] = ip
        else:
            result["stdout"] = f"{ip}:{port}"

    return result


def get_first_pod_by_label(
    namespace: str,
    label_selector: str,
    context: str | None = None,
):
    command = [
        "kubectl",
    ]

    if context:
        command.extend(["--context", context])

    command.extend(
        [
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            label_selector,
            "-o",
            "jsonpath={.items[0].metadata.name}",
        ]
    )

    return run_command(command)


def get_pod_logs(
    namespace: str,
    pod_name: str,
    tail: int = 80,
    previous: bool = False,
    context: str | None = None,
):
    command = [
        "kubectl",
    ]

    if context:
        command.extend(["--context", context])

    if previous:
        command.append("--previous")

    command.extend(
        [
            "logs",
            pod_name,
            "-n",
            namespace,
            "--tail",
            str(tail),
        ]
    )

    return run_command(command)


def get_pod_logs_container(
    namespace: str,
    pod_name: str,
    container: str,
    tail: int = 200,
    previous: bool = False,
    context: str | None = None,
    timestamps: bool = True,
):
    """
    Read-only container logs for one container (`kubectl logs -c`).
    `timestamps=True` prepends server-side timestamps so evidence keeps
    pod/namespace/container/time attribution even for unformatted app logs.
    """
    command = [
        "kubectl",
    ]

    if context:
        command.extend(["--context", context])

    if previous:
        command.append("--previous")

    command.extend(
        [
            "logs",
            pod_name,
            "-n",
            namespace,
            "-c",
            container,
            "--tail",
            str(tail),
        ]
    )

    if timestamps:
        command.append("--timestamps")

    return run_command(command)


def get_pod_events_json(
    namespace: str,
    pod_name: str,
    context: str | None = None,
):
    """
    Read-only structured events for a pod (`kubectl get events -o json`),
    for timeline-ready reason/type/timestamp extraction.
    """
    command = [
        "kubectl",
    ]

    if context:
        command.extend(["--context", context])

    command.extend(
        [
            "get",
            "events",
            "-n",
            namespace,
            "--field-selector",
            f"involvedObject.name={pod_name}",
            "--sort-by=.metadata.creationTimestamp",
            "-o",
            "json",
        ]
    )

    result = run_command(command)

    if not result.get("success"):
        return result

    try:
        data = json.loads(result.get("stdout", "{}"))
    except (TypeError, ValueError):
        return {
            "success": False,
            "stderr": "unable to parse events json",
        }

    return {
        "success": True,
        "items": data.get("items", []) or [],
    }


def get_pod_state(
    namespace: str,
    pod_name: str,
    context: str | None = None,
):
    """
    Structured pod state: phase, node, IP, and per-container status with
    current + last-terminated reasons and exit codes (e.g. OOMKilled,
    CrashLoopBackOff, ImagePullBackOff).
    """
    command = [
        "kubectl",
    ]

    if context:
        command.extend(["--context", context])

    command.extend(
        [
            "get",
            "pod",
            pod_name,
            "-n",
            namespace,
            "-o",
            "json",
        ]
    )

    result = run_command(command)

    if not result.get("success"):
        return result

    try:
        pod = json.loads(result.get("stdout", "{}"))
    except (TypeError, ValueError):
        return {
            "success": False,
            "stderr": "unable to parse pod json",
        }

    def _state(state):
        if not state:
            return None

        if "waiting" in state:
            inner = state["waiting"]
            return {
                "kind": "waiting",
                "reason": inner.get("reason"),
                "message": (inner.get("message") or "")[:500],
                "exit_code": None,
                "started_at": None,
            }

        if "running" in state:
            inner = state["running"]
            return {
                "kind": "running",
                "reason": None,
                "message": None,
                "exit_code": None,
                "started_at": inner.get("startedAt"),
            }

        if "terminated" in state:
            inner = state["terminated"]
            return {
                "kind": "terminated",
                "reason": inner.get("reason"),
                "message": (inner.get("message") or "")[:500],
                "exit_code": inner.get("exitCode"),
                "started_at": inner.get("startedAt"),
            }

        return None

    containers = []

    for status in pod.get("status", {}).get("containerStatuses", []) or []:
        containers.append(
            {
                "name": status.get("name"),
                "ready": status.get("ready"),
                "restart_count": status.get("restartCount"),
                "image": status.get("image"),
                "state": _state(status.get("state")),
                "last_state": _state(status.get("lastState")),
            }
        )

    return {
        "success": True,
        "state": {
            "phase": pod.get("status", {}).get("phase"),
            "node": pod.get("spec", {}).get("nodeName"),
            "pod_ip": pod.get("status", {}).get("podIP"),
            "containers": containers,
        },
    }


def get_node_state(node_name: str, context: str | None = None):
    """Structured node state: conditions, allocatable resources, capacity, unschedulable."""
    command = ["kubectl"]
    if context:
        command.extend(["--context", context])
    command.extend(["get", "node", node_name, "-o", "json"])

    result = run_command(command)
    if not result.get("success"):
        return result

    try:
        node = json.loads(result.get("stdout", "{}"))
    except (TypeError, ValueError):
        return {"success": False, "stderr": "unable to parse node json"}

    conditions = []
    for cond in node.get("status", {}).get("conditions", []) or []:
        conditions.append({
            "type": cond.get("type"),
            "status": cond.get("status"),
            "reason": cond.get("reason"),
            "message": (cond.get("message") or "")[:300],
            "last_transition": cond.get("lastTransitionTime"),
        })

    return {
        "success": True,
        "node": {
            "name": node_name,
            "unschedulable": node.get("spec", {}).get("unschedulable", False),
            "capacity": node.get("status", {}).get("capacity", {}),
            "allocatable": node.get("status", {}).get("allocatable", {}),
            "conditions": conditions,
            "addresses": [
                {"type": a.get("type"), "address": a.get("address")}
                for a in node.get("status", {}).get("addresses", []) or []
            ],
        },
    }


def get_node_details(node_name: str, context: str | None = None):
    """Raw `kubectl describe node` output (conditions, taints, resource usage)."""
    command = ["kubectl"]
    if context:
        command.extend(["--context", context])
    command.extend(["describe", "node", node_name])
    return run_command(command)


def get_node_events(
    node_name: str,
    context: str | None = None,
):
    """Raw `kubectl get events` filtered to the Node object (text form)."""
    command = ["kubectl"]
    if context:
        command.extend(["--context", context])
    command.extend(
        [
            "get",
            "events",
            "--field-selector",
            f"involvedObject.kind=Node,involvedObject.name={node_name}",
            "--sort-by=.metadata.creationTimestamp",
        ]
    )
    return run_command(command)


def get_node_events_json(
    node_name: str,
    context: str | None = None,
):
    """
    Structured Node events (`kubectl get events -o json` filtered to the
    Node object) for timeline-ready reason/type/timestamp extraction.
    """
    command = ["kubectl"]
    if context:
        command.extend(["--context", context])
    command.extend(
        [
            "get",
            "events",
            "--field-selector",
            f"involvedObject.kind=Node,involvedObject.name={node_name}",
            "--sort-by=.metadata.creationTimestamp",
            "-o",
            "json",
        ]
    )

    result = run_command(command)

    if not result.get("success"):
        return result

    try:
        data = json.loads(result.get("stdout", "{}"))
    except (TypeError, ValueError):
        return {
            "success": False,
            "stderr": "unable to parse node events json",
        }

    return {
        "success": True,
        "items": data.get("items", []) or [],
    }


def cordon_node(node_name: str, context: str | None = None):
    command = ["kubectl"]
    if context:
        command.extend(["--context", context])
    command.extend(["cordon", node_name])
    return run_command(command)


def uncordon_node(node_name: str, context: str | None = None):
    command = ["kubectl"]
    if context:
        command.extend(["--context", context])
    command.extend(["uncordon", node_name])
    return run_command(command)


def drain_node(node_name: str, context: str | None = None, grace_period: int = 30):
    command = ["kubectl"]
    if context:
        command.extend(["--context", context])
    command.extend([
        "drain", node_name,
        "--ignore-daemonsets",
        "--delete-emptydir-data",
        "--grace-period", str(grace_period),
        "--force",
    ])
    return run_command(command)


def get_node_resource_usage(node_name: str, context: str | None = None):
    """Get pods on a node and summarize resource usage."""
    command = ["kubectl"]
    if context:
        command.extend(["--context", context])
    command.extend([
        "get", "pods", "-A", "--field-selector", f"spec.nodeName={node_name}",
        "-o", "json",
    ])

    result = run_command(command)
    if not result.get("success"):
        return result

    try:
        data = json.loads(result.get("stdout", "{}"))
    except (TypeError, ValueError):
        return {"success": False, "stderr": "unable to parse pods json"}

    pods = []
    restarts_total = 0
    for item in data.get("items", []) or []:
        restarts = sum(
            cs.get("restartCount", 0)
            for cs in item.get("status", {}).get("containerStatuses", []) or []
        )
        restarts_total += restarts
        phase = item.get("status", {}).get("phase", "Unknown")
        ns = item.get("metadata", {}).get("namespace", "")
        name = item.get("metadata", {}).get("name", "")
        pods.append({
            "namespace": ns,
            "name": name,
            "phase": phase,
            "restarts": restarts,
        })

    return {
        "success": True,
        "node": node_name,
        "pod_count": len(pods),
        "restarts_total": restarts_total,
        "pods": pods,
    }
