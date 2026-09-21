"""Persistent incident/investigation history.

Every OpenSRE investigation funnels through `opensre_cli.investigate()`,
which auto-saves a compact record here (JSONL, newest-last on disk).
The Incident Report page (`/incident`) reads this back so analyses run
from the AI Analysis dashboard (or anywhere else) remain accessible
later — across reloads and backend restarts.

Only a compact summary + the raw CLI output are stored; full evidence
payloads are intentionally NOT persisted (they are large and mostly
point-in-time snapshots). Records are pruned to MAX_RECORDS.
"""

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
STORE_PATH = DATA_DIR / "incidents.jsonl"

MAX_RECORDS = 200
MAX_STDOUT_CHARS = 20000
MAX_STDERR_CHARS = 4000
MAX_QUESTION_CHARS = 500

_lock = threading.Lock()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "").replace("\r", "")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_report_dict(stdout: str):
    """Mirror of frontend `extractReport`: last balanced JSON object in the
    CLI output that contains a "report" key. Returns the dict or None."""
    text = _strip_ansi(stdout or "")
    last_valid = None
    stack = []
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append(i)
        elif ch == "}":
            if not stack:
                continue
            start = stack.pop()
            try:
                candidate = json.loads(text[start:i + 1])
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(candidate, dict) and "report" in candidate:
                last_valid = candidate
    return last_valid


def describe_target(payload: dict) -> dict:
    """Best-effort (target_type, label, namespace, pod, cluster) from the
    evidence/alert payload handed to `opensre investigate`. Payload shapes
    vary by route (pod / database / stack / nginx / coredns / elk / chat),
    so every lookup is defensive."""
    payload = payload if isinstance(payload, dict) else {}
    target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    pod = payload.get("pod") if isinstance(payload.get("pod"), dict) else {}
    labels = payload.get("labels") if isinstance(payload.get("labels"), dict) else {}

    target_type = target.get("type") or ("pod" if (pod.get("name") or labels.get("pod")) else "unknown")
    name = target.get("name") or target.get("database") or ""
    namespace = (
        pod.get("namespace")
        or target.get("namespace")
        or labels.get("namespace")
        or ""
    )
    pod_name = pod.get("name") or labels.get("pod") or ""
    cluster = payload.get("cluster") or labels.get("cluster") or ""

    if target_type == "pod" and namespace and pod_name:
        label = f"{namespace}/{pod_name}"
    elif target_type == "database" and name:
        label = name.capitalize() if isinstance(name, str) else str(name)
    elif name:
        label = str(name)
    elif namespace and pod_name:
        label = f"{namespace}/{pod_name}"
    else:
        label = str(target_type)

    return {
        "target_type": str(target_type),
        "target_label": label,
        "namespace": namespace or None,
        "pod": pod_name or None,
        "cluster": cluster or None,
    }


def _summarize_report(report: dict) -> dict:
    if not isinstance(report, dict):
        return {}
    summary = {}
    for key in (
        "root_cause", "validity_score", "summary", "impact",
        "evidence", "is_noise", "classification", "confidence",
        "affected_component",
    ):
        if key in report:
            summary[key] = report[key]
    return summary


def save(payload: dict, cli_result: dict, source: str = "investigation") -> dict:
    """Persist one investigation record. Never raises (history must not
    break investigations). Returns the stored record's summary."""
    try:
        return _save(payload, cli_result, source)
    except Exception:
        return {}


def _save(payload: dict, cli_result: dict, source: str) -> dict:
    stdout = _strip_ansi((cli_result or {}).get("stdout") or "")
    stderr = _strip_ansi((cli_result or {}).get("stderr") or "")
    report = extract_report_dict(stdout) or {}
    info = describe_target(payload or {})

    question = (payload or {}).get("question") or ""
    if not isinstance(question, str):
        question = str(question)

    record = {
        "id": uuid.uuid4().hex[:12],
        "ts": _utcnow(),
        "source": source,
        "target_type": info["target_type"],
        "target_label": info["target_label"],
        "namespace": info["namespace"],
        "pod": info["pod"],
        "cluster": info["cluster"],
        "question": question[:MAX_QUESTION_CHARS] or None,
        "success": bool((cli_result or {}).get("success")),
        "returncode": (cli_result or {}).get("returncode"),
        "report": _summarize_report(report),
        "stdout": stdout[-MAX_STDOUT_CHARS:],
        "stderr": stderr[-MAX_STDERR_CHARS:] or None,
    }

    with _lock:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with STORE_PATH.open("a") as handle:
                handle.write(json.dumps(record) + "\n")
            _prune_locked()
        except OSError:
            pass
    return _to_summary(record)


def _prune_locked() -> None:
    """Keep only the newest MAX_RECORDS lines (caller holds _lock)."""
    try:
        with STORE_PATH.open() as handle:
            lines = [ln for ln in handle if ln.strip()]
    except FileNotFoundError:
        return
    except OSError:
        return
    if len(lines) <= MAX_RECORDS:
        return
    tmp = STORE_PATH.with_suffix(".tmp")
    try:
        with tmp.open("w") as handle:
            handle.writelines(lines[-MAX_RECORDS:])
        tmp.replace(STORE_PATH)
    except OSError:
        pass


def _read_all() -> list:
    try:
        with STORE_PATH.open() as handle:
            records = []
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue
            return records
    except FileNotFoundError:
        return []
    except OSError:
        return []


def _to_summary(record: dict) -> dict:
    stdout = record.get("stdout") or ""
    return {
        "id": record.get("id"),
        "ts": record.get("ts"),
        "source": record.get("source"),
        "target_type": record.get("target_type"),
        "target_label": record.get("target_label"),
        "namespace": record.get("namespace"),
        "pod": record.get("pod"),
        "cluster": record.get("cluster"),
        "question": record.get("question"),
        "success": record.get("success"),
        "report": record.get("report") or {},
        "stdout_preview": stdout[:800],
    }


def list_incidents(limit: int = 100) -> dict:
    """Newest-first summaries (stdout truncated to a preview)."""
    with _lock:
        records = _read_all()
    records.reverse()
    return {"success": True, "data": [_to_summary(r) for r in records[:limit]]}


def get_incident(incident_id: str) -> dict:
    with _lock:
        records = _read_all()
    for record in records:
        if record.get("id") == incident_id:
            return {"success": True, "data": record}
    return {"success": False, "error": f"Incident '{incident_id}' not found"}


def delete_incident(incident_id: str) -> dict:
    with _lock:
        records = _read_all()
        kept = [r for r in records if r.get("id") != incident_id]
        if len(kept) == len(records):
            return {"success": False, "error": f"Incident '{incident_id}' not found"}
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with STORE_PATH.open("w") as handle:
                for record in kept:
                    handle.write(json.dumps(record) + "\n")
        except OSError as exc:
            return {"success": False, "error": str(exc)}
    return {"success": True, "id": incident_id}


def clear() -> dict:
    with _lock:
        try:
            if STORE_PATH.exists():
                STORE_PATH.unlink()
        except OSError as exc:
            return {"success": False, "error": str(exc)}
    return {"success": True}
