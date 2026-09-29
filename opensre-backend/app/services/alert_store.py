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

# Alertmanager re-fires a firing alert every `repeat_interval` (1m in
# infra/k8s/alerting/alertmanager.yaml). Three missed repeats means
# Alertmanager is no longer telling us about the alert, so the record is
# stale and keeping it `firing` would pin a phantom incident to the UI.
STALE_FIRING_SECONDS = 180

_lock = threading.Lock()

STATUS_FIRING = "firing"
STATUS_RESOLVED = "resolved"

# Investigation lifecycle. `pending` is only ever a transient state that a
# live worker thread owns; if a process dies mid-investigation nothing will
# ever move it on, so startup recovery rewrites it to `interrupted`.
INVESTIGATION_PENDING = "pending"
INVESTIGATION_INTERRUPTED = "interrupted"
INVESTIGATION_RESOLVED = "resolved"
INVESTIGATION_FAILED = "failed"
INVESTIGATION_SKIPPED = "skipped"

# States that mean "the last attempt did not produce a usable RCA", so a
# repeat firing is allowed to retry instead of being silently ignored.
RETRYABLE_INVESTIGATION_STATES = frozenset(
    {INVESTIGATION_PENDING, INVESTIGATION_FAILED, INVESTIGATION_INTERRUPTED}
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value) -> datetime | None:
    """Best-effort RFC3339/ISO-8601 parse; None if unusable."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


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
        "investigation_status": INVESTIGATION_PENDING,
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
                record["investigation_status"] = INVESTIGATION_SKIPPED
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


def mark_investigating(fingerprint: str) -> dict | None:
    """Flag an alert as having a live worker before the thread starts.

    Persisting `pending` up front means a crash mid-run is visible as
    orphaned state (see `recover_orphaned_investigations`) rather than
    leaving the previous attempt's result looking current.
    """
    with _lock:
        records = _read_all()
        for record in records:
            if record.get("fingerprint") == fingerprint:
                record["investigation_status"] = INVESTIGATION_PENDING
                record["investigation"] = None
                _replace(records, record)
                return record
    return None


def update_investigation(fingerprint: str, result: dict) -> dict | None:
    """Attach the OpenSRE investigation result to a stored alert."""
    with _lock:
        records = _read_all()
        for record in records:
            if record.get("fingerprint") == fingerprint:
                record["investigation_status"] = (
                    INVESTIGATION_RESOLVED if result.get("success") else INVESTIGATION_FAILED
                )
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
        # The saved-report link the dashboard needs: the persisted incident
        # id of the latest RCA run, plus the outcome for the table badge.
        "incident_id": investigation.get("incident_id") if investigation else None,
        "investigation_success": bool(investigation.get("success")) if investigation else None,
        "investigation_error": (investigation.get("error") or None) if investigation else None,
    }


def _age_seconds(record: dict, now: datetime) -> float | None:
    """Seconds since we last heard about this alert, or None if unknown."""
    seen = _parse_ts(record.get("last_seen")) or _parse_ts(record.get("first_seen"))
    if seen is None:
        return None
    return (now - seen).total_seconds()


def recover_orphaned_investigations() -> list[dict]:
    """Rewrite `pending` investigations that no live worker owns.

    `_run_investigation()` runs in a daemon thread, so if the process dies
    (rollout, crash, node restart) a record persisted as `pending` is left
    pointing at a worker that no longer exists. Nothing else ever advances
    it, and repeat firings do not re-investigate, so it would sit at
    `pending` forever. Mark those `interrupted` so the next repeat firing
    can retry.
    """
    reaped = []
    with _lock:
        records = _read_all()
        dirty = False
        for record in records:
            if record.get("investigation_status") != INVESTIGATION_PENDING:
                continue
            record["investigation_status"] = INVESTIGATION_INTERRUPTED
            if not record.get("investigation"):
                record["investigation"] = {
                    "success": False,
                    "error": "investigation was interrupted before it completed "
                             "(backend restarted mid-run)",
                }
            reaped.append(record)
            dirty = True
        if dirty:
            _write_all(records)
    return reaped


def reap_stale_firing(
    stale_seconds: float = STALE_FIRING_SECONDS,
    now: datetime | None = None,
) -> list[dict]:
    """Auto-resolve firing alerts that Alertmanager stopped re-delivering.

    We only ever learn an alert resolved from a webhook. If the resolution
    is missed -- backend down, or Alertmanager restarting and losing its
    state -- the record stays `firing` forever and the dashboard shows a
    phantom incident that no longer exists. Because Alertmanager re-fires
    every `repeat_interval`, silence past `stale_seconds` means it is no
    longer firing, so close the lifecycle here.
    """
    now = now or datetime.now(timezone.utc)
    reaped = []
    with _lock:
        records = _read_all()
        dirty = False
        for record in records:
            if record.get("status") != STATUS_FIRING:
                continue
            age = _age_seconds(record, now)
            # An unparseable timestamp is not evidence of staleness; leave it.
            if age is None or age < stale_seconds:
                continue
            record["status"] = STATUS_RESOLVED
            record["ends_at"] = record.get("ends_at") or _utcnow()
            record["auto_resolved"] = True
            if record.get("investigation_status") == INVESTIGATION_PENDING:
                record["investigation_status"] = INVESTIGATION_INTERRUPTED
            reaped.append(record)
            dirty = True
        if dirty:
            _write_all(records)
    return reaped


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
