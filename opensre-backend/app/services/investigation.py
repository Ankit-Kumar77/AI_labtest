import socket
from datetime import datetime, timezone

from app.core.config import settings
from app.services import aerospike
from app.services import containers
from app.services import coredns as coredns_service
from app.services import db_investigation
from app.services import elasticsearch as elasticsearch_service
from app.services import git_correlation
from app.services import grafana
from app.services import k8s_log_analysis
from app.services import kubectl
from app.services import nginx as nginx_service
from app.services import victoriametrics
from app.services import yugabyte


SERVICE_TARGETS = ("aerospike", "yugabyte")
STACK_TARGET = "stack"

VM_QUERIES = {
    "request_rate_rps": "sum(rate(http_requests_total[1m]))",
    "error_rate_5xx_per_s": "sum(rate(http_requests_total{status=\"5xx\"}[1m]))",
    "error_share_percent": (
        "sum(rate(http_requests_total{status=\"5xx\"}[1m])) "
        "/ clamp_min(sum(rate(http_requests_total[1m])), 1e-3) * 100"
    ),
    "p50_latency_seconds": (
        "histogram_quantile(0.50, "
        "sum(rate(http_request_duration_highr_seconds_bucket[1m])) by (le))"
    ),
    "p95_latency_seconds": (
        "histogram_quantile(0.95, "
        "sum(rate(http_request_duration_highr_seconds_bucket[1m])) by (le))"
    ),
    "p99_latency_seconds": (
        "histogram_quantile(0.99, "
        "sum(rate(http_request_duration_highr_seconds_bucket[1m])) by (le))"
    ),
}

OTEL_COLLECTOR_LABEL = "app.kubernetes.io/name=opentelemetry-collector"
OTEL_NAMESPACE = "observability"
OTEL_ENDPOINTS = {
    "otlp_grpc": "127.0.0.1:4317",
    "otlp_http": "127.0.0.1:4318",
    "in_cluster_service": (
        "otel-collector-opentelemetry-collector."
        "observability.svc.cluster.local:4317"
    ),
}
GRAFANA_DASHBOARD_UID = "opensre-overview"


def _vm_scalar(query: str):
    try:
        result = victoriametrics.query(query)

        if not result.get("success"):
            return None

        series = result.get("data", {}).get("data", {}).get("result", [])
        if series and series[0].get("value"):
            return round(float(series[0]["value"][1]), 4)
    except (KeyError, IndexError, TypeError, ValueError):
        return None

    return None


def _vm_series(query: str):
    """Return raw {metric, value} series for a VM instant query (bounded)."""
    try:
        result = victoriametrics.query(query)
        if not result.get("success"):
            return []
        series = result.get("data", {}).get("data", {}).get("result", [])
        return [
            {
                "metric": s.get("metric", {}),
                "value": s.get("value", [None, None])[1],
            }
            for s in (series or [])[:50]
        ]
    except (KeyError, IndexError, TypeError, ValueError):
        return []


def _collect_node_metrics(node_name: str):
    """
    Node-level metrics from node-exporter (job=node) and kube-state-metrics:
    load, CPU, memory, root-filesystem pressure, network + disk rates and the
    KSM-reported node conditions. Returns None-free dict (keys omitted when
    the metric is absent so OpenSRE never sees fabricated zeros).
    """
    label = f'node="{node_name}"'
    metrics: dict = {}

    for key, query in (
        ("load1", f"node_load1{{{label}}}"),
        ("load5", f"node_load5{{{label}}}"),
        ("load15", f"node_load15{{{label}}}"),
        ("cpu_cores", f"count(node_cpu_seconds_total{{mode=\"idle\",{label}}})"),
        (
            "cpu_used_cores",
            f"sum(rate(node_cpu_seconds_total{{mode!=\"idle\",{label}}}[5m]))",
        ),
        ("mem_available_bytes", f"node_memory_MemAvailable_bytes{{{label}}}"),
        ("mem_total_bytes", f"node_memory_MemTotal_bytes{{{label}}}"),
        ("mem_buffers_bytes", f"node_memory_Buffers_bytes{{{label}}}"),
        ("mem_cached_bytes", f"node_memory_Cached_bytes{{{label}}}"),
        (
            "net_rx_bytes_per_s",
            f"sum(rate(node_network_receive_bytes_total{{device!=\"lo\",{label}}}[5m]))",
        ),
        (
            "net_tx_bytes_per_s",
            f"sum(rate(node_network_transmit_bytes_total{{device!=\"lo\",{label}}}[5m]))",
        ),
        (
            "disk_read_bytes_per_s",
            f"sum(rate(node_disk_read_bytes_total{{{label}}}[5m]))",
        ),
        (
            "disk_written_bytes_per_s",
            f"sum(rate(node_disk_written_bytes_total{{{label}}}[5m]))",
        ),
        ("major_page_faults_rps", f"node_vmstat_pgmajfault{{{label}}}"),
        ("open_fds", f"max(node_filefd_allocated{{{label}}})"),
        (
            "proc_running",
            f"sum(node_procs_running{{{label}}})",
        ),
    ):
        value = _vm_scalar(query)
        if value is not None:
            metrics[key] = value

    if metrics.get("cpu_cores") and metrics.get("cpu_used_cores") is not None:
        metrics["cpu_utilization_percent"] = round(
            metrics["cpu_used_cores"] / max(metrics["cpu_cores"], 1e-6) * 100,
            2,
        )

    mem_avail = metrics.get("mem_available_bytes")
    mem_total = metrics.get("mem_total_bytes")
    if mem_avail is not None and mem_total:
        metrics["mem_utilization_percent"] = round(
            (mem_total - mem_avail) / max(mem_total, 1e-6) * 100, 2
        )

    # Root-filesystem pressure: pick the largest real (non-tmpfs/ramfs)
    # mountpoint and report its used percent. Robust across kind/minikube
    # where "/" may be an overlay or a sub-volume.
    fs = _vm_series(f"node_filesystem_size_bytes{{{label}}}")
    usable = [
        s
        for s in fs
        if (s["metric"].get("fstype") or "") not in ("tmpfs", "ramfs", "overlay")
        and s["metric"].get("mountpoint")
    ]
    if usable:
        biggest = max(usable, key=lambda s: float(s["value"] or 0))
        mountpoint = biggest["metric"]["mountpoint"]
        avail = _vm_scalar(
            f"node_filesystem_avail_bytes{{mountpoint=\"{mountpoint}\",{label}}}"
        )
        if avail is not None:
            total = float(biggest["value"] or 0)
            metrics["root_fs_mountpoint"] = mountpoint
            metrics["root_fs_size_bytes"] = round(total, 4)
            metrics["root_fs_avail_bytes"] = avail
            metrics["root_fs_utilization_percent"] = round(
                (total - avail) / max(total, 1e-6) * 100, 2
            )

    # KSM-reported node conditions: the `status` LABEL holds the current
    # condition value and the metric value (1/0) marks whether that state is
    # active. We keep only the currently-active state per condition type.
    conditions = _vm_series(f"kube_node_status_condition{{{label}}}")
    condition_summary = []
    for item in conditions:
        if (item.get("value") or "0").strip() != "1":
            continue
        condition_summary.append(
            {
                "type": item["metric"].get("condition"),
                "status": item["metric"].get("status"),
            }
        )
    if condition_summary:
        metrics["kube_node_conditions"] = condition_summary
        metrics["kube_node_pressure"] = [
            c["type"] for c in condition_summary
            if c["status"] == "true" and c["type"] != "Ready"
        ]

    allocatable = _vm_series(f"kube_node_status_allocatable{{{label}}}")
    capacity = _vm_series(f"kube_node_status_capacity{{{label}}}")
    metrics["allocatable"] = {
        item["metric"].get("resource"): item.get("value")
        for item in allocatable
        if item.get("value") is not None
    }
    metrics["capacity"] = {
        item["metric"].get("resource"): item.get("value")
        for item in capacity
        if item.get("value") is not None
    }

    info = _vm_series(f"kube_node_info{{{label}}}")
    if info:
        node_info = {
            k: v for k, v in info[0]["metric"].items()
            if k in (
                "os_image", "kernel_version", "kubelet_version",
                "container_runtime_version", "internal_ip", "system_uuid",
            )
        }
        metrics["node_info"] = node_info

    return metrics


def _collect_node_pod_signals(
    node_name: str,
    context: str | None = None,
    max_pods: int = 6,
    tail: int = 60,
):
    """
    Walk the pods scheduled on a node and extract per-pod failure signals
    (state reasons + relevant container log lines). Only degraded or
    restarted pods are deep-dived to keep the investigation bounded.
    """
    usage_result = kubectl.get_node_resource_usage(node_name, context)
    if not usage_result.get("success"):
        return {"error": usage_result.get("stderr", "unable to collect node pods")}

    pods = usage_result.get("pods", []) or []
    degraded = [
        p for p in pods
        if p.get("phase") not in ("Running", "Succeeded", "Completed")
        or (p.get("restarts") or 0) > 0
    ] or pods
    degraded = degraded[:max_pods]

    signals = []
    for pod in degraded[:max_pods]:
        state_result = kubectl.get_pod_state(pod["namespace"], pod["name"], context)
        container_states = []
        reasons = []
        if state_result.get("success"):
            for container in (
                (state_result.get("state") or {}).get("containers") or []
            ):
                current = container.get("state") or {}
                last = container.get("last_state") or {}
                reason = current.get("reason") or last.get("reason")
                container_states.append(
                    {
                        "container": container.get("name"),
                        "ready": container.get("ready"),
                        "restarts": container.get("restart_count"),
                        "reason": reason,
                        "exit_code": last.get("exit_code"),
                        "started_at": current.get("started_at"),
                    }
                )
                if reason:
                    reasons.append(
                        f"{container.get('name')}:{reason}"
                        + (f"(exit {last.get('exit_code')})" if last.get("exit_code") is not None else "")
                    )

        # Container log signals (current + previous when restarted).
        log_entries = []
        fetch_tail = max(5, min(tail, 100))
        for container in container_states[:2]:
            current = kubectl.get_pod_logs_container(
                pod["namespace"], pod["name"], container["container"],
                tail=fetch_tail, context=context,
            )
            if current.get("success"):
                log_entries.extend(
                    k8s_log_analysis.extract_relevant_lines(
                        current.get("stdout", ""),
                        pod["name"], pod["namespace"],
                        container["container"], False, max_lines=4,
                    )
                )
            if (container.get("restarts") or 0) > 0:
                previous = kubectl.get_pod_logs_container(
                    pod["namespace"], pod["name"], container["container"],
                    tail=min(fetch_tail, 40), previous=True, context=context,
                )
                if previous.get("success"):
                    log_entries.extend(
                        k8s_log_analysis.extract_relevant_lines(
                            previous.get("stdout", ""),
                            pod["name"], pod["namespace"],
                            container["container"], True, max_lines=4,
                        )
                    )

        signals.append(
            {
                "namespace": pod["namespace"],
                "pod": pod["name"],
                "phase": pod["phase"],
                "restarts": pod["restarts"],
                "state_reasons": reasons,
                "container_states": container_states[:4],
                "log_signals": log_entries[: k8s_log_analysis.MAX_RELEVANT_LINES],
            }
        )

    return {
        "node": node_name,
        "pod_count": len(pods),
        "restarts_total": usage_result.get("restarts_total", 0),
        "pods": pods[:40],
        "deep_dive": signals,
    }


def collect_pod_evidence(
    namespace: str,
    pod_name: str,
    context: str | None = None,
    tail: int = 200,
):
    evidence = {
        "pod": {
            "namespace": namespace,
            "name": pod_name,
        },
        "cluster": context,
        "kubernetes": {},
        "metrics": {},
    }

    # Structured pod state (phase, node, container statuses, OOM/BackOff reasons)
    state_result = kubectl.get_pod_state(namespace, pod_name, context)

    if state_result.get("success"):
        evidence["kubernetes"]["state"] = state_result.get("state")
    else:
        evidence["kubernetes"]["state_error"] = (
            state_result.get("stderr")
            or state_result.get("error")
            or "Unable to collect pod state"
        )

    # Kubernetes pod details
    pod_result = kubectl.get_pod_details(
        namespace,
        pod_name,
        context,
    )

    if pod_result.get("success"):
        evidence["kubernetes"]["pod_details"] = pod_result.get(
            "stdout",
            "",
        )
    else:
        evidence["kubernetes"]["pod_details_error"] = pod_result.get(
            "stderr",
            "Unable to collect pod details",
        )

    # Pod status / restarts
    status_result = kubectl.get_pod_status(
        namespace,
        pod_name,
        context,
    )

    if status_result.get("success"):
        evidence["kubernetes"]["pod_status"] = status_result.get(
            "stdout",
            "",
        )
    else:
        evidence["kubernetes"]["pod_status_error"] = status_result.get(
            "stderr",
            "Unable to collect pod status",
        )

    # Pod events (raw text, kept for compatibility)
    events_result = kubectl.get_pod_events(
        namespace,
        pod_name,
        context,
    )

    if events_result.get("success"):
        evidence["kubernetes"]["events"] = events_result.get(
            "stdout",
            "",
        )
    else:
        evidence["kubernetes"]["events_error"] = events_result.get(
            "stderr",
            "Unable to collect pod events",
        )

    # Structured events (reason/type/timestamps) for the incident timeline.
    events_json = kubectl.get_pod_events_json(
        namespace,
        pod_name,
        context,
    )

    if events_json.get("success"):
        evidence["kubernetes"]["events_structured"] = (
            k8s_log_analysis.parse_events(events_json.get("items", []))
        )
    else:
        evidence["kubernetes"]["events_structured_error"] = (
            events_json.get("stderr") or "Unable to collect structured events"
        )
        evidence["kubernetes"]["events_structured"] = []

    # Container logs (root-cause detail: exceptions, OOM, probe failures).
    # Per-container, current + previous (when restarted), with relevant-line
    # extraction so OpenSRE gets signal instead of raw dumps.
    state = evidence["kubernetes"].get("state") or {}
    state_containers = state.get("containers") or []
    container_names = [c.get("name") for c in state_containers if c.get("name")][:4]

    fetch_tail = max(10, min(tail, 500))
    per_container = []
    relevant_lines = []

    def _collect_container_logs(container_name):
        current = kubectl.get_pod_logs_container(
            namespace,
            pod_name,
            container_name,
            tail=fetch_tail,
            context=context,
        )
        current_text = ""
        if current.get("success"):
            current_text = current.get("stdout", "") or ""
        restart_count = next(
            (
                c.get("restart_count") or 0
                for c in state_containers
                if c.get("name") == container_name
            ),
            0,
        )
        previous_text = ""
        previous_available = False
        if restart_count > 0:
            previous = kubectl.get_pod_logs_container(
                namespace,
                pod_name,
                container_name,
                tail=min(fetch_tail, 100),
                previous=True,
                context=context,
            )
            if previous.get("success"):
                previous_text = previous.get("stdout", "") or ""
                previous_available = True

        entry = {
            "container": container_name,
            "restart_count": restart_count,
            "current_success": current.get("success", False),
            "current_tail": current_text[-4000:],
            "previous_available": previous_available,
            "previous_tail": previous_text[-3500:] if previous_text else None,
        }
        if not current.get("success"):
            entry["logs_error"] = (current.get("stderr") or "")[:500]

        # Relevant-line extraction (verbatim lines, capped).
        for text, is_previous in (
            (current_text, False),
            (previous_text, True),
        ):
            if text.strip():
                relevant_lines.extend(
                    k8s_log_analysis.extract_relevant_lines(
                        text,
                        pod_name,
                        namespace,
                        container_name,
                        is_previous,
                        max_lines=8,
                    )
                )
        return entry

    if container_names:
        for name in container_names:
            per_container.append(_collect_container_logs(name))
    else:
        # Fallback: pod state unavailable — legacy single-call behavior so
        # the evidence shape never comes back empty.
        logs_result = kubectl.get_pod_logs(
            namespace,
            pod_name,
            tail=min(fetch_tail, 60),
            context=context,
        )

        if logs_result.get("success"):
            evidence["kubernetes"]["logs_tail"] = logs_result.get(
                "stdout",
                "",
            )[-4000:]
            relevant_lines.extend(
                k8s_log_analysis.extract_relevant_lines(
                    logs_result.get("stdout", ""),
                    pod_name,
                    namespace,
                    None,
                    False,
                )
            )
        else:
            evidence["kubernetes"]["logs_error"] = logs_result.get(
                "stderr",
                "Unable to collect pod logs",
            )

        previous_result = kubectl.get_pod_logs(
            namespace,
            pod_name,
            tail=40,
            previous=True,
            context=context,
        )

        if previous_result.get("success"):
            evidence["kubernetes"]["logs_previous"] = previous_result.get(
                "stdout",
                "",
            )[-3500:]
            relevant_lines.extend(
                k8s_log_analysis.extract_relevant_lines(
                    previous_result.get("stdout", ""),
                    pod_name,
                    namespace,
                    None,
                    True,
                )
            )
        else:
            evidence["kubernetes"]["logs_previous_available"] = False

    if per_container:
        # Compatibility keys point at the first (primary) container.
        primary = per_container[0]
        evidence["kubernetes"]["logs_tail"] = primary.get("current_tail", "")
        if primary.get("logs_error") and not primary.get("current_success"):
            evidence["kubernetes"]["logs_error"] = primary["logs_error"]
        if primary.get("previous_available"):
            evidence["kubernetes"]["logs_previous"] = primary.get("previous_tail", "")
        else:
            evidence["kubernetes"]["logs_previous_available"] = any(
                c.get("previous_available") for c in per_container
            )

    relevant_lines = relevant_lines[: k8s_log_analysis.MAX_RELEVANT_LINES]
    primary_text = (
        per_container[0].get("current_tail", "") if per_container else ""
    )
    evidence["kubernetes"]["log_analysis"] = {
        "per_container": [
            {
                "container": c.get("container"),
                "restart_count": c.get("restart_count"),
                "current_success": c.get("current_success"),
                "previous_available": c.get("previous_available"),
            }
            for c in per_container
        ],
        "relevant_lines": relevant_lines,
        "signal_counts": k8s_log_analysis.summarize_signals(relevant_lines),
        "tail_context": k8s_log_analysis.tail_context(primary_text),
    }

    # Merged best-effort timeline: event timestamps + log signals.
    evidence["kubernetes"]["timeline"] = k8s_log_analysis.build_timeline(
        relevant_lines,
        evidence["kubernetes"].get("events_structured", []),
    )

    # Pod endpoint
    endpoint_result = kubectl.get_pod_endpoint(
        namespace,
        pod_name,
        context,
    )

    endpoint = None

    if endpoint_result.get("success"):
        endpoint = endpoint_result.get("stdout", "").strip()
        evidence["kubernetes"]["endpoint"] = endpoint
    else:
        evidence["kubernetes"]["endpoint_error"] = endpoint_result.get(
            "stderr",
            "Unable to determine pod endpoint",
        )

    # VictoriaMetrics evidence (scraped endpoint)
    if endpoint:
        up_result = victoriametrics.query(
            f'up{{instance="{endpoint}"}}'
        )

        evidence["metrics"]["up"] = up_result

        memory_result = victoriametrics.query(
            f'process_resident_memory_bytes{{instance="{endpoint}"}}'
        )

        evidence["metrics"]["memory"] = memory_result

        cpu_result = victoriametrics.query(
            f'process_cpu_seconds_total{{instance="{endpoint}"}}'
        )

        evidence["metrics"]["cpu"] = cpu_result

        requests_result = victoriametrics.query(
            f'http_requests_total{{instance="{endpoint}"}}'
        )

        evidence["metrics"]["requests"] = requests_result

    # Fallback: if instance queries returned empty, try pod-label queries
    pod_label = f'pod="{pod_name}"'
    for key, metric_name in [
        ("up", "up"),
        ("memory", "process_resident_memory_bytes"),
        ("cpu", "process_cpu_seconds_total"),
        ("requests", "http_requests_total"),
    ]:
        existing = evidence["metrics"].get(key, {})
        existing_result = existing.get("data", {}).get("data", {}).get("result", []) if isinstance(existing, dict) else []
        if not existing_result:
            fallback = victoriametrics.query(f'{metric_name}{{{pod_label}}}')
            if fallback.get("success"):
                fallback_result = fallback.get("data", {}).get("data", {}).get("result", [])
                if fallback_result:
                    evidence["metrics"][key] = fallback

    # VictoriaMetrics per-pod traffic + latency metrics (kubernetes-pods job)
    evidence["metrics"]["pod"] = _collect_pod_metrics(pod_name)

    # Elasticsearch logs for this pod (ERROR/EXCEPTION/TIMEOUT signals).
    # A healthy-but-quiet pod (0 logs) is reported as CLEAN, not as an
    # error: silence is a valid signal and must not break the investigation.
    try:
        es_logs = elasticsearch_service.get_pod_logs(
            pod_name, namespace, since_minutes=60, limit=100,
        )
        if es_logs.get("success") and es_logs.get("available"):
            evidence["elasticsearch"] = {
                "health": elasticsearch_service.elk_health(),
                "pod_logs_tail": _es_tail(es_logs.get("hits", [])),
                "log_total": es_logs.get("total", 0),
                "signal_counts": elasticsearch_service.find_error_patterns(
                    namespace=namespace, since_minutes=60, limit=20,
                ).get("patterns_found", 0),
            }
            if not es_logs.get("total", 0):
                evidence["elasticsearch"]["clean"] = True
                evidence["elasticsearch"].pop("pod_logs_tail", None)
        else:
            evidence["elasticsearch"] = {
                "health": elasticsearch_service.elk_health(),
                "error": es_logs.get("error", "ES unavailable or no logs"),
            }
    except Exception as exc:
        evidence["elasticsearch"] = {
            "health": elasticsearch_service.elk_health(),
            "error": str(exc)[:300],
        }

    # Compact CoreDNS/DNS evidence so every pod investigation is DNS-aware:
    # DNS resolution failures surface here even when the alert names a pod.
    # Log tails are dropped (counts + probe + health are what matter here).
    try:
        evidence["coredns"] = _collect_coredns_summary(
            {}, context, include_logs=False
        )
    except Exception as exc:
        evidence["coredns"] = {"error": str(exc)[:300]}

    evidence["git"] = git_correlation.correlate_commits(incident_start=None)

    # Explicit question so the CLI grounds the RCA on pod evidence. Without
    # it the CLI falls back to a generic "Incident" alert and the model can
    # not determine a root cause from the collected facts.
    pod_state = evidence["kubernetes"].get("state") or {}
    restarts = sum(
        (c.get("restart_count") or 0)
        for c in (pod_state.get("containers") or [])
    )
    log_signals = (
        (evidence["kubernetes"].get("log_analysis") or {}).get(
            "signal_counts", {}
        )
        or {}
    )
    es = evidence.get("elasticsearch") or {}
    vm = ((evidence.get("metrics") or {}).get("pod") or {}) or {}
    evidence["question"] = (
        f"Investigate Kubernetes pod {pod_name} in namespace {namespace}. "
        f"State: {pod_state.get('phase', 'unknown')}/"
        f"{pod_state.get('reason') or 'unknown'} "
        f"(ready={pod_state.get('ready', 'unknown')}). "
        f"Container restarts total: {restarts}. "
        f"Log signals: {log_signals}. "
        f"ES log total (last 60m): {es.get('log_total', 0) or 0}. "
        f"Pod metrics: request rate "
        f"{vm.get('request_rate_rps', 'n/a')} rps, "
        f"error {vm.get('error_rate_5xx_per_s', 'n/a')} rps "
        f"({vm.get('error_share_percent', 'n/a')}%), "
        f"p99 latency {vm.get('p99_latency_seconds', 'n/a')}s. "
        "Correlate Kubernetes pod state, events and container logs (current "
        "and previous), VictoriaMetrics pod metrics (request/error rate, "
        "latency, infra CPU/memory/restarts), Elasticsearch log signals, "
        "CoreDNS and GitHub evidence. Provide root cause, confidence, "
        "evidence, timeline, affected component, and remediation."
    )

    return {
        "success": True,
        "evidence": evidence,
    }


def _es_tail(hits, limit: int = 20, max_chars: int = 3000):
    """Compact log tail built from ES search hits (log lines verbatim)."""
    lines = []
    for hit in (hits or [])[:limit]:
        source = hit.get("_source") or {}
        line = source.get("log") or source.get("message") or ""
        if line:
            lines.append(line)
    return "\n".join(lines)[-max_chars:]


def collect_node_evidence(
    node_name: str,
    context: str | None = None,
    tail: int = 200,
):
    """
    Collect comprehensive, read-only evidence for a Kubernetes node so OpenSRE
    can root-cause node-level incidents: node state/conditions, describe
    output, Node events, per-pod failure signals, node-exporter/KSM metrics
    and Elasticsearch error signals for pods scheduled on the node.
    """
    evidence = {
        "target": {
            "type": "node",
            "name": node_name,
        },
        "cluster": context,
        "node": {"name": node_name},
        "kubernetes": {},
        "metrics": {"node": {}},
        "elasticsearch": {},
        "git": {},
    }

    # Structured node state (conditions, allocatable, capacity, addresses).
    state_result = kubectl.get_node_state(node_name, context)
    if state_result.get("success"):
        evidence["kubernetes"]["state"] = state_result.get("node")
    else:
        evidence["kubernetes"]["state_error"] = (
            state_result.get("stderr") or "Unable to collect node state"
        )

    # Raw `kubectl describe node` (taints, kubelet args, per-bucket requests).
    details_result = kubectl.get_node_details(node_name, context)
    if details_result.get("success"):
        evidence["kubernetes"]["node_details"] = details_result.get("stdout", "")
    else:
        evidence["kubernetes"]["node_details_error"] = (
            details_result.get("stderr") or "Unable to collect node details"
        )

    # Pods scheduled on the node + deep-dive failure signals.
    signals = _collect_node_pod_signals(node_name, context, tail=tail)
    evidence["kubernetes"]["node_usage"] = {
        k: v for k, v in signals.items() if k in (
            "node", "pod_count", "restarts_total", "pods",
        )
    }
    if signals.get("error"):
        evidence["kubernetes"]["node_usage_error"] = signals["error"]
    evidence["kubernetes"]["pod_signals"] = signals.get("deep_dive", [])

    relevant_lines = [
        entry
        for pod in signals.get("deep_dive", [])
        for entry in pod.get("log_signals", [])
    ][: k8s_log_analysis.MAX_RELEVANT_LINES]
    evidence["kubernetes"]["log_analysis"] = {
        "relevant_lines": relevant_lines,
        "signal_counts": k8s_log_analysis.summarize_signals(relevant_lines),
    }

    # Node events (raw + structured timeline).
    events_result = kubectl.get_node_events(node_name, context)
    if events_result.get("success"):
        evidence["kubernetes"]["events"] = events_result.get("stdout", "")
    else:
        evidence["kubernetes"]["events_error"] = events_result.get(
            "stderr", "Unable to collect node events"
        )

    events_json = kubectl.get_node_events_json(node_name, context)
    node_events = []
    if events_json.get("success"):
        node_events = k8s_log_analysis.parse_events(events_json.get("items", []))
    else:
        evidence["kubernetes"]["events_structured_error"] = (
            events_json.get("stderr") or "Unable to collect structured events"
        )
    evidence["kubernetes"]["events_structured"] = node_events
    evidence["kubernetes"]["timeline"] = k8s_log_analysis.build_timeline(
        relevant_lines, node_events
    )

    # Node-exporter + KSM metrics.
    evidence["metrics"]["node"] = _collect_node_metrics(node_name)

    # Elasticsearch error signals for the node's degraded pods (ES has no
    # node field, so we aggregate per-namespace + per-degraded-pod queries).
    try:
        health = elasticsearch_service.elk_health()
        es_section = {"health": health}
        if es_section["health"].get("success"):
            summary = elasticsearch_service.error_summary(since_minutes=60)
            es_section["error_counts"] = (summary.get("counts") or {}) or {}
            es_section["errors_total"] = sum(
                (summary.get("counts") or {}).values()
            )
            flagged = []
            for pod in signals.get("deep_dive", [])[:5]:
                pod_logs = elasticsearch_service.get_pod_logs(
                    pod["pod"], pod["namespace"], since_minutes=60, limit=20,
                )
                if pod_logs.get("success") and pod_logs.get("available"):
                    signals_n = elasticsearch_service.find_error_patterns(
                        namespace=pod["namespace"],
                        since_minutes=60,
                        limit=10,
                    ).get("patterns_found", 0)
                    flagged.append(
                        {
                            "namespace": pod["namespace"],
                            "pod": pod["pod"],
                            "log_total": pod_logs.get("total", 0),
                            "error_signals": signals_n,
                            "log_tail": _es_tail(pod_logs.get("hits", [])),
                        }
                    )
            es_section["flagged_pods"] = flagged
        evidence["elasticsearch"] = es_section
    except Exception as exc:
        evidence["elasticsearch"] = {
            "health": elasticsearch_service.elk_health(),
            "error": str(exc)[:300],
        }

    evidence["coredns"] = _collect_coredns_summary(
        {}, context, include_logs=False
    )

    evidence["git"] = git_correlation.correlate_commits(incident_start=None)

    # Explicit question so the CLI grounds the RCA on node evidence.
    state = evidence["kubernetes"].get("state") or {}
    degraded = [
        p for p in signals.get("deep_dive", [])
        if p["phase"] not in ("Running", "Succeeded", "Completed")
    ]
    evidence["question"] = (
        f"Investigate Kubernetes node {node_name}. "
        f"Conditions: {[(c.get('type'), c.get('status')) for c in state.get('conditions', [])]}. "
        f"Unschedulable: {state.get('unschedulable', False)}. "
        f"Pod count: {signals.get('pod_count', 0)}, restarts total: "
        f"{signals.get('restarts_total', 0)}. "
        f"Degraded pods on node: {len(degraded)} "
        f"({', '.join(p['pod'] for p in degraded[:6])} ). "
        "Correlate Kubernetes node state, Node events, node-exporter and "
        "cAdvisor metrics (CPU/memory/disk/network), pod state and container "
        "logs, Elasticsearch log signals and GitHub evidence. "
        "Provide root cause, confidence, evidence, timeline, affected "
        "component, and remediation."
    )

    return {
        "success": True,
        "evidence": evidence,
    }


def _collect_pod_metrics(pod_name: str):
    """
    Request rate, error rate and p50/p95/p99 latency for a single pod
    (kubernetes-pods scrape with a `pod` label). Returns None-free dict;
    missing keys mean the pod has no instrumented traffic.
    """
    label = f'pod="{pod_name}"'
    metrics = {}

    rate = _vm_scalar(
        f'sum(rate(http_requests_total{{{label}}}[1m]))'
    )
    error_rate = _vm_scalar(
        f'sum(rate(http_requests_total{{{label},status="5xx"}}[1m]))'
    )

    if rate is not None:
        metrics["request_rate_rps"] = rate

    if error_rate is not None:
        metrics["error_rate_5xx_per_s"] = error_rate
        metrics["error_share_percent"] = round(
            error_rate / (rate or 1e-6) * 100, 2
        )

    if rate is not None:
        base = (
            f'sum(rate(http_request_duration_highr_seconds_bucket{{{label}}}[1m]))'
            " by (le)"
        )
        for quantile, name in ((0.50, "p50"), (0.95, "p95"), (0.99, "p99")):
            value = _vm_scalar(f"histogram_quantile({quantile}, {base})")
            if value is not None:
                metrics[f"{name}_latency_seconds"] = value

    # cAdvisor + kube-state-metrics infra signals (every pod, instrumented or
    # not): CPU cores / memory working set / restarts. `container!=""`
    # excludes the pod-sandbox slot so only real app containers count.
    infra = {
        "cpu_cores": (
            f'sum(rate(container_cpu_usage_seconds_total'
            f'{{pod="{pod_name}", container!=""}}[5m]))'
        ),
        "mem_working_set_bytes": (
            f'sum(container_memory_working_set_bytes'
            f'{{pod="{pod_name}", container!=""}})'
        ),
        "mem_rss_bytes": (
            f'sum(container_memory_rss_bytes'
            f'{{pod="{pod_name}", container!=""}})'
        ),
        "restarts": (
            f'sum(kube_pod_container_status_restarts_total'
            f'{{pod="{pod_name}"}})'
        ),
    }
    for key, query in infra.items():
        value = _vm_scalar(query)
        if value is not None:
            metrics[key] = value

    return metrics


def _namespace_summary(namespace: str, context: str | None = None):
    summary = {"namespace": namespace, "pods": []}

    pods_result = kubectl.get_pods(context)

    if not pods_result.get("success"):
        summary["error"] = pods_result.get("stderr", "Unable to list pods")
        return summary

    allowed_statuses = ("Running", "Succeeded", "Completed")
    status_counts = {}

    for line in pods_result.get("stdout", "").splitlines()[1:]:
        columns = line.split()
        if len(columns) >= 5 and columns[0] == namespace:
            entry = {
                "name": columns[1],
                "ready": columns[2],
                "status": columns[3],
                "restarts": columns[4],
            }
            summary["pods"].append(entry)
            status_counts[columns[3]] = status_counts.get(columns[3], 0) + 1

    summary["pod_count"] = len(summary["pods"])
    summary["status_counts"] = status_counts
    summary["degraded_pods"] = [
        pod
        for pod in summary["pods"]
        if pod["status"] not in allowed_statuses
    ]

    return summary


def _tcp_probe(host: str, ports):
    result = {}

    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.5)
        try:
            sock.connect((host, port))
            result[str(port)] = {"reachable": True}
        except Exception as exc:
            result[str(port)] = {"reachable": False, "error": str(exc)}
        finally:
            sock.close()

    return result


def _collect_kubernetes_summary(evidence, context):
    summary = {}

    nodes_result = kubectl.get_nodes(context)

    if nodes_result.get("success"):
        nodes = []

        for line in nodes_result.get("stdout", "").splitlines()[1:]:
            columns = line.split()
            if columns:
                nodes.append(
                    {
                        "name": columns[0],
                        "status": columns[1],
                        "roles": columns[2] if len(columns) > 2 else "",
                    }
                )

        summary["nodes"] = nodes
    else:
        summary["nodes_error"] = nodes_result.get("stderr")

    pods_result = kubectl.get_pods(context)
    pods = []

    if pods_result.get("success"):
        for line in pods_result.get("stdout", "").splitlines()[1:]:
            columns = line.split()
            if len(columns) >= 5:
                pods.append(
                    {
                        "namespace": columns[0],
                        "name": columns[1],
                        "ready": columns[2],
                        "status": columns[3],
                        "restarts": columns[4],
                    }
                )

    allowed_statuses = ("Running", "Succeeded", "Completed")

    summary["pods"] = pods[:60]
    summary["degraded_pods"] = [
        pod for pod in pods if pod["status"] not in allowed_statuses
    ]

    status_counts = {}
    for pod in pods:
        status_counts[pod["status"]] = status_counts.get(pod["status"], 0) + 1

    summary["pod_status_counts"] = status_counts
    evidence["kubernetes"] = summary


def _collect_vm_summary(evidence):
    summary = {"health": victoriametrics.health()}

    jobs_result = victoriametrics.label_values("job")

    if jobs_result.get("success"):
        summary["scrape_jobs"] = jobs_result.get("data", [])

    up_result = victoriametrics.query("up")

    if up_result.get("success"):
        targets = []

        for item in up_result.get("data", {}).get("data", {}).get("result", []):
            metric = item.get("metric", {})
            value = "0"

            if item.get("value"):
                value = str(item["value"][1])

            targets.append(
                {
                    "job": metric.get("job"),
                    "instance": metric.get("instance"),
                    "pod": metric.get("pod"),
                    "up": value,
                }
            )

        summary["scrape_targets"] = targets[:30]
        summary["scrape_targets_total"] = len(targets)
        summary["scrape_targets_down"] = len(
            [t for t in targets if t["up"] != "1"]
        )

    for key, query in VM_QUERIES.items():
        summary[key] = _vm_scalar(query)

    evidence["victoriametrics"] = summary


def _collect_otel_summary(evidence, context):
    summary = {
        "pipeline": "otlp (grpc :4317, http :4318) -> prometheusremotewrite -> victoriametrics",
        "endpoints": OTEL_ENDPOINTS,
        "reachability": _tcp_probe("127.0.0.1", (4317, 4318)),
    }

    pod_result = kubectl.get_first_pod_by_label(
        OTEL_NAMESPACE,
        OTEL_COLLECTOR_LABEL,
        context,
    )

    if pod_result.get("success"):
        pod = pod_result.get("stdout", "").strip()

        if pod:
            summary["collector_pod"] = pod

            status_result = kubectl.get_pod_status(
                OTEL_NAMESPACE,
                pod,
                context,
            )

            if status_result.get("success"):
                summary["pod_status"] = status_result.get("stdout", "").strip()

            logs_result = kubectl.get_pod_logs(
                OTEL_NAMESPACE,
                pod,
                tail=40,
                context=context,
            )

            if logs_result.get("success"):
                summary["logs_tail"] = logs_result.get("stdout", "")[-3500:]
            else:
                summary["logs_error"] = logs_result.get("stderr")
    else:
        summary["collector_pod_error"] = pod_result.get("stderr")

    evidence["opentelemetry"] = summary


def _collect_grafana_summary(evidence):
    summary = {
        "health": grafana.health(),
        "datasources": grafana.datasources().get("datasources", []),
        "dashboards": grafana.dashboards().get("dashboards", []),
    }

    dashboard_result = grafana.dashboard_summary(GRAFANA_DASHBOARD_UID)

    if dashboard_result.get("success"):
        summary["primary_dashboard"] = dashboard_result.get("summary")
    else:
        summary["primary_dashboard_error"] = dashboard_result.get("error")

    evidence["grafana"] = summary


def collect_database_evidence(database: str):
    """
    Collect comprehensive database investigation evidence for YugabyteDB or Aerospike.
    This provides read-only investigation data for OpenSRE root-cause analysis.
    """
    database = database.lower()

    if database == "yugabyte":
        evidence = db_investigation.investigate_yugabyte()
    elif database == "aerospike":
        evidence = db_investigation.investigate_aerospike()
    else:
        return {
            "success": False,
            "error": f"Unknown database '{database}'. Supported: yugabyte, aerospike",
        }

    # Add target metadata for OpenSRE
    evidence["target"] = {
        "type": "database",
        "database": database,
        "name": database,
    }

    # Add git correlation
    evidence["git"] = git_correlation.correlate_commits(incident_start=None)

    return {
        "success": True,
        "evidence": evidence,
    }


def collect_all_database_evidence():
    """
    Collect investigation evidence for both YugabyteDB and Aerospike.
    """
    evidence = db_investigation.investigate_all_databases()

    evidence["target"] = {
        "type": "databases",
        "name": "all-databases",
    }

    evidence["git"] = git_correlation.correlate_commits(incident_start=None)

    return {
        "success": True,
        "evidence": evidence,
    }


def _collect_database_summary(evidence):
    summary = {}

    try:
        summary["aerospike"] = aerospike.health()
    except Exception as exc:
        summary["aerospike"] = {"success": False, "error": str(exc)}

    try:
        summary["yugabyte"] = yugabyte.health()
    except Exception as exc:
        summary["yugabyte"] = {"success": False, "error": str(exc)}

    evidence["databases"] = summary


def _collect_nginx_summary(evidence, context: str | None = None):
    try:
        nginx_ev = nginx_service.investigate_nginx(context=context)
        evidence["nginx"] = nginx_ev.get("nginx", nginx_ev)
        # also surface a compact health check
        evidence["nginx"]["_health_compact"] = nginx_service.health(context=context)
    except Exception as exc:
        evidence["nginx"] = {"success": False, "error": str(exc)}


def _collect_coredns_summary(
    evidence,
    context: str | None = None,
    tail: int = 150,
    include_logs: bool = True,
):
    """Bounded CoreDNS/DNS evidence for stack- and pod-level investigations."""
    try:
        coredns_ev = coredns_service.investigate_coredns(
            context=context, tail=tail
        )
        node = coredns_ev.get("coredns", coredns_ev)
        if not include_logs and isinstance(node, dict):
            node = dict(node)
            logs = node.get("logs")
            if isinstance(logs, dict):
                logs = dict(logs)
                logs.pop("aggregated_raw", None)
                per_pod = logs.get("per_pod")
                if isinstance(per_pod, list):
                    logs["per_pod"] = [
                        {k: v for k, v in entry.items() if k != "logs_tail"}
                        for entry in per_pod
                        if isinstance(entry, dict)
                    ]
                node["logs"] = logs
        evidence["coredns"] = node
    except Exception as exc:
        evidence["coredns"] = {"success": False, "error": str(exc)}

    return evidence["coredns"]


def collect_coredns_evidence(
    context: str | None = None,
    tail: int = 150,
    include_affected: bool = True,
):
    """
    Collect comprehensive CoreDNS/DNS investigation evidence and correlate it
    with cluster metrics, affected workloads and GitHub — read-only, safe.
    Keeps CoreDNS investigation independent of database integrations.
    """
    coredns_ev = coredns_service.investigate_coredns(
        context=context, tail=tail
    )

    evidence: dict = {
        "target": {"type": "coredns", "name": "coredns"},
        "cluster": context,
        "coredns": coredns_ev.get("coredns", coredns_ev),
        "affected": {},
        "victoriametrics": {},
        "git": {},
    }

    # Possibly-affected workloads: opensre namespace snapshot (bounded).
    if include_affected:
        try:
            evidence["affected"] = _namespace_summary("opensre", context)
        except Exception as exc:
            evidence["affected"] = {"error": str(exc)}

    # Cluster request/error/latency context for the "Metrics" investigation step.
    try:
        _collect_vm_summary(evidence)
    except Exception as exc:
        evidence["victoriametrics"] = {"error": str(exc)}

    evidence["git"] = git_correlation.correlate_commits(incident_start=None)

    # Aggregated CoreDNS summary for top-level convenience.
    coredns_summary = (
        evidence["coredns"].get("summary")
        if isinstance(evidence.get("coredns"), dict)
        else {}
    )
    evidence["summary"] = coredns_summary

    return {"success": True, "evidence": evidence}


def collect_nginx_evidence(context: str | None = None, tail: int = 250):
    """
    Collect comprehensive Nginx investigation evidence and correlate it
    with Kubernetes, VictoriaMetrics and GitHub — read-only, safe.
    Keeps Nginx investigation independent of database integrations.
    """
    nginx_ev = nginx_service.investigate_nginx(context=context, tail=tail)

    # correlate with cluster / metrics / git
    evidence: dict = {
        "target": {"type": "nginx", "name": "nginx"},
        "cluster": context,
        "nginx": nginx_ev.get("nginx", nginx_ev),
        "kubernetes": {},
        "victoriametrics": {},
        "git": {},
    }

    # lightweight cluster summary (reuse existing helper)
    try:
        _collect_kubernetes_summary(evidence, context)
    except Exception as exc:
        evidence["kubernetes"] = {"error": str(exc)}

    try:
        _collect_vm_summary(evidence)
    except Exception as exc:
        evidence["victoriametrics"] = {"error": str(exc)}

    evidence["git"] = git_correlation.correlate_commits(incident_start=None)

    # aggregated nginx summary for top-level convenience
    nginx_summary = evidence["nginx"].get("summary") if isinstance(evidence.get("nginx"), dict) else {}
    evidence["summary"] = nginx_summary

    return {"success": True, "evidence": evidence}


def collect_elasticsearch_evidence(
    namespace: str | None = None,
    pod: str | None = None,
    service: str | None = None,
    since_minutes: int = 60,
    limit: int = 50,
):
    """
    Collect structured Elasticsearch evidence for investigation.
    Read-only — no write/delete/index operations.
    Independent of databases.
    """
    summary = elasticsearch_service.error_summary(
        namespace=namespace, since_minutes=since_minutes,
    )
    patterns = elasticsearch_service.find_error_patterns(
        namespace=namespace, since_minutes=since_minutes, limit=limit,
    )
    logs = elasticsearch_service.search_logs(
        namespace=namespace, service=service,
        pod=pod, since_minutes=since_minutes, limit=limit,
    )
    evidence: dict = {
        "target": {"type": "elasticsearch", "name": "elk"},
        "elasticsearch": {
            "health": elasticsearch_service.elk_health(),
            "summary": summary.get("counts", {}),
            "error_samples": patterns.get("results", [])[:10],
            "logs": logs.get("hits", []),
            "patterns_found": patterns.get("patterns_found", 0),
        },
        "victoriametrics": {},
    }
    try:
        _collect_vm_summary(evidence)
    except Exception as exc:
        evidence["victoriametrics"] = {"error": str(exc)}

    evidence["git"] = git_correlation.correlate_commits(incident_start=None)

    return {"success": True, "evidence": evidence}


def collect_stack_evidence(context: str | None = None):
    """
    Collect evidence for the entire observability stack (Kubernetes,
    VictoriaMetrics, OpenTelemetry and Grafana) so AI investigations can
    analyze platform-level incidents beyond a single pod or service.
    """
    evidence = {
        "target": {
            "type": STACK_TARGET,
            "name": "opensre-demo observability stack",
            "components": ["kubernetes", "coredns", "victoriametrics",
                           "opentelemetry", "grafana"],
        },
        "cluster": context,
        "kubernetes": {},
        "coredns": {},
        "victoriametrics": {},
        "opentelemetry": {},
        "grafana": {},
        "databases": {},
        "nginx": {},
    }

    _collect_kubernetes_summary(evidence, context)
    _collect_coredns_summary(evidence, context)
    _collect_vm_summary(evidence)
    _collect_otel_summary(evidence, context)
    _collect_grafana_summary(evidence)
    _collect_database_summary(evidence)
    _collect_nginx_summary(evidence, context)

    evidence["git"] = git_correlation.correlate_commits(incident_start=None)

    return {
        "success": True,
        "evidence": evidence,
    }


def collect_target_evidence(target: str):
    """
    Collect evidence for a host service (container-backed database) that is
    not part of the Kubernetes cluster, so AI investigations can analyze it.
    """
    target = target.lower()

    if target not in SERVICE_TARGETS:
        return {
            "success": False,
            "error": (
                f"Unknown target '{target}'. Supported targets: "
                f"{', '.join(SERVICE_TARGETS)}"
            ),
        }

    evidence = {
        "target": {
            "type": target,
            "name": target,
        },
        "cluster": None,
        "service": {},
        "container": {},
        "metrics": {},
    }

    if target == "aerospike":
        evidence["service"]["display_name"] = "Aerospike"
        evidence["service"]["endpoint"] = settings.AEROSPIKE_HOSTS
        evidence["service"]["health"] = aerospike.health()
    else:
        evidence["service"]["display_name"] = "YugabyteDB"
        evidence["service"]["endpoint"] = (
            f"{settings.YUGABYTE_HOST}:{settings.YUGABYTE_PORT}"
        )
        evidence["service"]["health"] = yugabyte.health()

    evidence["container"]["state"] = containers.container_state(target)

    logs = containers.container_logs(target, tail=150)

    if logs.get("success"):
        evidence["container"]["logs"] = logs.get("stdout", "")[-4000:]
    else:
        evidence["container"]["logs_error"] = logs.get("stderr") or logs.get(
            "error",
            "Unable to collect container logs",
        )

    return {
        "success": True,
        "evidence": evidence,
    }


def collect_workflow_evidence(run_id: int):
    """
    Collect evidence for a GitHub Actions workflow run so OpenSRE can
    investigate a failed pipeline. Gathers run details, job steps with
    failed log snippets, and the commit it ran against.
    """
    from app.services import github

    run_result = github.get_workflow_run(run_id)
    if not run_result.get("success"):
        return {
            "success": False,
            "error": run_result.get("error", "Unable to fetch workflow run"),
        }

    run = run_result["data"]
    head_sha = run.get("head_sha") or ""
    conclusion = run.get("conclusion") or run.get("status") or "unknown"

    evidence = {
        "workflow_run": {
            "id": run.get("id"),
            "name": run.get("name"),
            "branch": run.get("head_branch"),
            "conclusion": conclusion,
            "status": run.get("status"),
            "head_sha": head_sha,
            "short_sha": head_sha[:7],
            "html_url": run.get("html_url"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "event": run.get("event"),
            "run_number": run.get("run_number"),
        },
        "jobs": [],
        "failed_jobs": [],
        "git": git_correlation.correlate_commits(
            incident_start=run.get("created_at"),
            branch=run.get("head_branch"),
        ),
    }

    jobs_result = github.get_workflow_run_jobs_with_logs(run_id)
    failed_steps = []

    if jobs_result.get("success"):
        for job in jobs_result.get("data") or []:
            job_info = {
                "name": job.get("name"),
                "id": job.get("id"),
                "conclusion": job.get("conclusion"),
                "status": job.get("status"),
                "started_at": job.get("started_at"),
                "completed_at": job.get("completed_at"),
                "steps": [],
                "logs_snippet": job.get("logs_snippet"),
            }
            for step in job.get("steps") or []:
                step_info = {
                    "name": step.get("name"),
                    "conclusion": step.get("conclusion"),
                    "status": step.get("status"),
                    "number": step.get("number"),
                }
                job_info["steps"].append(step_info)
                if step.get("conclusion") == "failure":
                    failed_steps.append(
                        f"{job.get('name')} / {step.get('name')}"
                    )

            evidence["jobs"].append(job_info)
            if job.get("conclusion") == "failure":
                evidence["failed_jobs"].append(job_info)

    summary_lines = [
        f"workflow: {run.get('name')} (run #{run_id})",
        f"branch: {run.get('head_branch')}",
        f"commit: {head_sha[:7]}",
        f"conclusion: {conclusion}",
        f"event: {run.get('event')}",
    ]
    if failed_steps:
        summary_lines.append(f"failed steps: {'; '.join(failed_steps)}")

    git_corr = evidence.get("git") or {}
    suspected = git_corr.get("suspected_commit") or {}
    if suspected.get("sha"):
        summary_lines.append(
            f"suspected change-point: {suspected['sha'][:7]} "
            f"'{suspected.get('message', '')}'"
        )

    evidence["summary"] = "\n".join(summary_lines)

    return {
        "success": True,
        "evidence": evidence,
    }


_ALERT_NAMESPACE_KEYS = (
    "namespace",
    "kubernetes_namespace_name",
    "exported_namespace",
    "opensre_namespace",
)

_ALERT_POD_KEYS = (
    "pod",
    "kubernetes_pod_name",
    "exported_pod",
)


def _alert_target(alert: dict):
    labels = alert.get("labels") or {}

    namespace = None
    for key in _ALERT_NAMESPACE_KEYS:
        if labels.get(key):
            namespace = labels[key]
            break
    else:
        annotations = alert.get("annotations") or {}
        for key in _ALERT_NAMESPACE_KEYS:
            if annotations.get(key):
                namespace = annotations[key]
                break

    pod = None
    for key in _ALERT_POD_KEYS:
        if labels.get(key):
            pod = labels[key]
            break

    return namespace, pod


def _detect_database_alert(alert: dict) -> str | None:
    """
    Detect if an alert is related to YugabyteDB or Aerospike.
    Returns 'yugabyte', 'aerospike', or None.
    """
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    
    # Check alertname for database references
    alertname = labels.get("alertname", "") or annotations.get("alertname", "")
    alertname_lower = alertname.lower()
    
    # Check description/summary for database keywords
    description = (annotations.get("description", "") or annotations.get("summary", "") or "").lower()
    summary = (annotations.get("summary", "") or "").lower()
    
    # Database-specific keywords
    yugabyte_keywords = [
        "yugabyte", "yb-", "ysql", "ycql", "tserver", "master",
        "pg_", "postgres", "connection refused.*5433", "connection refused.*yugabyte"
    ]
    
    aerospike_keywords = [
        "aerospike", "asd", "namespace", "aerospike.*connection", 
        "connection refused.*3000", "proxy_errors", "client_connections"
    ]
    
    # Check all text fields
    all_text = f"{alertname_lower} {description} {summary}"
    
    for keyword in yugabyte_keywords:
        if keyword in all_text:
            return "yugabyte"
    
    for keyword in aerospike_keywords:
        if keyword in all_text:
            return "aerospike"
    
    # Check for database label
    db_label = labels.get("database", "").lower()
    if db_label in ("yugabyte", "yugabytedb", "yb"):
        return "yugabyte"
    if db_label in ("aerospike", "aero"):
        return "aerospike"
    
    # Check for service label
    service = labels.get("service", "").lower()
    if "yugabyte" in service:
        return "yugabyte"
    if "aerospike" in service:
        return "aerospike"
    
    return None


def collect_alert_evidence(
    alert: dict,
    context: str | None = None,
):
    """
    Attach live cluster evidence to an alert so the OpenSRE RCA can verify
    claims about a pod or namespace instead of relying on the alert alone.

    The OpenSRE CLI's investigate command treats its input as an ALERT payload
    (it reads `labels` / `annotations` directly), so this returns a normalised
    alert whose description carries a compact evidence digest. That is what
    makes the report grounded: the agent reads `annotations.description` and
    can cite real pod/event/metric facts.
    """
    namespace, pod = _alert_target(alert)

    evidence = None

    # Nginx-specific path: if alert looks like nginx / gateway, collect nginx evidence directly
    try:
        if nginx_service.is_nginx_alert(alert):
            result = collect_nginx_evidence(context)
            if result.get("success"):
                evidence = result["evidence"]
                evidence["target"] = {
                    "type": "nginx",
                    "namespace": namespace or (evidence.get("nginx") or {}).get("namespace", "opensre"),
                    "name": "nginx",
                }
    except Exception:
        pass

    # CoreDNS/DNS-specific path: DNS latency / resolution-failure alerts get
    # CoreDNS evidence (pods, logs, probe, metrics) + affected workloads.
    # Runs after the nginx branch so gateway alerts keep nginx priority.
    if evidence is None:
        try:
            if coredns_service.is_coredns_alert(alert):
                result = collect_coredns_evidence(context)
                if result.get("success"):
                    evidence = result["evidence"]
                    evidence["target"] = {
                        "type": "coredns",
                        "namespace": namespace
                        or (evidence.get("coredns") or {}).get(
                            "namespace", "kube-system"),
                        "name": "coredns",
                    }
        except Exception:
            pass

    # Database-specific path: database-related alerts get database evidence
    # Checks for YugabyteDB or Aerospike related alerts
    if evidence is None:
        try:
            db_type = _detect_database_alert(alert)
            if db_type:
                result = investigation.collect_database_evidence(db_type)
                if result.get("success"):
                    evidence = result["evidence"]
                    evidence["target"] = {
                        "type": "database",
                        "database": db_type,
                        "name": db_type,
                    }
        except Exception:
            pass

    if evidence is None and namespace and pod:
        result = collect_pod_evidence(namespace, pod, context)

        if result.get("success"):
            evidence = result["evidence"]
            evidence["kubernetes"]["namespace"] = _namespace_summary(
                namespace,
                context,
            )
            evidence["target"] = {
                "type": "pod",
                "namespace": namespace,
                "name": pod,
            }

    if evidence is None:
        result = collect_stack_evidence(context)

        if result.get("success"):
            evidence = result["evidence"]
            evidence["target"] = {
                "type": STACK_TARGET,
                "namespace": namespace,
                "name": "opensre-demo observability stack",
            }

    if evidence is None:
        # Never silently forward a bare alert: that produces unverifiable
        # "Non-Validated Claims" triage instead of a grounded RCA.
        return {
            "success": False,
            "error": "Unable to collect live cluster evidence for the alert",
        }

    starts_at = alert.get("startsAt") or alert.get("starts_at")
    git_corr = git_correlation.correlate_commits(incident_start=starts_at)
    evidence["git"] = git_corr

    digest = _evidence_digest(alert, evidence, git_corr)

    payload = _normalize_alert(alert, digest)

    return {
        "success": True,
        "payload": payload,
        "evidence": evidence,
        "evidence_digest": digest,
    }


def _normalize_alert(alert: dict, digest: str):
    """
    Flatten an alert into the exact shape the OpenSRE CLI's investigate
    command reads, with the evidence digest embedded in the description.
    """
    labels = dict(alert.get("labels") or {})
    annotations = dict(alert.get("annotations") or {})

    if not labels.get("alertname"):
        labels["alertname"] = alert.get("alertname") or "OpenSRE Alert"

    summary = annotations.get("summary") or annotations.get("message")
    description = annotations.get("description") or annotations.get("message")

    annotations["description"] = (
        f"{digest}\n\nalert description: {description}"
        if description
        else digest
    )

    if not annotations.get("summary") and summary:
        annotations["summary"] = summary

    return {
        "status": alert.get("status") or "firing",
        "labels": labels,
        "annotations": annotations,
        "startsAt": alert.get("startsAt") or alert.get("starts_at"),
        "endsAt": alert.get("endsAt") or alert.get("ends_at"),
    }


def _evidence_digest(alert: dict, evidence: dict, git_corr=None, max_chars: int = 2200):
    """
    Compact, human-readable summary of the live facts collected for an alert.
    Folding this into the payload (and description) guarantees the agent sees
    the evidence even if it ignores the structured `evidence` key.
    """
    lines = []

    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    alertname = labels.get("alertname") or alert.get("alertname") or "alert"

    lines.append(
        f"ALERT: {alertname} "
        f"(severity={labels.get('severity') or 'unknown'}, "
        f"namespace={labels.get('namespace') or '?'})"
    )
    summary = annotations.get("summary") or annotations.get("message")
    if summary:
        lines.append(f"alert summary: {summary}")

    pod = evidence.get("pod") or {}
    if pod.get("name"):
        lines.append(f"target pod: {pod['name']} (namespace {pod['namespace']})")

    k8s = evidence.get("kubernetes") or {}

    state = k8s.get("state")
    if state:
        lines.append(
            f"pod phase={state.get('phase')} "
            f"node={state.get('node')} ip={state.get('pod_ip')}"
        )
        for container in state.get("containers", []) or []:
            current = container.get("state") or {}
            last = container.get("last_state") or {}
            detail = current.get("reason") or last.get("reason")
            exit_code = last.get("exit_code")
            restart = container.get("restart_count") or 0
            lines.append(
                f"container {container.get('name')}: ready={container.get('ready')} "
                f"restarts={restart} lastState={detail or 'n/a'}"
                + (f" exitCode={exit_code}" if exit_code is not None else "")
            )

    status = (k8s.get("pod_status") or "").strip().splitlines()
    if status and len(status) > 1:
        header = status[0].replace("NOMINATED NODE", "NOMINATED").replace(
            "READINESS GATES",
            "READINESS",
        )
        lines.append(f"kubectl: {header}")
        lines.append(f"kubectl: {status[1]}")

    structured_events = k8s.get("events_structured") or []
    if structured_events:
        lines.append("k8s events (newest relevant first):")
        for event in structured_events[:8]:
            ts = event.get("timestamp") or "no-ts"
            count = event.get("count", 1)
            suffix = f" x{count}" if isinstance(count, int) and count > 1 else ""
            lines.append(
                f"  {ts} {event.get('type')}/{event.get('reason')}{suffix}: "
                f"{(event.get('message') or '')[:200]}"
            )
    else:
        events = (k8s.get("events") or "").strip().splitlines()
        reason_tokens = (
            "OOMKilled",
            "CrashLoopBackOff",
            "ImagePullBackOff",
            "BackOff",
            "FailedScheduling",
            "Unhealthy",
            "Failed",
            "Killing",
            "Evicted",
        )
        relevant = [
            line
            for line in events[1:]
            if any(token in line for token in reason_tokens)
        ]
        if relevant:
            lines.append("cluster events:")
            lines.extend(f"  {line}" for line in relevant[-6:])

    log_analysis = k8s.get("log_analysis") or {}
    relevant_lines = log_analysis.get("relevant_lines") or []
    if relevant_lines:
        counts = log_analysis.get("signal_counts") or {}
        summary = ", ".join(
            f"{signal}={count}" for signal, count in sorted(counts.items())
        )
        lines.append(f"relevant log signals ({summary}):")
        for entry in relevant_lines[:8]:
            where = entry.get("container") or "?"
            if entry.get("previous"):
                where += "(previous)"
            ts = f"{entry.get('ts')} " if entry.get("ts") else ""
            lines.append(
                f"  [{where}][{entry.get('signal')}] {ts}{(entry.get('line') or '')[:200]}"
            )
    else:
        logs = (k8s.get("logs_tail") or "").strip().splitlines()
        if logs:
            lines.append("log tail (last lines):")
            lines.extend(f"  {line[:220]}" for line in logs[-5:] if line.strip())

    timeline = k8s.get("timeline") or []
    if timeline:
        lines.append("timeline:")
        for item in timeline[:6]:
            ts = item.get("ts") or "no-ts"
            lines.append(f"  {ts} [{item.get('source')}] {(item.get('text') or '')[:180]}")

    metrics = evidence.get("metrics") or {}
    pod_metrics = metrics.get("pod") or {}
    if pod_metrics:
        pieces = [
            f"req/s={pod_metrics.get('request_rate_rps')}",
            f"5xx/s={pod_metrics.get('error_rate_5xx_per_s')}",
            f"5xx%={pod_metrics.get('error_share_percent')}",
            f"p50={pod_metrics.get('p50_latency_seconds')}s",
            f"p95={pod_metrics.get('p95_latency_seconds')}s",
            f"p99={pod_metrics.get('p99_latency_seconds')}s",
        ]
        if pod_metrics.get("cpu_cores") is not None:
            pieces.append(f"cpu={pod_metrics['cpu_cores']} cores")
        if pod_metrics.get("mem_working_set_bytes") is not None:
            pieces.append(
                f"mem={round(pod_metrics['mem_working_set_bytes'] / 1048576, 1)}Mi"
            )
        if pod_metrics.get("restarts") is not None:
            pieces.append(f"restarts={pod_metrics['restarts']}")
        lines.append("pod metrics (last 1m): " + ", ".join(pieces))

    # Elasticsearch log signals summary
    es = evidence.get("elasticsearch") or {}
    if (es.get("health") or {}).get("success"):
        if es.get("clean"):
            lines.append(f"ES: healthy and clean — no logs for this pod in the last 60m (log_total={es.get('log_total', 0)})")
        elif es.get("signal_counts", 0) > 0:
            lines.append(
                f"ES log signals: {es.get('signal_counts')} ERROR/EXCEPTION/TIMEOUT patterns "
                f"(total logs: {es.get('log_total', 0)})"
            )
            sample_lines = es.get("pod_logs_tail", "").splitlines()
            error_signals = [l for l in sample_lines if any(
                tok in l.lower() for tok in ["error", "exception", "timeout", "failed"])]
            if error_signals:
                lines.append("notable log entries:")
                for l in error_signals[:5]:
                    lines.append(f"  - {l[:200]}")
        elif es.get("error") is None:
            lines.append(
                f"ES: healthy — {es.get('log_total', 0)} logs in last 60m, no error patterns"
            )

    ns = k8s.get("namespace")
    if isinstance(ns, dict) and ns.get("status_counts"):
        counts = ", ".join(
            f"{k}={v}" for k, v in sorted(ns["status_counts"].items())
        )
        degraded = ns.get("degraded_pods") or []
        if degraded:
            lines.append(
                "namespace degraded pods: "
                + ", ".join(
                    f"{p['name']}({p['status']})" for p in degraded[:8]
                )
            )
        lines.append(f"namespace {ns.get('namespace')} status counts: {counts}")

    kubernetes = evidence.get("kubernetes") or {}
    if isinstance(ns, dict) and isinstance(kubernetes, dict) and \
            kubernetes.get("pod_status_counts"):
        counts = ", ".join(
            f"{k}={v}"
            for k, v in sorted(kubernetes["pod_status_counts"].items())
        )
        degraded = kubernetes.get("degraded_pods") or []
        if degraded:
            lines.append(
                "degraded pods: "
                + ", ".join(
                    f"{p['name']}({p['status']})" for p in degraded[:8]
                )
            )
        lines.append(f"cluster pod status counts: {counts}")

    vm = evidence.get("victoriametrics") or {}
    vm_metrics = [
        f"req/s={vm.get('request_rate_rps')}",
        f"5xx/s={vm.get('error_rate_5xx_per_s')}",
        f"5xx%={vm.get('error_share_percent')}",
        f"p50={vm.get('p50_latency_seconds')}s",
        f"p95={vm.get('p95_latency_seconds')}s",
        f"p99={vm.get('p99_latency_seconds')}s",
    ]
    if any(value is not None for value in (
        vm.get("request_rate_rps"),
        vm.get("error_share_percent"),
        vm.get("p99_latency_seconds"),
    )):
        lines.append("cluster metrics (last 1m): " + ", ".join(vm_metrics))

    if vm.get("scrape_targets_total") is not None:
        lines.append(
            f"scrape targets: {vm['scrape_targets_total']} total, "
            f"{vm['scrape_targets_down']} down "
            f"(health={(vm.get('health') or {}).get('status')})"
        )

    if evidence.get("grafana"):
        lines.append(
            f"grafana health: {(evidence['grafana'].get('health') or {}).get('status')}"
        )

    otel = evidence.get("opentelemetry") or {}
    if otel.get("pod_status"):
        lines.append(f"otel collector pod: {otel['pod_status']}")

    databases = evidence.get("databases") or {}
    for name, item in databases.items():
        if isinstance(item, dict) and not item.get("success"):
            lines.append(f"database {name}: unhealthy ({item.get('error')})")

    # Nginx summary (when present)
    nginx = evidence.get("nginx") or {}
    if isinstance(nginx, dict) and (nginx.get("summary") or nginx.get("health_status") or nginx.get("pods")):
        summary = nginx.get("summary") or {}
        health_status = nginx.get("health_status") or (nginx.get("health") or {}).get("status") if isinstance(nginx.get("health"), dict) else None
        cfg_status = nginx.get("config_status") or (nginx.get("config_validation") or {}).get("valid")
        pods = nginx.get("pods", [])
        if health_status:
            lines.append(f"nginx health: {health_status} (pods={len(pods) if isinstance(pods, list) else '?'})")
        if cfg_status is not None:
            lines.append(f"nginx config: {'valid' if cfg_status is True or cfg_status=='valid' else 'invalid' if cfg_status is False or cfg_status=='invalid' else str(cfg_status)}")
        if summary:
            pieces = []
            if summary.get("http_5xx") is not None:
                pieces.append(f"5xx={summary.get('http_5xx')}")
            if summary.get("http_502") is not None:
                pieces.append(f"502={summary.get('http_502')}")
            if summary.get("http_503") is not None:
                pieces.append(f"503={summary.get('http_503')}")
            if summary.get("http_504") is not None:
                pieces.append(f"504={summary.get('http_504')}")
            if summary.get("connection_refused"):
                pieces.append(f"conn_refused={summary.get('connection_refused')}")
            if summary.get("upstream_timeout"):
                pieces.append(f"upstream_timeout={summary.get('upstream_timeout')}")
            if summary.get("dns_failures"):
                pieces.append(f"dns_fail={summary.get('dns_failures')}")
            if pieces:
                lines.append("nginx access/error: " + ", ".join(pieces))
            lat = summary.get("upstream_latency") or {}
            if lat and lat.get("p99") is not None:
                lines.append(f"nginx upstream latency p50={lat.get('p50')}s p95={lat.get('p95')}s p99={lat.get('p99')}s avg={lat.get('avg')}s")
        # error samples
        log_analysis = nginx.get("log_analysis") or {}
        err = log_analysis.get("error", {}) if isinstance(log_analysis, dict) else {}
        if isinstance(err, dict) and err.get("interesting_tail"):
            for l in err["interesting_tail"][:3]:
                lines.append(f"nginx log: {l[:180]}")

    # CoreDNS/DNS summary (when present: stack, pod, alert or coredns evidence)
    coredns = evidence.get("coredns") or {}
    if isinstance(coredns, dict) and (
        coredns.get("summary") or coredns.get("health_status")
        or coredns.get("pods") or coredns.get("error")
    ):
        summary = coredns.get("summary") or {}
        if coredns.get("error") and not summary:
            lines.append(f"coredns: evidence unavailable ({coredns['error']})")
        else:
            health_status = summary.get("health_status") or coredns.get(
                "health_status")
            pods_total = summary.get("total_pods")
            if health_status:
                pieces = [f"health={health_status}"]
                if pods_total:
                    pieces.append(
                        f"pods={summary.get('running_pods', '?')}/"
                        f"{pods_total} running"
                    )
                restarts = summary.get("restart_count_total")
                if restarts:
                    pieces.append(f"restarts={restarts}")
                lines.append("coredns " + ", ".join(pieces))
            probe_verdict = summary.get("probe_verdict")
            if probe_verdict:
                probe_line = (
                    f"coredns dns probe: {probe_verdict} "
                    f"({summary.get('probe_succeeded', '?')}/"
                    f"{summary.get('probe_attempts', '?')} ok"
                )
                if summary.get("probe_latency_ms_avg") is not None:
                    probe_line += (
                        f", avg={summary['probe_latency_ms_avg']}ms"
                    )
                probe_line += ")"
                lines.append(probe_line)
            dns_pieces = []
            if summary.get("servfail"):
                dns_pieces.append(f"SERVFAIL={summary['servfail']}")
            if summary.get("dns_timeouts"):
                dns_pieces.append(f"timeouts={summary['dns_timeouts']}")
            if summary.get("dns_refused"):
                dns_pieces.append(f"refused={summary['dns_refused']}")
            if summary.get("forward_errors"):
                dns_pieces.append(
                    f"forward_errors={summary['forward_errors']}")
            if summary.get("loop_detected"):
                dns_pieces.append("loop_detected")
            if dns_pieces:
                lines.append("coredns dns failures: " + ", ".join(dns_pieces))
            elif summary and not summary.get("servfail") and (
                summary.get("log_lines")
            ):
                lines.append(
                    f"coredns logs: {summary['log_lines']} lines, "
                    "no SERVFAIL/timeout/forward errors in tail"
                )
            log_analysis = coredns.get("log_analysis") or {}
            interesting = log_analysis.get("interesting_tail") or []
            for line in interesting[:3]:
                lines.append(f"coredns log: {line[:180]}")
            probe = coredns.get("probe") or {}
            if isinstance(probe, dict) and probe.get("error") and not probe.get(
                "available"
            ):
                lines.append(f"coredns probe: unavailable ({probe['error']})")

    git_lines = git_correlation.git_digest_lines(git_corr)
    if git_lines:
        lines.append("")
        lines.extend(git_lines)

    digest = "\n".join(lines).strip()
    return digest[:max_chars]


def pod_alert_payload(evidence: dict, namespace: str, pod_name: str) -> dict:
    """
    Build the alert-shaped payload the OpenSRE CLI reads for a pod
    investigation, with the compact evidence digest folded into
    `annotations.description`.

    The CLI has no live k8s tool integrations in this deployment (only the
    knowledge/runbook tool is available), so the agent can only ground its
    RCA on the input payload. Passing the raw evidence blob produces a
    generic "Unable to determine root cause"; folding the digest into the
    description — the same mechanism the alert flow uses — gives the model
    the concrete pod facts (container last-state, events, log signals, ES
    error entries, metrics, git correlation) to reason about.
    """
    cluster = (evidence.get("cluster") or "") or "kind-opensre-demo"
    alertname = f"Kubernetes Pod Unhealthy: {pod_name}"
    alert = {
        "status": "firing",
        "alertname": alertname,
        "startsAt": datetime.now(timezone.utc).isoformat(),
        "labels": {
            "alertname": alertname,
            "severity": "high",
            "namespace": namespace,
            "pod": pod_name,
            "cluster": cluster,
        },
        "annotations": {
            "summary": (
                f"Kubernetes pod {namespace}/{pod_name} is in an unhealthy "
                "state (crashes, errors or performance degradation)."
            ),
        },
    }
    payload = _normalize_alert(alert, _evidence_digest(alert, evidence, evidence.get("git")))
    payload["question"] = evidence.get("question") or (
        f"Investigate Kubernetes pod {pod_name} in namespace {namespace}. "
        "Determine root cause, confidence, evidence, timeline, affected "
        "component, and remediation from the attached evidence."
    )
    return payload

