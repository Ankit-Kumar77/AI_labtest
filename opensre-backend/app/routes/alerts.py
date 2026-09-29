"""Alertmanager webhook receiver.

Alertmanager (in-cluster) posts here over Kubernetes Service DNS. Each
alert in the envelope is validated, fingerprinted into a lifecycle record,
announced on the event bus, and handed to the existing investigation
engine -- all of that lives in `app/services/alert_ingest.py`, which the
pull-side sync also uses so both delivery paths behave identically.

This module is the HTTP edge only: parse, validate, delegate.
"""

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services import alert_events, alert_ingest, alert_store

router = APIRouter(
    prefix="/api/alerts",
    tags=["Alerts"],
)

# Re-exported so tests and callers keep a single drain entrypoint.
wait_for_investigations = alert_ingest.wait_for_investigations

# How often an idle SSE stream wakes to re-check the shutdown flag. Short
# enough that a `--reload` restart is not noticeably delayed.
KEEPALIVE_SECONDS = 2


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

    return alert_ingest.ingest(alerts, envelope)


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
                # An SSE stream is open by design, so uvicorn's graceful
                # shutdown would block on it forever. Poll the shutdown
                # flag often enough that a --reload restart is quick.
                if alert_events.is_shutting_down():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
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
