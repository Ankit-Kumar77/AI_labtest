import json
import shutil

from app.utils.command import run_command
from app.core.config import settings


def _runtime():
    for name in ("podman", "docker"):
        if shutil.which(name):
            return name
    return None


def _inspect(name):
    runtime = _runtime()

    if not runtime:
        return {"success": False, "error": "No container runtime found (podman/docker)"}

    return run_command([runtime, "inspect", name, "--format", "{{json .State}}"])


def container_state(name):
    """Get container state - works for both Docker containers and K8s pods."""
    # Try Docker/Podman first (for local development)
    result = _inspect(name)
    
    if result.get("success"):
        try:
            state = json.loads(result.get("stdout", "").strip().splitlines()[-1])
        except Exception as e:
            return {"success": False, "error": str(e), "raw": result.get("stdout")}

        return {
            "success": True,
            "name": name,
            "running": state.get("Running"),
            "status": state.get("Status"),
            "exit_code": state.get("ExitCode"),
            "restart_count": state.get("RestartCount"),
            "oom_killed": state.get("OOMKilled"),
            "error": state.get("Error"),
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
            "raw": state,
        }
    
    # Fallback: try Kubernetes pod (for databases running in K8s)
    # StatefulSet pods are <statefulset>-0, and chaos API uses "yugabyte" alias
    k8s_pods = {
        "yugabytedb": "yugabytedb-0",
        "yugabyte": "yugabytedb-0",
        "aerospike": "aerospike-0",
    }
    if name in k8s_pods:
        ns = "databases"
        pod_name = k8s_pods[name]
        # For StatefulSets, the pod name is <statefulset>-0 for replica 0
        pod_result = run_command(["kubectl", "get", "pod", pod_name, "-n", ns, "-o", "json"])
        
        if pod_result.get("success"):
            try:
                pod = json.loads(pod_result.get("stdout", ""))
                status = pod.get("status", {})
                container_statuses = status.get("containerStatuses", [])
                
                if container_statuses:
                    cs = container_statuses[0]
                    return {
                        "success": True,
                        "name": name,
                        "running": cs.get("ready", False),
                        "status": status.get("phase"),
                        "exit_code": cs.get("lastState", {}).get("terminated", {}).get("exitCode"),
                        "restart_count": cs.get("restartCount", 0),
                        "oom_killed": cs.get("lastState", {}).get("terminated", {}).get("reason") == "OOMKilled",
                        "error": cs.get("lastState", {}).get("terminated", {}).get("reason"),
                        "started_at": cs.get("state", {}).get("running", {}).get("startedAt"),
                        "finished_at": cs.get("lastState", {}).get("terminated", {}).get("finishedAt"),
                        "pod_phase": status.get("phase"),
                        "pod_ip": status.get("podIP"),
                    }
            except Exception as e:
                return {"success": False, "error": str(e)}
    
    return {"success": False, "error": f"Container/pod '{name}' not found", "tried": ["docker/podman", "kubernetes"]}


def container_logs(name, tail=150):
    """Get container logs - works for both Docker containers and K8s pods."""
    # Try Docker/Podman first
    runtime = _runtime()
    
    if runtime:
        result = run_command([runtime, "logs", "--tail", str(tail), name])
        if result.get("success"):
            return result
    
    # Fallback: try Kubernetes pod
    k8s_pods = {
        "yugabytedb": "yugabytedb-0",
        "yugabyte": "yugabytedb-0",
        "aerospike": "aerospike-0",
    }
    if name in k8s_pods:
        ns = "databases"
        pod_name = k8s_pods[name]
        return run_command(["kubectl", "logs", pod_name, "-n", ns, "--tail", str(tail)])
    
    return {"success": False, "error": f"Could not find container/pod '{name}'"}