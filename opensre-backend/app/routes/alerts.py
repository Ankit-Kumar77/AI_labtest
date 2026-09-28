"""Alertmanager webhook receiver.

Alertmanager (in-cluster) posts here over Kubernetes Service DNS. Each
alert in the envelope is:

1. validated and fingerprinted into a lifecycle record (`alert_store`),
2. announced on the event bus for the UI stream and (optionally) Slack,
3. handed to the EXISTING investigation engine -- `investigation.
   collect_alert_evidence()` to ground it, then `opensre_cli.investigate()`,
   which is the same engine the AI Analysis page and the Alert Report page
   already use. No second RCA path is introduced.

Investigations run in a background thread so Alertmanager's webhook always
gets a fast 200 and is never held up by a multi-minute LLM run.
"""

import asyncio
import json
import threading

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services import alert_events, alert_store, investigation, opensre_cli, slack

router = APIRouter(
    prefix="/api/alerts",
    tags=["Alerts"],
)

# Guards against starting the same investigation twice when Alertmanager
# races a repeat against a resolution.
_investigation_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

# In-flight investigation threads. Tracked so they can be drained (tests,
# and clean shutdown) instead of silently outliving their request.
_inflight: set[threading.Thread] = set()
_inflight_guard = threading.Lock()


def _spawn(target, *args) -> None:
    thread = threading.Thread(target=target, args=args, daemon=True)
    with _inflight_guard:
        _inflight.add(thread)
    thread.start()


def wait_for_investigations(timeout: float = 30.0) -> None:
    """Block until in-flight investigations finish (best effort)."""
    with _inflight_guard:
        threads = list(_inflight)
    for thread in threads:
        thread.join(timeout=timeout)
    # Materialize before discarding: difference_update() mutates the very
    # set a lazy generator would be iterating.
    with _inflight_guard:
        finished = [t for t in list(_inflight) if not t.is_alive()]
    with _inflight_guard:
        _inflight.difference_update(finished)


def _lock_for(fingerprint: str) -> threading.Lock:
    with _locks_guard:
        if fingerprint not in _investigation_locks:
            _investigation_locks[fingerprint] = threading.Lock()
        return _investigation_locks[fingerprint]


def _run_investigation(record: dict) -> None:
    """Background RCA using the existing engine."""
    fingerprint = record["fingerprint"]

    with _lock_for(fingerprint):
        # Re-read: the alert may have resolved while we were queued.
        current = alert_store.get_alert(fingerprint)
        if current is None or current.get("status") != "firing":
            return

        alert = {
            "labels": current.get("labels") or {},
            "annotations": current.get("annotations") or {},
            "status": "firing",
            "startsAt": current.get("starts_at") or "",
        }

        try:
            evidence = investigation.collect_alert_evidence(alert)

            if not evidence.get("success"):
                alert_store.update_investigation(
                    fingerprint,
                    {"success": False, "error": evidence.get("error", "evidence collection failed")},
                )
                return

            result = opensre_cli.investigate(evidence["payload"], source="alert")
            alert_store.update_investigation(fingerprint, result)
        except Exception as exc:  # noqa: BLE001 - never kill the worker
            alert_store.update_investigation(fingerprint, {"success": False, "error": str(exc)})

        alert_events.publish("alert.investigated", alert_store.get_alert(fingerprint) or record)


def _handle_alert(alert: dict, envelope: dict) -> dict:
    record, created = alert_store.upsert(alert, envelope)
    fingerprint = record["fingerprint"]
    status = record["status"]

    if status == "firing":
        if created:
            alert_events.publish("alert.firing", record)
            slack.notify_firing(record)
            alert_store.mark_slack_notified(fingerprint)
            _spawn(_run_investigation, record)
        return {"fingerprint": fingerprint, "status": status, "created": created}

    # Resolved
    alert_events.publish("alert.resolved", record)
    if record.get("slack_notified"):
        slack.notify_resolved(record)
        alert_store.mark_slack_notified(fingerprint, "slack_resolved_notified")
    return {"fingerprint": fingerprint, "status": status, "created": created}


@router.post("/alertmanager")
async def alertmanager_webhook(request: Request):
    """Receive an Alertmanager webhook envelope.

    Always returns 200 for a well-formed envelope: a non-2xx would make
    Alertmanager retry and re-deliver, which we already dedupe.
    """
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"success": False, "error": "invalid JSON body"}

    if not isinstance(payload, dict):
        return {"success": False, "error": "expected a JSON object"}

    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not alerts:
        return {"success": False, "error": "alerts must be a non-empty list"}

    envelope = {
        "source": "alertmanager",
        "receiver": payload.get("receiver"),
        "groupLabels": payload.get("groupLabels") or {},
        "commonLabels": payload.get("commonLabels") or {},
        "commonAnnotations": payload.get("commonAnnotations") or {},
        "externalURL": payload.get("externalURL"),
        "status": payload.get("status"),
    }

    results = []
    for alert in alerts:
        if not isinstance(alert, dict) or not (
            alert.get("labels") or alert.get("annotations")
        ):
            results.append({"status": "skipped", "error": "alert has no labels/annotations"})
            continue

        # Rule out structurally empty alerts before touching the store.
        if not (alert.get("labels") or {}).get("alertname"):
            results.append({"status": "skipped", "error": "alert is missing alertname label"})
            continue

        try:
            results.append(_handle_alert(alert, envelope))
        except Exception as exc:  # noqa: BLE001 - one bad alert must not 500 the batch
            results.append({"status": "error", "error": str(exc)})

    return {
        "success": True,
        "received": len(alerts),
        "processed": len(results),
        "results": results,
    }


@router.get("")
def list_alerts(limit: int = 100):
    """Newest-first alert lifecycle records."""
    return alert_store.list_alerts(limit=max(1, min(limit, 500)))


@router.get("/active")
def active_alerts():
    """Currently firing alerts only."""
    return alert_store.active_alerts()


@router.get("/stream")
async def stream():
    """Server-sent events for the frontend notification center.

    One-way is sufficient (alerts originate server-side), so SSE is
    preferred over WebSockets here.
    """
    queue = alert_events.subscribe()

    async def generator():
        try:
            # Prime the client with recent events so a fresh page load is
            # not blank, then hand over to the live feed.
            for event in alert_events.recent():
                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    # Keep-alive comment so proxies do not drop the stream.
                    yield ": keep-alive\n\n"
                    continue

                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        finally:
            alert_events.unsubscribe(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{fingerprint}")
def get_alert(fingerprint: str):
    record = alert_store.get_alert(fingerprint)
    if record is None:
        return {"success": False, "error": "alert not found", "fingerprint": fingerprint}
    return {"success": True, "alert": record}


@router.delete("")
def clear_alerts():
    return alert_store.clear()
