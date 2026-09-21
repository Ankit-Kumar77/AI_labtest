import re
import httpx
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings
from app.services import portforward
from app.utils.command import run_command

router = APIRouter(
    prefix="/api/chaos",
    tags=["Chaos"],
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNBOOK = PROJECT_ROOT / "chaos" / "runbook.sh"
SEED_SCRIPT = PROJECT_ROOT / "chaos" / "seed-data.sh"

INJECT_ACTIONS = {
    "aerospike-down": "aerospike-down",
    "yugabyte-down": "yugabyte-down",
    "pod-crash": "pod-crash",
    "pod-delete": "pod-delete",
    "pod-cpu": "pod-cpu",
    "pod-memory": "pod-memory",
    "pod-latency": "pod-latency",
    "flaky-latency": "flaky-latency",
    "system-pod-kill": "system-pod-kill",
    "coredns-kill": "coredns-kill",
    "coredns-down": "coredns-down",
    "coredns-latency": "coredns-latency",
    "elk-error": "elk-error",
    "elk-connection-refused": "elk-connection-refused",
    "elk-timeout": "elk-timeout",
    "node-cordon": "node-cordon",
    "node-drain": "node-drain",
    "node-network-latency": "node-network-latency",
    # Data integrity injection actions (call demo API)
    "insert-empty-yugabyte": "insert-empty-yugabyte",
    "insert-empty-aerospike": "insert-empty-aerospike",
    "insert-duplicates-yugabyte": "insert-duplicates-yugabyte",
    "insert-duplicates-aerospike": "insert-duplicates-aerospike",
    "insert-invalid-yugabyte": "insert-invalid-yugabyte",
    "insert-invalid-aerospike": "insert-invalid-aerospike",
    # Database failure injection (K8s-based)
    "yugabyte-unavailable": "yugabyte-unavailable",
    "yugabyte-latency": "yugabyte-latency",
    "yugabyte-connection-pressure": "yugabyte-connection-pressure",
    "aerospike-unavailable": "aerospike-unavailable",
    "aerospike-latency": "aerospike-latency",
}

RECOVER_ACTIONS = {
    "aerospike-up": "aerospike-up",
    "yugabyte-up": "yugabyte-up",
    "latency-off": "latency-off",
    "flaky-latency-off": "flaky-latency-off",
    "network-latency-off": "network-latency-off",
    "coredns-up": "coredns-up",
    "coredns-latency-off": "coredns-latency-off",
    "elk-recover": "elk-recover",
    "uncordon": "uncordon",
    "all": "all",
    # Database failure recovery (K8s-based)
    "yugabyte-unavailable-recover": "yugabyte-unavailable-recover",
    "yugabyte-latency-recover": "yugabyte-latency-recover",
    "yugabyte-connection-pressure-recover": "yugabyte-connection-pressure-recover",
    "aerospike-unavailable-recover": "aerospike-unavailable-recover",
    "aerospike-latency-recover": "aerospike-latency-recover",
}

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class ActionRequest(BaseModel):
    action: str


class GameDayRequest(BaseModel):
    action: str
    duration_s: int = 30


def _strip_ansi(text):
    return ANSI_RE.sub("", text or "")


def _command(command, timeout=180):
    result = run_command(command, timeout=timeout)
    return {
        "success": result.get("success", False),
        "stdout": _strip_ansi(result.get("stdout", "")),
        "stderr": _strip_ansi(result.get("stderr", "")),
        "returncode": result.get("returncode", -1),
    }


def _k8s_db_state(name):
    """Check Kubernetes StatefulSet pod state for databases (source of truth).

    Maps chaos names to actual StatefulSet pods in the `databases` namespace:
      yugabyte/yugabytedb -> yugabytedb-0, aerospike -> aerospike-0.
    Returns running/stopped/missing so the dashboard reflects K8s, not docker.
    """
    pod_map = {
        "yugabyte": "yugabytedb-0",
        "yugabytedb": "yugabytedb-0",
        "aerospike": "aerospike-0",
    }
    pod = pod_map.get(name)
    if not pod:
        return None
    result = _command(
        ["kubectl", "get", "pod", pod, "-n", "databases",
         "-o", "jsonpath={.status.phase}:{.status.containerStatuses[0].ready}"]
    )
    if not result.get("success"):
        return "stopped"
    out = (result.get("stdout") or "").strip()
    # e.g. "Running:true" -> running, anything else -> stopped
    if out == "Running:true":
        return "running"
    if "Running" in out:
        return "stopped"
    if not out:
        return "stopped"
    return "stopped"


def _container_state(name):
    # Databases now run as K8s StatefulSets — check K8s first.
    if name in ("yugabyte", "yugabytedb", "aerospike"):
        return _k8s_db_state(name)

    docker = _command(
        ["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Status}}"]
    )
    stdout = docker.get("stdout", "").strip()

    if not stdout and (not docker.get("success") or docker.get("returncode") != 0):
        podman = _command(
            ["podman", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Status}}"]
        )
        stdout = podman.get("stdout", "").strip()

    if stdout.startswith("Up"):
        return "running"
    if stdout:
        return "stopped"
    return "missing"


def _worker_node_state():
    result = _command(["kubectl", "get", "nodes", "--no-headers"])
    for line in result.get("stdout", "").splitlines():
        columns = line.split()
        if columns and columns[0] == "opensre-demo-worker" and len(columns) > 1:
            status = columns[1]
            if "SchedulingDisabled" in status:
                return "cordoned"
            if status == "Ready":
                return "ready"
            return status.lower()
    return "unknown"


def _opensre_pods():
    result = _command(["kubectl", "get", "pods", "-n", "opensre", "--no-headers"])
    pods = []
    for line in result.get("stdout", "").splitlines():
        columns = line.split()
        if len(columns) >= 4:
            pods.append(
                {
                    "name": columns[0],
                    "ready": columns[1],
                    "status": columns[2],
                    "restarts": columns[3],
                }
            )
    return pods


def _action_failed(action, result):
    return {
        "success": False,
        "action": action,
        "error": result.get("stderr") or result.get("stdout") or "Unknown failure",
    }


@router.get("/actions")
def actions():
    return {
        "success": True,
        "inject": list(INJECT_ACTIONS.keys()),
        "recover": list(RECOVER_ACTIONS.keys()),
        "ops": ["seed"],
    }


@router.get("/history")
def history(limit: int = 200):
    """Newest-first experiment timeline from chaos/experiments/events.jsonl."""
    from app.services import game_day

    return game_day.history(limit=min(limit, 500))


@router.get("/active")
def active_faults():
    """Currently-active faults tracked in chaos/experiments/active.json."""
    from app.services import game_day

    return game_day.active()


@router.get("/status")
def status():
    runbook = _command(["bash", str(RUNBOOK), "status"])

    return {
        "success": True,
        "containers": {
            "aerospike": _container_state("aerospike"),
            "yugabyte": _container_state("yugabyte"),
        },
        "node": {
            "name": "opensre-demo-worker",
            "state": _worker_node_state(),
        },
        "pods": _opensre_pods(),
        "runbook": runbook,
    }


@router.post("/inject")
def inject(request: ActionRequest):
    action = INJECT_ACTIONS.get(request.action)
    if not action:
        return {
            "success": False,
            "error": f"Unknown failure '{request.action}'. Available: {list(INJECT_ACTIONS.keys())}",
        }

    # Data integrity injection actions - call demo API endpoints
    if action in {
        "insert-empty-yugabyte", "insert-empty-aerospike",
        "insert-duplicates-yugabyte", "insert-duplicates-aerospike",
        "insert-invalid-yugabyte", "insert-invalid-aerospike"
    }:
        target = "yugabyte" if "yugabyte" in action else "aerospike"
        endpoint_map = {
            "insert-empty": "/api/demo/db-scenario/data-integrity/insert-empty",
            "insert-duplicates": "/api/demo/db-scenario/data-integrity/insert-duplicates",
            "insert-invalid": "/api/demo/db-scenario/data-integrity/insert-invalid",
        }
        # Determine which type of injection
        if "empty" in action:
            endpoint = endpoint_map["insert-empty"]
        elif "duplicates" in action:
            endpoint = endpoint_map["insert-duplicates"]
        else:
            endpoint = endpoint_map["insert-invalid"]

        base_url = f"http://127.0.0.1:8001"
        try:
            # Recover can block on StatefulSet rollout (up to ~4 min) —
            # keep the client timeout above the rollout wait.
            with httpx.Client(timeout=300.0) as client:
                resp = client.post(f"{base_url}{endpoint}", json={"target": target})
                if resp.status_code == 200:
                    return {"success": True, "action": action, "stdout": resp.text}
                else:
                    return {"success": False, "action": action, "error": resp.text}
        except Exception as e:
            return {"success": False, "action": action, "error": str(e)}

    # Database failure injection (K8s-based) - call demo API endpoints
    if action in {
        "yugabyte-unavailable", "yugabyte-latency", "yugabyte-connection-pressure",
        "aerospike-unavailable", "aerospike-latency"
    }:
        target = "yugabyte" if "yugabyte" in action else "aerospike"
        
        if "unavailable" in action:
            endpoint = f"/api/demo/db-scenario/unavailable/fail"
        elif "latency" in action:
            endpoint = f"/api/demo/db-scenario/latency/induce"
        elif "connection-pressure" in action:
            endpoint = f"/api/demo/db-scenario/connection-pressure/induce"
        else:
            endpoint = f"/api/demo/db-scenario/unavailable/fail"

        base_url = f"http://127.0.0.1:8001"
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(f"{base_url}{endpoint}", json={"target": target})
                if resp.status_code == 200:
                    return {"success": True, "action": action, "stdout": resp.text}
                else:
                    return {"success": False, "action": action, "error": resp.text}
        except Exception as e:
            return {"success": False, "action": action, "error": str(e)}

    result = _command(["bash", str(RUNBOOK), action])
    if not result.get("success"):
        return _action_failed(action, result)

    return {
        "success": True,
        "action": action,
        "stdout": result.get("stdout", ""),
    }


@router.post("/recover")
def recover(request: ActionRequest):
    action = RECOVER_ACTIONS.get(request.action)
    if not action:
        return {
            "success": False,
            "error": f"Unknown recovery '{request.action}'. Available: {list(RECOVER_ACTIONS.keys())}",
        }

    # Database failure recovery (K8s-based) - call demo API endpoints
    if action in {
        "yugabyte-unavailable-recover", "yugabyte-latency-recover", 
        "yugabyte-connection-pressure-recover",
        "aerospike-unavailable-recover", "aerospike-latency-recover"
    }:
        target = "yugabyte" if "yugabyte" in action else "aerospike"
        
        if "unavailable-recover" in action:
            endpoint = f"/api/demo/db-scenario/unavailable/recover"
        elif "latency-recover" in action:
            endpoint = f"/api/demo/db-scenario/latency/recover"
        elif "connection-pressure-recover" in action:
            endpoint = f"/api/demo/db-scenario/connection-pressure/recover"
        else:
            endpoint = f"/api/demo/db-scenario/unavailable/recover"

        base_url = f"http://127.0.0.1:8001"
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(f"{base_url}{endpoint}", json={"target": target})
                if resp.status_code == 200:
                    return {"success": True, "action": action, "stdout": resp.text}
                else:
                    return {"success": False, "action": action, "error": resp.text}
        except Exception as e:
            return {"success": False, "action": action, "error": str(e)}

    result = _command(["bash", str(RUNBOOK), "recover", action], timeout=200)
    if not result.get("success"):
        return _action_failed(action, result)

    pf = None
    pf_note = None
    if action in ("aerospike-up", "yugabyte-up"):
        # The port-forward dies on scale-to-0 — restart it so the DB page
        # flips back to connected without manual terminal steps.
        target = "aerospike" if action.startswith("aerospike") else "yugabyte"
        pf = portforward.ensure(target)
        pf_note = (
            f"Port-forward: {pf.get('detail')} "
            f"(`{pf.get('command')}`)"
        )

    stdout = result.get("stdout", "")
    if pf_note:
        stdout = (stdout + "\n\n" + pf_note).strip()

    return {
        "success": True,
        "action": action,
        "stdout": stdout,
        "port_forward": pf,
        "port_forward_hint": (
            "Database recoveries scale a StatefulSet back up — the kubectl "
            "port-forward for that DB dies when it scales to 0. If the DB "
            "page still shows Unreachable after a successful rollout, "
            "restart the forward (yugabyte: `kubectl port-forward -n "
            "databases svc/yugabytedb 5433:5433`, aerospike: `kubectl "
            "port-forward -n databases svc/aerospike 3001:3000`) and "
            "re-check health."
            if action in ("aerospike-up", "yugabyte-up") else None
        ),
    }


@router.post("/seed")
def seed():
    result = _command(["bash", str(SEED_SCRIPT)])
    if not result.get("success"):
        return _action_failed("seed", result)

    return {
        "success": True,
        "action": "seed",
        "stdout": result.get("stdout", ""),
    }


@router.post("/game-day")
def game_day(request: GameDayRequest):
    """Run a full baseline -> inject -> measure -> recover -> report cycle."""
    from app.services import game_day as game_day_service

    return game_day_service.run_game_day(request.action, request.duration_s)