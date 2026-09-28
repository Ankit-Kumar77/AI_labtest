"""Alert lifecycle store for Alertmanager-driven incidents.

Alertmanager is chatty: the same alert is re-sent every `repeat_interval`
while firing, and once more when it resolves. This module keeps ONE
incident per alert fingerprint so repeats update the existing record
instead of flooding the UI with duplicates.

Persisted as JSONL (newest-last on disk) with the same conventions as
`incident_history.py`: a single threading lock, an on-disk prune, and
compact summaries for list views.

Records are keyed by `fingerprint` (taken from the Alertmanager payload
when present, otherwise derived from the label set so it stays stable
across restarts). Raw payloads are intentionally NOT persisted.
"""

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
STORE_PATH = DATA_DIR / "alerts.jsonl"

MAX_RECORDS = 200
MAX_ANNOTATION_CHARS = 1000

_lock = threading.Lock()

STATUS_FIRING = "firing"
STATUS_RESOLVED = "resolved"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_fingerprint(alert: dict) -> str:
    """Stable fingerprint for an alert.

    Prefer Alertmanager's own fingerprint so our identity matches theirs.
    Fall back to a hash over the label set (plus alertname), which is what
    Alertmanager itself keys on -- the name/namespace/pod triple is
    enough to separate distinct firing conditions of the same service.
    """
    provided = (alert or {}).get("fingerprint")
    if provided:
        return str(provided)

    labels = (alert or {}).get("labels") or {}
    parts = [f"{k}={labels[k]}" for k in sorted(labels)]
    if not parts:
        parts = [f"alertname={(alert or {}).get('alertname', 'unknown')}"]

    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


def _clip(value) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    text = text.strip()
    if len(text) > MAX_ANNOTATION_CHARS:
        return text[:MAX_ANNOTATION_CHARS] + "..."
    return text


def _build_record(alert: dict, envelope: dict, fingerprint: str) -> dict:
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}

    return {
        "id": fingerprint,
        "fingerprint": fingerprint,
        "status": STATUS_FIRING,
        "alertname": labels.get("alertname") or alert.get("alertname") or "UnknownAlert",
        "severity": labels.get("severity") or "warning",
        "namespace": labels.get("namespace") or "",
        "pod": labels.get("pod") or "",
        "node": labels.get("node") or "",
        "container": labels.get("container") or "",
        "instance": labels.get("instance") or "",
        "summary": _clip(annotations.get("summary") or annotations.get("description") or ""),
        "description": _clip(annotations.get("description") or ""),
        "labels": dict(labels),
        "annotations": dict(annotations),
        "generator_url": alert.get("generatorURL") or envelope.get("generatorURL") or "",
        "source": envelope.get("source") or "alertmanager",
        "first_seen": _utcnow(),
        "last_seen": _utcnow(),
        "starts_at": alert.get("startsAt") or "",
        "ends_at": alert.get("endsAt") or "",
        "notification_count": 1,
        "investigation_status": "pending",
        "investigation": None,
        "slack_notified": False,
    }


def _read_all() -> list:
    if not STORE_PATH.exists():
        return []

    records = []
    try:
        with STORE_PATH.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []

    return records


def _write_all(records: list) -> None:
    """Atomically replace the store file.

    A plain truncating write would let a concurrent reader (a background
    investigation thread, or a UI poll) observe a half-written -- or
    momentarily empty -- file and then clobber the record set. Writing to
    a temp file and renaming makes the swap instant and all-or-nothing.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    tmp_path = STORE_PATH.with_suffix(".jsonl.tmp")

    with tmp_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")

    os.replace(tmp_path, STORE_PATH)


def _prune_locked(records: list) -> list:
    if len(records) > MAX_RECORDS:
        records = records[-MAX_RECORDS:]
    return records


def upsert(alert: dict, envelope: dict | None = None) -> tuple[dict, bool]:
    """Record one alert, deduplicating by fingerprint.

    Returns ``(record, created)`` where ``created`` is True only for the
    first time a fingerprint is seen (or when a resolved alert fires
    again), so callers can trigger investigations and notifications once
    per lifecycle instead of once per webhook repeat.
    """
    envelope = envelope or {}
    fingerprint = compute_fingerprint(alert)
    resolved = str(alert.get("status", STATUS_FIRING)).lower() == STATUS_RESOLVED

    with _lock:
        records = _read_all()
        existing = None
        for record in reversed(records):
            if record.get("fingerprint") == fingerprint:
                existing = record
                break

        if existing is None:
            record = _build_record(alert, envelope, fingerprint)
            if resolved:
                record["status"] = STATUS_RESOLVED
                record["ends_at"] = alert.get("endsAt") or _utcnow()
                record["investigation_status"] = "skipped"
                record["slack_notified"] = False
            records.append(record)
            _write_all(_prune_locked(records))
            return record, True

        if resolved:
            # Resolution is terminal for this lifecycle: never re-open an
            # incident that Alertmanager has already told us is healthy.
            if existing.get("status") == STATUS_RESOLVED:
                existing["last_seen"] = _utcnow()
                existing["ends_at"] = alert.get("endsAt") or existing.get("ends_at") or _utcnow()
                _replace(records, existing)
                return existing, False

            existing["status"] = STATUS_RESOLVED
            existing["last_seen"] = _utcnow()
            existing["ends_at"] = alert.get("endsAt") or _utcnow()
            existing["notification_count"] = existing.get("notification_count", 0) + 1
            # Only notify Slack about recovery if we ever announced the fire.
            existing["slack_resolved_notified"] = False
            _replace(records, existing)
            return existing, True

        # Repeat of an already-firing alert: update in place, no new incident.
        if existing.get("status") == STATUS_FIRING:
            existing["last_seen"] = _utcnow()
            existing["notification_count"] = existing.get("notification_count", 0) + 1
            annotations = alert.get("annotations") or {}
            if annotations.get("summary"):
                existing["summary"] = _clip(annotations["summary"])
            _replace(records, existing)
            return existing, False

        # Was resolved, now firing again -> new incident lifecycle.
        record = _build_record(alert, envelope, fingerprint)
        records.append(record)
        _write_all(_prune_locked(records))
        return record, True


def _replace(records: list, record: dict) -> None:
    for index, candidate in enumerate(records):
        if candidate.get("fingerprint") == record.get("fingerprint"):
            records[index] = record
            break
    _write_all(_prune_locked(records))


def update_investigation(fingerprint: str, result: dict) -> dict | None:
    """Attach the OpenSRE investigation result to a stored alert."""
    with _lock:
        records = _read_all()
        for record in records:
            if record.get("fingerprint") == fingerprint:
                record["investigation_status"] = "resolved" if result.get("success") else "failed"
                record["investigation"] = result
                _replace(records, record)
                return record
    return None


def mark_slack_notified(fingerprint: str, field: str = "slack_notified") -> dict | None:
    with _lock:
        records = _read_all()
        for record in records:
            if record.get("fingerprint") == fingerprint:
                record[field] = True
                _replace(records, record)
                return record
    return None


def get_alert(fingerprint: str) -> dict | None:
    # Take the lock: a background investigation thread may be rewriting the
    # store concurrently, and an unlocked read could see it mid-swap.
    with _lock:
        records = _read_all()
        for record in reversed(records):
            if record.get("fingerprint") == fingerprint:
                return record
    return None


def _to_summary(record: dict) -> dict:
    investigation = record.get("investigation") or {}
    return {
        "id": record.get("fingerprint"),
        "fingerprint": record.get("fingerprint"),
        "status": record.get("status"),
        "alertname": record.get("alertname"),
        "severity": record.get("severity"),
        "namespace": record.get("namespace"),
        "pod": record.get("pod"),
        "container": record.get("container"),
        "summary": record.get("summary"),
        "first_seen": record.get("first_seen"),
        "last_seen": record.get("last_seen"),
        "notification_count": record.get("notification_count"),
        "investigation_status": record.get("investigation_status"),
        "has_investigation": bool(investigation),
    }


def list_alerts(limit: int = 100) -> dict:
    with _lock:
        records = _read_all()
    return {
        "success": True,
        "count": min(len(records), limit),
        "total": len(records),
        "alerts": [_to_summary(r) for r in reversed(records)][:limit],
    }


def active_alerts() -> dict:
    with _lock:
        records = [r for r in _read_all() if r.get("status") == STATUS_FIRING]
    return {
        "success": True,
        "count": len(records),
        "alerts": [_to_summary(r) for r in reversed(records)],
    }


def clear() -> dict:
    with _lock:
        removed = len(_read_all())
        _write_all([])
    return {"success": True, "deleted": removed}
