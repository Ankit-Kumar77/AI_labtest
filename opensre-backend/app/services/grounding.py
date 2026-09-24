"""Deterministic grounding helpers for OpenSRE investigations.

The LLM RCA is only trustworthy when it stays inside the collected evidence.
This module builds a compact, deterministic "ground truth" digest from the
evidence payload, then checks the model's markdown RCA for claims that
contradict it (crashes/port-bind/DNS/IPv6 stories with no supporting signal,
or mentions of systems that are absent from the evidence). When such claims
are found, opensre_cli performs one bounded corrective run.

Everything here is read-only and never raises.
"""

import re

CRASH_MARKERS = re.compile(
    r"\b(?:crashloop|crash-loop|crashloopbackoff|crash-loops|crashed|is crashing|"
    r"failed to start|failing to start|won't start|cannot start|not starting|"
    r"restart loop|in a restart loop|restarting continuously|restart failure|"
    r"restarting loop|pod is down|exit code\s*255|never became ready)\b",
    re.IGNORECASE,
)

PORT_BIND_MARKERS = re.compile(
    r"\b(?:address already in use|already in use by another process|"
    r"port conflict|hostport|host port|bind\s*\(.*EADDRINUSE|"
    r"cannot bind|failed to bind|port\s+\d+\s+is taken)\b",
    re.IGNORECASE,
)

DNS_MARKERS = re.compile(
    r"\b(?:dns\s*(?:resol\w+|fail(?:ure|ed|ures|s)|issue|problem|error)|"
    r"persistent\s+dns|nxdomain|servfail|no such host|name does not resolve|"
    r"could not resolve|cannot resolve|resolution failure)\b",
    re.IGNORECASE,
)

IPV6_MARKERS = re.compile(r"\b(?:ipv6|ip-?v6|aaaa\s+record|aaaa\s+queries|ip\s?v6)\b", re.IGNORECASE)

# Node-level host/systemd/dbus/cordon-drain-reboot claims: only valid if the
# EVIDENCE itself reports a node/host fault. A Running component on a Ready
# node with only a historical container lastState cannot support these.
NODE_HOST_MARKERS = re.compile(
    r"\b(?:/run/systemd|systemd|dbus|cordon|drain(?:ing)?\s+(?:the\s+)?node|"
    r"reboot(?:ing)?(?:\s+the)?\s+(?:host\s+)?node|node\s+reboot|"
    r"node\s+maintenance)\b",
    re.IGNORECASE,
)

# Claims of a cluster-wide Kubernetes control-plane/API outage (the classic
# confabulation: "worker nodes can't reach the K8s API server 10.96.0.1:443").
# These need DIRECT API-reachability evidence; a healthy Running component
# proves the cluster is reachable.
CLUSTER_MARKERS = re.compile(
    r"(?:kubernetes\s+api(?:\s*[-/]\s*)?server|api\s*server(?:\s*downgrade)?|"
    r"kube\s*(?:api|-)?apiserver|10\.96\.0\.1|cluster[- ]wide|"
    r"control[- ]plane|network\s+partition)(?:.{0,40}?)?"
    r"(?:unreachable|unavailable|timeout|timed\s*out|down|cannot\s+"
    r"(?:connect|reach)|unable\s+to\s+(?:communicate|reach|connect)|not\s+reachable|"
    r"i/o\s+timeout|is\s+down|connectivity\s+issue|outage)",
    re.IGNORECASE,
)

# Present-tense "the container runtime/node is failing to create cgroups /
# start the container for this pod" claims. Only the pod's HISTORICAL
# lastState carries such an error; a Running pod contradicts it being current.
RUNTIME_CREATE_MARKERS = re.compile(
    r"\b(?:is failing to (?:create|start)|is unable to (?:create|start)|"
    r"preventing new|unable to create new|keeps failing to (?:create|start)|"
    r"consistently failing to (?:create|start)|currently failing|is preventing)\b"
    r"[^.\n]{0,60}\b(?:cgroup|scope|scopes|cgroup scope|unit|units?|slice|"
    r"slices?|systemd scopes?|runc|shim|containerd(?: task)?|container runtime|"
    r"entrypoint)\b",
    re.IGNORECASE,
)

FOREIGN_TECH = [
    "redis", "kafka", "mysql", "memcached", "mongodb", "rabbitmq",
    "keycloak", "jenkins", "nats", "dynamodb",
]

HEALTHY_STATUSES = ("connected", "running", "healthy", "ok", "up", "ready")

SIGNAL_SCOPES = ("health", "connection_status", "connections", "cluster_health",
                 "latency", "latency_info", "namespace_info", "namespaces",
                 "namespace_set_stats", "demo_set_stats", "operation_errors",
                 "recent_errors", "slow_queries", "table_stats", "schema",
                 "summary", "log_analysis")


# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------

def _safe(v):
    if v is None:
        return ""
    if isinstance(v, dict):
        return " ".join(f"{k}={_safe(val)}" for k, val in v.items())
    if isinstance(v, (list, tuple)):
        return " ".join(_safe(x) for x in v)
    return str(v)


def collect_kv(text: str, limit: int = 60) -> list[str]:
    """Pull flat `key=value` / `key: value` pairs (JSON dump) for scanning."""
    pairs = re.findall(
        r"(?:\"([A-Za-z_][A-Za-z0-9_]*)\":\s*|\b([A-Za-z_][A-Za-z0-9_]*)[=:]\s*)"
        r"(\"[^\"]*\"|-?[0-9][0-9.,]*|\"[A-Za-z0-9_ ./:@\-]+\")",
        text,
    )
    out = []
    for k1, k2, val in pairs[:limit]:
        key = k1 or k2
        if key.lower() in ("password", "token", "secret", "api_key", "key"):
            continue
        out.append(f"{key}={val.strip('\"')}")
    return out


def _digest_payload(payload: dict) -> list[str]:
    """Best-effort fallback: JSON-serialized key/value pairs from the payload."""
    try:
        import json
        text = json.dumps(payload, default=str)
    except Exception:
        text = _safe(payload)
    lines = collect_kv(text)
    seen = set()
    deduped = []
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        deduped.append(line)
        if len(deduped) >= 80:
            break
    return deduped


def _first_row(*collections):
    for coll in collections:
        if not isinstance(coll, (list, tuple)) or not coll:
            continue
        first = coll[0]
        if isinstance(first, dict):
            return first
    return None


def _pick(values: dict, *keys):
    for k in keys:
        v = values.get(k)
        if v not in (None, "", []):
            return v
    return None


def extract_signal_lines(evidence: dict) -> list[str]:
    """Deterministic, human-readable signal lines for the flowing evidence.

    Safe on any evidence shape — unknown structures simply produce nothing.
    """
    lines: list[str] = []

    investigations = evidence.get("investigations")
    base = evidence.get("database") or "target"
    if isinstance(investigations, dict):
        conn = investigations.get("connections") or investigations.get("connection_status")
        if isinstance(conn, dict):
            row = _first_row(conn.get("data"))
            if isinstance(row, dict):
                total = _pick(row, "total_connections", "total")
                active = _pick(row, "active_connections", "active")
                idle_txn = _pick(row, "idle_in_transaction")
                waiting = _pick(row, "waiting_connections")
                parts = []
                if total is not None:
                    parts.append(f"total={total}")
                if active is not None:
                    parts.append(f"active={active}")
                if idle_txn is not None:
                    parts.append(f"idle_in_transaction={idle_txn}")
                if waiting is not None:
                    parts.append(f"waiting={waiting}")
                if parts:
                    lines.append(f"{base} connections: " + ", ".join(parts))

        slow = investigations.get("slow_queries")
        if isinstance(slow, dict) and isinstance(slow.get("data"), list):
            for row in slow["data"][:3]:
                if not isinstance(row, dict):
                    continue
                preview = _pick(row, "query_preview", "query")
                mean = _pick(row, "mean_exec_time", "mean_time_ms")
                calls = _pick(row, "calls", "exec_count")
                text = f"{base} slow query:"
                if isinstance(preview, str):
                    text += f" {preview[:120]}"
                if mean is not None:
                    text += f" | mean_exec_time={mean}"
                if calls is not None:
                    text += f" | calls={calls}"
                lines.append(text)

        active = investigations.get("recent_errors")
        if isinstance(active, dict) and isinstance(active.get("data"), list):
            waits = 0
            samples = []
            for row in active["data"][:10]:
                if not isinstance(row, dict):
                    continue
                if _pick(row, "wait_event_type") or _pick(row, "wait_event"):
                    waits += 1
                q = _pick(row, "query_preview", "query")
                if isinstance(q, str) and len(samples) < 2:
                    samples.append(q[:110])
            if waits:
                lines.append(f"{base} sessions with wait events: {waits}")
            for s in samples:
                lines.append(f"{base} active session query: {s}")

        latency = investigations.get("latency") or investigations.get("latency_info")
        if isinstance(latency, dict) and isinstance(latency.get("data"), list):
            for row in latency["data"][:2]:
                if not isinstance(row, dict):
                    continue
                node = row.get("node", base)
                reads = _pick(row, "read_latency_gt_8ms", "read_latency_gt_4ms",
                              "read_latency_gt_128ms")
                writes = _pick(row, "write_latency_gt_8ms", "write_latency_gt_4ms",
                               "write_latency_gt_128ms")
                tot_r = _pick(row, "total_read_ops")
                tot_w = _pick(row, "total_write_ops")
                parts = []
                if reads is not None:
                    parts.append(f"reads>8ms={reads}")
                if writes is not None:
                    parts.append(f"writes>8ms={writes}")
                if tot_r is not None:
                    parts.append(f"total_read_ops={tot_r}")
                if tot_w is not None:
                    parts.append(f"total_write_ops={tot_w}")
                if parts:
                    lines.append(f"{base} {node} latency: " + ", ".join(parts))

        stats = investigations.get("demo_set_stats") or investigations.get(
            "namespace_set_stats")
        if isinstance(stats, dict) and isinstance(stats.get("data"), dict):
            for node_name, row in list(stats["data"].items())[:2]:
                if isinstance(row, str):
                    if "objects=" in row:
                        m = re.search(r"objects=(\d+)", row)
                        if m:
                            lines.append(
                                f"{base} set {node_name}: objects={m.group(1)}")
                elif isinstance(row, dict):
                    objs = _pick(row, "objects", "n_objects")
                    if objs is not None:
                        lines.append(
                            f"{base} set {node_name}: objects={objs}")

    # schema / table names
    schema = investigations.get("schema") if isinstance(investigations, dict) else None
    if isinstance(schema, dict) and isinstance(schema.get("data"), dict):
        tables = schema["data"].get("tables")
        if isinstance(tables, list):
            names = [t.get("table_name") for t in tables if isinstance(t, dict)
                     and t.get("table_name")]
            if names:
                lines.append(f"{base} tables: " + ", ".join(sorted(set(names))[:12]))

    # nginx summary
    if isinstance(evidence.get("nginx"), dict):
        summ = evidence["nginx"].get("summary")
        if isinstance(summ, dict):
            parts = []
            for k in ("http_5xx", "http_502", "http_503", "http_504",
                      "connection_refused", "upstream_timeout", "dns_failures",
                      "config_errors"):
                if summ.get(k) not in (None, ""):
                    parts.append(f"{k}={summ[k]}")
            if parts:
                lines.append("nginx summary: " + ", ".join(parts))
        cfg = evidence["nginx"].get("config_status")
        if cfg:
            lines.append(f"nginx config: {cfg}")

    if not lines:
        lines = _digest_payload(evidence)
    return lines


def extract_known_entities(evidence: dict) -> set[str]:
    """Entity nouns (pods, dbs, namespaces, tables, sets) mentioned in evidence."""
    raw = _safe(evidence).lower()
    names = set()

    dc = evidence.get("database")
    if isinstance(dc, str):
        names.add(dc.lower())
    tgt = evidence.get("target")
    if isinstance(tgt, dict):
        for k in ("database", "name"):
            v = tgt.get(k)
            if isinstance(v, str):
                names.add(v.lower())

    k8s = evidence.get("kubernetes")
    if isinstance(k8s, dict):
        pod_state = k8s.get("pod_state")
        if isinstance(pod_state, dict):
            for k in ("pod", "name", "pod_name"):
                v = pod_state.get(k)
                if isinstance(v, str):
                    names.add(v.lower())
        details = k8s.get("pod_details")
        if isinstance(details, str):
            for name in re.findall(r"\b([a-z0-9][a-z0-9-]{2,40})\b", details.lower()):
                if "-" in name or "_" in name:
                    names.add(name)

    for word in re.findall(r"\b[a-z0-9][a-z0-9-]{2,40}\b", raw):
        if (("service" in word or "yugabyte" in word or "aerospike" in word
             or "-0" in word or word.startswith("catalog") or word.startswith("nginx")
             or word.startswith("flaky") or word.startswith("order-"))):
            names.add(word)

    # restrict to a known "in-domain" vocabulary so foreign names get caught
    domain = {
        "yugabyte", "yugabytedb", "aerospike", "nginx", "catalog-api",
        "flaky-service", "order-service", "traffic-gen", "coredns",
        "kibana", "elasticsearch", "grafana", "victoriametrics",
        "vmagent", "otel-collector", "fluent-bit", "node-exporter",
        "kube-state-metrics", "opensre", "databases", "observability",
        "local-path-storage", "yugabytedb-0", "aerospike-0",
        "order-service-bad", "crashloop", "memory-hog", "pending-pod",
        "imagepull", "node-stress",
    }
    names.union(domain)
    return {n for n in names if n}


def build_context(evidence: dict) -> dict:
    """Deterministic context used for both prompt grounding and RCA scanning."""
    raw_text = _safe(evidence).lower()

    target = None
    if isinstance(evidence.get("database"), str):
        target = evidence["database"].lower()
    tgt = evidence.get("target")
    if isinstance(tgt, dict):
        target = (tgt.get("database") or tgt.get("name") or target)
    if isinstance(target, str):
        target = target.lower()
    else:
        target = None

    healthy = False
    phase = None

    investigations = evidence.get("investigations") if isinstance(
        evidence.get("investigations"), dict) else None
    if investigations:
        health = investigations.get("health")
        if isinstance(health, dict):
            healthy = bool(health.get("success")) or health.get("status") == "connected"

    svc = evidence.get("service")
    if isinstance(svc, dict) and isinstance(svc.get("health"), dict):
        healthy = healthy or bool(svc["health"].get("success"))

    if isinstance(evidence.get("nginx"), dict):
        nginx = evidence["nginx"]
        healthy = healthy or nginx.get("health_status") == "healthy"

    coredns = evidence.get("coredns")
    if isinstance(coredns, dict):
        hs = coredns.get("health_status")
        if hs == "healthy":
            healthy = True
        summ = coredns.get("summary")
        if isinstance(summ, dict):
            rp, tp = summ.get("running_pods"), summ.get("total_pods")
            if isinstance(rp, int) and isinstance(tp, int) and tp > 0 and rp == tp:
                healthy = True

    es = evidence.get("elasticsearch")
    if isinstance(es, dict) and isinstance(es.get("health"), dict):
        healthy = healthy or es["health"].get("status") in ("green", "yellow")

    k8s = evidence.get("kubernetes")
    if isinstance(k8s, dict):
        for src in (k8s.get("state"), k8s.get("pod_state")):
            if isinstance(src, dict):
                phase = src.get("phase") or src.get("status")
                if phase:
                    break
        if not phase:
            pst = k8s.get("pod_status")
            if isinstance(pst, dict):
                phase = pst.get("phase")
            elif isinstance(pst, str) and "running" in pst.lower():
                phase = "Running"
        if isinstance(phase, str):
            phase = phase.lower()
            if phase not in ("running", "crashloopbackoff", "pending",
                             "succeeded", "terminated", "failed", "error",
                             "down", "unknown", "notdeployed", "not deployed"):
                phase = None

    if target in (None, "target"):
        if isinstance(evidence.get("pod"), str):
            target = evidence["pod"].lower()
        elif isinstance(k8s, dict):
            for src in (k8s.get("pod_state"), k8s.get("state")):
                if isinstance(src, dict) and (src.get("pod") or src.get("name")):
                    target = (src.get("pod") or src.get("name")).lower()
                    break
            if target in (None, "target") and isinstance(k8s.get("pod_details"), str):
                m = re.search(r"^Name:\s+(\S+)", k8s["pod_details"])
                if m:
                    target = m.group(1).lower()
    if target is None:
        target = "target"

    lines = extract_signal_lines(evidence)
    facts_lines = [f"- {l}" for l in lines[:40]]
    # StatefulSet scaled-to-zero / replica state (database chaos targets).
    # Surfaces the deterministic fact so the model doesn't guess between
    # eviction, deletion and crash when the workload was simply scaled down.
    sts_facts: list[str] = []
    if isinstance(k8s, dict):
        sts_status = k8s.get("statefulset_status")
        if isinstance(sts_status, dict):
            desired = sts_status.get("desired_replicas")
            ready = sts_status.get("ready_replicas")
            if isinstance(desired, int):
                if phase is None:
                    phase = "down" if desired == 0 else None
                sts_name = k8s.get("statefulset") or "workload"
                if desired == 0:
                    sts_facts.append(
                        f"- StatefulSet {sts_name}: desired_replicas=0 "
                        f"(ready={ready}) - workload scaled to zero, no "
                        "replicas are running"
                    )
                else:
                    sts_facts.append(
                        f"- StatefulSet {sts_name}: desired_replicas={desired} "
                        f"(ready={ready})"
                    )
        if isinstance(k8s.get("events"), list):
            seen = set()
            for ev in k8s["events"]:
                reason = ev.get("reason")
                message = ev.get("message")
                if reason in ("SuccessfulDelete", "SuccessfulCreate", "Killing",
                              "Scaled up replica set", "Scaled down replica set"):
                    line = f"- k8s event: {reason} - {message}"
                    if line not in seen:
                        seen.add(line)
                        sts_facts.append(line)
                    if reason in ("SuccessfulDelete",) and message and "delete" in str(message).lower():
                        break
                    if len(seen) >= 3:
                        break
    if isinstance(phase, str):
        top = f"- kubernetes pod phase: {phase}"
        if top not in facts_lines:
            facts_lines.insert(0, top)
    facts_lines.extend(sts_facts)
    if healthy and re.search(r"dns\.podman|plugin/errors", raw_text):
        facts_lines.append(
            "- coreDNS log noise detected ('.dns.podman'/plugin errors): this is "
            "an environment/upstream artifact visible for many names, NOT "
            "evidence that the target pod has a DNS misconfiguration."
        )
    facts = "\n".join(facts_lines)

    return {
        "target": target,
        "healthy": healthy,
        "phase": phase,
        "facts": facts,
        "evidence_text": raw_text,
        "known_entities": extract_known_entities(evidence),
    }


# ---------------------------------------------------------------------------
# RCA scanning
# ---------------------------------------------------------------------------

def _rca_case(rca: str) -> str:
    return (rca or "").lower()


def unsupported_claims(rca: str, ctx: dict) -> list[str]:
    """Returns short, human-readable warnings for claims that contradict the
    collected evidence."""
    rca_lower = _rca_case(rca)
    haystack = (ctx.get("evidence_text") or "")

    def backed_by(signal_words):
        return any(w in haystack for w in signal_words)

    warnings: list[str] = []

    healthy = bool(ctx.get("healthy")) or (ctx.get("phase") == "running")
    if healthy:
        if CRASH_MARKERS.search(rca_lower):
            warnings.append(
                "RCA claims a crash/restart failure, but the evidence shows "
                "the component is healthy (Running/connected).")
    if PORT_BIND_MARKERS.search(rca_lower) and not backed_by(
            ("already in use", "hostport", "host port", "port conflict",
             "cannot bind", "failed to bind")):
        warnings.append(
            "RCA claims a port-bind/hostPort conflict, but the evidence "
            "contains no such signal.")
    if DNS_MARKERS.search(rca_lower) and not backed_by(
            ("servfail", "nxdomain", "no such host", "name does not resolve",
             "could not resolve", "cannot resolve", "resolution failure",
             "dns lookup", "dns query failed")):
        warnings.append(
            "RCA claims a DNS resolution failure, but the evidence has "
            "no DNS failure signal for this target.")
    if IPV6_MARKERS.search(rca_lower) and not backed_by(("ipv6", "aaaa")):
        warnings.append(
            "RCA invokes an IPv6/AAAA explanation, which is unsupported "
            "by the collected evidence.")
    if CLUSTER_MARKERS.search(rca_lower) and not backed_by(
            ("connection refused", "connect: connection refused",
             "no route to host", "host unreachable", "dial tcp",
             "unable to connect to the server", "connect timeout")):
        warnings.append(
            "RCA claims a cluster-wide Kubernetes API/control-plane outage "
            "or network partition, but the collected evidence shows the "
            "component (and cluster) reachable with no such signal.")
    if NODE_HOST_MARKERS.search(rca_lower) and not backed_by(
            ("systemd", "dbus", "/run/systemd", "cordon", "drain",
             "journalctl", "kubelet down", "node not ready")):
        warnings.append(
            "RCA claims node-level host/systemd/dbus failures or node "
            "operations (cordon/drain/reboot), but the evidence shows a "
            "running component on a ready node with no node-level fault.")
    if (ctx.get("phase") == "running"
            and RUNTIME_CREATE_MARKERS.search(rca_lower)
            and not re.search(
                r"\b(?:historical|earlier|previous|previously|laststate|"
                r"at startup|prior)\b", rca_lower, re.IGNORECASE)):
        warnings.append(
            "RCA frames the container runtime/node as currently failing to "
            "create cgroups or start the container; the evidence shows the "
            "pod Running now, so any StartError/container-create failure "
            "belongs to the HISTORICAL lastState, not the current state.")

    known = {e.lower() for e in ctx.get("known_entities") or set()}
    for tech in FOREIGN_TECH:
        if re.search(rf"\b{re.escape(tech)}\b", rca_lower):
            if tech not in known:
                warnings.append(
                    f"RCA mentions '{tech}', which is absent from the evidence "
                    f"collected for this target.")

    db_target = ctx.get("target")
    hay = ctx.get("evidence_text") or ""
    if db_target in ("yugabyte", "aerospike"):
        for tech, other in (("aerospike", "yugabyte"), ("yugabyte", "aerospike")):
            if db_target != other:
                continue
            pat = (rf"\b{re.escape(tech)}(?:db)?(?!-\d)\b"
                   if tech == "yugabyte"
                   else rf"\b{re.escape(tech)}(?!-\d)\b")
            if tech not in hay and re.search(pat, rca_lower):
                warnings.append(
                    f"RCA attributes the issue to '{tech}', but the "
                    f"investigation target is '{db_target}' and no '{tech}' "
                    f"evidence was collected.")

    return warnings


def correction_instruction(warnings: list[str]) -> str:
    notes = "\n".join(f"- {w}" for w in warnings)
    return (
        "CORRECTION REQUIRED: the previous diagnosis contained claims that "
        "CONTRADICT the provided evidence. Specifically:\n"
        f"{notes}\n"
        "Ignore those. Re-derive the root cause STRICTLY from the facts and "
        "evidence provided in this payload. If the evidence only shows "
        "performance/connection degradation (systems still Running and "
        "connected), say exactly that — do not invent a crash, outage, DNS, "
        "IPv6, or port conflict. If the evidence reports the component is "
        "currently Running/ready while a container lastState shows an earlier "
        "StartError/restart/exit code from a previous attempt, describe that "
        "as HISTORICAL (e.g., 'an earlier start attempt'), never as an "
        "ongoing 'failing to start' or 'CrashLoopBackOff'. Do not claim "
        "node-level systemd/dbus/cgroup failures or recommend "
        "cordon/drain/reboot of the node when the component is Running and "
        "the evidence contains no node fault. Be concise and "
        "evidence-grounded."
    )