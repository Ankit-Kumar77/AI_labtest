"""Alertmanager read API client and alert-state sync for the host backend.

## Why this exists

Alertmanager's configured webhook receiver points at
`opensre-backend.opensre.svc.cluster.local:8001` -- the IN-CLUSTER backend
pod. In this kind/podman setup pods cannot reach the host, so the HOST
backend that actually serves the dashboard never received a single
webhook, and its alert store stayed empty even while Alertmanager was
happily delivering alerts to the pod.

Rather than fight the network, the host backend PULLS. Alertmanager
exposes a read API (`/api/v2/alerts`, `/api/v2/status`, ...) reachable
through the same `kubectl port-forward` pattern the databases already use,
so `sync()` feeds polled state through the identical ingestion path the
webhook uses (`alert_ingest`). One lifecycle, two delivery mechanisms.
"""

import logging
import re
import threading
import time

import requests
import yaml

from app.core.config import settings
from app.services import alert_ingest, incident_history, kubectl, portforward

log = logging.getLogger("opensre.alertmanager")

TIMEOUT = 5

# Poll faster than Alertmanager's 1m repeat_interval so a newly firing
# alert surfaces promptly, but not so fast that we hammer the API.
SYNC_INTERVAL_SECONDS = 20

# `kubectl logs --timestamps` prefixes lines with "2026-09-29T07:21:24.327Z ".
_KUBE_TS_RE = re.compile(r"^\S+\s+")

# Guard so a manual "Sync now" click and the background loop cannot run
# the same poll concurrently and double-count notifications.
_sync_lock = threading.Lock()


def _base() -> str:
    return settings.ALERTMANAGER_URL.rstrip("/")


def _get(path: str, **kwargs):
    return requests.get(f"{_base()}{path}", timeout=TIMEOUT, **kwargs)


def _err(exc: Exception, action: str) -> dict:
    log.warning("alertmanager %s failed: %s", action, exc)
    return {"success": False, "error": str(exc), "unreachable": True}


def _parse_config(original: str) -> dict:
    """Parse Alertmanager's raw config YAML back into a dict.

    The webhook URL is redacted to `<secret>` by the API, so a receiver's
    real endpoint is never exposed to the browser -- only its existence.
    """
    if not original:
        return {}
    try:
        parsed = yaml.safe_load(original)
    except yaml.YAMLError as exc:
        log.warning("could not parse alertmanager config: %s", exc)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def health() -> dict:
    """Liveness plus the configured receivers, for the dashboard banner."""
    try:
        response = _get("/-/healthy")
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "health check")

    return {"success": True, "status": response.text.strip() or "OK"}


def status() -> dict:
    """Cluster status and the routing config Alertmanager is running."""
    try:
        response = _get("/api/v2/status")
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "status")

    cluster = data.get("cluster") or {}
    # The v2 status API exposes the config only as raw YAML under
    # `config.original` (the parsed tree was dropped in v2), so parse it
    # back out to report the route and receivers the demo depends on.
    original = (data.get("config") or {}).get("original") or ""
    config = _parse_config(original)
    route = config.get("route") or {}

    return {
        "success": True,
        "version": (data.get("versionInfo") or {}).get("version"),
        "cluster": {
            "name": cluster.get("name"),
            "status": cluster.get("status"),
            "peers": len(cluster.get("peers") or []),
        },
        "route": {
            "receiver": route.get("receiver"),
            "group_by": route.get("group_by") or [],
            "group_wait": route.get("group_wait"),
            "group_interval": route.get("group_interval"),
            "repeat_interval": route.get("repeat_interval"),
        },
        "receivers": [
            {
                "name": r.get("name"),
                "webhooks": len(r.get("webhook_configs") or []),
            }
            for r in (config.get("receivers") or [])
            if isinstance(r, dict)
        ],
        "inhibit_rules": len(config.get("inhibit_rules") or []),
        "uptime": data.get("uptime"),
        "original_config": original,
    }


def _to_ingest_alert(entry: dict) -> dict:
    """Map an Alertmanager `/api/v2/alerts` entry to the webhook shape.

    The read API nests the state (`status.state`) and omits the top-level
    `status` string the webhook uses, so normalize it here rather than
    teaching `alert_store` two schemas.
    """
    state = ((entry.get("status") or {}).get("state")) or "active"
    return {
        "labels": entry.get("labels") or {},
        "annotations": entry.get("annotations") or {},
        "status": "firing" if state == "active" else "resolved",
        "startsAt": entry.get("startsAt") or "",
        "endsAt": entry.get("endsAt") or "",
        "fingerprint": entry.get("fingerprint"),
        "generatorURL": entry.get("generatorURL") or "",
    }


def list_alerts() -> dict:
    """Raw Alertmanager alert list, normalized for display."""
    try:
        response = _get("/api/v2/alerts")
        response.raise_for_status()
        entries = response.json()
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "alert list")

    alerts = []
    for entry in entries:
        labels = entry.get("labels") or {}
        state = (entry.get("status") or {}).get("state") or "unknown"
        alerts.append(
            {
                "alertname": labels.get("alertname", "UnknownAlert"),
                "severity": labels.get("severity", "warning"),
                "namespace": labels.get("namespace", ""),
                "pod": labels.get("pod", ""),
                "state": state,
                "summary": (entry.get("annotations") or {}).get("summary", ""),
                "startsAt": entry.get("startsAt") or "",
                "fingerprint": entry.get("fingerprint", ""),
                "receivers": [r.get("name") for r in (entry.get("receivers") or [])],
            }
        )

    return {"success": True, "count": len(alerts), "alerts": alerts}


def silences() -> dict:
    try:
        response = _get("/api/v2/silences")
        response.raise_for_status()
        entries = response.json()
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "silence list")

    return {
        "success": True,
        "count": len(entries),
        "silences": [
            {
                "id": s.get("id"),
                "comment": s.get("comment", ""),
                "createdBy": s.get("createdBy", ""),
            }
            for s in entries
        ],
    }


def sync() -> dict:
    """Pull current alert state and run it through the shared lifecycle.

    Returns counts so the dashboard can show that the pull actually
    delivered alerts into the incident store, not just that it ran.
    """
    if not _sync_lock.acquire(blocking=False):
        return {"success": True, "skipped": True, "detail": "sync already in progress"}

    try:
        response = _get("/api/v2/alerts")
        response.raise_for_status()
        entries = response.json()
    except Exception as exc:  # noqa: BLE001
        _sync_lock.release()
        return _err(exc, "sync")

    try:
        alerts = [_to_ingest_alert(entry) for entry in entries]
        if not alerts:
            return {"success": True, "polled": 0, "firing": 0, "ingested": 0, "results": []}

        result = alert_ingest.ingest(
            alerts,
            {
                "source": "alertmanager-sync",
                "receiver": "opensre-backend",
                "externalURL": f"{_base()}",
            },
        )

        firing = sum(1 for a in alerts if a["status"] == "firing")
        created = sum(1 for r in result["results"] if r.get("created"))

        log.info(
            "synced %d alert(s) from Alertmanager (%d firing, %d new incident(s))",
            len(alerts), firing, created,
        )
        return {
            "success": True,
            "polled": len(alerts),
            "firing": firing,
            "ingested": result["processed"],
            "new_incidents": created,
            "results": result["results"],
        }
    finally:
        _sync_lock.release()


def logs(tail: int = 150) -> dict:
    """Live Alertmanager container log tail, via `kubectl logs`.

    The dashboard uses this to show alerts arriving continuously: each
    delivery produces a "Notify success" line against the receiver. The pod
    is resolved by label so the exact name (which has a random suffix) is
    never hardcoded.
    """
    resolved = kubectl.get_first_pod_by_label("observability", "app=alertmanager")
    pod = (resolved.get("stdout") or "").strip()
    if not pod:
        return {
            "success": False,
            "unreachable": True,
            "error": resolved.get("stderr") or "could not resolve the Alertmanager pod",
        }

    result = kubectl.get_pod_logs_container(
        "observability",
        pod,
        "alertmanager",
        tail=max(1, min(tail, 2000)),
        timestamps=True,
    )
    if not result.get("success"):
        return {
            "success": False,
            "unreachable": True,
            "error": result.get("stderr") or "failed to read Alertmanager logs",
        }

    # `kubectl logs --timestamps` prefixes every line with the server-side
    # timestamp; the Alertmanager line already carries its own `time=...`,
    # so drop the duplicate prefix to keep each line readable.
    lines = []
    for line in (result.get("stdout") or "").splitlines():
        stripped = _KUBE_TS_RE.sub("", line).strip()
        if stripped:
            lines.append(stripped)
    return {"success": True, "pod": pod, "tail": len(lines), "lines": lines}


def reports(limit: int = 50) -> dict:
    """Saved RCA reports for alert-driven investigations.

    Every alert-driven investigation is auto-saved to incident history when
    it completes -- including failures ("token exhausted", provider 402), so
    the dashboard can show both the success story and the wall it hit. One
    entry per alert fingerprint, newest first.
    """
    result = incident_history.list_incidents(limit=500)
    newest_by_fingerprint = {}
    for record in result.get("data", []):
        if record.get("source") != "alert":
            continue
        # Newest-first ordering means the first record we meet is the latest.
        key = record.get("fingerprint") or f"nameless-{record.get('id')}"
        if key in newest_by_fingerprint:
            continue
        newest_by_fingerprint[key] = record

    reports_ = list(newest_by_fingerprint.values())[:max(1, min(limit, 200))]
    return {"success": True, "count": len(reports_), "reports": reports_}


def overview() -> dict:
    """Everything the Alerting page needs, in one round trip.

    Folds in the port-forward self-heal so the page can tell the user
    exactly which command to run when the forward is down.
    """
    health_result = health()
    reachable = bool(health_result.get("success"))

    payload = {
        "success": reachable,
        "health": health_result,
        "url": _base(),
        "port_forward": {
            "port": portforward.FORWARDS["alertmanager"]["local"],
            "alive": portforward.is_alive("alertmanager"),
            "command": portforward.command_string("alertmanager"),
        },
    }

    if not reachable:
        payload["detail"] = (
            "Alertmanager is unreachable from the host backend. Start the "
            "port-forward, then use Sync now."
        )
        payload.update({"status": None, "alerts": {"count": 0, "alerts": []}, "silences": {"count": 0, "silences": []}})
        return payload

    status_result = status()
    alerts_result = list_alerts()
    silences_result = silences()

    payload["status"] = status_result if status_result.get("success") else None
    payload["alerts"] = alerts_result if alerts_result.get("success") else {"count": 0, "alerts": []}
    payload["silences"] = silences_result if silences_result.get("success") else {"count": 0, "silences": []}
    return payload


def send_test_alert(name: str = "OpenSREDemoAlert", severity: str = "critical") -> dict:
    """Push a synthetic alert into Alertmanager to prove the chain works.

    Posts to Alertmanager's `/api/v2/alerts`, so it travels the real
    routing path: grouping, inhibition, and the configured webhook
    receiver. The next `sync()` then ingests it into an incident.
    """
    payload = [
        {
            "labels": {
                "alertname": name,
                "severity": severity,
                "namespace": "opensre",
                "pod": "catalog-api-5f86594b59-x5q6n",
                "source": "alertmanager-demo",
            },
            "annotations": {
                "summary": f"Synthetic demo alert {name} injected from the dashboard",
                "description": (
                    "Injected via POST /api/v2/alerts to demonstrate the full "
                    "vmalert -> Alertmanager -> backend -> incident -> RCA chain."
                ),
            },
            "startsAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    ]

    try:
        response = requests.post(f"{_base()}/api/v2/alerts", json=payload, timeout=TIMEOUT)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "test alert injection")

    return {
        "success": True,
        "alertname": name,
        "detail": "Alert accepted by Alertmanager; it will route and sync on the next poll.",
    }
