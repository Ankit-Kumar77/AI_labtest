"""Nginx investigation service — read-only, production-grade.

Collects evidence from Nginx running in Kubernetes (or as a local container)
without mutating state. Covers:

- access logs & error logs (via kubectl logs)
- HTTP 4xx/5xx, especially 502/503/504
- upstream connection refused / timeout / DNS failures
- Nginx configuration errors & validation (nginx -t / nginx -T)
- health / config validation status
- upstream response latency where available ($upstream_response_time)

All operations are read-only and safe to run in production.
"""

import re
import json
from app.utils.command import run_command
from app.core.config import settings

NGINX_NAMESPACE = getattr(settings, "NGINX_NAMESPACE", "opensre")
NGINX_LABEL = getattr(settings, "NGINX_LABEL", "app=nginx")
NGINX_CONTAINER_NAME = getattr(settings, "NGINX_CONTAINER_NAME", "nginx")
NGINX_CONFIG_PATH = "/etc/nginx/nginx.conf"

# ---------------------------------------------------------------------------
# Regexes for log parsing (covers default and custom log formats)
# ---------------------------------------------------------------------------

# Access log example (with upstream timing):
# 10.0.0.1 - - [11/Sep/2026:12:00:00 +0000] "GET /api HTTP/1.1" 502 154 "-" "curl/7.88" rt=0.005 uct="0.003" uaddr="10.244.0.5:8000"
ACCESS_STATUS_RE = re.compile(r'"\s(?P<status>\d{3})\s')
UPSTREAM_TIME_RE = re.compile(r'(?:upstream_response_time|uct|upstream_time)[=:\s"]+(?P<val>[\d\.]+|-)')
# fallback: rt= or request_time
REQUEST_TIME_RE = re.compile(r'(?:request_time|rt)[=:\s"]+(?P<val>[\d\.]+)')

# Error log signatures
ERROR_PATTERNS = {
    "connection_refused": re.compile(r"connect\(\) failed.*Connection refused|connection refused|upstream.*refused", re.IGNORECASE),
    "upstream_timeout": re.compile(r"upstream timed out|timed out.*upstream|upstream.*timeout|504 Gateway Timeout", re.IGNORECASE),
    "dns_resolution_failure": re.compile(r"host not found in upstream|no resolver defined|name does not resolve|DNS.*fail|could not be resolved", re.IGNORECASE),
    "config_error": re.compile(r"\[emerg\]|\[crit\].*unknown directive|invalid.*parameter|directive.*is not allowed|nginx:.*failed|test failed", re.IGNORECASE),
    "ssl_error": re.compile(r"SSL.*error|SSL_do_handshake.*failed", re.IGNORECASE),
    "too_many_open_files": re.compile(r"too many open files|accept\(\) failed", re.IGNORECASE),
}

HTTP_4XX_RE = re.compile(r'"\s(?P<status>4\d{2})\s')
HTTP_5XX_RE = re.compile(r'"\s(?P<status>5\d{2})\s')

def _run(cmd: list[str]):
    return run_command(cmd)

def _kubectl_base(context: str | None = None):
    cmd = ["kubectl"]
    if context:
        cmd.extend(["--context", context])
    return cmd

def get_nginx_pods(namespace: str | None = None, context: str | None = None):
    """List nginx pods by label selector."""
    ns = namespace or NGINX_NAMESPACE
    cmd = _kubectl_base(context) + [
        "get", "pods", "-n", ns, "-l", NGINX_LABEL, "-o", "json"
    ]
    result = _run(cmd)
    if not result.get("success"):
        return result
    try:
        data = json.loads(result.get("stdout", "{}"))
        pods = []
        for item in data.get("items", []) or []:
            meta = item.get("metadata", {})
            status = item.get("status", {})
            cs_list = status.get("containerStatuses", []) or []
            pods.append({
                "name": meta.get("name"),
                "namespace": meta.get("namespace"),
                "phase": status.get("phase"),
                "pod_ip": status.get("podIP"),
                "node": item.get("spec", {}).get("nodeName"),
                "containers": [
                    {
                        "name": c.get("name"),
                        "ready": c.get("ready"),
                        "restart_count": c.get("restartCount"),
                        "state": c.get("state"),
                        "last_state": c.get("lastState"),
                    }
                    for c in cs_list
                ],
                "labels": meta.get("labels", {}),
            })
        return {"success": True, "pods": pods, "count": len(pods), "raw": data}
    except Exception as e:
        return {"success": False, "error": str(e), "raw": result.get("stdout")}

def get_nginx_pod_names(namespace: str | None = None, context: str | None = None):
    res = get_nginx_pods(namespace, context)
    if not res.get("success"):
        return res
    names = [p["name"] for p in res.get("pods", [])]
    return {"success": True, "pods": names, "count": len(names)}

def describe_nginx(namespace: str | None = None, pod_name: str | None = None, context: str | None = None):
    """kubectl describe for nginx pod or deployment."""
    ns = namespace or NGINX_NAMESPACE
    if pod_name:
        cmd = _kubectl_base(context) + ["describe", "pod", pod_name, "-n", ns]
        return _run(cmd)
    # fallback: describe deployment
    cmd = _kubectl_base(context) + ["describe", "deployment", "-l", NGINX_LABEL, "-n", ns]
    alt = _run(cmd)
    if alt.get("success") and alt.get("stdout"):
        return alt
    # try get events for namespace
    cmd2 = _kubectl_base(context) + ["get", "events", "-n", ns, "--sort-by=.lastTimestamp"]
    return _run(cmd2)

def get_nginx_events(namespace: str | None = None, context: str | None = None):
    ns = namespace or NGINX_NAMESPACE
    cmd = _kubectl_base(context) + [
        "get", "events", "-n", ns,
        "--field-selector", f"involvedObject.kind=Pod",
        "--sort-by=.lastTimestamp"
    ]
    # widen: get all events in ns and filter client-side if needed
    result = _run(cmd)
    if not result.get("success"):
        # fallback to all events
        cmd2 = _kubectl_base(context) + ["get", "events", "-n", ns]
        return _run(cmd2)
    return result

def get_nginx_logs(namespace: str | None = None, pod_name: str | None = None, tail: int = 200, previous: bool = False, context: str | None = None, container: str | None = None):
    """Fetch nginx container logs (access + error go to stdout/stderr via kubectl logs)."""
    ns = namespace or NGINX_NAMESPACE
    if not pod_name:
        # resolve first pod
        pods_res = get_nginx_pods(ns, context)
        if not pods_res.get("success") or not pods_res.get("pods"):
            return {"success": False, "error": "No nginx pods found", "details": pods_res}
        pod_name = pods_res["pods"][0]["name"]
    cmd = _kubectl_base(context)
    if previous:
        cmd.append("--previous")
    cmd.extend(["logs", pod_name, "-n", ns, "--tail", str(tail)])
    if container:
        cmd.extend(["-c", container])
    # try without container first
    result = _run(cmd)
    if not result.get("success") and "container" in result.get("stderr", "").lower():
        # retry with explicit container nginx
        cmd2 = _kubectl_base(context)
        if previous:
            cmd2.append("--previous")
        cmd2.extend(["logs", pod_name, "-n", ns, "--tail", str(tail), "-c", "nginx"])
        return _run(cmd2)
    return result

def nginx_config_test(namespace: str | None = None, pod_name: str | None = None, context: str | None = None):
    """Run `nginx -t` inside the pod (read-only validation)."""
    ns = namespace or NGINX_NAMESPACE
    if not pod_name:
        pods_res = get_nginx_pods(ns, context)
        if not pods_res.get("success") or not pods_res.get("pods"):
            return {"success": False, "error": "No nginx pods found for config test"}
        pod_name = pods_res["pods"][0]["name"]
    cmd = _kubectl_base(context) + ["exec", "-n", ns, pod_name, "--", "nginx", "-t"]
    result = _run(cmd)
    # nginx -t prints to stderr on success too ("syntax is ok, test is successful")
    combined = (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")
    is_ok = "syntax is ok" in combined.lower() and "test is successful" in combined.lower()
    # Also consider exit code 0 + those phrases as success
    return {
        "success": result.get("success") or is_ok,
        "valid": is_ok,
        "stdout": result.get("stdout"),
        "stderr": result.get("stderr"),
        "combined": combined.strip(),
        "returncode": result.get("returncode"),
    }

def nginx_config_dump(namespace: str | None = None, pod_name: str | None = None, context: str | None = None, max_chars: int = 6000):
    """Run `nginx -T` (dump effective config) — read-only."""
    ns = namespace or NGINX_NAMESPACE
    if not pod_name:
        pods_res = get_nginx_pods(ns, context)
        if not pods_res.get("success") or not pods_res.get("pods"):
            return {"success": False, "error": "No nginx pods found for config dump"}
        pod_name = pods_res["pods"][0]["name"]
    cmd = _kubectl_base(context) + ["exec", "-n", ns, pod_name, "--", "nginx", "-T"]
    result = _run(cmd)
    combined = (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")
    truncated = combined[:max_chars]
    # Detect upstream definitions
    upstreams = re.findall(r"upstream\s+(\S+)\s*\{", combined)
    servers = re.findall(r"server\s+([^;]+);", combined)
    return {
        "success": result.get("success"),
        "dump": truncated,
        "truncated": len(combined) > max_chars,
        "upstreams": upstreams[:20],
        "servers": servers[:20],
        "stderr": result.get("stderr", "")[:2000],
        "returncode": result.get("returncode"),
    }

def service_and_endpoints(namespace: str | None = None, context: str | None = None):
    ns = namespace or NGINX_NAMESPACE
    svc_cmd = _kubectl_base(context) + ["get", "svc", "-n", ns, "-l", NGINX_LABEL, "-o", "wide"]
    svc = _run(svc_cmd)
    ep_cmd = _kubectl_base(context) + ["get", "endpoints", "-n", ns, "-l", NGINX_LABEL, "-o", "wide"]
    ep = _run(ep_cmd)
    # fallback: get all svc in ns
    if not svc.get("success") or not svc.get("stdout", "").strip():
        svc2 = _run(_kubectl_base(context) + ["get", "svc", "-n", ns])
        if svc2.get("success"):
            svc = svc2
    return {"service": svc, "endpoints": ep}

def _analyze_access_logs(log_text: str):
    lines = [l for l in (log_text or "").splitlines() if l.strip()]
    total = len(lines)
    status_counts: dict[str,int] = {}
    count_4xx = 0
    count_5xx = 0
    count_502 = count_503 = count_504 = 0
    upstream_times: list[float] = []
    request_times: list[float] = []
    samples_5xx: list[str] = []
    samples_502: list[str] = []

    for line in lines:
        m = ACCESS_STATUS_RE.search(line)
        if m:
            status = m.group("status")
            status_counts[status] = status_counts.get(status, 0) + 1
            if status.startswith("4"):
                count_4xx += 1
            if status.startswith("5"):
                count_5xx += 1
                if len(samples_5xx) < 5:
                    samples_5xx.append(line[:300])
            if status == "502":
                count_502 += 1
                if len(samples_502) < 3:
                    samples_502.append(line[:300])
            elif status == "503":
                count_503 += 1
            elif status == "504":
                count_504 += 1
        # upstream latency
        um = UPSTREAM_TIME_RE.search(line)
        if um:
            val = um.group("val")
            if val != "-":
                try:
                    upstream_times.append(float(val))
                except ValueError:
                    pass
        rm = REQUEST_TIME_RE.search(line)
        if rm:
            try:
                request_times.append(float(rm.group("val")))
            except ValueError:
                pass

    latency_stats = {}
    if upstream_times:
        upstream_times.sort()
        latency_stats = {
            "count": len(upstream_times),
            "p50": round(upstream_times[len(upstream_times)//2], 4),
            "p95": round(upstream_times[int(len(upstream_times)*0.95)] if len(upstream_times) > 20 else upstream_times[-1], 4),
            "p99": round(upstream_times[int(len(upstream_times)*0.99)] if len(upstream_times) > 100 else upstream_times[-1], 4),
            "max": round(max(upstream_times), 4),
            "avg": round(sum(upstream_times)/len(upstream_times), 4),
        }
    req_latency = {}
    if request_times:
        request_times.sort()
        req_latency = {
            "count": len(request_times),
            "p50": round(request_times[len(request_times)//2], 4),
            "p99": round(request_times[int(len(request_times)*0.99)] if len(request_times) > 100 else request_times[-1], 4),
            "max": round(max(request_times), 4),
        }

    return {
        "total_lines": total,
        "status_counts": status_counts,
        "http_4xx": count_4xx,
        "http_5xx": count_5xx,
        "http_502": count_502,
        "http_503": count_503,
        "http_504": count_504,
        "error_rate_percent": round(count_5xx / total * 100, 2) if total else 0,
        "upstream_latency": latency_stats,
        "request_latency": req_latency,
        "samples_5xx": samples_5xx,
        "samples_502": samples_502,
    }

def _analyze_error_logs(log_text: str):
    lines = [l for l in (log_text or "").splitlines() if l.strip()]
    counts = {k: 0 for k in ERROR_PATTERNS}
    samples: dict[str, list[str]] = {k: [] for k in ERROR_PATTERNS}
    for line in lines:
        for key, pat in ERROR_PATTERNS.items():
            if pat.search(line):
                counts[key] += 1
                if len(samples[key]) < 3:
                    samples[key].append(line[:400])
    # interesting tokens that are not classified but indicate upstream trouble
    interesting = []
    for line in lines[-20:]:
        if any(tok in line.lower() for tok in ["upstream", "connect()", "timeout", "emerg", "crit", "error"]):
            if len(interesting) < 10:
                interesting.append(line[:400])
    return {
        "total_lines": len(lines),
        "counts": counts,
        "samples": samples,
        "interesting_tail": interesting,
        "has_config_error": counts["config_error"] > 0,
        "has_connection_refused": counts["connection_refused"] > 0,
        "has_timeout": counts["upstream_timeout"] > 0,
        "has_dns_failure": counts["dns_resolution_failure"] > 0,
    }

def analyze_logs(combined_logs: str):
    """Unified analysis — splits access vs error heuristically then analyzes."""
    # Nginx logs interleaved; we run both analyzers on whole text
    access = _analyze_access_logs(combined_logs)
    error = _analyze_error_logs(combined_logs)
    return {"access": access, "error": error}

def health(namespace: str | None = None, context: str | None = None):
    """Quick health: are nginx pods running and config valid?"""
    ns = namespace or NGINX_NAMESPACE
    pods_res = get_nginx_pods(ns, context)
    if not pods_res.get("success"):
        return {"success": False, "status": "unknown", "error": pods_res.get("error") or pods_res.get("stderr"), "details": pods_res}
    pods = pods_res.get("pods", [])
    if not pods:
        return {"success": True, "status": "not_deployed", "pods": [], "message": f"No pods with label {NGINX_LABEL} in namespace {ns}"}
    running = [p for p in pods if p.get("phase") == "Running"]
    ready_containers = sum(1 for p in pods for c in p.get("containers", []) if c.get("ready"))
    # config check best-effort
    cfg = nginx_config_test(ns, pods[0]["name"], context) if pods else None
    return {
        "success": True,
        "status": "healthy" if running and ready_containers else "degraded",
        "namespace": ns,
        "pods": pods,
        "running_pods": len(running),
        "total_pods": len(pods),
        "ready_containers": ready_containers,
        "config_validation": cfg,
    }

def investigate_nginx(namespace: str | None = None, context: str | None = None, tail: int = 250):
    """
    Comprehensive read-only nginx investigation — returns structured evidence.
    """
    ns = namespace or NGINX_NAMESPACE
    evidence: dict = {
        "nginx": {
            "namespace": ns,
            "label": NGINX_LABEL,
            "pods": [],
            "health": {},
            "config_validation": {},
            "config_dump": {},
            "logs": {},
            "log_analysis": {},
            "service": {},
            "events": {},
        }
    }

    # pods
    pods_res = get_nginx_pods(ns, context)
    evidence["nginx"]["pods_raw"] = pods_res
    pods = pods_res.get("pods", []) if pods_res.get("success") else []
    evidence["nginx"]["pods"] = pods

    # health
    h = health(ns, context)
    evidence["nginx"]["health"] = h

    # events
    ev = get_nginx_events(ns, context)
    evidence["nginx"]["events"] = {
        "success": ev.get("success", False),
        "stdout": (ev.get("stdout") or "")[:4000],
        "stderr": ev.get("stderr", "")[:1000],
    }

    # service / endpoints
    se = service_and_endpoints(ns, context)
    evidence["nginx"]["service"] = {
        "service_stdout": (se["service"].get("stdout") or "")[:3000],
        "service_success": se["service"].get("success"),
        "endpoints_stdout": (se["endpoints"].get("stdout") or "")[:3000],
        "endpoints_success": se["endpoints"].get("success"),
    }

    # logs + analysis (per-pod, plus aggregated)
    all_logs = ""
    per_pod_logs = []
    for pod in pods[:3]:  # limit to 3 pods
        pod_name = pod["name"]
        logs_res = get_nginx_logs(ns, pod_name, tail=tail, context=context)
        prev_res = get_nginx_logs(ns, pod_name, tail=80, previous=True, context=context)
        text = (logs_res.get("stdout") or "")[-8000:]
        prev_text = (prev_res.get("stdout") or "")[-4000:] if prev_res.get("success") else ""
        combined = text + ("\n" + prev_text if prev_text else "")
        analysis = analyze_logs(combined) if combined.strip() else {"access": {}, "error": {}}
        per_pod_logs.append({
            "pod": pod_name,
            "logs_success": logs_res.get("success"),
            "logs_tail": text[-4000:],
            "logs_previous_tail": prev_text[-3000:],
            "logs_error": logs_res.get("stderr", "")[:1000] if not logs_res.get("success") else None,
            "analysis": analysis,
        })
        all_logs += "\n" + combined

    evidence["nginx"]["logs"]["per_pod"] = per_pod_logs
    evidence["nginx"]["logs"]["aggregated_raw"] = all_logs[:10000]

    if all_logs.strip():
        evidence["nginx"]["log_analysis"] = analyze_logs(all_logs)
    else:
        # still provide empty analysis so callers have stable shape
        evidence["nginx"]["log_analysis"] = {
            "access": {"total_lines": 0, "http_5xx": 0, "http_502": 0, "http_503": 0, "http_504": 0},
            "error": {"total_lines": 0, "counts": {k: 0 for k in ERROR_PATTERNS}},
        }

    # config validation & dump (only if pods exist)
    if pods:
        # config test
        ct = nginx_config_test(ns, pods[0]["name"], context)
        evidence["nginx"]["config_validation"] = ct
        # config dump
        cd = nginx_config_dump(ns, pods[0]["name"], context)
        evidence["nginx"]["config_dump"] = cd
        # determine health/config status strings
        evidence["nginx"]["health_status"] = h.get("status")
        evidence["nginx"]["config_status"] = "valid" if ct.get("valid") else ("invalid" if ct.get("success") is False or ct.get("valid") is False else "unknown")
    else:
        evidence["nginx"]["config_validation"] = {"success": False, "valid": None, "error": "No nginx pods"}
        evidence["nginx"]["config_dump"] = {"success": False, "error": "No nginx pods"}
        evidence["nginx"]["health_status"] = "not_deployed"
        evidence["nginx"]["config_status"] = "unknown"

    # summary for digest
    try:
        agg = evidence["nginx"]["log_analysis"]
        access = agg.get("access", {})
        error = agg.get("error", {})
        counts = error.get("counts", {}) if isinstance(error, dict) else {}
        evidence["nginx"]["summary"] = {
            "total_log_lines": (agg.get("access") or {}).get("total_lines", 0) + (agg.get("error") or {}).get("total_lines", 0),
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
    except Exception:
        evidence["nginx"]["summary"] = {}

    return evidence

# ---------------------------------------------------------------------------
# Alert helpers — classify whether an alert is nginx-related
# ---------------------------------------------------------------------------

def is_nginx_alert(alert: dict) -> bool:
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    haystack = " ".join([
        str(labels.get("alertname", "")),
        str(labels.get("job", "")),
        str(labels.get("pod", "")),
        str(labels.get("app", "")),
        str(annotations.get("summary", "")),
        str(annotations.get("description", "")),
    ]).lower()
    tokens = ["nginx", "502", "503", "504", "upstream", "gateway", "bad gateway", "service unavailable", "gateway timeout"]
    return any(t in haystack for t in tokens)
