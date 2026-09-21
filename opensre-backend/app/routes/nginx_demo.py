"""Demo scenario for Nginx failures — controlled, reversible, read-only investigation.

Flow:
  1) fail      -> introduce invalid upstream or unavailable upstream
  2) investigate -> collect nginx + k8s/metrics/logs + git + OpenSRE RCA
  3) recover   -> restore valid config

Keeps Nginx investigation independent of database integrations.
"""

import json
import tempfile
import os
from pathlib import Path
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import investigation
from app.services import opensre_cli
from app.services import nginx as nginx_service
from app.utils.command import run_command

router = APIRouter(prefix="/api/demo/nginx", tags=["Demo - Nginx"])

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATE_FILE = PROJECT_ROOT / "chaos" / "experiments" / "nginx_demo_state.json"

# Templates for broken configs (applied via kubectl if cluster exists)
BROKEN_UPSTREAM = """
upstream catalog-api {
    server 127.0.0.1:59999 max_fails=1 fail_timeout=5s;
}
server {
    listen 80;
    location / {
        proxy_pass http://catalog-api;
        proxy_connect_timeout 2s;
        proxy_read_timeout 3s;
    }
}
"""

BROKEN_DNS = """
upstream catalog-api {
    server nonexistent-upstream-host.invalid:8000;
}
server {
    listen 80;
    location / {
        proxy_pass http://catalog-api;
        proxy_connect_timeout 2s;
    }
}
"""

class DemoRequest(BaseModel):
    mode: str = "unavailable"  # unavailable | invalid_config | dns | timeout
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

def _kubectl(context: str | None):
    base = ["kubectl"]
    if context:
        base.extend(["--context", context])
    return base

def _try_patch_configmap(mode: str, context: str | None):
    """
    Best-effort: patch the nginx ConfigMap with a broken upstream.
    Returns dict with success flag and details. Never raises.
    If cluster not available, returns a simulated result.
    """
    # Synthetic fallback content for when kubectl unavailable
    synthetic = {
        "unavailable": "upstream connection refused while connecting to upstream, connect() failed (111: Connection refused) while connecting to upstream, client: 10.0.0.1, server: _, request: \"GET /api/products HTTP/1.1\", upstream: \"http://127.0.0.1:59999/api/products\", host: \"localhost\"",
        "invalid_config": "[emerg] unknown directive \"proxypass\" in /etc/nginx/nginx.conf:22",
        "dns": "host not found in upstream \"nonexistent-upstream-host.invalid\" in /etc/nginx/nginx.conf:10",
        "timeout": "upstream timed out (110: Connection timed out) while connecting to upstream, client: 10.0.0.1, server: _, request: \"GET /api/products HTTP/1.1\", upstream: \"http://10.244.0.12:8000/api/products\", host: \"localhost\"",
    }
    msg = synthetic.get(mode, synthetic["unavailable"])

    # Check cluster availability
    check = run_command(_kubectl(context) + ["get", "ns", nginx_service.NGINX_NAMESPACE])
    if not check.get("success"):
        # No cluster — record synthetic failure for investigation to surface
        return {"success": True, "simulated": True, "message": f"Cluster unavailable — simulated nginx {mode} failure recorded", "synthetic_log": msg}

    # Save current config for recovery. The raw `get -o yaml` output carries
    # server-side metadata (resourceVersion/uid/managedFields) that makes a
    # later `kubectl apply` fail, so sanitize to a clean manifest first —
    # otherwise recover reports success while the broken config stays live.
    get_cm = run_command(_kubectl(context) + ["get", "cm", "nginx-config", "-n", nginx_service.NGINX_NAMESPACE, "-o", "yaml"])
    if get_cm.get("success"):
        backup_path = PROJECT_ROOT / "chaos" / "experiments" / "nginx_config_backup.yaml"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(_sanitize_configmap_manifest(get_cm.get("stdout", "")))

    # Build broken nginx.conf patch (minimal)
    broken_map = {
        "unavailable": BROKEN_UPSTREAM,
        "timeout": BROKEN_UPSTREAM,
        "dns": BROKEN_DNS,
        "invalid_config": "events {}\nhttp { server { proxypass http://invalid; } }",  # invalid directive
    }
    broken = broken_map.get(mode, BROKEN_UPSTREAM)

    # Try to create/patch ConfigMap
    # We create a patch file and apply
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: nginx-config
  namespace: {nginx_service.NGINX_NAMESPACE}
data:
  nginx.conf: |
{chr(10).join('    ' + line for line in broken.splitlines())}
""")
        fname = f.name
    try:
        result = run_command(_kubectl(context) + ["apply", "-f", fname])
        if result.get("success"):
            # rollout restart to force reload / crash
            run_command(_kubectl(context) + ["rollout", "restart", "deployment/nginx", "-n", nginx_service.NGINX_NAMESPACE])
            # For invalid_config mode we expect config test to fail
            run_command(_kubectl(context) + ["rollout", "status", "deployment/nginx", "-n", nginx_service.NGINX_NAMESPACE, "--timeout", "15s"])
        return {
            "success": result.get("success"),
            "stdout": result.get("stdout", "")[:2000],
            "stderr": result.get("stderr", "")[:2000],
            "synthetic_log": msg,
            "simulated": False,
        }
    finally:
        try:
            os.unlink(fname)
        except Exception:
            pass

def _sanitize_configmap_manifest(raw_yaml: str) -> str:
    """Strip server-side metadata so the manifest is re-appliable on restore."""
    try:
        import yaml as _yaml
        doc = _yaml.safe_load(raw_yaml) or {}
    except Exception:
        return raw_yaml
    if not isinstance(doc, dict) or doc.get("kind") != "ConfigMap":
        return raw_yaml
    meta = doc.get("metadata") or {}
    clean = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": meta.get("name", "nginx-config"),
            "namespace": meta.get("namespace", nginx_service.NGINX_NAMESPACE),
        },
        "data": doc.get("data") or {},
    }
    try:
        import yaml as _yaml
        return _yaml.safe_dump(clean, default_flow_style=False)
    except Exception:
        return raw_yaml


def _try_restore_config(context: str | None):
    backup_path = PROJECT_ROOT / "chaos" / "experiments" / "nginx_config_backup.yaml"
    if backup_path.exists():
        # Sanitize on read too — older backups were saved raw and are not
        # directly appliable (stale resourceVersion/uid).
        try:
            raw = backup_path.read_text()
            clean = _sanitize_configmap_manifest(raw)
            if clean != raw:
                backup_path.write_text(clean)
        except Exception:
            pass
        result = run_command(_kubectl(context) + ["apply", "-f", str(backup_path)])
        if result.get("success"):
            run_command(_kubectl(context) + ["rollout", "restart", "deployment/nginx", "-n", nginx_service.NGINX_NAMESPACE])
            rollout = run_command(_kubectl(context) + ["rollout", "status", "deployment/nginx", "-n", nginx_service.NGINX_NAMESPACE, "--timeout", "30s"])
            result["rollout_status"] = (rollout.get("stdout") or rollout.get("stderr") or "")[:1000]
            result["rollout_success"] = rollout.get("success", False)
            return result
        # Backup apply failed (e.g. legacy raw backup) — fall through to the
        # known-good manifest instead of reporting a hollow success.
        result["warning"] = "backup apply failed, falling back to infra manifest"
    else:
        result = {"success": False, "warning": "no backup found, falling back to infra manifest"}
    # Fallback: re-apply the good config from infra/k8s if exists
    good_cm = PROJECT_ROOT / "infra" / "k8s" / "nginx-configmap.yaml"
    if good_cm.exists():
        good_result = run_command(_kubectl(context) + ["apply", "-f", str(good_cm)])
        if good_result.get("success"):
            run_command(_kubectl(context) + ["rollout", "restart", "deployment/nginx", "-n", nginx_service.NGINX_NAMESPACE])
            rollout = run_command(_kubectl(context) + ["rollout", "status", "deployment/nginx", "-n", nginx_service.NGINX_NAMESPACE, "--timeout", "30s"])
            good_result["rollout_status"] = (rollout.get("stdout") or rollout.get("stderr") or "")[:1000]
            good_result["rollout_success"] = rollout.get("success", False)
        if isinstance(result, dict) and result.get("warning"):
            good_result["warning"] = result["warning"]
        return good_result
    # No backup and no good file — attempt to just delete the broken patch marker
    return {"success": True, "simulated": True, "message": "No backup found; synthetic state cleared"}

@router.post("/fail")
def nginx_fail(request: DemoRequest):
    mode = request.mode.lower()
    if mode not in ("unavailable", "invalid_config", "dns", "timeout"):
        mode = "unavailable"
    result = _try_patch_configmap(mode, request.context)
    state = {
        "mode": mode,
        "failed": True,
        "simulated": result.get("simulated", False),
        "synthetic_log": result.get("synthetic_log"),
        "kubectl_success": result.get("success"),
        "ts": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }
    _save_state(state)
    # also record to experiments events if helper exists
    try:
        from app.services import game_day  # reuse helper if available
    except Exception:
        pass
    return {
        "success": True,
        "scenario": "nginx-failure",
        "mode": mode,
        "description": {
            "unavailable": "Upstream unavailable — connection refused (127.0.0.1:59999)",
            "invalid_config": "Nginx configuration error — invalid directive / unknown upstream",
            "dns": "Upstream DNS resolution failure — host not found",
            "timeout": "Upstream timeout — proxied server did not respond in time",
        }.get(mode),
        "result": result,
        "next": "POST /api/demo/nginx/investigate to collect evidence + run OpenSRE",
    }

@router.post("/investigate")
def nginx_investigate(request: DemoRequest):
    """
    Collect Nginx evidence, correlate with Kubernetes/metrics/logs and GitHub,
    then run OpenSRE investigation. No hardcoded RCA — evidence drives the result.
    """
    mode = request.mode.lower() if request.mode else _load_state().get("mode", "unavailable")
    # Collect real evidence
    evidence_result = investigation.collect_nginx_evidence(context=request.context)
    if not evidence_result.get("success"):
        return evidence_result
    evidence = evidence_result["evidence"]

    # If we are in simulated mode, inject synthetic logs so the RCA has signal
    # even when no real cluster is present — this keeps demo deterministic.
    state = _load_state()
    if state.get("failed") and state.get("synthetic_log"):
        # inject into nginx log_analysis
        nginx_ev = evidence.get("nginx") or {}
        evidence["nginx"] = nginx_ev
        synthetic = state["synthetic_log"]
        # ensure paths exist
        if "log_analysis" not in nginx_ev:
            nginx_ev["log_analysis"] = {}
        # prepend a synthetic access log line for 502 + synthetic error
        synthetic_access = '10.244.0.1 - - [11/Sep/2026:12:00:00 +0000] "GET /api/products HTTP/1.1" 502 154 "-" "curl/7.88" rt=3.005 uct="3.002" uaddr="127.0.0.1:59999"'
        if mode == "dns":
            synthetic_access = '10.244.0.1 - - [11/Sep/2026:12:00:00 +0000] "GET /api/products HTTP/1.1" 502 154 "-" "curl" rt=0.001 uct="-" uaddr="-"'
        elif mode == "invalid_config":
            synthetic_access = '10.244.0.1 - - [11/Sep/2026:12:00:00 +0000] "GET /api/products HTTP/1.1" 500 0 "-" "curl"'
        nginx_ev["synthetic_failure"] = {"mode": mode, "synthetic_log": synthetic}
        # also push into per_pod logs if present
        logs = nginx_ev.get("logs") or {}
        per_pod = logs.get("per_pod") or []
        if per_pod:
            per_pod[0]["logs_tail"] = (per_pod[0].get("logs_tail") or "") + "\n" + synthetic + "\n" + synthetic_access
        else:
            # create a virtual entry
            analysis = nginx_service.analyze_logs(synthetic + "\n" + synthetic_access)
            nginx_ev["log_analysis"] = analysis

    # Recompute summary after synthetic injection (if any)
    try:
        la = (evidence.get("nginx") or {}).get("log_analysis") or {}
        if la:
            access = la.get("access") or {}
            error = la.get("error") or {}
            counts = error.get("counts", {}) if isinstance(error, dict) else {}
            evidence["nginx"]["summary"] = {
                "total_log_lines": access.get("total_lines", 0) + error.get("total_lines", 0),
                "http_4xx": access.get("http_4xx", 0),
                "http_5xx": access.get("http_5xx", 0),
                "http_502": access.get("http_502", 0),
                "http_503": access.get("http_503", 0),
                "http_504": access.get("http_504", 0),
                "connection_refused": counts.get("connection_refused", 0),
                "upstream_timeout": counts.get("upstream_timeout", 0),
                "dns_failures": counts.get("dns_resolution_failure", 0),
                "config_errors": counts.get("config_error", 0),
                "upstream_latency": access.get("upstream_latency", {}),
            }
            evidence["summary"] = evidence["nginx"]["summary"]
    except Exception:
        pass

    # Add question framing for OpenSRE
    summ = (evidence.get("nginx") or {}).get("summary", {}) or {}
    evidence["question"] = (
        f"Nginx failure demo mode={mode}. "
        f"Nginx evidence: 5xx={summ.get('http_5xx', '?')}, "
        f"502={summ.get('http_502', '?')}, "
        f"503={summ.get('http_503', '?')}, "
        f"504={summ.get('http_504', '?')}, "
        f"connection_refused={summ.get('connection_refused', '?')}, "
        f"upstream_timeout={summ.get('upstream_timeout', '?')}, "
        f"dns_failures={summ.get('dns_failures', '?')}, "
        f"config_errors={summ.get('config_errors', '?')}. "
        "Correlate Nginx logs (access/error), Kubernetes pod/events, "
        "VictoriaMetrics (request rate / 5xx / latency), and GitHub history if relevant. "
        "Provide root cause, confidence, evidence, timeline, affected component, and remediation. "
        "Do not assume any relationship to YugabyteDB or Aerospike."
    )
    evidence["demo_mode"] = mode
    evidence["demo_state"] = state

    opensre_result = opensre_cli.investigate(evidence)
    return {
        "success": opensre_result.get("returncode") == 0 if "returncode" in opensre_result else opensre_result.get("success", False),
        "scenario": "nginx-failure",
        "mode": mode,
        "evidence": evidence,
        "opensre": opensre_result,
    }

@router.post("/recover")
def nginx_recover(request: DemoRequest):
    result = _try_restore_config(request.context)
    # Only clear the demo marker when the restore actually worked (or was
    # purely synthetic with nothing to restore) — never report a hollow ok.
    restored = bool(result.get("success", False))
    if restored:
        _clear_state()
    # Verify health after recovery
    health = nginx_service.health(context=request.context)
    return {
        "success": restored,
        "error": None if restored else (result.get("stderr") or result.get("error") or "restore failed — demo state kept so the failure stays visible"),
        "scenario": "nginx-failure",
        "recovery": {
            "action": "nginx-recover",
            "kubectl_result": result,
        },
        "health": health,
        "next": "GET /api/nginx/health to verify green",
    }

@router.get("/status")
def nginx_status(context: str | None = Query(default=None)):
    state = _load_state()
    health = nginx_service.health(context=context)
    evidence = None
    try:
        ev_res = investigation.collect_nginx_evidence(context=context)
        evidence = ((ev_res.get("evidence") or {}).get("nginx") or {}).get("summary") if ev_res.get("success") else None
    except Exception:
        pass
    return {
        "success": True,
        "state": state if state else {"failed": False, "message": "No active nginx demo failure"},
        "health": health,
        "summary": evidence,
    }

@router.get("/modes")
def nginx_modes():
    return {
        "modes": [
            {"id": "unavailable", "name": "Upstream unavailable (connection refused)", "status": "502"},
            {"id": "timeout", "name": "Upstream timeout (504)", "status": "504"},
            {"id": "dns", "name": "DNS resolution failure (host not found)", "status": "502"},
            {"id": "invalid_config", "name": "Invalid nginx config (emerg)", "status": "500"},
        ]
    }
