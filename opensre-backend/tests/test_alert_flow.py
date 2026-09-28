"""Regression tests for the Alertmanager-driven incident flow.

Covers the webhook contract (validation, dedup by fingerprint, lifecycle,
investigation trigger) plus the optional Slack notifier. The investigation
itself is mocked: the point of these tests is the alert plumbing, not the
LLM.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import alerts
from app.services import alert_store, investigation, opensre_cli, slack

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

RESOLVED = dict(
    FIRING,
    status="resolved",
    annotations={"summary": "p99 recovered"},
    endsAt="2026-09-28T08:05:00Z",
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Point the alert store at a temp file and stub the heavy calls.

    Teardown drains in-flight investigation threads: they resolve
    STORE_PATH at call time, so a thread that outlives its test would
    write into the NEXT test's temp store and corrupt it.
    """
    store = tmp_path / "alerts.jsonl"
    monkeypatch.setattr(alert_store, "STORE_PATH", store)

    triggered = []

    def fake_investigation(alert, source="alert"):
        triggered.append(alert)
        return {"success": True, "report": {"summary": "mock RCA"}}

    monkeypatch.setattr(investigation, "collect_alert_evidence", lambda alert, **kw: {
        "success": True, "payload": alert,
    })
    monkeypatch.setattr(opensre_cli, "investigate", fake_investigation)

    calls = []
    # Spy (not stub) so the real Slack behaviour stays observable -- the
    # "not configured" test needs the genuine return values.
    real_firing, real_resolved = slack.notify_firing, slack.notify_resolved

    def spy_firing(alert):
        calls.append(("firing", alert.get("fingerprint")))
        return real_firing(alert)

    def spy_resolved(alert):
        calls.append(("resolved", alert.get("fingerprint")))
        return real_resolved(alert)

    monkeypatch.setattr(slack, "notify_firing", spy_firing)
    monkeypatch.setattr(slack, "notify_resolved", spy_resolved)
    monkeypatch.setattr(slack, "SLACK_WEBHOOK_URL", "", raising=False)

    yield {"store": store, "triggered": triggered, "slack": calls}

    alerts.wait_for_investigations(timeout=10)


def post(alerts, **extra):
    return client.post("/api/alerts/alertmanager", json={"alerts": alerts, **extra})


# --------------------------------------------------------------------------
# Webhook validation
# --------------------------------------------------------------------------

def test_rejects_invalid_json():
    resp = client.post(
        "/api/alerts/alertmanager",
        content="{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is False
    assert "invalid JSON" in resp.json()["error"]


def test_rejects_non_object_body():
    resp = client.post("/api/alerts/alertmanager", json=[1, 2, 3])
    assert resp.json()["success"] is False
    assert "JSON object" in resp.json()["error"]


def test_rejects_empty_alert_list():
    assert client.post("/api/alerts/alertmanager", json={"alerts": []}).json()["success"] is False
    assert client.post("/api/alerts/alertmanager", json={}).json()["success"] is False


def test_skips_alert_without_labels_or_annotations():
    result = post([{"status": "firing"}]).json()["results"][0]
    assert result["status"] == "skipped"


def test_skips_alert_missing_alertname():
    result = post([{"labels": {"pod": "x"}, "annotations": {"summary": "s"}}]).json()["results"][0]
    assert result["status"] == "skipped"
    assert "alertname" in result["error"]


def test_one_bad_alert_does_not_block_a_good_one():
    body = post([
        {"labels": {"pod": "no-alertname"}, "annotations": {"summary": "bad"}},
        FIRING,
    ]).json()
    assert body["received"] == 2
    statuses = [r["status"] for r in body["results"]]
    assert statuses == ["skipped", "firing"]


# --------------------------------------------------------------------------
# Lifecycle + dedup
# --------------------------------------------------------------------------

def test_firing_creates_incident_and_triggers_investigation(isolated_store):
    result = post([FIRING]).json()["results"][0]
    assert result["created"] is True
    assert result["status"] == "firing"

    stored = alert_store.list_alerts()["alerts"][0]
    assert stored["alertname"] == "HighLatency"
    assert stored["pod"] == "catalog-api-5f86594b59-x5q6n"

    # The existing investigation engine is driven with the alert.
    _wait_for(lambda: isolated_store["triggered"])
    assert isolated_store["triggered"][0]["labels"]["alertname"] == "HighLatency"


def test_repeat_firing_is_deduplicated_into_one_incident(isolated_store):
    fingerprints = {post([FIRING]).json()["results"][0]["fingerprint"] for _ in range(4)}

    assert len(fingerprints) == 1, "fingerprint must be stable for the same label set"
    assert alert_store.list_alerts()["total"] == 1

    stored = alert_store.list_alerts()["alerts"][0]
    assert stored["notification_count"] == 4
    # One investigation, not one per repeat.
    _wait_for(lambda: isolated_store["triggered"])
    assert len(isolated_store["triggered"]) == 1


def test_distinct_pods_produce_distinct_incidents(isolated_store):
    other = dict(FIRING, labels=dict(FIRING["labels"], pod="other-pod"))
    post([FIRING])
    post([other])
    assert alert_store.list_alerts()["total"] == 2


def test_resolve_closes_the_incident(isolated_store):
    fp = post([FIRING]).json()["results"][0]["fingerprint"]
    result = post([RESOLVED]).json()["results"][0]

    assert result["status"] == "resolved"
    record = alert_store.get_alert(fp)
    assert record["status"] == "resolved"
    assert record["ends_at"] == RESOLVED["endsAt"]
    assert alert_store.active_alerts()["count"] == 0


def test_repeat_resolve_is_idempotent(isolated_store):
    fp = post([FIRING]).json()["results"][0]["fingerprint"]
    post([RESOLVED])
    second = post([RESOLVED]).json()["results"][0]

    assert second["created"] is False
    assert alert_store.get_alert(fp)["status"] == "resolved"
    assert alert_store.list_alerts()["total"] == 1


def test_refire_after_resolve_opens_a_new_lifecycle(isolated_store):
    post([FIRING])
    post([RESOLVED])
    again = post([FIRING]).json()["results"][0]

    assert again["created"] is True
    assert again["status"] == "firing"
    assert alert_store.list_alerts()["total"] == 2


# --------------------------------------------------------------------------
# Slack
# --------------------------------------------------------------------------

def test_slack_not_configured_is_a_no_op(isolated_store, monkeypatch):
    monkeypatch.setattr(slack, "SLACK_WEBHOOK_URL", "")
    assert slack.is_configured() is False
    assert slack.notify_firing({"alertname": "X"})["dispatched"] is False
    assert slack.notify_resolved({"alertname": "X"})["dispatched"] is False


def test_slack_payload_shape_for_firing():
    payload = slack.build_payload(
        {
            "fingerprint": "fp1", "alertname": "HighLatency", "severity": "critical",
            "namespace": "opensre", "pod": "catalog-api-1", "summary": "p99 high",
            "notification_count": 3,
        },
        "firing",
    )
    assert "FIRING" in payload["text"]
    blocks = payload["attachments"][0]["blocks"]
    assert "HighLatency" in blocks[0]["text"]["text"]
    assert any("catalog-api-1" in f["text"] for f in blocks[1]["fields"])


def test_slack_payload_shape_for_resolved():
    payload = slack.build_payload(
        {"fingerprint": "fp1", "alertname": "HighLatency", "namespace": "opensre", "pod": "p"},
        "resolved",
    )
    assert "RESOLVED" in payload["text"]


def test_slack_notification_fires_on_new_alert_and_recovery(isolated_store):
    post([FIRING])
    post([RESOLVED])
    kinds = [c[0] for c in isolated_store["slack"]]
    assert "firing" in kinds
    assert "resolved" in kinds


def test_slack_not_sent_for_duplicate_repeats(isolated_store):
    for _ in range(3):
        post([FIRING])
    assert [c[0] for c in isolated_store["slack"]] == ["firing"]


# --------------------------------------------------------------------------
# Read APIs
# --------------------------------------------------------------------------

def test_list_and_get_endpoints(isolated_store):
    fp = post([FIRING]).json()["results"][0]["fingerprint"]

    listing = client.get("/api/alerts").json()
    assert listing["total"] == 1
    assert listing["alerts"][0]["fingerprint"] == fp

    detail = client.get(f"/api/alerts/{fp}").json()
    assert detail["success"] is True
    assert detail["alert"]["alertname"] == "HighLatency"

    assert client.get("/api/alerts/does-not-exist").json()["success"] is False


def test_active_endpoint_only_lists_firing(isolated_store):
    other = dict(FIRING, labels=dict(FIRING["labels"], pod="still-firing-pod"))

    post([FIRING])      # will be resolved below
    post([other])       # stays firing
    post([RESOLVED])    # closes only the catalog-api incident

    active = alert_store.active_alerts()
    assert active["count"] == 1
    assert active["alerts"][0]["pod"] == "still-firing-pod"
    assert all(a["status"] == "firing" for a in active["alerts"])


def test_webhook_returns_200_for_valid_envelope(isolated_store):
    """Alertmanager retries on non-2xx, so a good envelope must be 200."""
    assert post([FIRING]).status_code == 200


def _wait_for(predicate, timeout=5.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False
