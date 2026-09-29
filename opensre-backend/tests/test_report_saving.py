"""Tests for auto-saved alert-driven RCA reports and Alertmanager logs.

Covered here:
- alert-driven investigations save ONE persisted report per alert
  fingerprint (retries update, they do not flood the store);
- failures (e.g. exhausted token) are recorded, not dropped;
- the `/api/alertmanager/reports` endpoint returns those saved reports;
- the `/api/alertmanager/logs` endpoint tails the Alertmanager pod.
"""

from fastapi.testclient import TestClient

from app.main import app
from app.services import alert_store, incident_history, kubectl

client = TestClient(app)

FIRING = {
    "labels": {
        "alertname": "HighLatency",
        "namespace": "opensre",
        "pod": "catalog-api-5f86594b59-x5q6n",
        "severity": "warning",
    },
    "annotations": {"summary": "p99 request latency above 2s"},
    "status": "firing",
    "startsAt": "2026-09-28T08:00:00Z",
}

REPORT_STDOUT = (
    '{"report": "## problem\\ncatalog pod under request pressure", '
    '"root_cause": "connection pool exhausted", '
    '"validity_score": 0.82, '
    '"summary": "p99 crept above 2s", '
    '"impact": "~2% of requests slowed"}'
)


def test_alert_source_saves_dedupe_by_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(incident_history, "STORE_PATH", tmp_path / "incidents.jsonl")

    first = incident_history.save(
        FIRING,
        {"success": False, "error": "OpenSRE LLM provider refused (HTTP 402)", "stderr": "402"},
        source="alert",
    )
    second = incident_history.save(
        FIRING,
        {"success": True, "stdout": REPORT_STDOUT, "stderr": ""},
        source="alert",
    )
    third = incident_history.save(
        FIRING,
        {"success": False, "error": "quota exhausted on retry", "stderr": "402"},
        source="alert",
    )

    data = incident_history.list_incidents(limit=10)["data"]
    # Retries replace the SAME lifecycle: one saved report per alert, and the
    # stable id keeps any `/incident?report=<id>` deep link valid.
    assert len(data) == 1
    assert data[0]["id"] == first["id"]
    assert data[0]["id"] == second["id"]
    assert data[0]["id"] == third["id"]
    assert data[0]["success"] is False
    assert data[0]["error"] == "quota exhausted on retry"
    assert data[0]["fingerprint"] == alert_store.compute_fingerprint(FIRING)
    assert data[0]["alertname"] == "HighLatency"
    assert data[0]["pod"] == "catalog-api-5f86594b59-x5q6n"
    assert data[0]["severity"] == "warning"


def test_successful_retry_report_outcome_is_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(incident_history, "STORE_PATH", tmp_path / "incidents.jsonl")

    incident_history.save(
        FIRING,
        {"success": False, "error": "token exhausted (HTTP 402)", "stderr": "402"},
        source="alert",
    )
    saved = incident_history.save(
        FIRING,
        {"success": True, "stdout": REPORT_STDOUT, "stderr": ""},
        source="alert",
    )

    data = incident_history.list_incidents(limit=5)["data"]
    assert len(data) == 1
    assert data[0]["success"] is True
    assert data[0]["report"]["root_cause"] == "connection pool exhausted"
    assert data[0]["id"] == saved["id"]


def test_non_alert_sources_keep_appending(tmp_path, monkeypatch):
    monkeypatch.setattr(incident_history, "STORE_PATH", tmp_path / "incidents.jsonl")

    incident_history.save({"question": "q1"}, {"success": True, "stdout": ""}, source="investigation")
    incident_history.save({"question": "q2"}, {"success": True, "stdout": ""}, source="investigation")

    assert len(incident_history.list_incidents(limit=10)["data"]) == 2


def test_reports_endpoint_returns_alert_driven_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(incident_history, "STORE_PATH", tmp_path / "incidents.jsonl")

    incident_history.save(
        FIRING,
        {"success": True, "stdout": REPORT_STDOUT, "stderr": ""},
        source="alert",
    )
    incident_history.save(
        FIRING,
        {"success": False, "error": "OpenSRE LLM provider refused (HTTP 402)", "stderr": "402"},
        source="alert",
    )
    incident_history.save(
        {"question": "manual run"},
        {"success": True, "stdout": ""},
        source="investigation",
    )

    response = client.get("/api/alertmanager/reports?limit=10")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["count"] == 1
    report = data["reports"][0]
    assert report["alertname"] == "HighLatency"
    assert report["pod"] == "catalog-api-5f86594b59-x5q6n"
    assert report["success"] is False
    assert "402" in (report["error"] or "")


def test_logs_endpoint_tails_alertmanager_pod(monkeypatch):
    monkeypatch.setattr(
        kubectl,
        "get_first_pod_by_label",
        lambda namespace, label_selector: {"success": True, "stdout": "alertmanager-7d748655bd-4xbxr\n"},
    )
    monkeypatch.setattr(
        kubectl,
        "get_pod_logs_container",
        lambda namespace, pod_name, container, tail=200, previous=False, context=None, timestamps=True: {
            "success": True,
            "stdout": (
                "2026-09-29T07:21:24.327172558Z time=2026-09-29T07:21:24.326Z "
                "level=INFO msg=\"Notify success\" receiver=opensre-backend\n"
                "2026-09-29T07:21:13.820041823Z time=2026-09-29T07:21:13.818Z "
                "level=WARN msg=\"Notify attempt failed, will retry later\" receiver=opensre-backend\n"
            ),
        },
    )

    response = client.get("/api/alertmanager/logs?tail=10")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["pod"] == "alertmanager-7d748655bd-4xbxr"
    assert data["tail"] == 2
    # The duplicate `kubectl --timestamps` prefix is stripped, the AM line
    # itself (with its own time=...) is kept.
    assert data["lines"][0].startswith("time=2026-09-29T07:21:24")
    assert "Notify success" in data["lines"][0]
    assert any("Notify attempt failed" in line for line in data["lines"])


def test_alert_summary_exposes_saved_report_link(tmp_path, monkeypatch):
    monkeypatch.setattr(alert_store, "STORE_PATH", tmp_path / "alerts.jsonl")

    record, _ = alert_store.upsert(FIRING)
    alert_store.update_investigation(
        record["fingerprint"],
        {"success": True, "incident_id": "abc123", "error": None},
    )

    summary = alert_store.list_alerts()["alerts"][0]
    assert summary["incident_id"] == "abc123"
    assert summary["investigation_success"] is True

    failed = alert_store.get_alert(record["fingerprint"])
    alert_store.update_investigation(
        failed["fingerprint"],
        {"success": False, "incident_id": "abc123", "error": "quota exhausted"},
    )
    summary = alert_store.list_alerts()["alerts"][0]
    assert summary["investigation_success"] is False
    assert summary["investigation_error"] == "quota exhausted"