import json
import shutil

from app.utils.command import run_command


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
                    ready = bool(cs.get("ready", False))
                    last_terminated = (cs.get("lastState") or {}).get("terminated") or {}
                    last_reason = last_terminated.get("reason")
                    last_exit = last_terminated.get("exitCode")
                    # `lastState` describes a PREVIOUS run. Reporting it through
                    # the same flat keys as current state made a healthy pod
                    # look broken: aerospike-0 rendered
                    # running=true, status=Running, error="Error", exit_code=165
                    # because a prior attempt had died, which pushed the RCA
                    # model into inventing a current outage. Keep historical
                    # facts under `last_state` and only surface exit/error at
                    # the top level when the container is NOT currently running.
                    return {
                        "success": True,
                        "name": name,
                        "pod": pod_name,
                        "namespace": ns,
                        "running": ready,
                        "ready": ready,
                        "status": status.get("phase"),
                        "restart_count": cs.get("restartCount", 0),
                        "oom_killed": (
                            (cs.get("state") or {}).get("terminated") or {}
                        ).get("reason") == "OOMKilled",
                        "started_at": (cs.get("state") or {}).get("running", {}).get("startedAt"),
                        "exit_code": None if ready else last_exit,
                        "error": None if ready else last_reason,
                        "finished_at": (
                            (cs.get("state") or {}).get("terminated") or {}
                        ).get("finishedAt"),
                        "last_state": {
                            "reason": last_reason,
                            "exit_code": last_exit,
                            "finished_at": last_terminated.get("finishedAt"),
                            "oom_killed": last_reason == "OOMKilled",
                            "note": (
                                "historical: describes a PREVIOUS container run, "
                                "not current runtime state"
                            ),
                        },
                        "pod_phase": status.get("phase"),
                        "pod_ip": status.get("podIP"),
                    }
            except Exception as e:
                return {"success": False, "error": str(e)}
    
    return {"success": False, "error": f"Container/pod '{name}' not found", "tried": ["docker/podman", "kubernetes"]}


def container_logs(name, tail=150, previous=False):
    """Get container logs - works for both Docker containers and K8s pods.

    `previous=True` fetches the logs of the run BEFORE the current one. For a
    restarted container these are the only logs that explain the non-zero exit
    code surfaced in `last_state`; without them the RCA has an unexplained
    `exit_code`/`reason` and guesses a cause.
    """
    # Try Docker/Podman first
    runtime = _runtime()
    
    if runtime:
        cmd = [runtime, "logs", "--tail", str(tail)]
        if previous:
            cmd.append("--previous")
        result = run_command(cmd + [name])
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
        cmd = ["kubectl", "logs", pod_name, "-n", ns, "--tail", str(tail)]
        if previous:
            cmd.append("--previous")
        return run_command(cmd)
    
    return {"success": False, "error": f"Could not find container/pod '{name}'"}