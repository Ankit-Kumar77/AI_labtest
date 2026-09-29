"""Tests for the Alertmanager read API client and the pull-side alert sync.

The regression these guard: Alertmanager's webhook targets the IN-CLUSTER
backend, but the dashboard is served by the HOST backend, which pods cannot
reach. The host backend therefore has to PULL alert state and feed it
through the same ingestion path the webhook uses. If the mapping between
Alertmanager's `/api/v2/alerts` shape and the webhook shape is wrong, the
host store silently stays empty and the dashboard shows no incidents.

The HTTP layer is mocked; the point is the mapping, the dedup behaviour and
the fact that both delivery paths converge on one lifecycle.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.routes import alerts
from app.services import alert_events, alert_ingest, alert_store
from app.services import alertmanager as am

client = TestClient(app)


def _am_alert(
    alertname="HighLatency",
    state="active",
    pod="catalog-api-abc",
    fingerprint="fp-1",
):
    """An entry shaped like Alertmanager's /api/v2/alerts output."""
    return {
        "labels": {
            "alertname": alertname,
            "severity": "warning",
            "namespace": "opensre",
            "pod": pod,
        },
        "annotations": {"summary": f"{alertname} on {pod}"},
        "startsAt": "2026-09-29T06:00:00Z",
        "endsAt": "2026-09-29T07:00:00Z",
        "fingerprint": fingerprint,
        "generatorURL": f"http://vmalert:8880/{fingerprint}",
        "receivers": [{"name": "opensre-backend"}],
        "status": {"state": state},
    }


class _Resp:
    def __init__(self, payload, text="OK"):
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Temp store, stubbed investigation, and a clean alert base URL."""
    store = tmp_path / "alerts.jsonl"
    monkeypatch.setattr(alert_store, "STORE_PATH", store)
    monkeypatch.setattr(settings, "ALERTMANAGER_URL", "http://alertmanager.test:9093")

    triggered = []
    monkeypatch.setattr(
        alert_ingest.investigation, "collect_alert_evidence",
        lambda alert, **kw: {"success": True, "payload": alert},
    )
    monkeypatch.setattr(
        alert_ingest.opensre_cli, "investigate",
        lambda payload, source=None: triggered.append(payload)
        or {"success": True, "report": {"summary": "mock RCA"}},
    )

    yield {"store": store, "triggered": triggered}

    alerts.wait_for_investigations(timeout=10)


def _stub_alerts(monkeypatch, entries):
    """Make the alerts endpoint return `entries`, other reads succeed."""
    monkeypatch.setattr(
        am, "_get",
        lambda path, **kw: _Resp(entries if path == "/api/v2/alerts" else []),
    )


# --------------------------------------------------------------------------
# Shape mapping
# --------------------------------------------------------------------------

def test_active_state_maps_to_firing():
    assert am._to_ingest_alert(_am_alert(state="active"))["status"] == "firing"


def test_non_active_state_maps_to_resolved():
    assert am._to_ingest_alert(_am_alert(state="suppressed"))["status"] == "resolved"


def test_mapping_preserves_identity_fields():
    """The record must carry through what the UI groups and links on."""
    mapped = am._to_ingest_alert(_am_alert(alertname="HighErrorRate", fingerprint="fp-9"))

    assert mapped["fingerprint"] == "fp-9"
    assert mapped["labels"]["alertname"] == "HighErrorRate"
    assert mapped["labels"]["pod"] == "catalog-api-abc"
    assert mapped["annotations"]["summary"] == "HighErrorRate on catalog-api-abc"


def test_entry_without_status_defaults_to_firing():
    entry = _am_alert()
    entry.pop("status")
    assert am._to_ingest_alert(entry)["status"] == "firing"


# --------------------------------------------------------------------------
# Pull sync -> incident store
# --------------------------------------------------------------------------

def test_sync_ingests_polled_alerts_into_incidents(monkeypatch, isolated_store):
    _stub_alerts(monkeypatch, [_am_alert()])

    result = am.sync()

    assert result["success"] is True
    assert result["polled"] == 1
    assert result["firing"] == 1
    assert result["new_incidents"] == 1
    assert alert_store.active_alerts()["count"] == 1
    assert alert_store.list_alerts()["alerts"][0]["alertname"] == "HighLatency"


def test_sync_triggers_investigation(isolated_store, monkeypatch):
    """Polled alerts must drive the same RCA engine the webhook does."""
    _stub_alerts(monkeypatch, [_am_alert()])

    am.sync()
    alerts.wait_for_investigations(timeout=10)

    assert len(isolated_store["triggered"]) == 1
    assert isolated_store["triggered"][0]["labels"]["alertname"] == "HighLatency"


def test_repeat_sync_does_not_duplicate_incidents(monkeypatch, isolated_store):
    """Polling every 20s must not create one incident per poll."""
    _stub_alerts(monkeypatch, [_am_alert()])

    for _ in range(4):
        am.sync()

    assert alert_store.list_alerts()["total"] == 1
    assert alert_store.list_alerts()["alerts"][0]["notification_count"] == 4
    alerts.wait_for_investigations(timeout=10)
    assert len(isolated_store["triggered"]) == 1


def test_sync_closes_incident_when_alert_clears(monkeypatch):
    _stub_alerts(monkeypatch, [_am_alert()])
    fingerprint = am.sync()["results"][0]["fingerprint"]
    assert alert_store.get_alert(fingerprint)["status"] == "firing"

    # Alertmanager no longer lists it (or lists it resolved).
    _stub_alerts(monkeypatch, [_am_alert(state="suppressed")])
    am.sync()

    assert alert_store.get_alert(fingerprint)["status"] == "resolved"
    assert alert_store.active_alerts()["count"] == 0


def test_sync_with_no_alerts_is_a_no_op(monkeypatch):
    _stub_alerts(monkeypatch, [])

    result = am.sync()

    assert result["success"] is True
    assert result["polled"] == 0
    assert alert_store.list_alerts()["total"] == 0


def test_sync_reports_unreachable_without_raising(monkeypatch):
    def boom(path, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(am, "_get", boom)

    result = am.sync()

    assert result["success"] is False
    assert result["unreachable"] is True
    assert "connection refused" in result["error"]


def test_concurrent_syncs_do_not_double_count(monkeypatch):
    """A manual Sync now must not race the background loop."""
    import threading

    entered = threading.Event()
    release = threading.Event()

    def slow_get(path, **kw):
        entered.set()
        release.wait(timeout=5)
        return _Resp([_am_alert()])

    monkeypatch.setattr(am, "_get", slow_get)

    first = threading.Thread(target=am.sync)
    first.start()
    assert entered.wait(timeout=5), "first sync never reached the API"

    second = am.sync()
    release.set()
    first.join(timeout=10)

    assert second.get("skipped") is True
    assert alert_store.list_alerts()["total"] == 1


# --------------------------------------------------------------------------
# Both delivery paths share one lifecycle
# --------------------------------------------------------------------------

def test_webhook_and_sync_share_the_same_fingerprint(monkeypatch, isolated_store):
    """A sync must not open a second incident for an alert the webhook filed."""
    webhook_alert = {
        "labels": {"alertname": "HighLatency", "pod": "catalog-api-abc", "namespace": "opensre"},
        "annotations": {"summary": "HighLatency on catalog-api-abc"},
        "status": "firing",
        "startsAt": "2026-09-29T06:00:00Z",
        "fingerprint": "fp-1",
    }
    client.post("/api/alerts/alertmanager", json={"alerts": [webhook_alert]})
    alerts.wait_for_investigations(timeout=10)

    _stub_alerts(monkeypatch, [_am_alert()])
    result = am.sync()

    assert result["new_incidents"] == 0
    assert alert_store.list_alerts()["total"] == 1
    assert len(isolated_store["triggered"]) == 1


# --------------------------------------------------------------------------
# Status parsing
# --------------------------------------------------------------------------

def test_status_parses_route_from_raw_config(monkeypatch):
    """The v2 status API only exposes the config as raw YAML."""
    raw = (
        "global:\n  resolve_timeout: 5m\n"
        "route:\n  receiver: opensre-backend\n"
        "  group_by: [alertname, namespace, pod]\n"
        "  group_wait: 10s\n  repeat_interval: 1m\n"
        "receivers:\n  - name: opensre-backend\n    webhook_configs:\n      - url: http://x\n"
        "inhibit_rules:\n  - source_matchers:\n      - severity = critical\n"
    )
    monkeypatch.setattr(
        am, "_get",
        lambda path, **kw: _Resp(
            {
                "cluster": {"name": "c1", "status": "ready", "peers": [{"name": "c1"}]},
                "config": {"original": raw},
                "uptime": "2026-09-29T06:00:00Z",
                "versionInfo": {"version": "0.28.1"},
            }
        ),
    )

    result = am.status()

    assert result["route"]["receiver"] == "opensre-backend"
    assert result["route"]["group_by"] == ["alertname", "namespace", "pod"]
    assert result["route"]["repeat_interval"] == "1m"
    assert result["receivers"] == [{"name": "opensre-backend", "webhooks": 1}]
    assert result["inhibit_rules"] == 1
    assert result["version"] == "0.28.1"


def test_status_survives_unparseable_config(monkeypatch):
    monkeypatch.setattr(
        am, "_get",
        lambda path, **kw: _Resp({"cluster": {}, "config": {"original": "::: not yaml ["}}),
    )

    result = am.status()

    assert result["success"] is True
    assert result["route"]["receiver"] is None
    assert result["receivers"] == []


# --------------------------------------------------------------------------
# Overview / demo endpoints
# --------------------------------------------------------------------------

def test_overview_reports_unreachable_with_repair_command(monkeypatch):
    """The page must tell the user exactly what to run when AM is down."""
    def boom(path, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(am, "_get", boom)
    monkeypatch.setattr(am.portforward, "is_alive", lambda target: False)

    result = am.overview()

    assert result["success"] is False
    assert "port-forward" in result["detail"]
    assert "9093:9093" in result["port_forward"]["command"]


def test_overview_includes_alerts_and_silences(monkeypatch):
    monkeypatch.setattr(
        am, "_get",
        lambda path, **kw: {
            "/-/healthy": _Resp(None, "OK"),
            "/api/v2/alerts": _Resp([_am_alert()]),
            "/api/v2/silences": _Resp([{"id": "s1", "comment": "planned", "createdBy": "demo"}]),
            "/api/v2/status": _Resp({"cluster": {}, "config": {"original": ""}}),
        }[path],
    )
    monkeypatch.setattr(am.portforward, "is_alive", lambda target: True)

    result = am.overview()

    assert result["success"] is True
    assert result["health"]["status"] == "OK"
    assert result["alerts"]["count"] == 1
    assert result["silences"]["count"] == 1
    assert result["port_forward"]["alive"] is True


def test_test_alert_posts_the_expected_payload(monkeypatch):
    captured = {}

    class _Post:
        def raise_for_status(self):
            return None

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _Post()

    monkeypatch.setattr(am.requests, "post", fake_post)

    result = am.send_test_alert()

    assert result["success"] is True
    assert captured["url"].endswith("/api/v2/alerts")
    entry = captured["payload"][0]
    assert entry["labels"]["alertname"] == "OpenSREDemoAlert"
    assert entry["labels"]["namespace"] == "opensre"
    assert entry["annotations"]["summary"]


def test_test_alert_reports_failure(monkeypatch):
    def boom(url, json=None, timeout=None):
        raise OSError("nope")

    monkeypatch.setattr(am.requests, "post", boom)

    result = am.send_test_alert()

    assert result["success"] is False
    assert result["unreachable"] is True


def test_sync_endpoint_returns_incidents(monkeypatch):
    _stub_alerts(monkeypatch, [_am_alert()])

    body = client.post("/api/alertmanager/sync").json()

    assert body["polled"] == 1
    assert body["incidents"]["count"] == 1
    alerts.wait_for_investigations(timeout=10)


def test_overview_endpoint_is_served(monkeypatch):
    monkeypatch.setattr(
        am, "_get",
        lambda path, **kw: {
            "/-/healthy": _Resp(None, "OK"),
            "/api/v2/alerts": _Resp([]),
            "/api/v2/silences": _Resp([]),
            "/api/v2/status": _Resp({"cluster": {}, "config": {"original": ""}}),
        }[path],
    )
    monkeypatch.setattr(am.portforward, "is_alive", lambda target: True)

    body = client.get("/api/alertmanager/overview").json()

    assert body["success"] is True
    assert body["alerts"]["count"] == 0


# --------------------------------------------------------------------------
# Shutdown flag (SSE must not block uvicorn --reload)
# --------------------------------------------------------------------------

def test_shutdown_flag_round_trips():
    assert alert_events.is_shutting_down() is False
    alert_events.begin_shutdown()
    try:
        assert alert_events.is_shutting_down() is True
    finally:
        alert_events.reset_shutdown()
    assert alert_events.is_shutting_down() is False
