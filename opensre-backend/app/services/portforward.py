"""kubectl port-forward lifecycle for Kubernetes databases.

The backend reaches YugabyteDB/Aerospike (StatefulSets in `databases`) via
`kubectl port-forward` to localhost. That forward dies whenever the
StatefulSet scales to 0 (chaos down/fail), so after a recover the pod is
Running again but the backend still can't connect until the forward is
restarted. Previously the API only returned a hint telling the user to run
the command in a terminal — now recover paths call `ensure()` to restart
the forward automatically, so the dashboard flips back to connected
without manual steps.
"""

import socket
import subprocess
import time
from pathlib import Path

LOG_DIR = Path("/tmp/opensre-pf")
LOG_DIR.mkdir(parents=True, exist_ok=True)

FORWARDS = {
    # target -> namespace / service / pod / localhost port / service port
    "yugabyte": {"namespace": "databases", "svc": "yugabytedb", "pod": "yugabytedb-0", "local": 5433, "remote": 5433},
    "aerospike": {"namespace": "databases", "svc": "aerospike", "pod": "aerospike-0", "local": 3001, "remote": 3000},
}

# yugabyte is also addressed as yugabytedb in some paths
_ALIASES = {"yugabytedb": "yugabyte"}


def _resolve(target: str) -> str:
    target = (target or "").lower()
    return _ALIASES.get(target, target)


def command_string(target: str) -> str:
    """The manual equivalent, shown in hints when auto-heal fails."""
    cfg = FORWARDS[_resolve(target)]
    return (
        f"kubectl port-forward -n {cfg['namespace']} "
        f"svc/{cfg['svc']} {cfg['local']}:{cfg['remote']}"
    )


def _match_pattern(target: str) -> str:
    cfg = FORWARDS[_resolve(target)]
    return f"port-forward -n {cfg['namespace']} svc/{cfg['svc']}"


def is_alive(target: str, timeout: float = 2.0) -> bool:
    """True when something accepts TCP on the forward's localhost port."""
    try:
        cfg = FORWARDS[_resolve(target)]
    except KeyError:
        return False
    try:
        with socket.create_connection(("127.0.0.1", cfg["local"]), timeout=timeout):
            return True
    except OSError:
        return False


def _stop_stale(target: str) -> None:
    """Kill leftover forward processes for this svc (best-effort)."""
    try:
        subprocess.run(
            ["pkill", "-f", _match_pattern(target)],
            capture_output=True,
            timeout=10,
        )
        time.sleep(1)
    except Exception:
        pass


def _forward_pids(target: str) -> list:
    """PIDs of kubectl port-forward processes for this svc."""
    try:
        proc = subprocess.run(
            ["pgrep", "-f", _match_pattern(target)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return [int(p) for p in (proc.stdout or "").split() if p.strip().isdigit()]
    except Exception:
        return []


def _proc_start_epoch(pid: int) -> float | None:
    """When a process started (epoch seconds), via /proc stat field 22."""
    try:
        with open(f"/proc/{pid}/stat") as handle:
            parts = handle.read().rsplit(")", 1)[1].split()
        starttime_ticks = float(parts[19])  # field 22, zero-based after comm
        with open("/proc/stat") as handle:
            for line in handle:
                if line.startswith("btime"):
                    btime = float(line.split()[1])
                    break
            else:
                return None
        ticks_per_sec = 100  # os.sysconf may be unavailable; 100 is standard
        try:
            import os

            ticks_per_sec = os.sysconf("SC_CLK_TCK")
        except Exception:
            pass
        return btime + starttime_ticks / ticks_per_sec
    except Exception:
        return None


def _pod_start_epoch(target: str) -> float | None:
    """When the database pod (re)started, via .status.startTime."""
    import datetime

    try:
        cfg = FORWARDS[_resolve(target)]
        proc = subprocess.run(
            [
                "kubectl", "get", "pod", cfg["pod"],
                "-n", cfg["namespace"], "-o",
                "jsonpath={.status.startTime}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        stamp = (proc.stdout or "").strip()
        if not stamp:
            return None
        dt = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return None


def _forward_is_fresh(target: str) -> bool:
    """True when a matching forward process exists AND it started after
    (or around the same time as) the current pod.

    A forward that outlives a scale-to-0/recreate cycle keeps its LISTEN
    socket briefly while forwarding to a dead pod — an open port alone
    must not count as healthy.
    """
    pids = _forward_pids(target)
    if not pids:
        return False
    pod_epoch = _pod_start_epoch(target)
    if pod_epoch is None:
        # Pod gone (still scaled to 0) — any forward is useless.
        return False
    for pid in pids:
        proc_epoch = _proc_start_epoch(pid)
        if proc_epoch is not None and proc_epoch >= pod_epoch - 5:
            return True
    return False


def ensure(target: str, wait_s: int = 30) -> dict:
    """Restart the port-forward for `target` if it isn't listening.

    Returns {"success", "already_running"?, "detail", "command"}.
    Never raises — callers treat failure as a hint, not a recover failure.
    """
    try:
        key = _resolve(target)
        cfg = FORWARDS[key]
    except KeyError:
        return {"success": False, "detail": f"Unknown target '{target}'"}

    if is_alive(key) and _forward_is_fresh(key):
        return {
            "success": True,
            "already_running": True,
            "detail": f"Port-forward already listening on 127.0.0.1:{cfg['local']}",
            "command": command_string(key),
        }

    _stop_stale(key)
    # NOTE: no "already listening" shortcut here — if a foreign process
    # holds the port, the spawn below fails fast with an honest error
    # instead of a false success.

    log_path = LOG_DIR / f"pf-{cfg['svc']}.log"
    cmd = [
        "kubectl", "port-forward",
        "-n", cfg["namespace"], f"svc/{cfg['svc']}",
        f"{cfg['local']}:{cfg['remote']}",
    ]
    try:
        with open(log_path, "ab") as log:
            proc = subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except Exception as exc:
        return {
            "success": False,
            "detail": f"Failed to spawn port-forward: {exc}",
            "command": command_string(key),
        }

    deadline = time.time() + wait_s
    while time.time() < deadline:
        if proc.poll() is not None:
            return {
                "success": False,
                "detail": (
                    f"port-forward exited immediately (rc={proc.returncode}); "
                    f"see {log_path}. Is the service/pod up?"
                ),
                "command": command_string(key),
            }
        if is_alive(key):
            return {
                "success": True,
                "already_running": False,
                "detail": (
                    f"Port-forward restarted (pid {proc.pid}), "
                    f"listening on 127.0.0.1:{cfg['local']}"
                ),
                "command": command_string(key),
            }
        time.sleep(1)

    return {
        "success": False,
        "detail": (
            f"port-forward spawned (pid {proc.pid}) but "
            f"127.0.0.1:{cfg['local']} not listening after {wait_s}s; "
            f"see {log_path}"
        ),
        "command": command_string(key),
    }
