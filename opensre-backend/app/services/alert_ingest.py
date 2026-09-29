"""Shared alert ingestion path for the host backend.

Both Alertmanager delivery mechanisms funnel through `ingest()`:

1. the push webhook (`POST /api/alerts/alertmanager`), and
2. the pull sync (`app/services/alertmanager.py`), which reads
   Alertmanager's read API because pods cannot reach this host process.

Keeping one implementation is what makes the two behave identically:
each alert is fingerprinted into a lifecycle record (`alert_store`),
announced on the event bus for the UI stream and (optionally) Slack, and
handed to the EXISTING investigation engine --
`investigation.collect_alert_evidence()` to ground it, then
`opensre_cli.investigate()`, the same engine the AI Analysis page and the
Alert Report page already use. No second RCA path is introduced.

Investigations run in a background thread so the webhook always gets a fast
200 and is never held up by a multi-minute LLM run.
"""

import threading

from app.services import alert_events, alert_store, investigation, opensre_cli, slack

# Guards against starting the same investigation twice when Alertmanager
# races a repeat against a resolution.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

# In-flight investigation threads. Tracked so they can be drained (tests,
# and clean shutdown) instead of silently outliving their request.
_inflight: set[threading.Thread] = set()
_inflight_guard = threading.Lock()

# Fingerprints with a worker running RIGHT NOW. `_lock_for` only serialises
# runs -- it does not skip them -- so without this a repeat delivery that
# lands while a slow RCA is still in flight would queue another full
# investigation behind it, and the alert would be investigated once per
# poll for as long as the provider stays slow.
_running: set[str] = set()


def _claim(fingerprint: str) -> bool:
    """Take ownership of a fingerprint, or False if a worker already has it."""
    with _inflight_guard:
        if fingerprint in _running:
            return False
        _running.add(fingerprint)
        return True


def _release(fingerprint: str) -> None:
    with _inflight_guard:
        _running.discard(fingerprint)


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
        if fingerprint not in _locks:
            _locks[fingerprint] = threading.Lock()
        return _locks[fingerprint]


def _run_investigation(record: dict) -> None:
    """Background RCA using the existing engine."""
    fingerprint = record["fingerprint"]

    try:
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
    finally:
        _release(fingerprint)


def _handle_alert(alert: dict, envelope: dict) -> dict:
    record, created = alert_store.upsert(alert, envelope)
    fingerprint = record["fingerprint"]
    status = record["status"]

    if status == "firing":
        # Announce and notify Slack once per lifecycle, but let a repeat
        # firing RETRY the RCA when the previous attempt never produced a
        # usable result (failed on provider quota, or orphaned by a
        # restart). A successful investigation is terminal and is not
        # repeated, so a long-firing alert cannot burn quota in a loop.
        retry = record.get("investigation_status") in alert_store.RETRYABLE_INVESTIGATION_STATES
        if created:
            alert_events.publish("alert.firing", record)
            if not record.get("slack_notified"):
                slack.notify_firing(record)
                alert_store.mark_slack_notified(fingerprint)
        if created or retry:
            alert_store.mark_investigating(fingerprint)
            # Skip if a worker is genuinely in flight for this fingerprint:
            # the per-fingerprint lock only serialises, it does not skip, so
            # a poll landing mid-investigation must not queue a duplicate.
            if _claim(fingerprint):
                _spawn(_run_investigation, record)
        return {"fingerprint": fingerprint, "status": status, "created": created}

    # Resolved
    alert_events.publish("alert.resolved", record)
    if record.get("slack_notified"):
        slack.notify_resolved(record)
        alert_store.mark_slack_notified(fingerprint, "slack_resolved_notified")
    return {"fingerprint": fingerprint, "status": status, "created": created}


def _is_valid(alert: dict) -> str | None:
    """Reason this alert cannot be ingested, or None when it is usable."""
    if not isinstance(alert, dict) or not (alert.get("labels") or alert.get("annotations")):
        return "alert has no labels/annotations"
    if not (alert.get("labels") or {}).get("alertname"):
        return "alert is missing alertname label"
    return None


def ingest(alerts: list, envelope: dict) -> dict:
    """Run a batch of alerts through the shared lifecycle.

    Always returns 200-shaped output for a well-formed envelope: a non-2xx
    would make Alertmanager retry and re-deliver, which we already dedupe.
    One bad alert must never 500 the whole batch.
    """
    results = []
    for alert in alerts:
        reason = _is_valid(alert)
        if reason:
            results.append({"status": "skipped", "error": reason})
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
